"""Head-bound readiness through publish_pending, with exact logical I/O budgets."""

import json
import subprocess

import pytest

from merge_readiness_fixtures import PollingGitHub, PublicationOnlyGh, local_run
from nightshift import queue, specloop_intake as intake
from nightshift.config import Repo


@pytest.fixture
def polling(local_run, tmp_path):
    e = local_run
    e.row.update(status="paused", pause={"reason": "human_checks", "detail": "(none)"})
    e.run.pause("human_checks", detail="(none)")
    e.repo = Repo("owner/repo", engine="specloop")
    e.gh = PollingGitHub(e.row)
    e.publication = dict(e.row, round_id="round", checks="(none)", delivered=True,
                         result={"outcome": "published", "pr": e.row["pr"]})
    e.admission_path = tmp_path / "admissions/owner__repo/1.json"
    intake._save(e.admission_path, {"change": "c", "result": {"outcome": "admitted"}})
    e.publication_path = tmp_path / "publications/owner__repo/1.json"
    intake._save(e.publication_path, e.publication)
    e.path = tmp_path / "merge-readiness/owner__repo/1.json"
    e.receipt = lambda: json.loads(e.path.read_text())
    e.gh.comments.append({"id": 1, "body": "human checks\n<!-- nightshift:publish:c:round -->"})
    e.calls, e.errors = [], []

    def status(args, **kwargs):
        e.calls.append(args)
        assert args == ["specloop", "status", "--json"]  # No publish/finish or extra status.
        return subprocess.CompletedProcess(args, 0, json.dumps({"version": 1, "runs": [e.row]}), "")

    def poll(runner=None):
        e.gh.operations.clear()
        return intake.publish_pending(e.repo, queue.Labels(), tmp_path, runner=runner or e.gh,
            specloop_runner=status, on_error=lambda n, exc: e.errors.append((n, exc)))

    e.poll = poll
    protected = {p: p.read_bytes() for p in e.run.dir.iterdir() if p.is_file()}
    yield e
    assert {p: p.read_bytes() for p in protected} == protected


@pytest.mark.scenario("merge-readiness/published-run-eligibility/delivered-publication-is-assessed")
@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/ready-pr-is-labelled-once")
@pytest.mark.parametrize("legacy", [False, True])
def test_ready_pr_is_labelled_once(polling, legacy):
    e = polling
    if legacy:
        e.publication.pop("round_id")
        intake._save(e.publication_path, e.publication)
    publication, comment = e.publication_path.read_bytes(), dict(e.gh.comments[0])
    assert e.poll() == [] and not e.errors
    assert e.gh.operations == ["view", "checks", "remove-label:merge:blocked",
                               "add-label:merge:ready", "lookup", "post"]
    assert e.gh.labels == {"unrelated", "merge:ready"}
    assert e.receipt()["delivered"] and not e.receipt()["reasons"]
    for _ in range(3):  # Fresh status and receipts from disk on every restart/poll.
        assert e.poll() == [] and e.gh.operations == ["view"]
    assert len(e.calls) == 4 and not e.errors
    assert e.publication_path.read_bytes() == publication
    assert e.gh.publication_comments == [comment] and len(e.gh.readiness_comments) == 1
    assert e.row["head"] in e.gh.readiness_comments[0]["body"]


@pytest.mark.scenario("merge-readiness/head-bound-polling/matching-assessment-is-cached")
@pytest.mark.parametrize("blocked", [False, True])
def test_matching_assessment_is_cached(polling, monkeypatch, blocked):
    e = polling
    if blocked:
        e.gh.checks = []
    e.poll()
    assert e.gh.operations.count("view") == e.gh.operations.count("checks") == 1
    receipt = e.path.read_bytes()
    assert e.receipt()["label"] == ("merge:blocked" if blocked else "merge:ready")
    e.gh.checks = [{"name": "CI", "bucket": "pass", "state": "COMPLETED"}] if blocked else []
    e.gh.pr["body"] = "## Checks a human must make\n- [x] still pre-merge"
    e.gh.labels.add("agent:revise")

    def forbidden(*args, **kwargs):
        pytest.fail("cached polls must not read evidence, specs, or revision state")

    for name in ("_readiness_evidence", "_readiness_human_checks", "_readiness_revision"):
        monkeypatch.setattr(intake, name, forbidden)
    for _ in range(3):
        assert e.poll() == [] and e.gh.operations == ["view"]
        assert e.path.read_bytes() == receipt
    assert not e.errors and len(e.calls) == 4


@pytest.mark.scenario("merge-readiness/head-bound-polling/new-head-removes-ready")
@pytest.mark.parametrize("pending", [False, True])
def test_new_head_removes_ready_label(polling, pending):
    e = polling
    e.gh.fail = "post" if pending else None
    e.poll()
    assert e.receipt()["delivered"] is not pending
    e.gh.fail = None
    e.gh.pr["headRefOid"] = "b" * 40
    publication = e.publication_path.read_bytes()
    e.poll()
    assert e.gh.operations == ["view", "remove-label:merge:ready", "checks",
                               "add-label:merge:blocked", "lookup", "post" if pending else "patch"]
    assert e.gh.labels == {"unrelated", "merge:blocked"}
    assert e.receipt()["head"] == "b" * 40 and e.receipt()["delivered"]
    assert len(e.receipt()["reasons"]) == 1 and "does not match run head" in e.receipt()["reasons"][0]
    assert e.publication_path.read_bytes() == publication
    assert len(e.gh.readiness_comments) == len(e.gh.publication_comments) == 1


@pytest.mark.scenario("merge-readiness/head-bound-polling/unlabelled-head-is-reassessed")
@pytest.mark.parametrize("refresh", ["removed", "opposite", "both", "round", "run-head"])
def test_label_or_identity_change_refreshes_assessment(polling, refresh):
    e = polling
    e.gh.checks = []
    e.poll()
    assert e.receipt()["label"] == "merge:blocked"
    e.gh.checks = [{"name": "CI", "bucket": "pass", "state": "COMPLETED"}]
    if refresh == "removed":
        e.gh.labels.remove("merge:blocked")
    elif refresh == "opposite":
        e.gh.labels = {"unrelated", "merge:ready"}
    elif refresh == "both":
        e.gh.labels.add("merge:ready")
    else:
        e.publication["round_id"] = "new-round"
        if refresh == "run-head":
            e.publication["head"] = e.commit({"tests/new.py": "new\n"})
            e.gh.pr["headRefOid"] = e.row["head"]
        intake._save(e.publication_path, e.publication)
    e.poll()
    expected = ["view", "checks"]
    if refresh not in ("removed", "opposite"):
        expected.append("remove-label:merge:blocked")
    if refresh not in ("opposite", "both"):
        expected.append("add-label:merge:ready")
    assert e.gh.operations == expected + ["lookup", "patch"]
    assert e.receipt()["label"] == "merge:ready" and not e.errors
    assert e.gh.labels == {"unrelated", "merge:ready"} and len(e.gh.readiness_comments) == 1


@pytest.mark.scenario("merge-readiness/published-run-eligibility/ineligible-run-is-ignored")
@pytest.mark.parametrize("case", ["unmanaged", "unadmitted", "running", "other-pause", "ready-pause",
    "missing", "mismatch", "refused", "undelivered", "closed", "fork", "url", "branch"])
def test_ineligible_run_is_ignored(polling, case):
    e = polling
    if case == "unmanaged":
        e.admission_path.unlink()
    elif case == "unadmitted":
        intake._save(e.admission_path, {"change": "c", "result": {"outcome": "refused"}})
    elif case == "running":
        e.row["status"] = "running"
    elif case in ("other-pause", "ready-pause"):
        e.row["pause"]["reason"] = "ready_to_publish" if case == "ready-pause" else "review"
    elif case == "missing":
        e.publication_path.unlink()
    elif case in ("mismatch", "refused", "undelivered"):
        if case == "mismatch":
            e.publication["head"] = "old"
        elif case == "refused":
            e.publication["result"] = {"outcome": "refused"}
        else:
            e.publication["delivered"] = False
        intake._save(e.publication_path, e.publication)
    else:
        field, value = {"closed": ("state", "CLOSED"), "fork": ("isCrossRepository", True),
                        "url": ("url", "wrong"), "branch": ("headRefName", "spec/other")}[case]
        e.gh.pr[field] = value
    original = [dict(c) for c in e.gh.comments]
    if case == "undelivered":
        # This poll recovers publication; readiness waits for the next poll.
        e.gh.view_fields = "state,labels,headRefName,isCrossRepository,url"
        assert e.poll() == [{"outcome": "published", "pr": e.row["pr"]}]
        assert e.gh.operations == ["view", "lookup", "patch"]
    else:
        assert e.poll() == []
        assert e.gh.operations == (["view"] if case in ("closed", "fork", "url", "branch") else [])
        assert e.gh.comments == original
    assert not e.errors and not e.path.exists() and e.gh.labels == {"unrelated", "merge:blocked"}
    assert len(e.gh.publication_comments) == 1 and not e.gh.readiness_comments


def test_head_read_failure_makes_no_speculative_writes(polling):
    e = polling
    e.poll()
    receipt = e.path.read_bytes()
    e.gh.fail = "view"
    e.poll()
    assert e.gh.operations == ["view"] and e.path.read_bytes() == receipt
    assert e.gh.labels == {"unrelated", "merge:ready"} and len(e.errors) == 1


@pytest.mark.scenario("merge-readiness/head-bound-polling/new-head-removes-ready")
def test_head_change_removes_ready_before_failing_assessment(polling, monkeypatch):
    e = polling
    e.poll()
    e.gh.pr["headRefOid"] = "b" * 40

    def unavailable(*args, **kwargs):
        assert "merge:ready" not in e.gh.labels
        raise OSError("evidence read unavailable")

    monkeypatch.setattr(intake, "_readiness_evidence", unavailable)
    e.poll()
    assert e.gh.operations == ["view", "remove-label:merge:ready", "checks"]
    assert e.gh.labels == {"unrelated"} and len(e.errors) == 1
    assert json.loads(e.publication_path.read_text())["delivered"]


@pytest.mark.scenario("merge-readiness/head-bound-polling/unlabelled-head-is-reassessed")
@pytest.mark.parametrize("blocker", ["CI", "revision", "human checks", "head"])
def test_refresh_does_not_grant_readiness_with_remaining_blocker(polling, blocker):
    e = polling
    e.poll()
    e.gh.labels.remove("merge:ready")
    if blocker == "CI":
        e.gh.checks = []
    elif blocker == "revision":
        e.gh.labels.add("agent:revise")
    elif blocker == "human checks":
        e.gh.pr["body"] = "## Checks a human must make\n- [x] manual check"
    else:
        e.gh.pr["headRefOid"] = "b" * 40
    e.poll()
    assert e.gh.operations == ["view", "checks", "add-label:merge:blocked", "lookup", "patch"]
    assert e.receipt()["label"] == "merge:blocked"
    assert any(r.startswith(f"{blocker}:") for r in e.receipt()["reasons"])
    if blocker == "head":
        e.gh.labels.add("merge:ready")  # Inconsistent labels must refresh even after removal.
        e.poll()
        assert e.gh.operations == ["view", "remove-label:merge:ready", "checks"]
    assert not e.errors


def test_incomplete_same_head_delivery_reuses_intention(polling, monkeypatch):
    e = polling
    publication = e.publication_path.read_bytes()
    e.gh.fail = "post"
    e.poll()
    assert not e.receipt()["delivered"] and len(e.errors) == 1
    e.gh.fail = None

    def forbidden(*args, **kwargs):
        pytest.fail("matching pending delivery must reconcile without reassessing")

    monkeypatch.setattr(intake, "_readiness_assessment", forbidden)
    e.poll()
    assert e.gh.operations == ["view", "lookup", "post"] and e.receipt()["delivered"]
    assert e.publication_path.read_bytes() == publication and len(e.gh.readiness_comments) == 1


@pytest.mark.scenario("merge-readiness/head-bound-polling/unlabelled-head-is-reassessed")
def test_incomplete_unlabelled_head_is_reassessed(polling):
    e = polling
    e.gh.fail = "add-label:merge:ready"
    e.poll()
    assert not e.receipt()["delivered"] and e.gh.labels == {"unrelated"}
    e.gh.fail, e.gh.checks = None, []
    e.poll()
    assert e.gh.operations == ["view", "checks", "add-label:merge:blocked", "lookup", "post"]
    assert e.receipt()["delivered"] and e.receipt()["label"] == "merge:blocked"


def test_readiness_failure_continues_other_admissions(polling):
    e = polling
    # Both receipts identify the same fixture run; a failed first PR must not stop the batch.
    intake._save(e.admission_path.with_name("2.json"), {"change": "c", "result": {"outcome": "admitted"}})
    intake._save(e.publication_path.with_name("2.json"), e.publication)
    observed = []

    def github(args):
        assert args[:2] == ["pr", "view"]
        observed.append(args[2])
        if args[2] == "1":
            raise OSError("PR read unavailable")
        return json.dumps({"state": "CLOSED"})

    assert e.poll(runner=github) == [] and observed == ["1", "2"]
    assert [n for n, _ in e.errors] == [1]
    assert isinstance(e.errors[0][1], OSError)
    assert len(e.calls) == 1 and not e.path.exists()


def test_legacy_fake_retains_readiness_effects_and_poll_budget(polling):
    e, gh = polling, PublicationOnlyGh(labels=())
    gh.pr_url, gh.branch, gh.head = e.row["pr"], "spec/c", e.row["head"]
    e.poll(runner=gh)
    assert not e.errors and e.receipt()["label"] == "merge:ready"
    assert [c[:2] for c in gh.calls] == [["pr", "view"], ["pr", "checks"], ["pr", "edit"],
                                      ["api", "repos/owner/repo/issues/1/comments"], ["pr", "comment"]]
    assert gh.readiness.labels == {("owner/repo", "1"): {"merge:ready"}}
    assert len(gh.readiness.comments[("owner/repo", "1")]) == 1 and not gh.comments and not gh.labels
    e.poll(runner=gh)
    assert len(gh.calls) == 6 and gh.calls[-1][:2] == ["pr", "view"] and not e.errors

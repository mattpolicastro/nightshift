"""Recoverable readiness effects through the real gh retry/marker machinery."""

import json
import subprocess

import pytest

from merge_readiness_fixtures import local_run
from nightshift import daemon, queue, specloop_intake as intake
from nightshift.config import Repo


REPO = Repo("owner/repo", engine="specloop")
MARKER = "<!-- nightshift:approval:c:merge-readiness -->"


class GitHub:
    def __init__(self, env):
        self.pr = dict(headRefOid=env.row["head"], labels=[{"name": "unrelated"},
                       {"name": "merge:blocked"}], body="## Checks a human must make\n(none)")
        self.comments = [{"id": 1, "body": "publication\n<!-- nightshift:publish:c:round -->"}]
        self.operations = []
        self.fail = self.lose = self.pause = None
        self.lost_error = "HTTP 502 lost response"
        self.pause_before_ack = False
        self.broken_lookup = False
        self.real_run = subprocess.run

    def run(self, args, **kwargs):
        if args[0] != "gh":
            return self.real_run(args, **kwargs)
        assert not daemon.paused(), "no subprocess may begin while paused"
        assert self.receipt()["delivered"] is False, "intent must be durable before effects"
        a = args[1:]
        if a[:2] == ["pr", "edit"]:
            assert a[2:5] == ["1", "--repo", REPO.name]
            op = "remove" if a[5] == "--remove-label" else "add"
            assert a[5] in ("--remove-label", "--add-label") and len(a) == 7
        elif a[:2] == ["pr", "comment"]:
            op = "post"
            assert a[2:6] == ["1", "--repo", REPO.name, "--body"] and len(a) == 7
        elif a[0] == "api" and "--method" in a:
            op = "patch"
            assert a[1] == f"repos/{REPO.name}/issues/comments/2"
            assert a[2:5] == ["--method", "PATCH", "-f"] and len(a) == 6
        else:
            op = "lookup"
            assert a == ["api", f"repos/{REPO.name}/issues/1/comments", "--paginate", "--slurp"]
        self.operations.append(op)
        if self.fail == op or op == "lookup" and self.broken_lookup:
            self.fail = None
            return subprocess.CompletedProcess(args, 1, "", "HTTP 403 denied")
        if op == "remove":
            self.pr["labels"] = [label for label in self.pr["labels"] if label["name"] != a[-1]]
        elif op == "add" and a[-1] not in self.labels:
            self.pr["labels"].append({"name": a[-1]})
        elif op == "post":
            self.comments.append({"id": 2, "body": a[-1]})
        elif op == "patch":
            self.comments[1]["body"] = a[-1].removeprefix("body=")
        if self.pause == op:
            self.pause = None
            daemon.PAUSE_FILE.touch()
        if self.lose == op:
            self.lose = None
            return subprocess.CompletedProcess(args, 1, "", self.lost_error)
        # Readiness lives on a later page, beyond publication and a human comment.
        pages = [self.comments[:1], [{"id": 99, "body": "human comment"}], self.comments[1:]]
        return subprocess.CompletedProcess(args, 0, json.dumps(pages) if op == "lookup" else "", "")

    @property
    def labels(self):
        return {label["name"] for label in self.pr["labels"]}

    def gh(self, args):
        # The daemon guards each logical operation and every production retry.
        def check():
            if daemon.paused():
                raise intake.Paused()
        check()
        with queue.gh_guard(check):
            result = queue._gh(args, sleep=lambda _: None)
        check()
        if self.pause_before_ack and self.operations[-1] == "post":
            daemon.PAUSE_FILE.touch()
        return result


@pytest.fixture
def delivery(local_run, tmp_path, monkeypatch):
    env = local_run
    gh = GitHub(env)
    publication = dict(env.row, round_id="round", delivered=True,
                       result={"outcome": "published", "pr": env.row["pr"]})
    for folder, data in (("publications", publication), ("revisions", {"change": "other"})):
        intake._save(tmp_path / folder / "owner__repo/1.json", data)
    protected = [tmp_path / folder / "owner__repo/1.json" for folder in ("publications", "revisions")]
    protected += [p for p in env.run.dir.iterdir() if p.is_file()]
    before = {p: p.read_bytes() for p in protected}
    monkeypatch.setattr(queue.subprocess, "run", gh.run)
    env.assessment = intake._readiness_assessment(REPO, queue.Labels(), tmp_path, 1, env.row,
        gh.pr, runner=lambda _: '[{"name":"CI","bucket":"pass","state":"COMPLETED"}]')
    env.path = tmp_path / "merge-readiness/owner__repo/1.json"
    env.receipt = lambda: json.loads(env.path.read_text())
    gh.receipt = env.receipt
    env.deliver = lambda: intake._deliver_readiness(REPO, tmp_path, 1, env.row, publication,
                                                   gh.pr, env.assessment, runner=gh.gh)
    env.gh, env.publication = gh, publication
    yield env
    assert {p: p.read_bytes() for p in protected} == before
    assert gh.comments[0] == {"id": 1, "body": "publication\n<!-- nightshift:publish:c:round -->"}
    assert "unrelated" in gh.labels


@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/ready-pr-is-labelled-once")
@pytest.mark.parametrize("legacy", [False, True])
def test_ready_pr_is_labelled_once(delivery, legacy):
    e = delivery
    if legacy:
        e.publication.pop("round_id")
    e.deliver()
    assert e.gh.labels == {"unrelated", "merge:ready"}
    receipt = e.receipt()
    assert receipt == dict(e.assessment, change="c", pr=e.row["pr"], delivered=True,
                          publication_round=[e.row[k] for k in ("change", "head", "pr")] if legacy else "round")
    assert e.gh.operations == ["remove", "add", "lookup", "post"]
    assert e.gh.comments[1]["body"] == f"{queue.SELF_COMMENT_PREFIX} {e.assessment['body']}\n\n{MARKER}"
    for _ in range(3):  # Each delivery reloads disk state, including after a restart.
        e.deliver()
    assert e.receipt() == receipt and len(e.gh.comments) == 2
    assert e.gh.operations == ["remove", "add", "lookup", "post"]


@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/blocked-comment-lists-conditions")
def test_blockers_update_the_same_comment(delivery):
    e = delivery
    e.deliver()
    pr = dict(e.gh.pr, headRefOid="b" * 40, body="unknown",
              labels=[*e.gh.pr["labels"], {"name": "agent:revise"}])
    e.assessment = intake._readiness_assessment(REPO, queue.Labels(), e.path.parents[2], 1,
        e.row, pr, runner=lambda _: '[]')
    e.gh.pr = pr
    e.deliver()
    assert e.gh.labels == {"unrelated", "agent:revise", "merge:blocked"}
    assert [r.split(":")[0] for r in e.assessment["reasons"]] == ["head", "CI", "revision", "human checks"]
    assert e.assessment["body"] in e.gh.comments[1]["body"]
    assert len(e.gh.comments) == 2 and e.gh.operations[-4:] == ["remove", "add", "lookup", "patch"]
    operations = list(e.gh.operations)
    e.deliver()
    assert e.gh.operations == operations


@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/ambiguous-writes-recover")
@pytest.mark.parametrize("effect", ["remove", "add", "post", "patch"])
@pytest.mark.parametrize("internal_retry", [True, False])
def test_production_retries_reconcile_lost_responses(delivery, effect, internal_retry):
    e = delivery
    if effect == "patch":
        e.gh.comments.append({"id": 2, "body": f"old assessment\n{MARKER}"})
    e.gh.lose = effect
    if not internal_retry:
        e.gh.lost_error = "response lost without a retryable diagnosis"
        with pytest.raises(queue.GhError):
            e.deliver()
        assert not e.receipt()["delivered"]
    e.deliver()
    assert e.receipt()["delivered"] and e.gh.labels == {"unrelated", "merge:ready"}
    assert len(e.gh.comments) == 2 and e.assessment["body"] in e.gh.comments[1]["body"]
    expected = {"remove": ["remove", "remove", "add", "lookup", "post"],
                "add": ["remove", "add", "add", "lookup", "post"],
                "post": ["remove", "add", "lookup", "post", "lookup", "patch"],
                "patch": ["remove", "add", "lookup", "patch", "patch"]}
    if not internal_retry:
        expected["remove"] = expected["add"] = ["remove", "add", "lookup", "post"]
        expected["patch"] = ["remove", "add", "lookup", "patch", "lookup", "patch"]
    assert e.gh.operations == expected[effect]


@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/ambiguous-writes-recover")
def test_failed_retry_lookup_never_blind_posts(delivery, monkeypatch):
    e = delivery
    e.gh.lose = "post"
    real_run = e.gh.run
    def run(args, **kwargs):
        result = real_run(args, **kwargs)
        if e.gh.operations[-1] == "post":
            e.gh.broken_lookup = True
        return result
    monkeypatch.setattr(queue.subprocess, "run", run)
    with pytest.raises(queue.GhError):
        e.deliver()
    assert e.gh.operations == ["remove", "add", "lookup", "post", "lookup"]
    assert not e.receipt()["delivered"] and len(e.gh.comments) == 2
    e.gh.broken_lookup = False
    e.deliver()
    assert e.gh.operations[-2:] == ["lookup", "patch"] and e.receipt()["delivered"]


@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/pause-or-write-failure-retains-pending-delivery")
@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/ambiguous-writes-recover")
@pytest.mark.parametrize("effect", ["remove", "add", "lookup", "post", "patch", "ack"])
@pytest.mark.parametrize("failure", ["pause", "write", "retry_pause"])
def test_interruption_retains_pending_and_reconciles(delivery, monkeypatch, effect, failure):
    e = delivery
    if effect == "patch":
        e.gh.comments.append({"id": 2, "body": f"old\n{MARKER}"})
    real_save = intake._save
    def save(path, receipt):
        if path == e.path and receipt["delivered"]:
            raise OSError("local acknowledgement lost")
        real_save(path, receipt)
    if effect == "ack":
        if failure == "write":
            monkeypatch.setattr(intake, "_save", save)
        else:
            e.gh.pause_before_ack = True
    else:
        setattr(e.gh, "fail" if failure == "write" else "pause", effect)
        if failure == "retry_pause":
            e.gh.lose = effect
    with pytest.raises((intake.Paused, queue.GhError, OSError)):
        e.deliver()
    assert not e.receipt()["delivered"]
    if effect != "ack":
        assert e.gh.operations[-1] == effect
    operations = len(e.gh.operations)
    daemon.PAUSE_FILE.unlink(missing_ok=True)
    e.gh.pause_before_ack = False
    monkeypatch.setattr(intake, "_save", real_save)
    e.deliver()
    assert e.receipt()["delivered"] and len(e.gh.comments) == 2
    assert e.gh.labels == {"unrelated", "merge:ready"}
    if effect in ("lookup", "post", "patch", "ack"):
        assert "add" not in e.gh.operations[operations:]


@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/ambiguous-writes-recover")
def test_stale_pending_ready_is_never_replayed(delivery):
    e = delivery
    e.gh.fail = "post"
    with pytest.raises(queue.GhError):
        e.deliver()
    e.gh.pr["headRefOid"] = "b" * 40
    operations = list(e.gh.operations)
    with pytest.raises(ValueError, match="stale"):
        e.deliver()
    assert e.gh.operations == operations and not e.receipt()["delivered"]
    e.assessment = intake._readiness_assessment(REPO, queue.Labels(), e.path.parents[2], 1,
        e.row, e.gh.pr, runner=lambda _: '[]')
    e.deliver()
    assert e.gh.labels == {"unrelated", "merge:blocked"}
    assert e.gh.operations[len(operations):] == ["remove", "add", "lookup", "post"]
    assert "b" * 40 in e.gh.comments[1]["body"]


@pytest.mark.parametrize("field,value", [("delivered", 1), ("label", "other"), ("reasons", {}),
    ("head", None), ("run_head", ""), ("publication_round", []), ("body", None), ("pr", 1),
    ("publication_round", ["wrong"] * 3), ("head", "stale"), ("reasons", ["unexpected"]), ("change", "../bad")])
def test_invalid_known_fields_are_not_reset(delivery, field, value):
    e = delivery
    e.deliver()
    receipt = e.receipt()
    receipt[field] = value
    intake._save(e.path, receipt)
    before = e.path.read_bytes(), list(e.gh.operations)
    with pytest.raises(ValueError):
        e.deliver()
    assert (e.path.read_bytes(), e.gh.operations) == before


def test_unknown_receipt_fields_survive_reassessment(delivery):
    e = delivery
    e.deliver()
    intake._save(e.path, {**e.receipt(), "future": {"keep": True}})
    e.publication["round_id"] = "next-round"
    e.deliver()
    assert e.receipt()["future"] == {"keep": True}
    assert e.receipt()["publication_round"] == "next-round"
    assert e.gh.operations[-2:] == ["lookup", "patch"] and len(e.gh.comments) == 2

"""Separate readiness effects after recovering a real merged-head publication."""

import json

import pytest

from nightshift import daemon, queue, specloop_intake as intake
from specloop import cli, state
from test_specloop_base_publication import publication, scheduled
from test_specloop_base_sync import advance_base, base_env, sh


VIEW = ["pr", "view", "8", "--repo", "owner/sample", "--json",
        "state,labels,headRefName,headRefOid,isCrossRepository,url,body"]


def operation(args):
    if args[:2] == ["pr", "edit"]:
        assert args[5] in ("--add-label", "--remove-label") and len(args) == 7
        return f"{args[5][2:]}:{args[6]}"
    if args[0] == "api":
        return "patch" if "PATCH" in args else "lookup"
    assert args[:2] in (["pr", "view"], ["pr", "checks"], ["pr", "comment"])
    return "post" if args[1] == "comment" else args[1]


@pytest.fixture
def merged_publication(publication, tmp_path):
    p = publication
    old, base = p.ctx.head(), advance_base(p.env)
    assert len(p.poll()) == 1
    old_receipt = p.receipt()
    scheduled(p)
    assert cli.main(["run", "c"]) == 0
    head = p.ctx.head()
    assert sh(p.ctx.repo, "git", "show", "-s", "--format=%P", head).split() == [old, base]
    # The base-publication fixture deliberately has no evidence. Supply a
    # measured section here so both ready and blocked integration paths run.
    run = state.Run.for_change("c", require_existing=True)
    run.state.evidenced = [1]
    run.save()
    with (run.dir / "events.jsonl").open("a") as events:
        events.write(json.dumps(dict(event="evidence", section=1, passed=True,
                                     score=0.9, timeouts=0, uncovered=[])) + "\n")
    p.lose_published_response = True
    assert len(p.poll()) == 1
    assert not p.receipt().get("delivered")
    assert p.receipt()["head"] == head and p.receipt()["round_id"] != old_receipt["round_id"]
    assert p.status()["pause"]["reason"] == "human_checks"
    assert p.poll() == []  # Recover publication only; assessment waits one poll.
    assert p.receipt()["delivered"] and p.receipt()["result"]["outcome"] == "published"
    assert p.gh.labels == set() and not p.gh.readiness_comments
    assert len(p.gh.publication_comments) == 1
    assert [operation(a) for a in p.gh.calls] == ["view", "view", "view", "lookup", "post"]
    comment = dict(p.gh.publication_comments[0])
    assert f"<!-- nightshift:publish:c:{p.receipt()['round_id']} -->" in comment["body"]
    assert head[:12] in comment["body"] and old[:12] not in comment["body"]
    revision = tmp_path / "revisions/owner__sample/8.json"
    intake._save(revision, {"change": "other", "future": {"preserve": True}})
    protected = [tmp_path / "publications/owner__sample/8.json", revision]
    protected += [path for path in run.dir.iterdir() if path.is_file()]
    snapshot = {path: path.read_bytes() for path in protected}
    effects = list(p.operations)
    p.readiness_path = tmp_path / "merge-readiness/owner__sample/8.json"
    p.readiness = lambda: json.loads(p.readiness_path.read_text())
    p.assessment_calls = []

    def assess(runner=None):
        start, calls = len(p.gh.calls), len(p.calls)
        p.ctx.run.reload()  # Reload persisted CLI state on each simulated restart.
        errors = p.poll_once(gh_runner=runner)
        p.assessment_calls = p.gh.calls[start:]
        assert p.calls[calls:] == ["status"]
        assert p.assessment_calls[0] == VIEW
        return errors

    p.assess = assess
    yield p
    assert {path: path.read_bytes() for path in protected} == snapshot
    assert p.gh.publication_comments == [comment]
    assert p.gh.comments == p.gh.publication_comments + p.gh.readiness_comments
    assert p.calls.count("publish") == 2  # Handoff and stable publication only.
    assert p.operations == effects and p.merges == p.verified == p.pushes == [head]
    assert p.ctx.head() == head and p.status()["pause"]["reason"] == "human_checks"


@pytest.mark.scenario("merge-readiness/published-run-eligibility/delivered-publication-is-assessed")
@pytest.mark.scenario("merge-readiness/head-bound-polling/matching-assessment-is-cached")
@pytest.mark.parametrize("blocked", [False, True])
def test_recovered_publication_has_independent_cached_assessment(merged_publication, monkeypatch, blocked):
    p = merged_publication
    if blocked:  # A non-CI blocker: CI blockers are reassessed each poll.
        p.gh.body = "## Checks a human must make\n- [ ] no citation here"
    label = "merge:blocked" if blocked else "merge:ready"
    assert p.assess() == []
    assert [operation(a) for a in p.assessment_calls] == [
        "view", "checks", f"add-label:{label}", "lookup", "post"]
    receipt = p.readiness()
    assert receipt["label"] == label and receipt["delivered"]
    assert receipt["head"] == receipt["run_head"] == p.ctx.head()
    assert receipt["publication_round"] == p.receipt()["round_id"]
    assert bool(receipt["reasons"]) is blocked
    assert p.gh.labels == {label} and len(p.gh.readiness_comments) == 1
    assert p.ctx.head() in p.gh.readiness_comments[0]["body"]
    assert "<!-- nightshift:approval:c:merge-readiness -->" in p.gh.readiness_comments[0]["body"]
    p.gh.checks = [{"name": "CI", "bucket": "pass", "state": "COMPLETED"}] if blocked else []
    p.gh.body = "## Checks a human must make\n- [x] changed checklist"

    def forbidden(*args, **kwargs):
        pytest.fail("cached assessment must not read evidence, specs, or revision state")

    for name in ("_readiness_evidence", "_readiness_human_checks", "_readiness_revision"):
        monkeypatch.setattr(intake, name, forbidden)
    before = p.readiness_path.read_bytes(), [dict(c) for c in p.gh.comments]
    for _ in range(3):
        assert p.assess() == [] and p.assessment_calls == [VIEW]
        assert (p.readiness_path.read_bytes(), p.gh.comments) == before


@pytest.mark.scenario("merge-readiness/head-bound-polling/new-head-removes-ready")
@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/ambiguous-writes-recover")
@pytest.mark.parametrize("pending", [False, True])
def test_changed_head_never_replays_merged_head_ready(merged_publication, pending):
    p = merged_publication

    def lost(args):
        result = p.gh(args)
        if operation(args) == "post":
            raise OSError("accepted readiness comment response lost")
        return result

    assert len(p.assess(lost if pending else None)) == int(pending)
    assert p.readiness()["delivered"] is not pending
    assert p.gh.labels == {"merge:ready"}
    accepted = dict(p.gh.readiness_comments[0])
    p.gh.head = advance_base(p.env, "another upstream head\n")
    assert p.assess() == []
    assert [operation(a) for a in p.assessment_calls] == [
        "view", "remove-label:merge:ready", "checks", "add-label:merge:blocked", "lookup", "patch"]
    assert p.gh.labels == {"merge:blocked"}
    assert p.readiness()["head"] == p.gh.head and p.readiness()["delivered"]
    assert len(p.readiness()["reasons"]) == 1
    assert "does not match run head" in p.readiness()["reasons"][0]
    assert len(p.gh.readiness_comments) == 1
    assert p.gh.readiness_comments[0]["id"] == accepted["id"]
    assert p.gh.head in p.gh.readiness_comments[0]["body"]
    assert p.assess() == [] and p.assessment_calls == [VIEW]


@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/ambiguous-writes-recover")
@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/pause-or-write-failure-retains-pending-delivery")
@pytest.mark.parametrize("effect", ["add-label:merge:ready", "post", "ack"])
@pytest.mark.parametrize("failure", ["lost", "write", "pause"])
def test_readiness_interruption_preserves_merged_publication(merged_publication, monkeypatch, effect, failure):
    p = merged_publication
    attempts = []
    real_save = intake._save

    def save(path, receipt):
        if path == p.readiness_path and receipt["delivered"]:
            raise OSError("readiness acknowledgement lost")
        return real_save(path, receipt)

    def interrupted(args):
        if daemon.paused():
            raise intake.Paused()
        op = operation(args)
        attempts.append(op)
        if op == effect and failure == "write":
            raise queue.GhError("readiness write denied", transient=False)
        result = p.gh(args)
        if op == effect:
            if failure == "pause":
                daemon.PAUSE_FILE.touch()
                raise intake.Paused()
            raise OSError("readiness response lost after remote acceptance")
        if effect == "ack" and failure == "pause" and op == "post":
            daemon.PAUSE_FILE.touch()  # Exercise the production pre-ack pause check.
        return result

    if effect == "ack" and failure != "pause":
        monkeypatch.setattr(intake, "_save", save)
    if failure == "pause":
        with pytest.raises(intake.Paused):
            p.assess(interrupted)
    else:
        assert len(p.assess(interrupted)) == 1
    assert not p.readiness()["delivered"]
    expected = ["view", "checks", "add-label:merge:ready"]
    if effect != "add-label:merge:ready":
        expected += ["lookup", "post"]
    assert attempts == expected  # No external operation after the interruption.
    assert p.gh.labels == (set() if effect == "add-label:merge:ready" and failure == "write"
                           else {"merge:ready"})
    accepted = effect == "ack" or effect == "post" and failure != "write"
    assert len(p.gh.readiness_comments) == int(accepted)
    daemon.PAUSE_FILE.unlink(missing_ok=True)
    monkeypatch.setattr(intake, "_save", real_save)
    assert p.assess() == []
    retry = ["view"]
    if effect == "add-label:merge:ready" and failure == "write":
        retry += ["checks", "add-label:merge:ready"]
    assert [operation(a) for a in p.assessment_calls] == retry + ["lookup", "patch" if accepted else "post"]
    assert p.readiness()["delivered"] and p.gh.labels == {"merge:ready"}
    assert len(p.gh.readiness_comments) == 1
    assert p.assess() == [] and p.assessment_calls == [VIEW]


@pytest.mark.scenario("merge-readiness/recoverable-readiness-delivery/ambiguous-writes-recover")
def test_failed_reconciliation_does_not_duplicate_either_comment(merged_publication):
    p = merged_publication

    def lost(args):
        result = p.gh(args)
        if operation(args) == "post":
            raise OSError("accepted readiness comment response lost")
        return result

    assert len(p.assess(lost)) == 1
    accepted = dict(p.gh.readiness_comments[0])
    attempts = []

    def unavailable(args):
        attempts.append(operation(args))
        if operation(args) == "lookup":
            raise OSError("comment reconciliation unavailable")
        return p.gh(args)

    assert len(p.assess(unavailable)) == 1
    assert attempts == ["view", "lookup"] and not p.readiness()["delivered"]
    assert p.gh.readiness_comments == [accepted]
    assert p.assess() == []
    assert [operation(a) for a in p.assessment_calls] == ["view", "lookup", "patch"]
    assert p.readiness()["delivered"] and p.gh.readiness_comments == [accepted]

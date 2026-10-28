"""Committed design-only checks through real readiness, publication and recovery."""

import json

import pytest

from nightshift import specloop_intake as intake
from nightshift.cli import main
from specloop import cli, loop
from test_specloop_base_publication import publication
from test_specloop_base_sync import advance_base, base_env, commit, sh


CHECKS = [
    "- [ ] Observe `cap/checks/observe` during rollout.",
    "- [ ] Recheck `cap/checks/observe` after reopening.",
]
SPEC_BODY = "Closes #7\n\nImplementation of the approved spec."


@pytest.fixture
def design_publication(publication, tmp_path, request):
    p, ctx = publication, publication.ctx
    # Remove the fixture's scenario checks from both the loaded and saved manifest.
    path = ctx.run.manifest_path
    path.write_text(path.read_text().split("[coverage.exempt]")[0])
    ctx.m = cli._manifest(ctx.run)
    assert ctx.m.coverage_exempt == {}
    scenario = "Other" if getattr(request, "param", "unannotated") == "unmapped" else "Observe"
    commit(p.env.src, "openspec/changes/c/specs/cap/spec.md",
           f"### Requirement: Checks\n#### Scenario: {scenario}\n- WHEN rollout occurs\n")
    commit(p.env.src, "openspec/changes/c/design.md",
           f"## Human checks\n{CHECKS[0]}\n"
           f"### Verification and human rollout checklist\n{CHECKS[1]}\n")
    sh(ctx.repo, "git", "fetch", "-q", str(p.env.src), f"+spec/c:{ctx.ref}")
    ctx.run.reload()
    ctx.run.state.evidenced = [1]
    ctx.run.save()
    ctx.run.event("evidence", section=1, passed=True, score=0.9, timeouts=0, uncovered=[])
    assert loop.finals(ctx, ctx.head()) is False
    p.ready = p.status()
    assert p.ready["pause"]["reason"] == "ready_to_publish"
    detail = p.ready["pause"]["detail"]
    assert detail.splitlines() == [
        f"all tasks landed at {ctx.head()[:12]}; human checks after publishing:",
        *CHECKS, "Next: `specloop publish c`",
    ]
    p.gh.body = SPEC_BODY
    p.gh.head = ctx.head()
    p.receipt_path = tmp_path / "publications/owner__sample/8.json"
    assert not p.receipt_path.exists()  # Readiness already contains the checks before capture.
    p.readiness_path = tmp_path / "merge-readiness/owner__sample/8.json"
    historical = p.receipt_path.with_name("99.json")
    intake._save(historical, dict(change="old", head=p.env.initial,
                                 pr="https://github.com/owner/sample/pull/99",
                                 checks="- [ ] Historical check", round_id="old-round",
                                 result={"outcome": "published"}, delivered=True))
    before = historical.read_bytes()
    p.calls.clear()
    p.fetches.clear()
    p.operations.clear()
    yield p
    assert historical.read_bytes() == before
    assert p.gh.body == SPEC_BODY
    assert not any(call[:2] == ["pr", "create"] or
                   (call[:2] == ["pr", "edit"] and "--body" in call) for call in p.gh.calls)


def assert_delivered(p):
    receipt = p.receipt()
    assert (receipt["change"], receipt["head"], receipt["pr"]) == (
        "c", p.ready["head"], p.ready["pr"])
    assert receipt["checks"] == p.ready["pause"]["detail"]
    assert receipt["delivered"] is True
    assert receipt["result"]["outcome"] == "published"
    assert receipt["result"]["pr"] == p.gh.pr_url == p.ready["pr"]
    assert len(p.gh.publication_comments) == 1
    body = p.gh.publication_comments[0]["body"]
    assert "\n".join(CHECKS) in body
    assert f"<!-- nightshift:publish:c:{receipt['round_id']} -->" in body
    assert p.ready["head"][:12] in body and p.gh.pr_url in body
    assert "Review hosted CI on the PR" in body
    assert "specloop finish c" in body and "specloop publish c" not in body
    assert p.status()["pause"]["reason"] == "human_checks"


@pytest.mark.scenario("issue-intake/design-checks-survive-publication/design-only-checks-reach-the-receipt")
def test_design_human_checks_reach_publication(design_publication, monkeypatch):
    p = design_publication
    push = cli._push

    def captured_before_push(*args):
        assert p.receipt()["checks"] == p.ready["pause"]["detail"]
        assert "result" not in p.receipt() and not p.receipt().get("delivered")
        return push(*args)

    monkeypatch.setattr(cli, "_push", captured_before_push)
    assert p.poll_once() == []
    assert p.documents[-1] == {"version": 1, "outcome": "published", "pr": p.gh.pr_url}
    assert_delivered(p)
    assert p.calls.count("publish") == 1
    assert p.operations == [("fetch", 20), ("push", p.ready["head"])]
    assert sh(p.env.origin, "git", "rev-parse", "refs/heads/spec/c") == p.ready["head"]
    assert not p.merges and not p.verified
    assert p.gh.comments == p.gh.publication_comments


@pytest.mark.scenario("issue-intake/design-checks-survive-publication/design-only-checks-reach-the-receipt")
def test_design_checks_survive_base_preparation_handoff(design_publication):
    p = design_publication
    old = p.ready["head"]
    base = advance_base(p.env)
    assert len(p.poll_once()) == 1
    pending = p.receipt()
    assert pending["checks"] == p.ready["pause"]["detail"]
    assert "result" not in pending and not p.pushes and not p.gh.comments
    assert p.status()["pause"] is None
    assert main(["run", "c"]) == 0
    p.ready = p.status()
    head = p.ready["head"]
    assert head != old and p.ready["pause"]["reason"] == "ready_to_publish"
    assert p.ready["pause"]["detail"].splitlines()[1:-1] == CHECKS
    assert sh(p.ctx.repo, "git", "show", "-s", "--format=%P", head).split() == [old, base]
    assert p.poll_once() == []
    assert_delivered(p)
    assert p.receipt()["round_id"] != pending["round_id"]
    assert p.fetches == [20, 300, 20]
    assert p.merges == p.verified == p.pushes == [head]


@pytest.mark.scenario("issue-intake/design-checks-survive-publication/recovery-retains-design-checks")
@pytest.mark.parametrize("failure", ["publish_response", "comment_before", "comment_after", "ack"])
def test_design_checks_survive_publication_recovery(design_publication, monkeypatch, failure):
    p = design_publication
    p.lose_published_response = failure == "publish_response"
    save = intake._save

    def interrupted_save(path, receipt):
        if failure == "ack" and path == p.receipt_path and receipt.get("delivered"):
            raise OSError("publication acknowledgement interrupted")
        return save(path, receipt)

    def interrupted_comment(args):
        if args[:2] == ["pr", "comment"] and failure == "comment_before":
            raise OSError("comment delivery interrupted")
        result = p.gh(args)
        if args[:2] == ["pr", "comment"] and failure == "comment_after":
            raise OSError("accepted comment response lost")
        return result

    with monkeypatch.context() as interruption:
        interruption.setattr(intake, "_save", interrupted_save)
        assert len(p.poll_once(interrupted_comment)) == 1
    pending = p.receipt()
    assert pending["checks"] == p.ready["pause"]["detail"]
    assert not pending.get("delivered")
    assert p.status()["pause"]["reason"] == "human_checks"
    assert len(p.gh.publication_comments) == int(failure in ("comment_after", "ack"))
    accepted = [dict(comment) for comment in p.gh.publication_comments]

    def unavailable_lookup(args):
        if args[0] == "api" and args[1].endswith("/comments"):
            raise OSError("comment reconciliation unavailable")
        return p.gh(args)

    assert len(p.poll_once(unavailable_lookup)) == 1
    assert not p.receipt().get("delivered")
    assert p.gh.publication_comments == accepted
    p.ctx.run.reload()  # Simulate a fresh service poll using persisted CLI/receipt state.
    assert p.poll_once() == []
    assert_delivered(p)
    for key in ("change", "head", "pr", "round_id", "checks"):
        assert p.receipt()[key] == pending[key]
    if accepted:
        assert p.gh.publication_comments == accepted
    assert p.calls.count("publish") == 1 and p.pushes == [p.ready["head"]]
    assert p.fetches == [20]
    assert p.poll_once() == []  # Delivered polls assess readiness, never publish again.
    assert p.calls.count("publish") == 1 and len(p.gh.publication_comments) == 1


@pytest.mark.scenario("issue-intake/design-checks-survive-publication/unannotated-design-check-blocks-readiness")
@pytest.mark.parametrize("design_publication", ["unannotated", "unmapped"], indirect=True)
def test_design_receipt_blocks_merge_readiness(design_publication):
    p = design_publication
    assert p.poll_once() == []
    assert_delivered(p)
    saved = p.receipt_path.read_bytes()
    assert p.poll_once() == []
    assessment = json.loads(p.readiness_path.read_text())
    assert assessment["delivered"] is True and assessment["label"] == "merge:blocked"
    # Exact reasons establish that CI, evidence, head identity and revision all pass.
    assert assessment["reasons"] == [
        "human checks: outstanding or unknown post-deploy annotation for `cap/checks/observe`",
    ]
    assert assessment["head"] == assessment["run_head"] == p.ready["head"]
    assert assessment["publication_round"] == p.receipt()["round_id"]
    assert p.gh.labels == {"merge:blocked"} and len(p.gh.readiness_comments) == 1
    assert p.receipt_path.read_bytes() == saved
    assert intake._readiness_human_checks(
        p.status(), "## Checks a human must make\n(none)",
        publication_checks=p.receipt()["checks"]) == []  # Explicit PR body still takes precedence.
    assert p.calls.count("publish") == 1 and len(p.gh.publication_comments) == 1

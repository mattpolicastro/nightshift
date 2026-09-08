"""The workflow-scope refusal is correct. It used to fire far too late.

BACKLOG §8. Two of the 107 tracebacks are this, both swift-app #14 on
2026-08-08:

    ! [remote rejected] claude/14 -> claude/14 (refusing to allow a Personal
    Access Token to create or update workflow `.github/workflows/ci.yml`
    without `workflow` scope)

**The fix is not to grant the scope.** Withholding Workflows from the PAT is
what makes CI a genuinely held-out verifier: an agent physically cannot edit
the thing that judges its own work, so the guarantee rests on the credential
rather than on the agent choosing to respect it.

What was wrong was the timing, and one consequence the backlog did not record:
the refusal reached the daemon's crash handler, which RELEASES the issue and
deletes the branch — so the work became claimable again and failed at the same
push, spending another full budget each time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nightshift import queue, task, vcs
from nightshift.queue import Labels

@pytest.fixture(autouse=True)
def claim_dir(tmp_path, monkeypatch):
    """Claims are written to a real `~/.nightshift/claims` otherwise — which
    leaks between runs AND into the live daemon's state. The same fixture
    `test_queue.py` has carried since the beginning."""
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")


REJECTION = (
    "! [remote rejected] claude/14 -> claude/14 (refusing to allow a Personal "
    "Access Token to create or update workflow `.github/workflows/ci.yml` "
    "without `workflow` scope)"
)


# --- the late refusal, reported in its own words ----------------------------


def test_the_scope_rejection_is_distinguishable_from_any_other_push_failure(
    monkeypatch, tmp_path
):
    def failing(args, cwd=None):
        raise RuntimeError(f"git push origin… failed: {REJECTION}")

    monkeypatch.setattr(vcs, "_run", failing)
    with pytest.raises(vcs.WorkflowScopeRefusal):
        vcs.push(tmp_path, "claude/14", "main")


def test_an_ordinary_push_failure_is_left_alone(monkeypatch, tmp_path):
    """Only this one refusal is special-cased. A network error stays a crash."""

    def failing(args, cwd=None):
        raise RuntimeError("git push origin… failed: Connection reset by peer")

    monkeypatch.setattr(vcs, "_run", failing)
    with pytest.raises(RuntimeError) as caught:
        vcs.push(tmp_path, "claude/14", "main")
    assert not isinstance(caught.value, vcs.WorkflowScopeRefusal)


def test_the_escalation_says_where_the_work_is(monkeypatch):
    """The push is the step that FAILED, so the branch is local only. A human
    sent to look for it on the remote finds nothing."""
    text = task._WORKFLOW_ESCALATION.format(branch="claude/14", detail=REJECTION)

    flat = " ".join(text.split())
    assert "claude/14" in flat
    assert "A human has to make this change" in flat
    # WHERE the work is. The push is the step that failed, so a human sent to
    # look for this branch on the remote finds nothing.
    assert "the push is the step that failed" in flat.lower()
    assert "nowhere else" in flat
    # It must not read as an infrastructure fault to be fixed by widening the
    # token — that would delete the property the review model rests on.
    assert "the fix is not to grant the scope" in flat.lower()


# --- refusing before anything is spent --------------------------------------


def test_an_issue_naming_the_workflow_dir_is_recognised():
    assert queue.touches_workflows("Scope: `.github/workflows/ci.yml`") is True
    assert queue.touches_workflows("Scope: `src/index.ts` only") is False


def test_the_title_counts_too(monkeypatch, tmp_path):
    """`ci: bump .github/workflows/ci.yml` with a body that never repeats the
    path walked straight past this gate into the full-budget failure it exists
    to prevent."""
    calls: list[list[str]] = []

    def runner(args):
        calls.append(args)
        if args[:2] == ["issue", "list"]:
            if "agent:ready" not in args:
                return "[]"
            return (
                '[{"number": 14, "title": "ci: bump .github/workflows/ci.yml",'
                ' "body": "Use node 22."}]'
            )
        if args[:2] == ["issue", "view"]:
            return '{"labels": [{"name": "agent:ready"}]}'
        return ""

    assert queue.claim("matt/swift-app", tmp_path, Labels(), runner=runner) is None
    assert [c for c in calls if c[:2] == ["issue", "comment"]]


def test_such_an_issue_is_escalated_rather_than_claimed(monkeypatch, tmp_path):
    """One API call, where discovering it at `git push` costs a full budget."""
    calls: list[list[str]] = []

    def runner(args):
        calls.append(args)
        if args[:2] == ["issue", "list"]:
            if "agent:ready" not in args:
                return "[]"  # the revise queue, which is empty
            return (
                '[{"number": 14, "title": "ci: bump the action",'
                ' "body": "Edit `.github/workflows/ci.yml` to use node 22."}]'
            )
        if args[:2] == ["issue", "view"]:
            return '{"labels": [{"name": "agent:ready"}]}'
        return ""

    claimed = queue.claim("matt/swift-app", tmp_path, Labels(), runner=runner)

    assert claimed is None, "it must not be claimed"
    commented = [c for c in calls if c[:2] == ["issue", "comment"]]
    assert commented, "a human must be told why, not left with a silent skip"
    assert ".github/workflows/" in commented[0][-1]
    assert "one API call" in commented[0][-1]

    # The READY label goes, not `working` — the issue was never claimed, so it
    # still carries the former. Found by running this against a real issue,
    # which came back holding `needs-human` AND `agent:smoke`: a contradiction
    # `claim` refuses and `status` flags on every poll, so the refusal would be
    # correct and the issue would nag forever.
    edited = [c for c in calls if c[:2] == ["issue", "edit"]]
    assert edited, "the ready label must be removed, or it stays claimable-looking"
    assert "--add-label" in edited[0] and "needs-human" in edited[0]
    assert "agent:ready" in edited[0][edited[0].index("--remove-label") :]
    assert "agent:working" not in edited[0]
    # Nothing was branched, and no claim file was left behind.
    assert list(tmp_path.iterdir()) == []


def test_an_ordinary_issue_is_still_claimed(monkeypatch, tmp_path):
    """The gate is a substring, so the case that matters is that it does not
    swallow everything else."""
    def runner(args):
        if args[:2] == ["issue", "list"]:
            if "agent:ready" not in args:
                return "[]"  # the revise queue, which is empty
            return (
                '[{"number": 15, "title": "rng: a rangeFloat sibling",'
                ' "body": "Scope: `packages/rng/index.ts`."}]'
            )
        if args[:2] == ["issue", "view"]:
            return '{"labels": [{"name": "agent:ready"}]}'
        return ""

    monkeypatch.setattr(queue, "open_blockers", lambda *a, **k: [])
    claimed = queue.claim("matt/sample", tmp_path, Labels(), runner=runner)

    assert claimed is not None
    issue, _record = claimed
    assert issue.number == 15

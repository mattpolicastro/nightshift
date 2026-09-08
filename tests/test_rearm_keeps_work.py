"""Re-arming an escalated issue must not destroy the escalation's branch.

Observed 2026-08-05 on sample #5. The issue escalated (branch kept, worktree
gone — `test_escalation_keeps_work` covers that half), the issue was corrected
and re-labelled `agent:ready`, and the next claim crashed:

    RuntimeError: git worktree add… failed: fatal: a branch named 'claude/5'
    already exists

`add_worktree` called itself "idempotent for a crashed retry" while keying that
idempotency on the WORKTREE — which is the half an escalation deliberately
throws away. The branch is the half it keeps, and nothing looked at it.

The crash then made it worse. Recovery took the RELEASE path, which was the one
remaining call site handing a branch straight to `git branch -D`, so the
escalated branch was deleted — and the retry 4 seconds later "succeeded"
precisely because the work `bb483eb` exists to protect had just been destroyed.
A self-healing loop that heals by throwing away the thing it was guarding.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nightshift import vcs


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def clone(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(["init", "-q", "-b", "main"], seed)
    git(["config", "user.email", "t@t"], seed)
    git(["config", "user.name", "t"], seed)
    (seed / "base.txt").write_text("one\n")
    git(["add", "."], seed)
    git(["commit", "-qm", "first"], seed)
    git(["clone", "-q", "--bare", str(seed), str(origin)], tmp_path)

    clone = tmp_path / "clone"
    git(["clone", "-q", str(origin), str(clone)], tmp_path)
    return clone


def _escalated(clone: Path, root: Path, branch: str) -> str:
    """Leave the world as an escalation does: branch with a commit, no worktree."""
    wt = root / "task"
    vcs.add_worktree(clone, wt, branch, "origin/main", allowed_root=root)
    (wt / "work.txt").write_text("the escalated diff\n")
    git(["add", "."], wt)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "work"], wt)
    sha = git(["rev-parse", "HEAD"], wt).strip()
    # Teardown as the daemon does it post-escalation: worktree only.
    vcs.remove_worktree(clone, wt, allowed_root=root)
    return sha


def test_reclaiming_an_escalated_issue_does_not_crash(clone, tmp_path):
    root = tmp_path / "wt"
    _escalated(clone, root, "claude/5")

    # The re-arm. This raised before the fix.
    vcs.add_worktree(clone, root / "task", "claude/5", "origin/main", allowed_root=root)

    assert (root / "task").exists()


def test_the_escalated_commit_is_still_reachable_after_a_re_arm(clone, tmp_path):
    """The whole point. A branch ref costs nothing; 211 turns do not."""
    root = tmp_path / "wt"
    sha = _escalated(clone, root, "claude/5")

    kept = vcs.add_worktree(
        clone, root / "task", "claude/5", "origin/main", allowed_root=root
    )

    assert kept == f"claude/5-kept-{sha[:7]}"
    assert git(["rev-parse", kept], clone).strip() == sha


def test_the_new_attempt_starts_from_base_not_the_old_diff(clone, tmp_path):
    """Reusing the branch would silently change what the task is.

    The issue describes work against `main`. A worker resuming on top of a
    previous attempt's rejected diff is doing something else.
    """
    root = tmp_path / "wt"
    _escalated(clone, root, "claude/5")

    wt = root / "task"
    vcs.add_worktree(clone, wt, "claude/5", "origin/main", allowed_root=root)

    assert not (wt / "work.txt").exists()
    assert git(["log", "--oneline", "origin/main..HEAD"], wt).strip() == ""


def test_an_empty_leftover_branch_is_just_reused(clone, tmp_path):
    """A crash before the first commit leaves nothing worth keeping."""
    root = tmp_path / "wt"
    wt = root / "task"
    vcs.add_worktree(clone, wt, "claude/6", "origin/main", allowed_root=root)
    vcs.remove_worktree(clone, wt, allowed_root=root)

    kept = vcs.add_worktree(clone, wt, "claude/6", "origin/main", allowed_root=root)

    assert kept is None
    assert git(["branch", "--list", "claude/6-kept-*"], clone).strip() == ""


def test_preserving_twice_does_not_accumulate(clone, tmp_path):
    """Two re-arms of the same escalation keep one ref, not a chain."""
    root = tmp_path / "wt"
    sha = _escalated(clone, root, "claude/5")

    first = vcs.add_worktree(
        clone, root / "task", "claude/5", "origin/main", allowed_root=root
    )
    vcs.remove_worktree(clone, root / "task", allowed_root=root)
    # Put the same work back on the branch, as a repeated escalation would.
    git(["branch", "-f", "claude/5", sha], clone)
    second = vcs.add_worktree(
        clone, root / "task", "claude/5", "origin/main", allowed_root=root
    )

    assert first == second
    assert git(["rev-parse", first], clone).strip() == sha
    assert "kept-kept" not in git(["branch", "--list", "*kept*"], clone)


def test_an_unreadable_branch_is_treated_as_holding_work(clone):
    """Same rule as `_branch_to_delete`: failure to answer never means delete."""
    assert vcs.branch_has_commits(clone, "no/such/branch", "origin/main") is True

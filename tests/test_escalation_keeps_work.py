"""An escalation that committed work must not have the work deleted.

Observed 2026-08-05 on sample #2, the first task run against the real repo. It
escalated after two review rounds having committed 240 lines across three
files, and the daemon's teardown handed `claim.branch` to `remove_worktree`,
which ends in `git branch -D`. Worktree and branch both went. The commit
survived only as a dangling object, and only because it had not been gc'd yet.

The escalation comment is a set of `file:line` citations telling a human to go
read the diff. Deleting the diff makes the comment unreadable and throws away
the 211 turns that produced it — the expensive half of an escalation is the
analysis, and this destroyed the thing the analysis is about.

`task.py`'s teardown comment already said escalated work is kept. It named the
wrong actor: `task.run` only tears down on SHIP, and the daemon was the one
deleting unconditionally.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nightshift import vcs
from nightshift.daemon import _branch_to_delete


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


def _worktree_with(clone: Path, root: Path, branch: str, *, commit: bool) -> Path:
    wt = root / "task"
    vcs.add_worktree(clone, wt, branch, "origin/main", allowed_root=root)
    if commit:
        (wt / "work.txt").write_text("the escalated diff\n")
        git(["add", "."], wt)
        git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "work"], wt)
    return wt


def test_a_branch_with_commits_is_kept(clone, tmp_path):
    wt = _worktree_with(clone, tmp_path / "wt", "claude/2", commit=True)
    assert _branch_to_delete(wt, "origin/main", "claude/2") is None


def test_a_branch_with_no_commits_is_deleted(clone, tmp_path):
    """The 'committed nothing' escalation leaves nothing worth a ref."""
    wt = _worktree_with(clone, tmp_path / "wt", "claude/3", commit=False)
    assert _branch_to_delete(wt, "origin/main", "claude/3") == "claude/3"


def test_an_unreadable_worktree_keeps_the_branch(tmp_path):
    """Failing to answer must not be the same as answering 'delete it'."""
    assert _branch_to_delete(tmp_path / "gone", "origin/main", "claude/4") is None


def test_teardown_actually_leaves_the_commit_reachable(clone, tmp_path):
    """End to end: the commit survives teardown on a real repo.

    Reachability is the whole point — a dangling object is not preservation.
    """
    root = tmp_path / "wt"
    wt = _worktree_with(clone, root, "claude/2", commit=True)
    sha = git(["rev-parse", "HEAD"], wt).strip()

    vcs.remove_worktree(
        clone, wt, _branch_to_delete(wt, "origin/main", "claude/2"), allowed_root=root
    )

    assert not wt.exists(), "the worktree should still be torn down"
    assert "claude/2" in git(["branch", "--list", "claude/2"], clone)
    assert git(["rev-parse", "claude/2"], clone).strip() == sha


def test_teardown_removes_an_empty_branch(clone, tmp_path):
    root = tmp_path / "wt"
    wt = _worktree_with(clone, root, "claude/3", commit=False)

    vcs.remove_worktree(
        clone, wt, _branch_to_delete(wt, "origin/main", "claude/3"), allowed_root=root
    )

    assert not wt.exists()
    assert git(["branch", "--list", "claude/3"], clone).strip() == ""

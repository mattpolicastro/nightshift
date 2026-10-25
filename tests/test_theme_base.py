"""Remote theme branches must resolve against the actual git repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nightshift import vcs


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo_with_remote(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(["init", "--bare", "-b", "main"], origin)

    work = tmp_path / "work"
    work.mkdir()
    _git(["init", "-b", "main"], work)
    _git(["config", "user.email", "t@t"], work)
    _git(["config", "user.name", "t"], work)
    (work / "f.txt").write_text("hi")
    _git(["add", "."], work)
    _git(["commit", "-m", "init"], work)
    _git(["remote", "add", "origin", str(origin)], work)
    _git(["push", "-u", "origin", "main"], work)
    _git(["checkout", "-b", "narrow-tier"], work)
    _git(["push", "-u", "origin", "narrow-tier"], work)
    _git(["fetch", "origin"], work)
    return work


def test_remote_ref_exists_accepts_the_shorthand_its_callers_hold(tmp_path):
    """Shared callers can pass shorthand remote-tracking refs."""
    work = _repo_with_remote(tmp_path)
    assert vcs.remote_ref_exists(work, "origin/narrow-tier") is True
    assert vcs.remote_ref_exists(work, "origin/main") is True


def test_remote_ref_exists_is_false_for_a_branch_nobody_pushed(tmp_path):
    work = _repo_with_remote(tmp_path)
    assert vcs.remote_ref_exists(work, "origin/typo-tier") is False


def test_remote_ref_exists_still_takes_a_fully_qualified_ref(tmp_path):
    """The other caller in this module passes `refs/remotes/...` already."""
    work = _repo_with_remote(tmp_path)
    assert vcs.remote_ref_exists(work, "refs/remotes/origin/narrow-tier") is True
    assert vcs.remote_ref_exists(work, "refs/remotes/origin/nope") is False


def test_a_local_branch_is_not_a_remote_ref(tmp_path):
    """A theme that exists only locally must NOT satisfy the guard — the
    worker's worktree is cut in a different clone, which can only see what was
    pushed."""
    work = _repo_with_remote(tmp_path)
    _git(["checkout", "-b", "local-only"], work)
    assert vcs.remote_ref_exists(work, "origin/local-only") is False

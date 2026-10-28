"""The base a task branches and diffs against must be the REMOTE one.

Observed 2026-08-03: a PR shipped and was merged on GitHub, and the next two
tasks branched off a local `main` that had never been fetched. The dependency
they needed existed on the remote and not in their worktrees, so both
escalated and the cycle was wasted.

The branching half is the visible bug. The diffing half is the dangerous one:
the reviewer's rubric is built out of `git diff {base}`, so a stale base makes
somebody else's merge appear inside the worker's own diff — and "no unrelated
churn" is one of the things the reviewer is supposed to reject on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nightshift.config import Repo


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def origin_and_clone(tmp_path):
    """A bare origin, a clone of it, and a commit on origin the clone lacks."""
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

    # A merge lands on origin AFTER the clone was taken — exactly what happens
    # while the daemon is running.
    (seed / "dependency.txt").write_text("the thing the next task needs\n")
    git(["add", "."], seed)
    git(["commit", "-qm", "dependency"], seed)
    git(["push", "-q", str(origin), "main"], seed)

    return origin, clone


def test_base_ref_is_the_remote_tracking_ref():
    assert Repo(name="x/y", verify="true").base_ref == "origin/main"


def test_base_ref_follows_a_non_default_base():
    assert Repo(name="x/y", verify="true", base="trunk").base_ref == "origin/trunk"


def test_base_stays_a_plain_branch_name_for_pr_creation():
    """`gh pr create --base` rejects a remote-tracking ref."""
    assert Repo(name="x/y", verify="true").base == "main"


def test_local_main_is_stale_after_someone_merges(origin_and_clone):
    """The premise. If this ever fails, the bug fixed itself and these can go."""
    _, clone = origin_and_clone
    assert "dependency.txt" not in git(["ls-tree", "main", "--name-only"], clone)

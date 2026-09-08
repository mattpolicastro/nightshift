"""The harness must not be the thing that pushes the base branch.

"Workers never push and never merge" had two enforcement layers and a gap.
`worker._DENIED` stops the agent running `git push` at all, and AGENTS.md tells
it not to — but neither covers the HARNESS, which pushes on the agent's behalf
and never checked what branch it had been handed. A corrupt claim file or a
reconcile bug naming `main` would have pushed `main`, with a credential that
holds Contents write.

GitHub-side protection cannot close it: a fine-grained PAT acts as its owner,
so any ruleset strong enough to stop the daemon pushing `main` also stops Matt,
and "default to committing on `main`" is rule 1 of sample's CLAUDE.md. There is
no actor to tell the two apart — so the check has to live in the code path.
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
def origin_and_worktree(tmp_path):
    """A real origin and a worktree on a task branch, so pushes really run."""
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

    root = tmp_path / "wt"
    wt = root / "task"
    vcs.add_worktree(clone, wt, "claude/31", "origin/main", allowed_root=root)
    return origin, clone, wt


def test_a_task_branch_pushes(origin_and_worktree):
    """The guard must not break the only thing push is for."""
    origin, _clone, wt = origin_and_worktree
    (wt / "work.txt").write_text("the change\n")
    git(["add", "."], wt)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "work"], wt)

    vcs.push(wt, "claude/31", "main")

    assert "claude/31" in git(["ls-remote", "--heads", str(origin)], wt)


def test_pushing_the_configured_base_is_refused(origin_and_worktree):
    _origin, _clone, wt = origin_and_worktree
    with pytest.raises(vcs.RefusedPush):
        vcs.push(wt, "main", "main")


def test_a_non_default_base_is_refused(origin_and_worktree):
    """`base` is configurable, so the guard cannot just hardcode `main`."""
    _origin, _clone, wt = origin_and_worktree
    with pytest.raises(vcs.RefusedPush):
        vcs.push(wt, "trunk", "trunk")


def test_main_is_refused_even_without_a_base_argument(origin_and_worktree):
    """Backstop for any caller that forgets to pass it."""
    _origin, _clone, wt = origin_and_worktree
    with pytest.raises(vcs.RefusedPush):
        vcs.push(wt, "main")


def test_a_refused_push_reaches_no_remote(origin_and_worktree):
    """The refusal must happen BEFORE git runs, not be a message after it."""
    origin, _clone, wt = origin_and_worktree
    before = git(["ls-remote", "--heads", str(origin)], wt)

    with pytest.raises(vcs.RefusedPush):
        vcs.push(wt, "main", "main")

    assert git(["ls-remote", "--heads", str(origin)], wt) == before


def test_task_ships_with_the_base_passed_through():
    """`task.run` must hand the base down, or the guard only half applies.

    Reads the RESOLVED base rather than `repo.base`: since 2026-08-09 an issue
    may thread itself onto a theme branch with `base: <name>`, and `run()`
    resolves that into a local before using it. The claim is unchanged — a
    base reaches `push`, so the refuse-to-push-the-base guard can fire — only
    the expression it reads did.
    """
    import inspect

    from nightshift import task

    source = inspect.getsource(task.run)
    assert "vcs.push(worktree, claim.branch, base)" in source
    # And it must be that resolution, not a bare literal: pushing a theme's
    # work while the guard believes the base is `main` would let a worker push
    # to the trunk it was threaded away from.
    assert "base = queue.base_branch(issue.body) or repo.base" in source

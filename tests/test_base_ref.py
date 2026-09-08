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

from nightshift import task, vcs
from nightshift.config import Config, Repo
from nightshift.queue import Claim, Issue


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


def test_worktree_off_local_base_misses_the_merge(origin_and_clone, tmp_path):
    """What the daemon used to do, pinned so the regression is recognisable."""
    _, clone = origin_and_clone
    root = tmp_path / "wt"
    vcs.add_worktree(clone, root / "stale", "claude/1", "main", allowed_root=root)
    assert not (root / "stale" / "dependency.txt").exists()


def test_worktree_off_base_ref_gets_the_merge(origin_and_clone, tmp_path):
    """The fix: fetch, then branch from the remote ref."""
    _, clone = origin_and_clone
    root = tmp_path / "wt"
    repo = Repo(name="x/y", verify="true")

    vcs.fetch(clone)
    vcs.add_worktree(clone, root / "fresh", "claude/2", repo.base_ref, allowed_root=root)

    assert (root / "fresh" / "dependency.txt").exists()


def test_fetch_alone_does_not_move_the_local_branch(origin_and_clone):
    """Why base_ref exists rather than just adding a fetch.

    `git fetch` updates remote-tracking refs only. The local `main` stays put,
    so a fetch without the ref change would have fixed nothing.
    """
    _, clone = origin_and_clone
    vcs.fetch(clone)
    assert "dependency.txt" not in git(["ls-tree", "main", "--name-only"], clone)
    assert "dependency.txt" in git(["ls-tree", "origin/main", "--name-only"], clone)


def test_diff_against_a_stale_base_attributes_someone_elses_merge(
    origin_and_clone, tmp_path
):
    """The half that corrupts review rather than merely blocking work."""
    _, clone = origin_and_clone
    root = tmp_path / "wt"
    vcs.fetch(clone)
    wt = root / "work"
    vcs.add_worktree(clone, wt, "claude/3", "origin/main", allowed_root=root)
    (wt / "mine.txt").write_text("the worker's actual change\n")
    git(["add", "."], wt)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "work"], wt)

    stale = vcs.changed_files(wt, "main")
    fresh = vcs.changed_files(wt, "origin/main")

    assert "dependency.txt" in stale, "someone else's merge, read as the worker's"
    assert fresh == ["mine.txt"]


class Stop(Exception):
    """Halt task.run once it is past the part under test."""


def test_task_fetches_before_it_creates_the_worktree(monkeypatch, tmp_path):
    """Ordering is the whole fix — a fetch after the branch is decorative."""
    calls: list[str] = []
    monkeypatch.setattr(vcs, "fetch", lambda repo_dir: calls.append("fetch"))
    monkeypatch.setattr(vcs, "add_worktree", lambda *a, **k: calls.append("add_worktree"))

    def stop(*_a, **_k):
        raise Stop

    monkeypatch.setattr(vcs, "install", stop)
    monkeypatch.setattr(vcs, "remove_worktree", lambda *a, **k: None)

    cfg = Config(repos=[], worktree_root=tmp_path / "wt")
    claim = Claim(
        repo="x/y", number=1, branch="claude/1",
        worktree=str(tmp_path / "wt" / "x-1"), started_at="now",
    )
    issue = Issue(repo="x/y", number=1, title="t", body="b")

    with pytest.raises(Stop):
        task.run(
            cfg, Repo(name="x/y", verify="true"), tmp_path / "clone",
            issue, claim, transcript_dir=tmp_path / "tr",
        )

    assert calls == ["fetch", "add_worktree"]


def test_add_worktree_is_given_the_remote_ref_not_the_local_branch(
    monkeypatch, tmp_path
):
    """The bug was one argument. Pin the argument."""
    seen: dict[str, str] = {}
    monkeypatch.setattr(vcs, "fetch", lambda repo_dir: None)
    monkeypatch.setattr(
        vcs,
        "add_worktree",
        lambda repo_dir, wt, branch, base, **k: seen.update(base=base),
    )

    def stop(*_a, **_k):
        raise Stop

    monkeypatch.setattr(vcs, "install", stop)
    monkeypatch.setattr(vcs, "remove_worktree", lambda *a, **k: None)

    cfg = Config(repos=[], worktree_root=tmp_path / "wt")
    claim = Claim(
        repo="x/y", number=1, branch="claude/1",
        worktree=str(tmp_path / "wt" / "x-1"), started_at="now",
    )

    with pytest.raises(Stop):
        task.run(
            cfg, Repo(name="x/y", verify="true"), tmp_path / "clone",
            Issue(repo="x/y", number=1, title="t", body="b"),
            claim, transcript_dir=tmp_path / "tr",
        )

    assert seen["base"] == "origin/main"

"""`base:` — an issue threading itself onto a shared theme branch.

The parser lives in test_queue.py; this covers the part with consequences:
that `run()` refuses when the declared branch is not on the remote, rather
than falling back to the repo default.

Why refusing is the whole point. Falling back to `main` is the failure that
looks like success — the work lands, CI is green, the PR merges into a trunk
the theme was supposed to be kept off, and nobody learns the marker was a typo
until a conflict weeks later. One escalation comment is cheaper.
"""

from __future__ import annotations

from pathlib import Path

from nightshift import task, vcs
from nightshift.config import Config, Repo
from nightshift.queue import Claim, Issue


def _issue(body: str) -> Issue:
    return Issue(repo="o/r", number=1, title="t", body=body)


def _claim(tmp_path: Path) -> Claim:
    return Claim(
        repo="o/r",
        number=1,
        branch="claude/1",
        worktree=str(tmp_path / "wt" / "o-1"),
        started_at="now",
    )


def test_a_declared_theme_that_is_missing_escalates_before_any_work(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(vcs, "fetch", lambda *_a, **_k: None)
    monkeypatch.setattr(vcs, "remote_ref_exists", lambda *_a, **_k: False)

    # If the guard leaks, these are what it would reach next.
    def _boom(*_a, **_k):  # pragma: no cover - asserted by not being called
        raise AssertionError("started work despite a missing theme branch")

    monkeypatch.setattr(vcs, "add_worktree", _boom)
    monkeypatch.setattr(vcs, "resume_worktree", _boom)
    monkeypatch.setattr(task.queue, "comments_of", lambda *_a, **_k: [])

    report = task.run(
        Config(repos=[], worktree_root=tmp_path / "wt"),
        Repo(name="o/r", verify="true"),
        tmp_path,
        _issue("base: narrow-tier"),
        _claim(tmp_path),
    )

    assert report.step is task.Step.ESCALATE
    assert "narrow-tier" in report.reason
    assert "does not exist" in report.reason


def test_the_repo_default_is_never_existence_checked(tmp_path, monkeypatch):
    """No marker means `main`, and `main` is not something to second-guess —
    checking it would turn a fetch hiccup into a refusal to run at all."""
    monkeypatch.setattr(vcs, "fetch", lambda *_a, **_k: None)

    def _boom(*_a, **_k):  # pragma: no cover - asserted by not being called
        raise AssertionError("existence-checked the repo default")

    monkeypatch.setattr(vcs, "remote_ref_exists", _boom)
    monkeypatch.setattr(task.queue, "comments_of", lambda *_a, **_k: [])
    monkeypatch.setattr(
        vcs, "add_worktree", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("far enough"))
    )

    try:
        task.run(
            Config(repos=[], worktree_root=tmp_path / "wt"),
            Repo(name="o/r", verify="true"),
            tmp_path,
            _issue("no marker here"),
            _claim(tmp_path),
        )
    except RuntimeError as exc:
        assert "far enough" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected to reach add_worktree")


def test_task_py_threads_the_resolved_base_not_the_repo_default():
    """Source-text guard. The bug this prevents is a partial thread: cutting
    the worktree from the theme but opening the PR against `main`, which would
    propose the whole theme's diff into the trunk. Every base-consuming call
    must read the resolved local, so none may say `repo.base` after it.
    """
    src = Path(task.__file__).read_text()
    body = src[src.index("def run(") :]
    after = body[body.index("base = queue.base_branch") :]
    assert "repo.base_ref" not in after
    # Exactly two legitimate mentions: the resolution, and the guard's comparison.
    assert after.count("repo.base") == 2


# --- remote_ref_exists against a REAL repo -------------------------------
#
# These exist because the mocked tests above could not have caught the bug
# that actually shipped. They stub `remote_ref_exists` to assert what `run()`
# does with its answer, which is the right scope for them — and it means the
# function's own contract went unexercised. The first cut passed the shorthand
# `origin/narrow-tier` to `git show-ref --verify`, which accepts only
# fully-qualified refs and reports a shorthand as ABSENT rather than erroring.
# The guard then refused every theme branch, including ones that existed: it
# escalated sample #128 against a `narrow-tier` that was pushed and reachable.
#
# The lesson is narrow and worth keeping: a guard whose failure mode is
# "refuses valid input" needs at least one test against the real thing, or the
# mocks agree with each other about a world that does not exist.

import subprocess


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
    """`task.py` builds `f"origin/{base}"` and asks about that exact string."""
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

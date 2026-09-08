"""Which clone the daemon cuts worktrees from.

The `~/Projects/<repo-name>` convention was written when the sandbox was the
only enrollment — a repo nobody sits in interactively. Enrolling a repo that
IS worked in daily makes the daemon's per-task `fetch --prune`, `worktree
add`/`remove`, and `branch -D` land in the working clone.

The concrete cost is that a branch checked out in a worktree cannot be checked
out anywhere else: a worker holding `claude/23` blocks a human from checking
it out to read its PR. Observed 2026-08-05 in the other direction, which is
what makes the failure recognisable rather than theoretical — a leftover
worktree whose directory had been deleted still held `main`, and `git checkout
main` failed with a `fatal:` naming a path that no longer existed.

`dir` is optional and the fallback is the old behaviour, so an existing
`config.toml` keeps working with no edit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nightshift import vcs
from nightshift.cli import _repo_dirs
from nightshift.config import DEFAULT_CLONE_ROOT, Config, Repo, load


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def test_no_dir_keeps_the_convention():
    repo = Repo(name="example-owner/sample", verify="true")
    assert repo.clone_dir() == DEFAULT_CLONE_ROOT / "sample"


def test_dir_wins_over_the_convention():
    repo = Repo(
        name="example-owner/sample",
        verify="true",
        dir="/srv/nightshift-clones/sample",
    )
    assert repo.clone_dir() == Path("/srv/nightshift-clones/sample")


def test_dir_expands_a_tilde():
    """`config.toml` is hand-edited, so `~/...` is what someone will write."""
    repo = Repo(name="x/y", verify="true", dir="~/Projects/nightshift-clones/sample")
    assert repo.clone_dir() == Path.home() / "Projects" / "nightshift-clones" / "sample"
    assert "~" not in str(repo.clone_dir())


def test_dir_separates_the_daemon_clone_from_the_working_clone():
    """The point of the key: the two paths must not be the same directory."""
    working = Repo(name="example-owner/sample", verify="true").clone_dir()
    daemon = Repo(
        name="example-owner/sample",
        verify="true",
        dir=str(Path.home() / "Projects" / "nightshift-clones" / "sample"),
    ).clone_dir()
    assert daemon != working


def test_load_reads_dir_from_toml(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[repos]]\n'
        'name = "example-owner/sample"\n'
        'verify = "pnpm -r test"\n'
        'dir = "~/Projects/nightshift-clones/sample"\n'
    )
    (repo,) = load(cfg).repos
    assert repo.clone_dir() == Path.home() / "Projects" / "nightshift-clones" / "sample"


def test_load_without_dir_leaves_it_empty(tmp_path):
    """An existing config must not change meaning when the key is added."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[repos]]\nname = "x/sample"\nverify = "pnpm -r test"\n'
    )
    (repo,) = load(cfg).repos
    assert repo.dir == ""
    assert repo.clone_dir() == DEFAULT_CLONE_ROOT / "sample"


def test_cli_repo_dirs_honours_dir():
    """`_repo_dirs` is what daemon.loop and reconcile are handed."""
    cfg = Config(
        repos=[
            Repo(name="x/one", verify="true", dir="/tmp/clones/one"),
            Repo(name="x/two", verify="true"),
        ]
    )
    dirs = _repo_dirs(cfg)
    assert dirs["x/one"] == Path("/tmp/clones/one")
    assert dirs["x/two"] == DEFAULT_CLONE_ROOT / "two"


@pytest.fixture
def clone_with_worktree(tmp_path):
    """A clone with one branch already checked out in a worktree."""
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


def test_a_worktree_branch_is_locked_against_the_clone_it_came_from(
    clone_with_worktree, tmp_path
):
    """The premise, pinned. This is what enrolling your working clone costs.

    If git ever stops locking the branch this test fails, and the `dir` key
    becomes a tidiness preference rather than a correctness one.
    """
    clone = clone_with_worktree
    root = tmp_path / "wt"
    vcs.add_worktree(clone, root / "task", "claude/23", "origin/main", allowed_root=root)

    checkout = subprocess.run(
        ["git", "checkout", "claude/23"],
        cwd=clone,
        capture_output=True,
        text=True,
        check=False,
    )

    assert checkout.returncode != 0
    assert "already used by worktree" in checkout.stderr

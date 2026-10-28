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

from pathlib import Path

from nightshift.cli import _repo_dirs
from nightshift.config import DEFAULT_CLONE_ROOT, Config, Repo, load


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

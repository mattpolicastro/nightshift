"""Guards on the one irreversible operation in the codebase.

`remove_worktree` falls back to `shutil.rmtree`, and its argument comes from a
claim file on disk that `queue.reconcile` explicitly tolerates being corrupt or
hand-edited. Everything else here can be retried; this cannot.
"""

from __future__ import annotations

import pytest

from nightshift.vcs import UnsafePath, assert_disposable


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "nightshift-wt"
    r.mkdir()
    return r


def test_accepts_a_normal_worktree(root):
    wt = root / "sandbox-4"
    wt.mkdir()
    assert assert_disposable(wt, root) == wt.resolve()


def test_accepts_a_path_that_does_not_exist_yet(root):
    """add_worktree calls this before creating anything."""
    assert assert_disposable(root / "sandbox-9", root)


def test_rejects_the_root_itself(root):
    """A claim file with a truncated path must not wipe every worktree."""
    with pytest.raises(UnsafePath):
        assert_disposable(root, root)


def test_rejects_a_sibling_outside_the_root(root, tmp_path):
    outside = tmp_path / "Projects" / "sample"
    outside.mkdir(parents=True)
    with pytest.raises(UnsafePath):
        assert_disposable(outside, root)


def test_rejects_traversal_back_out_of_the_root(root, tmp_path):
    real = tmp_path / "Projects"
    real.mkdir(exist_ok=True)
    with pytest.raises(UnsafePath):
        assert_disposable(root / ".." / "Projects", root)


def test_rejects_the_filesystem_root():
    from pathlib import Path

    with pytest.raises(UnsafePath):
        assert_disposable(Path("/"), Path("/"))


def test_rejects_a_real_clone_even_inside_the_root(root):
    """The check that separates a worktree from a checkout.

    A git worktree has a `.git` *file*; a clone has a `.git` directory. If a
    real repo were ever placed or symlinked under the worktree root, deleting
    it would destroy unpushed work.
    """
    clone = root / "looks-like-a-worktree"
    (clone / ".git").mkdir(parents=True)
    with pytest.raises(UnsafePath, match="real clone"):
        assert_disposable(clone, root)


def test_accepts_a_worktree_whose_git_is_a_file(root):
    """What `git worktree add` actually produces."""
    wt = root / "sandbox-4"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /somewhere/.git/worktrees/sandbox-4\n")
    assert assert_disposable(wt, root)


def test_rejects_a_nested_path_that_escapes_via_symlink(root, tmp_path):
    """Resolution happens before the containment check, not after."""
    outside = tmp_path / "precious"
    outside.mkdir()
    link = root / "innocent"
    link.symlink_to(outside)
    with pytest.raises(UnsafePath):
        assert_disposable(link, root)

"""Real disposable Git tests for host-owned candidate creation; no remote calls."""
import os
import subprocess
from pathlib import Path

import pytest

from nightshift.workers import candidate, snapshot
from nightshift.workers.snapshot import SourceFile


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "edit.txt").write_bytes(b"original\n")
    (root / "delete.txt").write_bytes(b"remove\n")
    (root / "script.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    return root, git(root, "rev-parse", "HEAD")


def desired():
    return [SourceFile("edit.txt", b"edited\n"),
            SourceFile("new/nested.txt", b"added\n"),
            SourceFile("script.sh", b"#!/bin/sh\nexit 0\n", True)]


def test_valid_add_edit_delete_and_executable_bits_make_exact_commit(repository):
    root, base = repository
    result = candidate.create(root, base, desired())
    assert result.base_sha == base
    assert result.candidate_sha != base
    assert result.changed_paths == ("delete.txt", "edit.txt", "new/nested.txt", "script.sh")
    assert git(root, "rev-parse", "HEAD^") == base
    assert snapshot.from_git(root, result.candidate_sha) == snapshot.validate(desired())
    assert not git(root, "status", "--porcelain")
    assert not (root / "delete.txt").exists()
    assert (root / "script.sh").stat().st_mode & 0o111


@pytest.mark.parametrize("kind", ["tracked", "untracked", "staged", "moved"])
def test_dirty_or_moved_base_is_rejected_before_edits(repository, kind):
    root, base = repository
    if kind in ("tracked", "staged"):
        (root / "edit.txt").write_text("unrelated change")
        if kind == "staged":
            git(root, "add", "edit.txt")
    if kind == "untracked":
        (root / "unrelated.txt").write_text("preserve")
    if kind == "moved":
        git(root, "commit", "--allow-empty", "-qm", "other work")
    before = git(root, "rev-parse", "HEAD")
    before_status = git(root, "status", "--porcelain")
    with pytest.raises(ValueError):
        candidate.create(root, base, desired())
    assert git(root, "rev-parse", "HEAD") == before
    assert git(root, "status", "--porcelain") == before_status
    assert (root / "delete.txt").read_bytes() == b"remove\n"
    assert not (root / "new").exists()


@pytest.mark.parametrize("path", ["../escape", "/escape", ".git/config", ".codex/config.toml",
                                  ".agents/policy", "a/../../escape"])
def test_unsafe_paths_are_rejected_before_mutation(repository, path):
    root, base = repository
    with pytest.raises(ValueError):
        candidate.create(root, base, [SourceFile(path, b"bad")])
    assert git(root, "rev-parse", "HEAD") == base
    assert not git(root, "status", "--porcelain")


def test_symlink_in_baseline_is_rejected(repository, tmp_path):
    root, _ = repository
    outside = tmp_path / "outside"
    outside.write_text("preserve")
    (root / "link").symlink_to(outside)
    git(root, "add", "link")
    git(root, "commit", "-qm", "link")
    base = git(root, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="links"):
        candidate.create(root, base, desired())
    assert outside.read_text() == "preserve"
    assert git(root, "rev-parse", "HEAD") == base


def test_ignored_symlink_parent_cannot_redirect_writes(repository, tmp_path):
    root, _ = repository
    (root / ".gitignore").write_text("outside/\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "ignore")
    base = git(root, "rev-parse", "HEAD")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "outside").symlink_to(outside, target_is_directory=True)
    files = snapshot.from_git(root, base) + [SourceFile("outside/escape", b"bad")]
    with pytest.raises((ValueError, OSError)):
        candidate.create(root, base, files)
    assert not (outside / "escape").exists()
    assert git(root, "rev-parse", "HEAD") == base


def test_ignored_existing_target_is_not_overwritten(repository):
    root, _ = repository
    (root / ".gitignore").write_text("private.txt\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "ignore")
    base = git(root, "rev-parse", "HEAD")
    (root / "private.txt").write_text("preserve")
    files = snapshot.from_git(root, base) + [SourceFile("private.txt", b"replacement")]
    with pytest.raises(ValueError, match="overwrite"):
        candidate.create(root, base, files)
    assert (root / "private.txt").read_text() == "preserve"
    assert git(root, "rev-parse", "HEAD") == base


def test_assume_unchanged_cannot_hide_dirty_baseline(repository):
    root, base = repository
    git(root, "update-index", "--assume-unchanged", "edit.txt")
    (root / "edit.txt").write_text("hidden unrelated change")
    assert not git(root, "status", "--porcelain")
    with pytest.raises(ValueError, match="baseline content"):
        candidate.create(root, base, desired())
    assert (root / "edit.txt").read_text() == "hidden unrelated change"
    assert (root / "delete.txt").exists()


def test_no_op_never_creates_empty_commit(repository):
    root, base = repository
    with pytest.raises(ValueError, match="No candidate changes"):
        candidate.create(root, base, snapshot.from_git(root, base))
    assert git(root, "rev-parse", "HEAD") == base


def test_literal_pathspec_stages_only_requested_filename(repository):
    root, base = repository
    files = snapshot.from_git(root, base) + [SourceFile(":(glob)*", b"literal name")]
    result = candidate.create(root, base, files)
    assert result.changed_paths == (":(glob)*",)
    assert snapshot.from_git(root, result.candidate_sha) == snapshot.validate(files)


def test_commit_hooks_do_not_run(repository):
    root, base = repository
    hook = root / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho unsafe > hook-ran\nexit 1\n")
    hook.chmod(0o755)
    candidate.create(root, base, desired())
    assert not (root / "hook-ran").exists()


def test_attributes_do_not_transform_raw_candidate_blobs(repository):
    root, base = repository
    files = desired() + [SourceFile(".gitattributes", b"*.txt text eol=lf\n")]
    files = [SourceFile(f.path, b"crlf\r\n", f.executable) if f.path == "edit.txt" else f for f in files]
    result = candidate.create(root, base, files)
    assert snapshot.from_git(root, result.candidate_sha) == snapshot.validate(files)
    assert (root / "edit.txt").read_bytes() == b"crlf\r\n"


def test_configured_clean_filter_never_executes_anywhere_in_candidate_creation(repository, tmp_path):
    root, base = repository
    sentinel = tmp_path / "filter-ran"
    script = tmp_path / "clean-filter.py"
    script.write_text(
        "from pathlib import Path; import sys\n"
        + "Path(" + repr(str(sentinel)) + ").write_text('unsafe')\n"
        + "sys.stdout.buffer.write(sys.stdin.buffer.read())\n")
    import shlex
    import sys
    git(root, "config", "filter.sentinel.clean", shlex.join([sys.executable, str(script)]))
    git(root, "config", "filter.sentinel.required", "true")
    files = desired() + [SourceFile(".gitattributes", b"*.txt filter=sentinel\n")]
    result = candidate.create(root, base, files)
    assert snapshot.from_git(root, result.candidate_sha) == snapshot.validate(files)
    assert not sentinel.exists()

    # Existing baseline attributes must not activate filters during subsequent
    # dirtiness checks either, even when a stat refresh would be necessary.
    for entry in files:
        os.utime(root / entry.path, None)
    revised = [SourceFile(f.path, b"revised\n", f.executable) if f.path == "edit.txt" else f for f in files]
    second = candidate.create(root, result.candidate_sha, revised)
    assert snapshot.from_git(root, second.candidate_sha) == snapshot.validate(revised)
    assert not sentinel.exists()


def test_symlink_root_and_nested_checkout_path_are_rejected(repository, tmp_path):
    root, base = repository
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        candidate.create(alias, base, desired())
    nested = root / "nested"
    nested.mkdir()
    with pytest.raises(ValueError, match="worktree root"):
        candidate.create(nested, base, desired())


def test_atomic_replacement_does_not_modify_other_hardlink(repository, tmp_path):
    root, base = repository
    outside = tmp_path / "original-copy"
    os.link(root / "edit.txt", outside)
    candidate.create(root, base, desired())
    assert outside.read_bytes() == b"original\n"



def test_reference_compare_and_swap_preserves_a_moved_head_and_failed_candidate(repository, monkeypatch):
    root, base = repository
    tree = candidate._git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    other = candidate._git(root, "commit-tree", tree, "-p", base, "-m", "other work").decode().strip()
    original = candidate._git
    moved = False

    def move_before_update(path, *args, **kwargs):
        nonlocal moved
        if args[0] == "update-ref" and not moved:
            moved = True
            original(path, "update-ref", "HEAD", other, base)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(candidate, "_git", move_before_update)
    with pytest.raises(ValueError, match="update-ref"):
        candidate.create(root, base, desired())
    assert git(root, "rev-parse", "HEAD") == other
    assert (root / "edit.txt").read_bytes() == b"edited\n"
    assert (root / "new/nested.txt").read_bytes() == b"added\n"
    assert candidate._index_matches(root, desired(), base)

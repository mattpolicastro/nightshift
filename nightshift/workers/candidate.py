"""Host-only candidate commits for an exclusively harness-owned Git worktree.

Consumes complete validated source snapshots. No worker, queue, push, merge, or
rollback integration exists. On failure, leave all state for investigation.
Concurrent writers are unsupported; the caller must retain exclusive ownership.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import snapshot


@dataclass(frozen=True)
class CandidateResult:
    base_sha: str
    candidate_sha: str
    changed_paths: tuple[str, ...]


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
           "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
           "GIT_AUTHOR_NAME": "Nightshift", "GIT_COMMITTER_NAME": "Nightshift",
           "GIT_AUTHOR_EMAIL": "nightshift@localhost", "GIT_COMMITTER_EMAIL": "nightshift@localhost"}
    result = subprocess.run(
        ["git", "--literal-pathspecs", "-c", "core.hooksPath=" + os.devnull,
         "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false", *args],
        cwd=root, env=env, input=input_bytes, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=30)
    if result.returncode:
        raise ValueError("Candidate Git operation failed: " + args[0])
    return result.stdout


@contextmanager
def _parent(root_fd: int, path: str, *, create: bool = False):
    """Traverse relative directory descriptors; never follow a symlink parent."""
    descriptor = os.dup(root_fd)
    try:
        parts = PurePosixPath(path).parts
        for component in parts[:-1]:
            if create:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def _matches(root_fd: int, entry: snapshot.SourceFile) -> bool:
    with _parent(root_fd, entry.path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            return (stat.S_ISREG(info.st_mode) and info.st_size == len(entry.content)
                    and bool(info.st_mode & 0o111) == entry.executable
                    and stream.read(len(entry.content) + 1) == entry.content)


def _absent(root_fd: int, path: str) -> bool:
    try:
        with _parent(root_fd, path) as (parent, name):
            os.stat(name, dir_fd=parent, follow_symlinks=False)
            return False
    except FileNotFoundError:
        return True


def _write(root_fd: int, entry: snapshot.SourceFile) -> None:
    with _parent(root_fd, entry.path, create=True) as (parent, name):
        temporary = ".nightshift-candidate-" + uuid.uuid4().hex
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(entry.content)
            os.fchmod(stream.fileno(), 0o755 if entry.executable else 0o644)
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)


def _index_matches(root: Path, files: list[snapshot.SourceFile], sha: str) -> bool:
    expected = {}
    algorithm = "sha256" if len(sha) == 64 else "sha1"
    for entry in files:
        header = f"blob {len(entry.content)}\0".encode()
        digest = hashlib.new(algorithm, header + entry.content).hexdigest().encode()
        expected[entry.path.encode()] = (b"100755" if entry.executable else b"100644", digest)
    observed = {}
    for record in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        if stage != b"0" or path in observed:
            return False
        observed[path] = (mode, oid)
    return observed == expected


def create(worktree: Path, expected_base_sha: str, files: list[snapshot.SourceFile],
           *, message: str = "Apply isolated worker candidate") -> CandidateResult:
    """Apply a full regular-file snapshot and commit only its explicit changes.

    Refuse no-op snapshots and file/directory or case-only replacement conflicts.
    Hooks and signing are disabled; exact staged blobs are checked before commit
    and exact committed blobs afterwards. Never reset/revert on failure.
    """
    desired = snapshot.validate(files)
    if not isinstance(message, str) or not message.strip() or "\0" in message:
        raise ValueError("A nonempty commit message is required")
    if worktree.is_symlink():
        raise ValueError("Candidate worktree cannot be a symlink")
    root = worktree.resolve(strict=True)
    if Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise ValueError("Candidate must be the Git worktree root")
    baseline = snapshot.from_git(root, expected_base_sha)
    if _git(root, "rev-parse", "HEAD").decode().strip() != expected_base_sha:
        raise ValueError("Candidate base moved")
    if (not _index_matches(root, baseline, expected_base_sha)
            or _git(root, "ls-files", "--others", "--exclude-standard", "-z")):
        raise ValueError("Candidate worktree or index is dirty")
    old = {entry.path: entry for entry in baseline}
    new = {entry.path: entry for entry in desired}
    # Validate cross-snapshot spellings and path prefixes before changing files.
    snapshot.validate([snapshot.SourceFile(path, b"") for path in old.keys() | new.keys()])
    changed = tuple(sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path)))
    if not changed:
        raise ValueError("No candidate changes; empty commits are forbidden")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Git's assume-unchanged/filemode settings cannot hide a dirty baseline.
        for entry in baseline:
            if not _matches(root_fd, entry):
                raise ValueError("Candidate baseline content changed")
        for path in new.keys() - old.keys():
            if not _absent(root_fd, path):
                raise ValueError("Candidate would overwrite an existing untracked path")
        for path in changed:
            if path in old and not _matches(root_fd, old[path]):
                raise ValueError("Candidate path changed before applying snapshot")
            if path in new:
                if path not in old and not _absent(root_fd, path):
                    raise ValueError("Candidate path appeared before applying snapshot")
                _write(root_fd, new[path])
            else:
                with _parent(root_fd, path) as (parent, name):
                    os.unlink(name, dir_fd=parent)
    finally:
        os.close(root_fd)
    if _git(root, "rev-parse", "HEAD").decode().strip() != expected_base_sha:
        raise ValueError("Candidate base moved before staging")
    # Never run porcelain add/diff/status/commit: their index refresh and
    # clean-filter machinery can execute repository-configured programs.
    # Write raw blobs and explicit NUL-delimited index records instead.
    records = bytearray()
    for path in changed:
        if path in new:
            entry = new[path]
            oid = _git(root, "hash-object", "-w", "--stdin", "--no-filters",
                       input_bytes=entry.content).strip()
            mode = b"100755" if entry.executable else b"100644"
        else:
            mode, oid = b"0", b"0" * len(expected_base_sha)
        records.extend(mode + b" " + oid + b"\t" + path.encode() + b"\0")
    _git(root, "update-index", "-z", "--index-info", input_bytes=bytes(records))
    if not _index_matches(root, desired, expected_base_sha):
        raise ValueError("Staged candidate differs from expected source snapshot")
    _check_files(root, desired, old.keys() - new.keys())
    tree = _git(root, "write-tree").decode().strip()
    candidate = _git(root, "commit-tree", "--no-gpg-sign", tree, "-p", expected_base_sha,
                     "-m", message).decode().strip()
    if snapshot.from_git(root, candidate) != desired:
        raise ValueError("Created candidate differs from expected source snapshot")
    # Atomic expected-value update: a concurrently moved ref is never reset.
    _git(root, "update-ref", "-m", message, "HEAD", candidate, expected_base_sha)
    if (_git(root, "rev-parse", "HEAD").decode().strip() != candidate
            or _git(root, "rev-parse", "HEAD^").decode().strip() != expected_base_sha
            or not _index_matches(root, desired, candidate)):
        raise ValueError("Committed candidate differs from expected source snapshot")
    _check_files(root, desired, old.keys() - new.keys())
    return CandidateResult(expected_base_sha, candidate, changed)


def _check_files(root: Path, files: list[snapshot.SourceFile], deleted) -> None:
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if (any(not _matches(root_fd, entry) for entry in files)
                or any(not _absent(root_fd, path) for path in deleted)
                or _git(root, "ls-files", "--others", "--exclude-standard", "-z")):
            raise ValueError("Working files differ from expected source snapshot")
    finally:
        os.close(root_fd)

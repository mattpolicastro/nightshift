"""Bounded source archives for an isolated executor; never extract onto the host.

Only regular Git blobs are supported. Links, submodules and runtime policy files
require a separately reviewed transfer policy. Decoding returns data, not writes.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import selectors
import signal
import subprocess
import tarfile
import time
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_FILES = 2000
MAX_CONTENT_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
BLOCKED_COMPONENTS = {'.git', '.codex', '.agents'}


@dataclass(frozen=True)
class SourceFile:
    path: str
    content: bytes
    executable: bool = False


def _path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > 512
            or '\\' in value or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in value)):
        raise ValueError('invalid source path')
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or any(
        part in {'.', '..'} or part.endswith((' ', '.'))
        or unicodedata.normalize('NFC', part).casefold() in BLOCKED_COMPONENTS
        for part in path.parts
    ):
        raise ValueError('unsafe or runtime-control source path')
    return value


def _claim_spelling(path: str, spellings: dict[str, str]) -> None:
    # Implicit directory prefixes must not acquire different spellings either:
    # A/one and a/two alias a directory on a case-insensitive host.
    parts = PurePosixPath(path).parts
    for length in range(1, len(parts) + 1):
        literal = "/".join(parts[:length])
        key = unicodedata.normalize("NFC", literal).casefold()
        if key in spellings and spellings[key] != literal:
            raise ValueError("aliased source path component")
        spellings[key] = literal


def validate(files: list[SourceFile]) -> list[SourceFile]:
    if len(files) > MAX_FILES:
        raise ValueError('too many source files')
    seen: set[str] = set()
    spellings: dict[str, str] = {}
    total = 0
    for entry in files:
        path = _path(entry.path)
        _claim_spelling(path, spellings)
        # Case-insensitive host filesystems must not alias returned paths.
        folded = unicodedata.normalize('NFC', path).casefold()
        if folded in seen:
            raise ValueError('duplicate source path')
        if not isinstance(entry.content, bytes) or type(entry.executable) is not bool:
            raise ValueError('invalid source file')
        total += len(entry.content)
        if total > MAX_CONTENT_BYTES:
            raise ValueError('source exceeds content limit')
        seen.add(folded)
    for name in seen:
        if any(str(parent) in seen for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise ValueError('source file/directory conflict')
    return sorted(files, key=lambda f: f.path)


def encode(files: list[SourceFile]) -> bytes:
    """Produce a deterministic archive, without host paths or owner metadata."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for entry in validate(files):
            member = tarfile.TarInfo(entry.path)
            member.size = len(entry.content)
            member.mode = 0o755 if entry.executable else 0o644
            member.uid = member.gid = 1000
            archive.addfile(member, io.BytesIO(entry.content))
    value = stream.getvalue()
    if len(value) > MAX_ARCHIVE_BYTES:
        raise ValueError('archive exceeds size limit')
    return value


def decode(value: bytes) -> list[SourceFile]:
    """Validate untrusted uncompressed tar completely; perform no extraction."""
    if not isinstance(value, bytes) or len(value) > MAX_ARCHIVE_BYTES:
        raise ValueError('invalid or oversized source archive')
    files: list[SourceFile] = []
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(value), mode='r:') as archive:
            for member in archive:
                if (not member.isreg() or member.sparse is not None
                        or member.pax_headers or member.mode & ~0o777
                        or member.size < 0):
                    raise ValueError('only ordinary regular files are supported')
                _path(member.name)
                total += member.size
                if total > MAX_CONTENT_BYTES or len(files) >= MAX_FILES:
                    raise ValueError('source exceeds transfer limits')
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError('missing archive content')
                content = source.read(member.size + 1)
                if len(content) != member.size:
                    raise ValueError('incomplete archive content')
                files.append(SourceFile(member.name, content, bool(member.mode & 0o111)))
    except (tarfile.TarError, OSError, UnicodeError) as exc:
        raise ValueError('invalid source archive') from exc
    return validate(files)


def fingerprint(files: list[SourceFile]) -> str:
    return hashlib.sha256(encode(files)).hexdigest()


def from_git(repository: Path, sha: str) -> list[SourceFile]:
    """Read exact committed blobs; exclude untracked files and Git control data.

    This reads an operator-selected trusted local repository. It does not stage,
    commit, checkout, interpret repository scripts, or copy working-tree files.
    """
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', sha):
        raise ValueError('an exact commit object ID is required')
    env = {'PATH': '/usr/bin:/bin:/opt/homebrew/bin',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1',
           'GIT_NO_LAZY_FETCH': '1'}
    def git(*args, output_limit=MAX_FILES * 640):
        # Bound bytes while reading, not after subprocess.run has buffered a
        # potentially enormous tree listing. Suppress repository diagnostics.
        with subprocess.Popen(['git', *args], cwd=repository, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              start_new_session=True) as process:
            output = bytearray()
            deadline = time.monotonic() + 30
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ValueError('Git source read timed out')
                        if not selector.select(remaining):
                            raise ValueError('Git source read timed out')
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > output_limit:
                            raise ValueError('Git output exceeds transfer limit')
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                if code:
                    raise ValueError('Git source read failed')
                return bytes(output)
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        # Some supervising sandboxes prohibit process-group
                        # signals. These fixed, non-fetching Git reads do not
                        # run repository commands; still reap the direct child.
                        if process.poll() is None:
                            process.kill()
                process.wait()
    resolved = git('rev-parse', '--verify', sha + '^{commit}').decode().strip()
    if resolved != sha:
        raise ValueError('source must identify the commit itself')
    records = git('ls-tree', '-r', '-z', '-l', '--full-tree', sha).split(b'\0')
    entries = []
    total = 0
    for record in records:
        if not record:
            continue
        metadata, raw_path = record.split(b'\t', 1)
        mode, kind, oid, size = metadata.split()
        if kind != b'blob' or mode not in (b'100644', b'100755'):
            raise ValueError('links and submodules are unsupported')
        path = _path(raw_path.decode('utf-8'))
        total += int(size)
        if total > MAX_CONTENT_BYTES or len(entries) >= MAX_FILES:
            raise ValueError('Git tree exceeds transfer limits')
        entries.append((path, oid.decode(), int(size), mode == b'100755'))
    files = []
    for path, oid, size, executable in entries:
        content = git('cat-file', 'blob', oid, output_limit=size)
        if len(content) != size:
            raise ValueError('Git blob size changed')
        files.append(SourceFile(path, content, executable))
    return validate(files)


@contextmanager
def materialize(files: list[SourceFile], *, parent: Path | None = None):
    """Yield a fresh source directory, never overlay or extract onto a host tree.

    The enclosing temporary directory stays owner-only. Bind only the returned
    snapshot child read-only; its ordinary permissions allow a non-root executor
    to read files after mounting it. The caller must own/trust any supplied parent.
    """
    entries = validate(files)
    with tempfile.TemporaryDirectory(prefix="nightshift-snapshot-", dir=parent) as temporary:
        private = Path(temporary)
        private.chmod(0o700)
        root = private / "snapshot"
        root.mkdir(mode=0o755)
        root.chmod(0o755)
        for entry in entries:
            target = root.joinpath(*PurePosixPath(entry.path).parts)
            directory = root
            for component in PurePosixPath(entry.path).parts[:-1]:
                directory /= component
                try:
                    directory.mkdir(mode=0o755)
                    directory.chmod(0o755)
                except FileExistsError:
                    if directory.is_symlink() or not directory.is_dir():
                        raise ValueError("unexpected snapshot path")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(entry.content)
                os.fchmod(stream.fileno(), 0o755 if entry.executable else 0o644)
        yield root


def decode_container_archive(value: bytes, prefix: str = "workspace") -> list[SourceFile]:
    """Decode bounded Docker-cp tar data without host extraction.

    Only ordinary regular files and directory headers within one exact wrapper
    are accepted. Reject metadata extension headers before tarfile can hide them.
    """
    if not isinstance(value, bytes) or len(value) > MAX_ARCHIVE_BYTES:
        raise ValueError("invalid or oversized container archive")
    if len(PurePosixPath(_path(prefix)).parts) != 1:
        raise ValueError("wrapper prefix must be a single safe component")
    headers = []
    offset = 0
    total = 0
    try:
        while offset + tarfile.BLOCKSIZE <= len(value):
            block = value[offset:offset + tarfile.BLOCKSIZE]
            if block == bytes(tarfile.BLOCKSIZE):
                if len(value) - offset < 2 * tarfile.BLOCKSIZE or any(value[offset:]):
                    raise ValueError("invalid container archive trailer")
                break
            member = tarfile.TarInfo.frombuf(block, "utf-8", "strict")
            if (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
                    or member.mode & ~0o777 or member.size < 0
                    or member.sparse is not None or member.pax_headers):
                raise ValueError("unsupported container archive entry")
            if member.isdir() and member.size:
                raise ValueError("directory content is unsupported")
            total += member.size
            if total > MAX_CONTENT_BYTES or len(headers) >= MAX_FILES * 4 + 1:
                raise ValueError("container archive exceeds transfer limits")
            start = offset + tarfile.BLOCKSIZE
            end = start + member.size
            offset = start + ((member.size + 511) // 512) * 512
            if offset > len(value):
                raise ValueError("incomplete container archive content")
            headers.append((member, start, end))
        else:
            raise ValueError("missing container archive trailer")
    except (tarfile.TarError, OSError, UnicodeError) as exc:
        raise ValueError("invalid container archive") from exc

    seen = set()
    spellings = {}
    file_names = set()
    files = []
    wrapper_seen = False
    for member, start, end in headers:
        name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
        name = _path(name)
        _claim_spelling(name, spellings)
        if name != prefix and not name.startswith(prefix + "/"):
            raise ValueError("entry outside container wrapper")
        folded = unicodedata.normalize("NFC", name).casefold()
        if folded in seen:
            raise ValueError("duplicate container archive path")
        seen.add(folded)
        if name == prefix:
            if not member.isdir():
                raise ValueError("container wrapper must be a directory")
            wrapper_seen = True
            continue
        if not member.isdir():
            if len(files) >= MAX_FILES:
                raise ValueError("too many container source files")
            file_names.add(folded)
            files.append(SourceFile(name[len(prefix) + 1:], value[start:end], bool(member.mode & 0o111)))
    if not wrapper_seen:
        raise ValueError("container wrapper directory is missing")
    for name in seen:
        if any(str(parent) in file_names for parent in PurePosixPath(name).parents):
            raise ValueError("container file/directory conflict")
    return validate(files)

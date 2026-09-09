"""Private locked lifecycle for a stable keyring namespace, never authentication.

The operator supplies an existing private root. Only the stable directory name
persists between attempts; credentials are never read, copied, or provisioned.
Callers must stop/reap owned processes and confirm_stopped before lease cleanup.
A stale recovery marker requires explicit host investigation, never auto-repair.
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
import uuid
from pathlib import Path

from .container_session import SessionError

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_STARTUP = {'config.toml', 'environments.toml', 'nightshift-executor-launcher.py'}
_MAX_ASSET_BYTES = 64 * 1024
_MAX_ENTRIES = 4096
_MAX_DEPTH = 64


def _open_directory(path):
    if not isinstance(path, Path) or not path.is_absolute() or '..' in path.parts:
        raise SessionError('An explicit absolute private credential root is required')
    descriptor = os.open('/', _DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _private(info, *, directory=False):
    return ((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
            and (directory or info.st_nlink == 1))


class StableCredentialHome:
    """One exclusive attempt over root/codex-home; no model/daemon entrypoint."""
    def __init__(self, root: Path):
        self.root = root
        self.home = root / 'codex-home' if isinstance(root, Path) else None
        self._root_fd = self._home_fd = self._lock_fd = None
        self._entered = False
        self._marker = False
        self._stopped = False
        self.cleanup_succeeded = False
        self._nonce = uuid.uuid4().hex
        self._record_payload = json.dumps({"version": 1, "attempt": self._nonce, "namespace": "codex-home"}).encode()

    def _same_root(self):
        descriptor = _open_directory(self.root)
        try:
            info, held = os.fstat(descriptor), os.fstat(self._root_fd)
            if not _private(info, directory=True) or (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino):
                raise SessionError('Credential root changed while leased')
        finally:
            os.close(descriptor)
        if self._lock_fd is not None:
            lock = os.stat('.lock', dir_fd=self._root_fd, follow_symlinks=False)
            held = os.fstat(self._lock_fd)
            if not _private(lock) or (lock.st_dev, lock.st_ino) != (held.st_dev, held.st_ino):
                raise SessionError('Credential lock changed while leased')
        if self._marker:
            if set(os.listdir(self._root_fd)) != {'.lock', 'codex-home', 'attempt.json'}:
                raise SessionError('Credential control directory contains unowned state')
            marker = os.open('attempt.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._root_fd)
            with os.fdopen(marker, 'rb') as stream:
                if not _private(os.fstat(stream.fileno())) or stream.read(1024) != self._record_payload:
                    raise SessionError('Credential recovery marker changed while leased')
        if self._home_fd is not None:
            info = os.stat('codex-home', dir_fd=self._root_fd, follow_symlinks=False)
            held = os.fstat(self._home_fd)
            if not _private(info, directory=True) or (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino):
                raise SessionError('Credential namespace changed while leased')

    def __enter__(self):
        if self._entered:
            raise SessionError('Credential lease cannot be reused')
        self._entered = True
        try:
            self._root_fd = _open_directory(self.root)
            if not _private(os.fstat(self._root_fd), directory=True):
                raise SessionError('Credential root must be private and owned')
            self._lock_fd = os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=self._root_fd)
            if not _private(os.fstat(self._lock_fd)):
                raise SessionError('Credential lock must be a private owned regular file')
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Nothing in the namespace is inspected until the lock is held.
            self._same_root()
            lock = os.stat('.lock', dir_fd=self._root_fd, follow_symlinks=False)
            held = os.fstat(self._lock_fd)
            if (lock.st_dev, lock.st_ino) != (held.st_dev, held.st_ino):
                raise SessionError('Credential lock changed while acquiring it')
            names = set(os.listdir(self._root_fd))
            if names - {'.lock', 'codex-home'}:
                raise SessionError('Credential root contains stale or unowned state')
            if 'codex-home' in names:
                self._home_fd = os.open('codex-home', _DIR_FLAGS, dir_fd=self._root_fd)
                if not _private(os.fstat(self._home_fd), directory=True) or os.listdir(self._home_fd):
                    raise SessionError('Credential namespace contains stale or unowned state')
            marker = os.open('attempt.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self._root_fd)
            self._marker = True
            with os.fdopen(marker, 'wb') as stream:
                stream.write(self._record_payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(self._root_fd)  # Durable recovery clue precedes namespace mutation.
            if self._home_fd is None:
                os.mkdir('codex-home', 0o700, dir_fd=self._root_fd)
                self._home_fd = os.open('codex-home', _DIR_FLAGS, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            return self
        except BaseException as exc:
            self._release()
            if isinstance(exc, (KeyboardInterrupt, SystemExit, SessionError)):
                raise
            raise SessionError('Credential lease unavailable; inspect private recovery state') from None

    def _active(self):
        if not self._marker or self._root_fd is None or self._home_fd is None or self._stopped:
            raise SessionError('Credential lease is unavailable for startup')
        self._same_root()

    def write_asset(self, name: str, content: str):
        self._active()
        if name not in _STARTUP or not isinstance(content, str) or len(content.encode()) > _MAX_ASSET_BYTES:
            raise SessionError('Generated credential startup asset exceeds its limit')
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self._home_fd)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self._home_fd)

    def write_config(self, configuration: str):
        self.write_asset('config.toml', configuration)

    def validate_startup(self, expected: dict[str, str]):
        """Check exact generated metadata or executor startup assets before launch."""
        self._active()
        if (not isinstance(expected, dict) or not {'config.toml', 'environments.toml'} <= expected.keys()
                or expected.keys() - _STARTUP or set(os.listdir(self._home_fd)) != set(expected)):
            raise SessionError('Credential startup contains stale or unowned assets')
        for name, content in expected.items():
            if not isinstance(content, str) or len(content.encode()) > _MAX_ASSET_BYTES:
                raise SessionError('Generated credential startup asset exceeds its limit')
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._home_fd)
            with os.fdopen(descriptor, 'rb') as stream:
                if not _private(os.fstat(stream.fileno())) or stream.read(_MAX_ASSET_BYTES + 1) != content.encode():
                    raise SessionError('Credential startup asset differs from generated policy')
        os.fsync(self._home_fd)

    def confirm_stopped(self):
        """Host caller attests its provider and executor processes were reaped."""
        if not self._marker or self._root_fd is None:
            raise SessionError('Credential lease is unavailable')
        self._stopped = True

    def _empty(self, descriptor, budget, depth=0):
        if depth > _MAX_DEPTH:
            raise SessionError('Credential cleanup directory depth exceeds its bound')
        for name in os.listdir(descriptor):
            budget[0] -= 1
            if budget[0] < 0:
                raise SessionError('Credential cleanup entry count exceeds its bound')
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if info.st_uid != os.getuid():
                raise SessionError('Credential cleanup encountered an unowned entry')
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, _DIR_FLAGS, dir_fd=descriptor)
                try:
                    self._empty(child, budget, depth + 1)
                    os.fsync(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=descriptor)
            elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                # Only current-attempt assets reach here. Never read credential
                # contents or follow runtime-created links outside this namespace.
                os.unlink(name, dir_fd=descriptor)
            else:
                raise SessionError('Credential cleanup encountered an unsupported entry')

    def close(self):
        try:
            if not self._marker or self._root_fd is None:
                return
            if not self._stopped:
                raise SessionError('Credential cleanup requires confirmed process shutdown')
            self._same_root()
            self._empty(self._home_fd, [_MAX_ENTRIES])
            os.fsync(self._home_fd)
            if os.listdir(self._home_fd):
                raise SessionError('Credential namespace remains nonempty')
            os.unlink('attempt.json', dir_fd=self._root_fd)
            try:
                os.fsync(self._root_fd)
            except OSError:
                # Retain a recovery clue if durable marker removal is uncertain.
                marker = os.open('attempt.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=self._root_fd)
                with os.fdopen(marker, 'wb') as stream:
                    stream.write(b'{"version":1,"cleanup":"unconfirmed"}')
                    stream.flush()
                    os.fsync(stream.fileno())
                raise
            self._marker = False
            self.cleanup_succeeded = True
        except (OSError, ValueError) as exc:
            raise SessionError('Credential cleanup unconfirmed; inspect private recovery state') from None
        finally:
            self._release()

    def _release(self):
        for name in ('_home_fd', '_lock_fd', '_root_fd'):
            descriptor = getattr(self, name)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except SessionError as cleanup:
            if exc is None:
                raise
            exc.add_note(str(cleanup))
        return False

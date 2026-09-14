"""Dormant stable-worker prerequisites; no queue mutation or dispatch activation."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field, fields
from pathlib import Path

from .. import queue
from .base import WorkerResult
from .container_session import SessionError
from .credential_home import _open_directory


@dataclass(frozen=True)
class NativeMarkerEvidence:
    """Private reference to an already-prepared claim, not a durability boolean."""
    claim_path: Path = field(repr=False)
    run_id: str = field(repr=False)
    worktree: Path = field(repr=False)


def _absolute(path):
    return isinstance(path, Path) and path.is_absolute() and '..' not in path.parts and '\0' not in str(path)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate marker field')
        result[key] = value
    return result


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def validate_marker(marker: NativeMarkerEvidence, recovery_dir: Path) -> None:
    """Require matching owned claim bytes and sync file+directory before launch.

    The caller prepared the claim through the durable queue operation. Re-sync
    its current matching bytes here rather than trusting a supplied boolean or
    in-memory Claim. This does not acquire a claim's execution/queue ownership.
    """
    if (type(marker) is not NativeMarkerEvidence or not _absolute(marker.claim_path)
            or not _absolute(marker.worktree) or not _absolute(recovery_dir)
            or not isinstance(marker.run_id, str) or re.fullmatch('[0-9a-f]{32}', marker.run_id) is None):
        raise SessionError('Durable native marker evidence is required')
    parent = descriptor = None
    try:
        parent = _open_directory(marker.claim_path.parent)
        owner = os.fstat(parent)
        if owner.st_uid != os.getuid() or stat.S_IMODE(owner.st_mode) & 0o022:
            raise ValueError('Unsafe claim parent')
        descriptor = os.open(marker.claim_path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                or not 0 < before.st_size <= 65536):
            raise ValueError('Unsafe claim file')
        data = bytearray()
        while len(data) <= 65536:
            chunk = os.read(descriptor, 65537 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > 65536:
            raise ValueError('Oversized claim')
        claim = json.loads(data, object_pairs_hook=_unique,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
        native = claim.get('native_recovery') if isinstance(claim, dict) else None
        claim_fields = {item.name for item in fields(queue.Claim)}
        if not isinstance(claim, dict) or set(claim) != claim_fields:
            raise ValueError('Incomplete claim')
        record = queue.Claim(**claim)
        if (type(record.repo) is not str or not record.repo
                or type(record.number) is not int or record.number <= 0
                or type(record.branch) is not str or not record.branch
                or type(record.started_at) is not str or not record.started_at
                or record.phase not in {phase.value for phase in queue.Phase}
                or type(record.revise) is not bool
                or record.path != marker.claim_path
                or record.worktree != str(marker.worktree)
                or not isinstance(native, dict) or set(native) != {'version', 'run_id', 'recovery_dir'}
                or type(native['version']) is not int or native['version'] != 1
                or native['run_id'] != marker.run_id or native['recovery_dir'] != str(recovery_dir)
                or record.native_recovery != native):
            raise ValueError('Claim does not match native attempt')
        if _stamp(os.fstat(descriptor)) != _stamp(before):
            raise ValueError('Claim changed while read')
        os.fsync(descriptor)
        os.fsync(parent)
        current = os.stat(marker.claim_path.name, dir_fd=parent, follow_symlinks=False)
        if _stamp(current) != _stamp(before) or _stamp(os.fstat(descriptor)) != _stamp(before):
            raise ValueError('Claim changed while synced')
    except (OSError, ValueError, TypeError, UnicodeError):
        raise SessionError('Durable native marker could not be confirmed') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def allow_candidate(result: WorkerResult, *, provider_stopped: bool, executor_stopped: bool,
                    session_cleanup: bool, lease_cleanup: bool) -> bool:
    """Accept data only after a real successful turn and all owned cleanup."""
    return (type(result) is WorkerResult and result.status == 'succeeded'
            and all(value is True for value in (provider_stopped, executor_stopped, session_cleanup, lease_cleanup))
            and isinstance(result.thread_id, str) and bool(result.thread_id.strip())
            and isinstance(result.turn_id, str) and bool(result.turn_id.strip())
            and isinstance(result.requested_model, str) and bool(result.requested_model.strip())
            and result.observed_model == result.requested_model)

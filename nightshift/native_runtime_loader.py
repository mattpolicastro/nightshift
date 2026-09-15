"""Protected host-owned managed-runtime loading; public activation stays blocked.

Only the private function reads explicit operator-supplied paths. No environment,
issue text, API credential, custom endpoint or interactive profile is consulted.
"""
import json
import os
import re
import stat
from dataclasses import fields
from pathlib import Path

from .native_dispatch import _NativeRuntime, _validated_runtime
from .workers.base import WorkerBudgets
from .workers.chatgpt_admission import ChatGPTIdentity
from .workers.container_session import SessionError
from .workers.managed_task import _path, _validated_profile

_MANIFEST = {'version', 'credential_root', 'identity_reference', 'binary', 'binary_version',
    'implementation_image_id', 'review_image_id', 'verification_image_id', 'docker_host',
    'recovery_root', 'worktree_root', 'phases'}
_PHASE = {'driver', 'provider', 'auth', 'billing', 'model', 'reasoning_effort', 'budgets'}
_BUDGETS = {field.name for field in fields(WorkerBudgets)}
_IDENTITY = {'email', 'account_id'}
_LIMIT = 65536


def _unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate configuration field')
        result[name] = value
    return result


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _record(path):
    if type(path) is not type(Path()) or not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('Protected record path must be canonical')
    _path(path.parent, 'directory')
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        parent = os.fstat(directory)
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise ValueError('Protected parent ownership or permissions differ')
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                or not 0 < before.st_size <= _LIMIT):
            raise ValueError('Protected record ownership, type or size differs')
        content = bytearray()
        while len(content) <= _LIMIT:
            chunk = os.read(descriptor, min(8192, _LIMIT + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if (len(content) > _LIMIT or _stamp(os.fstat(descriptor)) != _stamp(before)
                or _stamp(os.stat(path.name, dir_fd=directory, follow_symlinks=False)) != _stamp(before)):
            raise ValueError('Protected record changed while read')
        # A renamed original reached through a new symlink has the same inode;
        # canonical path and permissions must be revalidated as well as identity.
        _path(path.parent, 'directory')
        current_directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            current = os.fstat(current_directory)
            if ((current.st_dev, current.st_ino) != (parent.st_dev, parent.st_ino)
                    or current.st_uid != os.getuid() or stat.S_IMODE(current.st_mode) != 0o700):
                raise ValueError('Protected parent changed while read')
        finally:
            os.close(current_directory)
        return json.loads(content, object_pairs_hook=_unique,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _phases(raw):
    if type(raw) is not dict or set(raw) != {'implement', 'review'}:
        raise ValueError('Exact managed phases required')
    result = {}
    for name, value in raw.items():
        if type(value) is not dict or set(value) != _PHASE:
            raise ValueError('Unknown managed phase fields')
        required = {'driver': 'codex-app-server', 'provider': 'openai', 'auth': 'chatgpt', 'billing': 'subscription'}
        if any(type(value[key]) is not str or value[key] != expected for key, expected in required.items()):
            raise ValueError('Only ChatGPT-managed subscription phases are supported')
        budgets = value['budgets']
        if type(budgets) is not dict or set(budgets) != _BUDGETS:
            raise ValueError('Every bounded budget must be explicit')
        for key, number in budgets.items():
            allowed = (int, float) if key in {'max_runtime_s', 'interrupt_grace_s'} else (int,)
            if type(number) not in allowed:
                raise ValueError('Invalid budget type')
        result[name] = {key: value[key] for key in _PHASE - {'billing', 'budgets'}}
        result[name]['budgets'] = WorkerBudgets(**budgets)
    return result


class ManagedRuntimeLoader:
    def __init__(self, *args, **kwargs):
        raise SessionError('Public managed runtime loading is disabled')

    @staticmethod
    def load(*args, **kwargs):
        raise SessionError('Public managed runtime loading is disabled')


def _load_managed_runtime(manifest_path: Path, *, identity_root: Path) -> _NativeRuntime:
    """Private loading only; returns validated data, never starts a process.

    Identity records are separate private metadata, not OAuth/API credential
    files. Model/account admission and actual version checks remain runtime gates.
    """
    try:
        manifest = _record(manifest_path)
        if (type(manifest) is not dict or set(manifest) != _MANIFEST
                or type(manifest['version']) is not int or manifest['version'] != 1):
            raise ValueError('Unrecognized managed manifest')
        reference = manifest['identity_reference']
        if (type(reference) is not str or len(reference.encode()) > 128
                or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', reference) is None):
            raise ValueError('Invalid opaque identity reference')
        _path(identity_root, 'directory')
        profile_args = {key: manifest[key] for key in _MANIFEST - {'version', 'worktree_root', 'phases'}}
        for name in ('credential_root', 'recovery_root', 'binary', 'worktree_root'):
            if type(manifest[name]) is not str:
                raise ValueError('Explicit canonical path strings required')
            if name != 'worktree_root':
                profile_args[name] = Path(manifest[name])
        profile = _validated_profile(**profile_args, phases=_phases(manifest['phases']))
        record = _record(identity_root / (reference + '.json'))
        if (type(record) is not dict or set(record) != _IDENTITY
                or type(record['email']) is not str or type(record['account_id']) is not str):
            raise ValueError('Private identity record differs')
        identity = ChatGPTIdentity(record['email'], record['account_id'])
        return _validated_runtime(profile, identity_reference=reference, identity=identity,
                                  worktree_root=Path(manifest['worktree_root']))
    except Exception:
        # No raw JSON, account metadata, local paths or exception context leaks.
        raise SessionError('Protected managed runtime configuration rejected') from None

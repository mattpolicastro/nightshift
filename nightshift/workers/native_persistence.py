"""Private durable native attempt evidence. Existing journals never authorize replay.

The claim lock is cooperative and remains held across both provider attempts and
cleanup. Recovery is inspection-only; this module never removes native markers,
ships a candidate, or derives billing from token evidence.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from .. import native_accounting, queue
from .container_session import SessionError
from .isolated_verification import IsolatedVerificationResult, ClauseResult
from .credential_home import _open_directory, _private
from .stable_worker_guard import NativeMarkerEvidence, validate_marker, _unique
from . import candidate, snapshot
from .candidate_pipeline import _current

_LIMIT = 128 * 1024
_BINDING = {'base_sha', 'verify_command', 'implementation_image_id',
            'verification_image_id', 'review_image_id', 'approved_context_fingerprint',
            'implementation_model', 'review_model',
            'implementation_reasoning_effort', 'review_reasoning_effort'}


def _data(value, depth=0):
    if depth > 8:
        raise SessionError('Native journal data exceeds its bounds')
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str and len(value.encode()) <= 16384:
        return
    if type(value) is list and len(value) <= 64:
        for item in value:
            _data(item, depth + 1)
        return
    if type(value) is dict and len(value) <= 64 and all(type(k) is str and len(k) <= 128 for k in value):
        for item in value.values():
            _data(item, depth + 1)
        return
    raise SessionError('Native journal requires bounded data-only evidence')


def _encode(value):
    _data(value)
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    if len(payload) > _LIMIT:
        raise SessionError('Native journal exceeds its bounds')
    return payload


def _sha(value):
    return type(value) is str and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value) is not None


def _fingerprint(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


class NativeAttemptJournal:
    def __init__(self, marker: NativeMarkerEvidence, recovery_dir, binding: dict):
        if type(marker) is not NativeMarkerEvidence:
            raise SessionError('Native journal requires an exact marker')
        if (type(binding) is not dict or set(binding) != _BINDING
                or any(binding[key] is not None and (type(binding[key]) is not str
                       or not binding[key] or binding[key].strip() != binding[key]
                       or len(binding[key].encode()) > 64 or any(ord(c) < 32 for c in binding[key]))
                       for key in ('implementation_reasoning_effort', 'review_reasoning_effort'))
                or not _sha(binding['base_sha'])
                or not _fingerprint(binding['approved_context_fingerprint'])
                or any(type(binding[key]) is not str or not binding[key].strip()
                       or len(binding[key].encode()) > 256 or any(ord(c) < 32 for c in binding[key])
                       for key in ('implementation_model', 'review_model'))
                or type(binding['verify_command']) is not str or not binding['verify_command'].strip()
                or any(type(binding[key]) is not str or re.fullmatch('sha256:[0-9a-f]{64}', binding[key]) is None
                       for key in ('implementation_image_id', 'verification_image_id', 'review_image_id'))):
            raise SessionError('Native journal requires exact execution bindings')
        self.marker, self.recovery_dir = marker, recovery_dir
        self._binding = json.loads(_encode(binding))
        self.initial_claim = None
        self._claim = None
        self._parent = self._lock = self._directory = None
        self._payload = None
        self._state = None
        self._entered = self._poisoned = False
        self._lock_name = marker.claim_path.name + '.native.lock'
        self._name = 'native-run-' + marker.run_id + '.json'

    @property
    def binding(self):
        return json.loads(_encode(self._binding))

    @property
    def state(self):
        return json.loads(_encode(self._state))

    def _read(self, directory, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not _private(info) or not 0 < info.st_size <= _LIMIT:
                raise SessionError('Native evidence ownership or size differs')
            payload = stream.read(_LIMIT + 1)
            if len(payload) > _LIMIT:
                raise SessionError('Native evidence exceeds its bounds')
            return payload

    def _same(self):
        if self._poisoned or self._directory is None or self._lock is None:
            raise SessionError('Native journal is unavailable')
        for path, held in ((self.marker.claim_path.parent, self._parent), (self.recovery_dir, self._directory)):
            current = _open_directory(path)
            try:
                a, b = os.fstat(current), os.fstat(held)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise SessionError('Native control directory changed')
                if a.st_uid != os.getuid() or stat.S_IMODE(a.st_mode) & 0o022:
                    raise SessionError('Native control directory permissions changed')
                if held == self._directory and not _private(a, directory=True):
                    raise SessionError('Native recovery directory is not private')
            finally:
                os.close(current)
        current = os.stat(self._lock_name, dir_fd=self._parent, follow_symlinks=False)
        held = os.fstat(self._lock)
        if not _private(current) or (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise SessionError('Native run lock changed')
        if self._payload is not None and self._read(self._directory, self._name) != self._payload:
            raise SessionError('Native journal changed outside its owner')
        if validate_marker(self.marker, self.recovery_dir) != self._claim:
            raise SessionError('Native claim changed outside its owner')

    def _replace(self, directory, name, payload):
        temporary = '.' + name + '.' + uuid.uuid4().hex + '.tmp'
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
            if self._read(directory, name) != payload:
                raise SessionError('Native durable write could not be confirmed')
        except BaseException:
            self._poisoned = True
            # Ambiguous temporary/final records remain available for inspection.
            raise

    def _save(self, state):
        self._same()
        payload = _encode(state)
        self._replace(self._directory, self._name, payload)
        self._payload, self._state = payload, json.loads(payload)

    def __enter__(self):
        if self._entered:
            raise SessionError('Native journal cannot be reused')
        self._entered = True
        try:
            if self._parent is None:
                self._parent = _open_directory(self.marker.claim_path.parent)
                info = os.fstat(self._parent)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
                    raise SessionError('Native claim directory ownership differs')
                self._lock = os.open(self._lock_name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     0o600, dir_fd=self._parent)
                if not _private(os.fstat(self._lock)):
                    raise SessionError('Native claim lock ownership differs')
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.fsync(self._lock)
                os.fsync(self._parent)
            self._claim = validate_marker(self.marker, self.recovery_dir)
            if self._claim.phase != 'implementing':
                raise SessionError('A new native journal requires an implementing claim')
            self.initial_claim = replace(self._claim, native_recovery=dict(self._claim.native_recovery))
            self._directory = _open_directory(self.recovery_dir)
            if not _private(os.fstat(self._directory), directory=True):
                raise SessionError('An existing private native recovery directory is required')
            # Even a partial or malformed previous run is not replay authority.
            if any(name == self._name or name.startswith('.' + self._name + '.')
                   for name in os.listdir(self._directory)):
                raise SessionError('Native attempt evidence exists; explicit recovery is required')
            self._save({'version': 1, 'run_id': self.marker.run_id,
                        'claim': asdict(self._claim), 'binding': self.binding,
                        'implement': None, 'review': None})
            return self
        except BaseException as exc:
            self.__exit__(None, None, None)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise SessionError('Native journal could not acquire durable ownership') from None

    def start(self, phase: str, binding: dict | None = None):
        state = self.state
        if phase not in ('implement', 'review'):
            raise SessionError('Unknown native journal phase')
        if phase == 'implement':
            if state['implement'] is not None or state['review'] is not None:
                raise SessionError('Native implementation cannot be replayed')
            if binding is not None and binding != self.binding:
                raise SessionError('Native implementation binding differs')
            state['implement'] = {'state': 'started', 'final': None}
        else:
            review = state['review']
            if not review or review['state'] != 'prepared' or self._claim.phase != 'reviewing':
                raise SessionError('Native review has no durable prepared transition')
            if binding is not None and binding != review['binding']:
                raise SessionError('Native review binding differs')
            review['state'] = 'started'
        self._save(state)

    def prepare_review(self, binding: dict, verification: IsolatedVerificationResult):
        if (type(binding) is not dict or set(binding) != {'candidate_sha', 'source_fingerprint'}
                or not _sha(binding['candidate_sha']) or not _fingerprint(binding['source_fingerprint'])):
            raise SessionError('Native review requires exact candidate binding')
        if (type(verification) is not IsolatedVerificationResult
                or verification.cleanup_succeeded is not True
                or type(verification.clauses) is not list or not verification.clauses
                or any(type(c) is not ClauseResult or type(c.exit_code) is not int
                       or c.exit_code != 0 for c in verification.clauses)
                or not verification.ok
                or verification.candidate_sha != binding['candidate_sha']
                or verification.source_fingerprint != binding['source_fingerprint']
                or verification.final_fingerprint != binding['source_fingerprint']
                or verification.command != self.binding['verify_command']
                or verification.image_id != self.binding['verification_image_id']):
            raise SessionError('Native review requires exact successful verification')
        state = self.state
        implementation = state['implement']
        if (not implementation or implementation['state'] != 'finished' or state['review'] is not None
                or implementation['final']['outcome'].get('status') != 'succeeded'
                or any(implementation['final']['outcome'].get(key) is not True for key in
                       ('provider_stopped', 'executor_stopped', 'session_cleanup', 'lease_cleanup'))):
            raise SessionError('Native implementation has no eligible durable result')
        state['review'] = {'state': 'prepared', 'binding': binding, 'final': None,
                           'verification': {'command': verification.command,
                                            'image_id': verification.image_id,
                                            'cleanup_succeeded': True}}
        self._save(state)

    def enter_reviewing(self, expected_claim):
        self._same()
        if (type(expected_claim) is not queue.Claim or expected_claim != self._claim
                or not self._state['review'] or self._state['review']['state'] != 'prepared'
                or self._claim.phase != 'implementing'):
            raise SessionError('Native claim cannot transition to review')
        next_claim = replace(self._claim, phase='reviewing', native_recovery=dict(self._claim.native_recovery))
        self._replace(self._parent, self.marker.claim_path.name, _encode(asdict(next_claim)))
        observed = validate_marker(self.marker, self.recovery_dir)
        if observed != next_claim:
            self._poisoned = True
            raise SessionError('Native review transition could not be confirmed')
        self._claim = next_claim
        return replace(next_claim, native_recovery=dict(next_claim.native_recovery))

    def finish(self, accounting: native_accounting.NativeAccounting, binding: dict):
        if (type(accounting) is not native_accounting.NativeAccounting
                or accounting.run_id != self.marker.run_id or type(binding) is not dict
                or type(binding.get('status')) is not str or not binding['status']):
            raise SessionError('Native final evidence is invalid')
        receipt = {'accounting': asdict(accounting), 'outcome': json.loads(_encode(binding))}
        state = self.state
        phase = state[accounting.phase]
        if not phase or phase['state'] not in ('started', 'finished'):
            raise SessionError('Native final evidence has no started attempt')
        if phase['state'] == 'finished':
            self._same()
            if _encode(phase['final']) != _encode(receipt):
                raise SessionError('Conflicting final native evidence')
            return
        phase['state'], phase['final'] = 'finished', receipt
        self._save(state)

    def __exit__(self, exc_type, exc, traceback):
        for field in ('_directory', '_lock', '_parent'):
            descriptor = getattr(self, field)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, field, None)
        return False


def _read_initial_claim(directory, claim_path, expected_claim, initial_bytes=None, initial_stamp=None):
    descriptor = os.open(claim_path.name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        # Ordinary claims may be 0644. The native CAS replacement is 0600.
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o022
                or not 0 < info.st_size <= 65536):
            raise SessionError('Initial claim is not an owned regular file')
        data = stream.read(65537)
        after = os.fstat(stream.fileno())
    stamp = lambda i: (i.st_dev, i.st_ino, i.st_size, i.st_mtime_ns, i.st_ctime_ns)
    current = os.stat(claim_path.name, dir_fd=directory, follow_symlinks=False)
    if len(data) > 65536 or stamp(info) != stamp(after) or stamp(info) != stamp(current):
        raise SessionError('Initial claim changed while read')
    parsed = json.loads(data, object_pairs_hook=_unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
    if _encode(parsed) != _encode(asdict(expected_claim)):
        raise SessionError('Initial claim differs from expected ownership')
    if initial_bytes is not None and (data != initial_bytes or stamp(info) != initial_stamp):
        raise SessionError('Initial claim changed before native replacement')
    return data, stamp(info)


class NativeClaimLease:
    """Fresh claimed-to-native preparation under one persistent ownership lock.

    Only an already-created clean owned worktree is admitted. The recovery parent
    is explicit and private. Existing lock history always requires inspection.
    A journal borrows duplicate descriptors of the SAME flock description, so
    its close cannot unlock this lease and there is no unlocked handoff window.
    """
    def __init__(self, expected_claim: queue.Claim, worktree: Path,
                 recovery_parent: Path, binding: dict, *, ownership=None):
        if (type(expected_claim) is not queue.Claim or expected_claim.phase != 'claimed'
                or expected_claim.revise is not False or expected_claim.native_recovery is not None
                or type(expected_claim.repo) is not str or not expected_claim.repo
                or type(expected_claim.number) is not int or expected_claim.number <= 0
                or type(expected_claim.branch) is not str or not expected_claim.branch
                or type(expected_claim.started_at) is not str or not expected_claim.started_at
                or not isinstance(worktree, Path) or not worktree.is_absolute()
                or expected_claim.worktree != str(worktree)
                or not isinstance(recovery_parent, Path) or not recovery_parent.is_absolute()):
            raise SessionError('Fresh exact non-revision claim ownership is required')
        self.expected_claim = queue.Claim(**json.loads(_encode(asdict(expected_claim))))
        self.worktree, self.recovery_parent = worktree, recovery_parent
        self.run_id = uuid.uuid4().hex
        self.recovery_dir = recovery_parent / self.run_id
        self.marker = NativeMarkerEvidence(expected_claim.path, self.run_id, worktree)
        self._control = NativeAttemptJournal(self.marker, self.recovery_dir, binding)
        self._recovery_parent_fd = self._worktree_fd = None
        self._entered = self._ready = self._opened = False
        self._initial_bytes = self._initial_stamp = None
        self._journal = None
        self._ownership = ownership

    def _owned_directory(self, path, *, private=False):
        descriptor = _open_directory(path)
        info = os.fstat(descriptor)
        if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022
                or (private and not _private(info, directory=True))):
            os.close(descriptor)
            raise SessionError('Native preparation directory ownership differs')
        return descriptor

    def _initial(self):
        self._initial_bytes, self._initial_stamp = _read_initial_claim(
            self._control._parent, self.marker.claim_path, self.expected_claim,
            self._initial_bytes, self._initial_stamp)

    def _paths(self):
        for path, held, private in ((self.marker.claim_path.parent, self._control._parent, False),
                (self.recovery_parent, self._recovery_parent_fd, True),
                (self.worktree, self._worktree_fd, False)):
            descriptor = self._owned_directory(path, private=private)
            try:
                a, b = os.fstat(descriptor), os.fstat(held)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise SessionError('Native preparation directory changed')
            finally:
                os.close(descriptor)
        current = os.stat(self._control._lock_name, dir_fd=self._control._parent, follow_symlinks=False)
        held = os.fstat(self._control._lock)
        if not _private(current) or (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise SessionError('Native preparation lock changed')

    def __enter__(self):
        if self._entered:
            raise SessionError('Native claim lease cannot be reused')
        self._entered = True
        try:
            control = self._control
            if self._ownership is None:
                control._parent = self._owned_directory(self.marker.claim_path.parent)
                # Even an unlocked old file is retained history, never a fresh run.
                control._lock = os.open(control._lock_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=control._parent)
                fcntl.flock(control._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.fsync(control._lock)
                os.fsync(control._parent)
            else:
                owner = self._ownership
                if (type(owner) is not NativePreparationLock or owner._handed_off
                        or owner.expected_claim != self.expected_claim or owner.worktree != self.worktree
                        or owner.recovery_parent != self.recovery_parent):
                    raise SessionError('Native preparation ownership differs')
                owner._same()
                owner._handed_off = True
                control._parent = os.dup(owner._parent)
                control._lock = os.dup(owner._lock)
                self._initial_bytes, self._initial_stamp = owner._initial_bytes, owner._initial_stamp
            self._initial()
            self._recovery_parent_fd = self._owned_directory(self.recovery_parent, private=True)
            self._worktree_fd = self._owned_directory(self.worktree)
            if Path(candidate._git(self.worktree, 'rev-parse', '--show-toplevel').decode().strip()) != self.worktree:
                raise SessionError('Native preparation requires the exact Git worktree root')
            baseline = snapshot.from_git(self.worktree, control.binding['base_sha'])
            _current(self.worktree, control.binding['base_sha'], baseline,
                     ('refs/heads/' + self.expected_claim.branch + '\n').encode())
            self._paths()
            self._initial()
            os.mkdir(self.run_id, mode=0o700, dir_fd=self._recovery_parent_fd)
            os.fsync(self._recovery_parent_fd)
            control._directory = self._owned_directory(self.recovery_dir, private=True)
            prepared = replace(self.expected_claim, phase='implementing',
                native_recovery={'version': 1, 'run_id': self.run_id, 'recovery_dir': str(self.recovery_dir)})
            self._paths()
            self._initial()
            control._replace(control._parent, self.marker.claim_path.name, _encode(asdict(prepared)))
            observed = validate_marker(self.marker, self.recovery_dir)
            if observed != prepared:
                raise SessionError('Durable native preparation could not be confirmed')
            control._claim = prepared
            control._same()
            self._ready = True
            return self
        except BaseException as exc:
            self.__exit__(None, None, None)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise SessionError('Native preparation failed; retain ownership evidence') from None

    def open_journal(self):
        if not self._ready or self._opened:
            raise SessionError('Native claim has no fresh journal handoff')
        self._control._same()
        journal = NativeAttemptJournal(self.marker, self.recovery_dir, self._control.binding)
        self._opened = True
        try:
            journal._parent = os.dup(self._control._parent)
            journal._lock = os.dup(self._control._lock)
        except BaseException:
            journal.__exit__(None, None, None)
            raise
        self._journal = journal
        return journal

    def __exit__(self, exc_type, exc, traceback):
        self._ready = False
        if self._journal is not None:
            self._journal.__exit__(exc_type, exc, traceback)
        self._control.__exit__(exc_type, exc, traceback)
        for field in ('_worktree_fd', '_recovery_parent_fd'):
            descriptor = getattr(self, field)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, field, None)
        return False


class NativePreparationLock:
    """Durable ownership before any Git creation, fetch, or base resolution.

    Performs no Git, credential or provider calls. The caller may only create
    the absent target while holding this lease. Existing history is never
    reused, even if the claim still says claimed and no source was created.
    """
    def __init__(self, expected_claim: queue.Claim, worktree: Path,
                 worktree_root: Path, recovery_parent: Path):
        if (type(expected_claim) is not queue.Claim or expected_claim.phase != 'claimed'
                or expected_claim.revise is not False or expected_claim.native_recovery is not None
                or type(expected_claim.repo) is not str or not expected_claim.repo
                or type(expected_claim.number) is not int or expected_claim.number <= 0
                or type(expected_claim.branch) is not str or not expected_claim.branch
                or type(expected_claim.started_at) is not str or not expected_claim.started_at
                or any(not isinstance(path, Path) or not path.is_absolute() or '..' in path.parts
                       for path in (worktree, worktree_root, recovery_parent))
                or expected_claim.worktree != str(worktree) or worktree_root not in worktree.parents
                or recovery_parent == worktree or worktree in recovery_parent.parents
                or recovery_parent in worktree.parents):
            raise SessionError('Fresh bounded native preparation ownership is required')
        self.expected_claim = queue.Claim(**json.loads(_encode(asdict(expected_claim))))
        self.worktree, self.worktree_root, self.recovery_parent = worktree, worktree_root, recovery_parent
        self._parent = self._lock = None
        self._directories = []
        self._initial_bytes = self._initial_stamp = None
        self._entered = self._ready = self._handed_off = False
        self._ownership_started = False
        self._ownership_started = False
        self._lock_name = expected_claim.path.name + '.native.lock'

    @property
    def ownership_started(self):
        """Whether this attempt created persistent native ownership evidence."""
        return self._ownership_started

    @property
    def ownership_started(self):
        return self._ownership_started

    def _directory(self, path, private=False):
        descriptor = _open_directory(path)
        try:
            for ancestor in (path, *path.parents):
                info = ancestor.lstat()
                sticky = (ancestor in {Path('/tmp'), Path('/private/tmp')} and info.st_uid == 0
                          and bool(info.st_mode & stat.S_ISVTX))
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                        or (info.st_mode & 0o022 and not sticky)):
                    raise SessionError('Native preparation ancestors are not protected')
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or (private and not _private(info, directory=True)):
                raise SessionError('Native preparation root ownership differs')
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _same(self):
        if not self._ready or self._lock is None:
            raise SessionError('Native preparation ownership is unavailable')
        for path, held, private in self._directories:
            descriptor = self._directory(path, private)
            try:
                a, b = os.fstat(descriptor), os.fstat(held)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise SessionError('Native preparation root changed')
            finally:
                os.close(descriptor)
        current = os.stat(self._lock_name, dir_fd=self._parent, follow_symlinks=False)
        held = os.fstat(self._lock)
        if not _private(current) or (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise SessionError('Native preparation lock changed')
        _read_initial_claim(self._parent, self.expected_claim.path, self.expected_claim,
                            self._initial_bytes, self._initial_stamp)

    def __enter__(self):
        if self._entered:
            raise SessionError('Native preparation ownership cannot be reused')
        self._entered = True
        try:
            self._parent = self._directory(self.expected_claim.path.parent)
            self._directories.append((self.expected_claim.path.parent, self._parent, False))
            self._lock = os.open(self._lock_name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=self._parent)
            self._ownership_started = True
            self._ownership_started = True
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fsync(self._lock)
            os.fsync(self._parent)
            self._initial_bytes, self._initial_stamp = _read_initial_claim(
                self._parent, self.expected_claim.path, self.expected_claim)
            for path, private in ((self.worktree_root, False), (self.worktree.parent, False),
                                  (self.recovery_parent, True)):
                self._directories.append((path, self._directory(path, private), private))
            if os.path.lexists(self.worktree):
                raise SessionError('Native preparation target already exists')
            self._ready = True
            self._same()
            return self
        except BaseException as exc:
            self.__exit__(None, None, None)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise SessionError('Native preparation stopped; retain ownership evidence') from None

    def __exit__(self, exc_type, exc, traceback):
        self._ready = False
        for _, descriptor, _ in self._directories:
            os.close(descriptor)
        self._directories = []
        self._parent = None
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
        return False

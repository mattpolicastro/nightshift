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

from .. import native_accounting, queue
from .container_session import SessionError
from .isolated_verification import IsolatedVerificationResult, ClauseResult
from .credential_home import _open_directory, _private
from .stable_worker_guard import NativeMarkerEvidence, validate_marker

_LIMIT = 128 * 1024
_BINDING = {'base_sha', 'verify_command', 'implementation_image_id',
            'verification_image_id', 'review_image_id', 'approved_context_fingerprint',
            'implementation_model', 'review_model'}


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

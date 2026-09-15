"""Synthetic native marker durability and cleanup gates; no process/model calls."""
import json
import os
from dataclasses import replace

import pytest

from nightshift import queue
from nightshift.workers import stable_worker_guard as module
from nightshift.workers.base import WorkerResult
from nightshift.workers.container_session import SessionError


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, 'CLAIM_DIR', tmp_path / 'claims')
    worktree = tmp_path / 'worktree'
    claim = queue.Claim('example/repo', 7, 'candidate/7', str(worktree), 'fixture')
    claim.write()
    recovery = tmp_path / 'recovery'
    claim._prepare_native('a' * 32, recovery)
    claim.path.chmod(0o600)
    marker = module.NativeMarkerEvidence(claim.path, 'a' * 32, worktree)
    return marker, recovery


def test_marker_syncs_exact_file_and_parent_before_admission(evidence, monkeypatch):
    marker, recovery = evidence
    calls = []
    original = module.os.fsync
    def sync(fd):
        calls.append(os.fstat(fd).st_ino)
        original(fd)
    monkeypatch.setattr(module.os, 'fsync', sync)
    module.validate_marker(marker, recovery)
    assert calls == [marker.claim_path.stat().st_ino, marker.claim_path.parent.stat().st_ino]
    assert marker.run_id not in repr(marker) and str(marker.worktree) not in repr(marker)


def test_validated_marker_remains_loadable_for_restart_retention(evidence):
    marker, recovery = evidence
    module.validate_marker(marker, recovery)
    records, repairs = queue._load_claims('example/repo')
    assert not repairs
    assert records[7].native_recovery == {
        'version': 1, 'run_id': marker.run_id, 'recovery_dir': str(recovery)}
    assert marker.claim_path.exists()


@pytest.mark.parametrize('field,value', [('version', True), ('version', 2), ('run_id', 'b' * 32),
                                       ('recovery_dir', '/different'), ('extra', 'field')])
def test_mismatched_marker_rejected(evidence, field, value):
    marker, recovery = evidence
    data = json.loads(marker.claim_path.read_text())
    data['native_recovery'][field] = value
    marker.claim_path.write_text(json.dumps(data))
    with pytest.raises(SessionError):
        module.validate_marker(marker, recovery)


@pytest.mark.parametrize('kind', ['worktree', 'missing', 'incomplete', 'wrong_location',
                                  'duplicate_json', 'symlink', 'hardlink', 'file_mode',
                                  'parent_mode', 'oversized'])
def test_unsafe_or_ambiguous_marker_rejected(evidence, kind, tmp_path):
    marker, recovery = evidence
    if kind == 'worktree': marker = replace(marker, worktree=tmp_path / 'other')
    elif kind == 'missing': marker.claim_path.unlink()
    elif kind == 'incomplete':
        marker.claim_path.write_text(json.dumps({'worktree': str(marker.worktree),
            'native_recovery': {'version': 1, 'run_id': marker.run_id,
                                'recovery_dir': str(recovery)}}))
    elif kind == 'wrong_location':
        moved = marker.claim_path.parent / 'other.json'
        marker.claim_path.rename(moved)
        marker = replace(marker, claim_path=moved)
    elif kind == 'duplicate_json': marker.claim_path.write_text('{"native_recovery":null,"native_recovery":{}}')
    elif kind in ('symlink', 'hardlink'):
        original = tmp_path / 'original'
        marker.claim_path.rename(original)
        if kind == 'symlink': marker.claim_path.symlink_to(original)
        else: os.link(original, marker.claim_path)
    elif kind == 'file_mode': marker.claim_path.chmod(0o644)
    elif kind == 'parent_mode': marker.claim_path.parent.chmod(0o777)
    else: marker.claim_path.write_bytes(b'x' * 65537)
    with pytest.raises(SessionError):
        module.validate_marker(marker, recovery)


@pytest.mark.parametrize('failure_at', [1, 2])
def test_failed_durability_never_admits(evidence, monkeypatch, failure_at):
    marker, recovery = evidence
    calls = []
    def sync(fd):
        calls.append(fd)
        if len(calls) == failure_at: raise OSError('synthetic failure')
    monkeypatch.setattr(module.os, 'fsync', sync)
    with pytest.raises(SessionError):
        module.validate_marker(marker, recovery)
    assert marker.claim_path.exists()


def test_marker_replacement_during_sync_rejected(evidence, monkeypatch):
    marker, recovery = evidence
    def sync(fd):
        if marker.claim_path.exists():
            marker.claim_path.unlink()
            marker.claim_path.write_text('{}')
            marker.claim_path.chmod(0o600)
    monkeypatch.setattr(module.os, 'fsync', sync)
    with pytest.raises(SessionError):
        module.validate_marker(marker, recovery)


def worker():
    return WorkerResult(status='succeeded', requested_model='model', observed_model='model',
                        thread_id='thread', turn_id='turn')


def test_candidate_requires_actual_turn_and_all_cleanup():
    completed = dict(provider_stopped=True, executor_stopped=True, session_cleanup=True, lease_cleanup=True)
    assert module.allow_candidate(worker(), **completed)
    for key in completed:
        for value in (False, None, 1, 'true'):
            assert not module.allow_candidate(worker(), **{**completed, key: value})
    for change in ({'status': 'failed'}, {'thread_id': None}, {'turn_id': None},
                   {'observed_model': 'other'}, {'requested_model': ''}):
        assert not module.allow_candidate(replace(worker(), **change), **completed)
    assert not module.allow_candidate(WorkerResult(status='succeeded'), **completed)

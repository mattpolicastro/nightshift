"""Synthetic durable evidence, actual filesystem permissions and process locks."""
import json
import multiprocessing
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from nightshift import queue, native_accounting
from nightshift.workers import native_persistence as module
from nightshift.workers.container_session import SessionError
from nightshift.workers.isolated_verification import IsolatedVerificationResult, ClauseResult
from nightshift.workers.stable_worker_guard import NativeMarkerEvidence, validate_marker


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, 'CLAIM_DIR', tmp_path / 'claims')
    claim = queue.Claim('fixture/repo', 3, 'fixture', str(tmp_path / 'worktree'), 'fixture', phase='implementing')
    claim.write()
    recovery = tmp_path / 'recovery'
    recovery.mkdir(mode=0o700)
    claim._prepare_native('a' * 32, recovery)
    marker = NativeMarkerEvidence(claim.path, 'a' * 32, tmp_path / 'worktree')
    binding = {'base_sha': 'b' * 40, 'verify_command': 'true',
               'implementation_image_id': 'sha256:' + 'c' * 64,
               'verification_image_id': 'sha256:' + 'd' * 64,
               'review_image_id': 'sha256:' + 'c' * 64,
               'approved_context_fingerprint': 'e' * 64,
               'implementation_model': 'fixture-implementation', 'review_model': 'fixture-review',
               'implementation_reasoning_effort': 'medium', 'review_reasoning_effort': 'high'}
    review = {'candidate_sha': 'f' * 40, 'source_fingerprint': '1' * 64}
    verification = IsolatedVerificationResult(review['candidate_sha'], 'true', binding['verification_image_id'],
        status='succeeded', cleanup_succeeded=True, source_fingerprint=review['source_fingerprint'],
        final_fingerprint=review['source_fingerprint'], clauses=[ClauseResult(('true',), 'succeeded', 0, '', 0)])
    outcome = {'status': 'succeeded', 'provider_stopped': True, 'executor_stopped': True,
               'session_cleanup': True, 'lease_cleanup': True}
    state = SimpleNamespace(claim=claim, marker=marker, recovery=recovery, binding=binding,
                            review=review, verification=verification, outcome=outcome)
    state.open = lambda: module.NativeAttemptJournal(marker, recovery, binding)
    state.accounting = lambda phase='implement': native_accounting.NativeAccounting(
        marker.run_id, phase, native_accounting.NativeTokens(input_tokens=7))
    return state


def test_durable_phase_order_and_idempotent_final_receipts(setup):
    with setup.open() as journal:
        assert journal.initial_claim == setup.claim
        journal.start('implement', setup.binding)
        assert journal.state['implement'] == {'state': 'started', 'final': None}
        journal.finish(setup.accounting(), setup.outcome)
        journal.finish(setup.accounting(), setup.outcome)
        journal.prepare_review(setup.review, setup.verification)
        with pytest.raises(SessionError): journal.start('review', setup.review)
        reviewing = journal.enter_reviewing(journal.initial_claim)
        assert validate_marker(setup.marker, setup.recovery) == reviewing
        assert reviewing.phase == 'reviewing'
        journal.start('review', setup.review)
        journal.finish(setup.accounting('review'), {'status': 'failed'})
        state = journal.state
        state['review']['state'] = 'forged'
        assert journal.state['review']['state'] == 'finished'
        assert journal.state['review']['final']['accounting']['tokens']['total_tokens'] is None
    record = setup.recovery / ('native-run-' + setup.marker.run_id + '.json')
    assert record.stat().st_mode & 0o777 == 0o600
    assert json.loads(record.read_text())['review']['final']['outcome']['status'] == 'failed'
    assert setup.claim.path.exists()
    with pytest.raises(SessionError):
        with setup.open(): pass


@pytest.mark.parametrize('phase', ['empty', 'started', 'finished'])
def test_any_existing_attempt_blocks_restart_and_never_infers_zero(setup, phase):
    with setup.open() as journal:
        if phase != 'empty': journal.start('implement')
        if phase == 'finished': journal.finish(setup.accounting(), {'status': 'failed'})
    with pytest.raises(SessionError):
        with setup.open(): pytest.fail('replayed attempt')
    state = json.loads(next(setup.recovery.glob('native-run-*.json')).read_text())
    if phase == 'started': assert state['implement']['final'] is None


def test_final_conflict_and_bool_integer_alias_rejected(setup):
    with setup.open() as journal:
        journal.start('implement')
        journal.finish(setup.accounting(), setup.outcome)
        for receipt in ({**setup.outcome, 'status': 'failed'}, {**setup.outcome, 'provider_stopped': 1}):
            with pytest.raises(SessionError): journal.finish(setup.accounting(), receipt)
        different = replace(setup.accounting(), tokens=native_accounting.NativeTokens(input_tokens=8))
        with pytest.raises(SessionError): journal.finish(different, setup.outcome)


@pytest.mark.parametrize('field', ['cleanup', 'sha', 'image', 'fingerprint', 'exit', 'namespace'])
def test_review_requires_typed_exact_successful_verification(setup, field):
    with setup.open() as journal:
        journal.start('implement')
        journal.finish(setup.accounting(), setup.outcome)
        evidence = setup.verification
        if field == 'cleanup': evidence.cleanup_succeeded = 1
        if field == 'sha': evidence.candidate_sha = '0' * 40
        if field == 'image': evidence.image_id = 'sha256:' + '0' * 64
        if field == 'fingerprint': evidence.final_fingerprint = '0' * 64
        if field == 'exit': evidence.clauses[0] = replace(evidence.clauses[0], exit_code=False)
        if field == 'namespace': evidence = SimpleNamespace(**vars(evidence), ok=True)
        with pytest.raises(SessionError): journal.prepare_review(setup.review, evidence)
        assert validate_marker(setup.marker, setup.recovery).phase == 'implementing'


def test_claim_change_blocks_transition_and_retains_journal(setup):
    with setup.open() as journal:
        journal.start('implement')
        journal.finish(setup.accounting(), setup.outcome)
        journal.prepare_review(setup.review, setup.verification)
        altered = replace(setup.claim, started_at='changed')
        altered.write()
        altered.path.chmod(0o600)
        with pytest.raises(SessionError): journal.enter_reviewing(journal.initial_claim)
    assert validate_marker(setup.marker, setup.recovery).started_at == 'changed'


@pytest.mark.parametrize('kind', ['symlink-lock', 'hardlink-lock', 'mode-lock', 'mode-root', 'symlink-root'])
def test_unsafe_control_paths_rejected(setup, tmp_path, kind):
    lock = setup.claim.path.with_name(setup.claim.path.name + '.native.lock')
    other = tmp_path / 'other'
    other.write_text('sentinel')
    other.chmod(0o600)
    if kind == 'symlink-lock': lock.symlink_to(other)
    if kind == 'hardlink-lock': os.link(other, lock)
    if kind == 'mode-lock': lock.write_text(''); lock.chmod(0o644)
    if kind == 'mode-root': setup.recovery.chmod(0o755)
    if kind == 'symlink-root':
        setup.recovery.rmdir()
        target = tmp_path / 'target'
        target.mkdir(mode=0o700)
        setup.recovery.symlink_to(target, target_is_directory=True)
    with pytest.raises(SessionError):
        with setup.open(): pass
    assert other.read_text() == 'sentinel'


def _lock_child(path, pipe):
    import fcntl
    descriptor = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pipe.send('blocked')
        else:
            pipe.send('acquired')
    finally:
        os.close(descriptor)
        pipe.close()


def test_real_second_process_lock_held_until_context_exit(setup):
    context = multiprocessing.get_context('spawn')
    def probe():
        reader, writer = context.Pipe(duplex=False)
        process = context.Process(target=_lock_child, args=(str(setup.claim.path) + '.native.lock', writer))
        process.start()
        writer.close()
        assert reader.poll(5)
        result = reader.recv()
        process.join(5)
        assert process.exitcode == 0
        reader.close()
        return result
    with setup.open(): assert probe() == 'blocked'
    assert probe() == 'acquired'


def test_failed_parent_sync_blocks_followups_and_retains_unknown(setup, monkeypatch):
    with setup.open() as journal:
        real = module.os.fsync
        def broken(descriptor):
            if descriptor == journal._directory: raise OSError('synthetic')
            real(descriptor)
        monkeypatch.setattr(module.os, 'fsync', broken)
        with pytest.raises(OSError): journal.start('implement')
        with pytest.raises(SessionError): journal.finish(setup.accounting(), setup.outcome)
    assert setup.claim.path.exists()
    assert next(setup.recovery.glob('native-run-*.json')).exists()


@pytest.mark.parametrize('value', ['', ' ', 7, 'm' * 257, 'model\n'])
def test_model_binding_is_bounded_nonempty_text(setup, value):
    setup.binding['review_model'] = value
    with pytest.raises(SessionError): setup.open()


def test_binding_copies_cannot_change_durable_execution_policy(setup):
    with setup.open() as journal:
        journal.binding['review_model'] = 'changed'
        assert journal.binding['review_model'] == 'fixture-review'
        assert journal.state['binding']['implementation_model'] == 'fixture-implementation'


def test_ambiguous_review_claim_sync_never_authorizes_reviewer(setup, monkeypatch):
    with setup.open() as journal:
        journal.start('implement')
        journal.finish(setup.accounting(), setup.outcome)
        journal.prepare_review(setup.review, setup.verification)
        original = module.os.fsync
        def fail_parent(descriptor):
            if descriptor == journal._parent: raise OSError('synthetic sync uncertainty')
            original(descriptor)
        monkeypatch.setattr(module.os, 'fsync', fail_parent)
        with pytest.raises(OSError): journal.enter_reviewing(journal.initial_claim)
        with pytest.raises(SessionError): journal.start('review', setup.review)
    # Rename may already have happened: neither resetting nor launching is safe.
    assert json.loads(setup.claim.path.read_text())['phase'] == 'reviewing'
    state = json.loads(next(setup.recovery.glob('native-run-*.json')).read_text())
    assert state['review']['state'] == 'prepared' and state['review']['final'] is None


def test_lock_replacement_detected_before_state_mutation(setup):
    with setup.open() as journal:
        lock = setup.claim.path.with_name(setup.claim.path.name + '.native.lock')
        lock.unlink()
        lock.write_bytes(b'')
        lock.chmod(0o600)
        with pytest.raises(SessionError): journal.start('implement')
        assert journal.state['implement'] is None


@pytest.fixture
def fresh_claim(tmp_path, monkeypatch):
    from test_candidate_pipeline import git
    monkeypatch.setattr(queue, 'CLAIM_DIR', tmp_path / 'claims')
    worktree = tmp_path / 'worktree'
    worktree.mkdir(mode=0o700)
    git(worktree, 'init', '-q')
    git(worktree, 'config', 'user.name', 'Fixture')
    git(worktree, 'config', 'user.email', 'fixture@example.invalid')
    (worktree / 'source.txt').write_text('before')
    git(worktree, 'add', 'source.txt')
    git(worktree, 'commit', '-qm', 'base')
    base = git(worktree, 'rev-parse', 'HEAD')
    claim = queue.Claim('fixture/repo', 7, git(worktree, 'branch', '--show-current'), str(worktree), 'fixture')
    claim.write()
    claim.path.chmod(0o644)
    recovery = tmp_path / 'recovery'
    recovery.mkdir(mode=0o700)
    binding = {'base_sha': base, 'verify_command': 'true',
        'implementation_image_id': 'sha256:' + '1' * 64,
        'verification_image_id': 'sha256:' + '2' * 64, 'review_image_id': 'sha256:' + '1' * 64,
        'approved_context_fingerprint': '3' * 64,
        'implementation_model': 'fixture', 'review_model': 'fixture',
        'implementation_reasoning_effort': 'medium', 'review_reasoning_effort': 'high'}
    result = SimpleNamespace(claim=claim, worktree=worktree, recovery=recovery, binding=binding)
    result.lease = lambda: module.NativeClaimLease(result.claim, worktree, recovery, binding)
    return result


def _probe_lock(path):
    context = multiprocessing.get_context('spawn')
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_lock_child, args=(str(path), writer))
    process.start()
    writer.close()
    try:
        assert reader.poll(5)
        value = reader.recv()
        process.join(5)
        assert process.exitcode == 0
        return value
    finally:
        if process.is_alive(): process.kill(); process.join(5)
        reader.close()


def test_prelaunch_lease_durable_marker_and_same_lock_handoff(fresh_claim):
    fixture = fresh_claim
    lock = fixture.claim.path.with_name(fixture.claim.path.name + '.native.lock')
    with fixture.lease() as lease:
        prepared = validate_marker(lease.marker, lease.recovery_dir)
        assert prepared.phase == 'implementing' and prepared.native_recovery['run_id'] == lease.run_id
        assert fixture.claim.path.stat().st_mode & 0o777 == 0o600
        assert lease.recovery_dir.stat().st_mode & 0o777 == 0o700
        assert _probe_lock(lock) == 'blocked'
        with lease.open_journal() as journal:
            assert journal.initial_claim == prepared
            journal.start('implement')
            assert _probe_lock(lock) == 'blocked'
        assert _probe_lock(lock) == 'blocked', 'journal exit must not unlock outer ownership'
        with pytest.raises(SessionError): lease.open_journal()
    assert _probe_lock(lock) == 'acquired'
    assert fixture.claim.path.exists() and lock.exists()
    with pytest.raises(SessionError):
        with fixture.lease(): pytest.fail('old claim replayed')


@pytest.mark.parametrize('kind', ['revise', 'existing-marker', 'phase', 'branch', 'base', 'dirty',
                                  'claim-mode', 'claim-link', 'recovery-mode', 'old-lock'])
def test_prelaunch_rejects_ambiguous_or_unowned_input(fresh_claim, tmp_path, kind):
    fixture = fresh_claim
    before = fixture.claim.path.read_bytes()
    if kind == 'revise': fixture.claim = replace(fixture.claim, revise=True)
    if kind == 'existing-marker': fixture.claim = replace(fixture.claim, native_recovery={})
    if kind == 'phase': fixture.claim = replace(fixture.claim, phase='implementing')
    if kind == 'branch':
        fixture.claim = replace(fixture.claim, branch='other')
        fixture.claim.write()
        before = fixture.claim.path.read_bytes()
    if kind == 'base': fixture.binding['base_sha'] = 'a' * 40
    if kind == 'dirty': (fixture.worktree / 'source.txt').write_text('changed')
    if kind == 'claim-mode': fixture.claim.path.chmod(0o666)
    if kind == 'claim-link':
        original = tmp_path / 'original-claim'
        fixture.claim.path.rename(original)
        fixture.claim.path.symlink_to(original)
    if kind == 'recovery-mode': fixture.recovery.chmod(0o755)
    if kind == 'old-lock':
        fixture.claim.path.with_name(fixture.claim.path.name + '.native.lock').write_text('')
    with pytest.raises(SessionError):
        with fixture.lease(): pytest.fail('unsafe preparation admitted')
    assert fixture.claim.path.read_bytes() == before
    assert not list(fixture.recovery.iterdir())


@pytest.mark.parametrize('failed_sync', [1, 2, 3, 4, 5])
def test_prelaunch_sync_uncertainty_never_returns_handoff(fresh_claim, monkeypatch, failed_sync):
    fixture = fresh_claim
    calls = [0]
    original = module.os.fsync
    def fail(fd):
        calls[0] += 1
        if calls[0] == failed_sync: raise OSError('synthetic sync uncertainty')
        original(fd)
    monkeypatch.setattr(module.os, 'fsync', fail)
    lease = fixture.lease()
    with pytest.raises(SessionError):
        with lease: pytest.fail('uncertain preparation returned')
    with pytest.raises(SessionError): lease.open_journal()
    assert fixture.claim.path.exists()
    assert fixture.claim.path.with_name(fixture.claim.path.name + '.native.lock').exists()
    if failed_sync == 5:
        assert json.loads(fixture.claim.path.read_text())['native_recovery'] is not None
    with pytest.raises(SessionError):
        with fixture.lease(): pytest.fail('uncertain ownership replayed')


def test_initial_claim_replacement_during_validation_blocks_cas(fresh_claim, monkeypatch):
    fixture = fresh_claim
    original = module.snapshot.from_git
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        replacement = replace(fixture.claim, started_at='other-owner')
        replacement.write()
        return value
    monkeypatch.setattr(module.snapshot, 'from_git', changed)
    with pytest.raises(SessionError):
        with fixture.lease(): pytest.fail('changed claim was overwritten')
    assert json.loads(fixture.claim.path.read_text())['started_at'] == 'other-owner'
    assert not list(fixture.recovery.iterdir())


@pytest.mark.parametrize('effort', ['', ' ', 1, False, 'x' * 65, 'high\n'])
def test_invalid_reasoning_effort_cannot_enter_durable_intent(setup, effort):
    setup.binding['review_reasoning_effort'] = effort
    with pytest.raises(SessionError): setup.open()


def test_reasoning_effort_remains_bound_before_any_provider_turn(setup):
    with setup.open() as journal:
        journal.start('implement')
        assert journal.state['binding']['implementation_reasoning_effort'] == 'medium'
        assert journal.state['binding']['review_reasoning_effort'] == 'high'

"""Real durable claims/journals/Git, with synthetic worker and reviewer results."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from nightshift.workers import stable_controller as module
from nightshift.workers.base import ReviewerVerdict, WorkerResult
from nightshift.workers.native_persistence import NativeAttemptJournal
from nightshift.workers.review_context import ApprovedTask, ReviewPolicy
from nightshift.workers.reviewer import ReviewOutcome
from nightshift.workers.stable_worker_guard import validate_marker
from test_stable_pipeline import setup as pipeline_setup
from test_candidate_pipeline import git, repository


@pytest.fixture
def harness(pipeline_setup, tmp_path, monkeypatch):
    state = pipeline_setup
    state.claim.phase = 'implementing'
    state.claim.write()
    state.claim.path.chmod(0o600)
    state.recovery = tmp_path / 'recovery'
    state.recovery.mkdir(mode=0o700)
    state.journal = state.recovery / ('native-run-' + state.marker.run_id + '.json')
    state.read = lambda: json.loads(state.journal.read_text())
    original_implement = module._implement_and_verify
    async def implement(*args, **kwargs):
        assert state.read()['implement'] == {'state': 'started', 'final': None}
        return await original_implement(*args, **kwargs)
    monkeypatch.setattr(module, '_implement_and_verify', implement)
    async def reviewer(request, files, **kwargs):
        state.calls.append('review')
        journal = state.read()
        assert journal['implement']['state'] == 'finished'
        assert journal['review']['state'] == 'started'
        assert journal['review']['binding']['candidate_sha'] == request.candidate_sha
        assert validate_marker(state.marker, state.recovery).phase == 'reviewing'
        assert git(state.root, 'rev-parse', 'HEAD') == request.candidate_sha
        # The claim-wide lock remains held through the provider/reviewer call.
        with pytest.raises(Exception):
            with NativeAttemptJournal(state.marker, state.recovery, journal['binding']): pass
        if state.failure == 'review-exception': raise RuntimeError('synthetic secret')
        if state.failure == 'review-cancel': raise asyncio.CancelledError()
        worker = WorkerResult(status='failed' if state.failure == 'review-worker' else 'succeeded',
            thread_id='new-review', turn_id='review-turn', requested_model='review-model',
            observed_model='review-model', usage={'outputTokens': 3},
            reviewer_verdict=ReviewerVerdict('FAIL' if state.failure == 'review-fail' else 'PASS', (), ()))
        return ReviewOutcome(worker, request.review_id, request.candidate_sha,
            request.verification.source_fingerprint, True, state.failure != 'review-cleanup', True)
    monkeypatch.setattr(module, '_review_stable_chatgpt', reviewer)
    state.call_controller = lambda: module._run_attempt(state.root, state.base, state.request, 'true',
        approved_task=ApprovedTask('task', 'Repair', 'Change source'), review_policy=ReviewPolicy('Check scope'),
        review_model='review-model', credential_root=tmp_path / 'credentials', binary=Path('/synthetic/codex'),
        image_id='sha256:' + 'a' * 64, verification_image_id='sha256:' + 'b' * 64,
        docker_host='unix:///synthetic/docker.sock', recovery_dir=state.recovery,
        expected_identity=None, native_marker=state.marker)
    return state


def test_joined_attempt_records_before_git_and_review_but_never_ships(harness, monkeypatch):
    from nightshift.workers import stable_pipeline
    create = stable_pipeline.candidate.create
    def observed(*args, **kwargs):
        receipt = harness.read()['implement']['final']
        assert receipt['accounting']['tokens']['input_tokens'] == 7
        assert receipt['outcome']['status'] == 'succeeded'
        assert git(harness.root, 'rev-parse', 'HEAD') == harness.base
        return create(*args, **kwargs)
    monkeypatch.setattr(stable_pipeline.candidate, 'create', observed)
    result = asyncio.run(harness.call_controller())
    assert result.status == 'reviewed_pending_human', (result.stage, result.detail)
    assert not result.ready_for_shipping
    assert harness.calls == ['implement', 'verify', 'review']
    assert result.accounting_persisted == {('a' * 32, 'implement'), ('a' * 32, 'review')}
    journal = harness.read()
    assert journal['review']['final']['accounting']['tokens']['output_tokens'] == 3
    assert harness.claim.path.exists()
    previous_calls = list(harness.calls)
    assert asyncio.run(harness.call_controller()).status == 'failed'
    assert harness.calls == previous_calls


@pytest.mark.parametrize('failure', ['worker', 'cleanup', 'verification', 'review-worker', 'review-fail', 'review-cleanup'])
def test_failed_workers_preserve_durable_usage_and_candidate(harness, failure):
    harness.failure = failure
    result = asyncio.run(harness.call_controller())
    assert result.status == 'failed' and not result.ready_for_shipping
    journal = harness.read()
    assert journal['implement']['final']['accounting']['tokens']['input_tokens'] == 7
    if failure.startswith('review'):
        assert journal['review']['final']['accounting']['tokens']['output_tokens'] == 3
        assert git(harness.root, 'rev-parse', 'HEAD') != harness.base
    assert harness.claim.path.exists()


def test_accounting_sync_failure_prevents_host_commit_and_review(harness, monkeypatch):
    def failed(*args): raise OSError('synthetic sync failed')
    monkeypatch.setattr(NativeAttemptJournal, 'finish', failed)
    result = asyncio.run(harness.call_controller())
    assert result.status == 'failed' and result.accounting and not result.accounting_persisted
    assert git(harness.root, 'rev-parse', 'HEAD') == harness.base
    assert harness.read()['implement']['final'] is None
    assert harness.calls == ['implement']


def test_phase_transition_failure_retains_prepared_candidate_without_review(harness, monkeypatch):
    def failed(*args): raise OSError('synthetic sync failed')
    monkeypatch.setattr(NativeAttemptJournal, 'enter_reviewing', failed)
    result = asyncio.run(harness.call_controller())
    assert result.status == 'failed' and harness.calls == ['implement', 'verify']
    assert harness.read()['review']['state'] == 'prepared'
    assert git(harness.root, 'rev-parse', 'HEAD') != harness.base


@pytest.mark.parametrize('failure', ['review-exception', 'review-cancel'])
def test_no_terminal_result_retains_unknown_incomplete_review(harness, failure):
    harness.failure = failure
    if failure == 'review-cancel':
        with pytest.raises(asyncio.CancelledError): asyncio.run(harness.call_controller())
    else: assert asyncio.run(harness.call_controller()).status == 'failed'
    assert harness.read()['review']['state'] == 'started'
    assert harness.read()['review']['final'] is None
    assert harness.claim.path.exists()


def test_review_accounting_sync_failure_cannot_finish_attempt(harness, monkeypatch):
    original = NativeAttemptJournal.finish
    def failed(journal, accounting, binding):
        if accounting.phase == 'review': raise OSError('synthetic sync failed')
        return original(journal, accounting, binding)
    monkeypatch.setattr(NativeAttemptJournal, 'finish', failed)
    result = asyncio.run(harness.call_controller())
    assert result.status == 'failed' and not result.ready_for_shipping
    assert ('a' * 32, 'review') in result.accounting
    assert ('a' * 32, 'review') not in result.accounting_persisted
    assert harness.read()['review']['final'] is None
    assert git(harness.root, 'rev-parse', 'HEAD') != harness.base
    assert harness.claim.path.exists()


@pytest.mark.parametrize('field,value', [
    ('review_id', 'wrong-observed-review'),
    ('candidate_sha', 'f' * 40),
    ('source_fingerprint', 'e' * 64),
])
def test_rejected_review_provenance_is_persisted_as_observed(harness, monkeypatch, field, value):
    original = module._review_stable_chatgpt
    observed = {}
    async def mismatch(*args, **kwargs):
        reviewed = await original(*args, **kwargs)
        observed['expected'] = getattr(reviewed, field)
        return replace(reviewed, **{field: value})
    monkeypatch.setattr(module, '_review_stable_chatgpt', mismatch)
    result = asyncio.run(harness.call_controller())
    assert result.status == 'failed' and not result.ready_for_shipping
    assert ('a' * 32, 'review') in result.accounting_persisted
    receipt = harness.read()['review']['final']['outcome']
    assert receipt[field] == value
    assert receipt['expected_' + field] == observed['expected']
    assert receipt[field] != receipt['expected_' + field]
    assert receipt['status'] == 'succeeded'  # Worker success is not binding approval.
    assert harness.claim.path.exists()

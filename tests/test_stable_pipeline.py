"""Real host Git with synthetic managed implementation and isolated evidence."""
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import queue
from nightshift.workers import stable_pipeline as module, snapshot
from nightshift.workers.base import WorkerRequest, WorkerResult
from nightshift.workers.isolated_verification import ClauseResult, IsolatedVerificationResult
from nightshift.workers.snapshot import SourceFile
from nightshift.workers.stable_worker import _StableRun
from nightshift.workers.stable_worker_guard import NativeMarkerEvidence, validate_marker
from test_candidate_pipeline import repository, git


@pytest.fixture
def setup(repository, tmp_path, monkeypatch):
    root, base = repository
    monkeypatch.setattr(queue, 'CLAIM_DIR', tmp_path / 'claims')
    claim = queue.Claim('example/repo', 8, git(root, 'branch', '--show-current'), str(root), 'fixture')
    claim.write()
    recovery = tmp_path / 'recovery'
    claim._prepare_native('a' * 32, recovery)
    marker = NativeMarkerEvidence(claim.path, 'a' * 32, root)
    state = SimpleNamespace(root=root, base=base, claim=claim, marker=marker, calls=[], failure=None)
    worker = WorkerResult(status='succeeded', requested_model='model', observed_model='model',
                          thread_id='thread', turn_id='turn', usage={'outputTokens': 0, 'inputTokens': 7})
    state.worker = worker
    state.files = (SourceFile('source.txt', b'after'),)
    async def implement(request, files, **kwargs):
        state.calls.append('implement')
        validate_marker(kwargs['native_marker'], kwargs['recovery_dir'])
        assert kwargs['native_marker'] == marker
        assert git(root, 'rev-parse', 'HEAD') == base
        assert files == snapshot.from_git(root, base)
        assert kwargs['native_marker'].worktree == root
        if state.failure == 'marker-change':
            claim.native_recovery['run_id'] = 'b' * 32
            claim.write()
        if state.failure == 'claim-field':
            claim.started_at = 'concurrent update'
            claim.write()
        if state.failure == 'worker': worker.status = 'failed'
        if state.failure == 'bad-usage': worker.usage = {'invented': 42}
        return _StableRun(worker, state.files, True, True, state.failure != 'cleanup', True)
    monkeypatch.setattr(module, '_run_stable_chatgpt', implement)
    def verify(repo, sha, command, **kwargs):
        state.calls.append('verify')
        assert sha != base and git(repo, 'rev-parse', 'HEAD') == sha
        fp = snapshot.fingerprint(snapshot.from_git(repo, sha))
        evidence = IsolatedVerificationResult(sha, command, kwargs['image_id'], status='succeeded',
            source_fingerprint=fp, final_fingerprint=fp, cleanup_succeeded=True,
            clauses=[ClauseResult(('true',), 'succeeded', 0, '', 0)])
        state.evidence = evidence
        if state.failure == 'verification': evidence.status = 'failed'
        if state.failure == 'wrong-sha': evidence.candidate_sha = base
        if state.failure == 'wrong-image': evidence.image_id = 'wrong'
        if state.failure == 'wrong-command': evidence.command = 'false'
        if state.failure == 'wrong-fingerprint': evidence.source_fingerprint = 'wrong'
        if state.failure == 'verify-cleanup': evidence.cleanup_succeeded = False
        if state.failure == 'final-change': (repo / 'source.txt').write_text('concurrent mutation')
        if state.failure == 'verify-exception': raise RuntimeError('synthetic secret')
        return evidence
    monkeypatch.setattr(module.isolated_verification, 'run', verify)
    state.request = WorkerRequest('implement', Path('/workspace'), 'change source', 'model')
    state.call = lambda: module._implement_and_verify(root, base, state.request, 'true',
        credential_root=tmp_path / 'credentials', binary=Path('/synthetic/codex'),
        image_id='sha256:' + 'a' * 64, verification_image_id='sha256:' + 'b' * 64,
        docker_host='unix:///synthetic/docker.sock', recovery_dir=recovery,
        expected_identity=None, native_marker=state.marker)
    return state


def test_bound_claim_base_commit_verification_and_accounting_still_require_review(setup):
    result = asyncio.run(setup.call())
    assert result.status == 'verified_pending_review' and not result.ready_for_shipping
    assert setup.calls == ['implement', 'verify']
    assert git(setup.root, 'rev-parse', 'HEAD^') == setup.base
    assert result.candidate_sha == git(setup.root, 'rev-parse', 'HEAD')
    assert result.accounting.key == ('a' * 32, 'implement')
    assert result.accounting.tokens.output_tokens == 0
    assert result.accounting.tokens.total_tokens is None
    assert setup.claim.path.exists()
    # Returned evidence cannot be changed through trusted adapter aliases.
    setup.worker.status = 'failed'
    setup.evidence.status = 'failed'
    assert result.implementation.ok and result.verification.ok


@pytest.mark.parametrize('failure', ['worker', 'cleanup', 'bad-usage', 'marker-change', 'claim-field'])
def test_implementation_failure_blocks_host_commit_and_retains_claim(setup, failure):
    setup.failure = failure
    result = asyncio.run(setup.call())
    assert result.status == 'failed' and not result.ready_for_shipping
    assert setup.calls == ['implement']
    assert git(setup.root, 'rev-parse', 'HEAD') == setup.base
    assert (setup.root / 'source.txt').read_bytes() == b'before'
    assert setup.claim.path.exists()
    if failure != 'bad-usage': assert result.accounting.tokens.input_tokens == 7
    else: assert result.files == setup.files


@pytest.mark.parametrize('failure', ['verification', 'wrong-sha', 'wrong-image', 'wrong-command',
    'wrong-fingerprint', 'verify-cleanup', 'verify-exception', 'final-change'])
def test_verification_ambiguity_preserves_candidate_and_accounting(setup, failure):
    setup.failure = failure
    result = asyncio.run(setup.call())
    assert result.status == 'failed' and result.candidate_sha != setup.base
    assert result.candidate_sha == git(setup.root, 'rev-parse', 'HEAD')
    assert result.files == setup.files and result.accounting.tokens.input_tokens == 7
    assert setup.claim.path.exists() and not result.ready_for_shipping
    assert 'synthetic secret' not in result.detail


def test_commit_exception_after_ref_update_retains_work_without_guessing_identity(setup, monkeypatch):
    original = module.candidate.create
    def ambiguous(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('synthetic interrupted inspection')
    monkeypatch.setattr(module.candidate, 'create', ambiguous)
    result = asyncio.run(setup.call())
    assert result.commit_ambiguous and result.candidate_sha == ''
    assert git(setup.root, 'rev-parse', 'HEAD') != setup.base
    assert result.files == setup.files and result.accounting is not None
    assert setup.calls == ['implement'] and setup.claim.path.exists()


def test_claim_from_another_worktree_cannot_authorize_this_base(setup, tmp_path):
    setup.marker = replace(setup.marker, worktree=tmp_path / 'other')
    result = asyncio.run(setup.call())
    assert result.status == 'failed' and setup.calls == []
    assert git(setup.root, 'rev-parse', 'HEAD') == setup.base


def test_expired_budget_after_verification_retains_candidate_without_accepting(setup, monkeypatch):
    original_verify = module.isolated_verification.run
    original_clock = module.time.monotonic
    offset = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: original_clock() + offset[0])
    def delayed(*args, **kwargs):
        evidence = original_verify(*args, **kwargs)
        offset[0] = setup.request.budgets.max_runtime_s + 1
        return evidence
    monkeypatch.setattr(module.isolated_verification, 'run', delayed)
    result = asyncio.run(setup.call())
    assert result.status == 'failed' and result.stage == 'final'
    assert result.candidate_sha == git(setup.root, 'rev-parse', 'HEAD')
    assert result.accounting is not None and setup.claim.path.exists()


def test_claim_branch_must_match_current_symbolic_head_before_worker(setup):
    setup.claim.branch = 'unrelated-branch'
    setup.claim.write()
    result = asyncio.run(setup.call())
    assert result.status == 'failed' and setup.calls == []
    assert git(setup.root, 'rev-parse', 'HEAD') == setup.base

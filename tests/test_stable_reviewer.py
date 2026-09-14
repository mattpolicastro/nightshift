"""Stable execution ordering with real credential leases and synthetic processes."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import queue
from nightshift.workers import stable_reviewer as module
from nightshift.workers.base import ReviewerVerdict, WorkerResult
from nightshift.workers import snapshot
from nightshift.workers.candidate_pipeline import ReviewInput, FileChange
from nightshift.workers.review_context import ApprovedTask, ReviewPolicy
from nightshift.workers.isolated_verification import IsolatedVerificationResult, ClauseResult
from test_candidate_pipeline import git
from nightshift.workers.chatgpt_admission import ChatGPTIdentity
from nightshift.workers.credential_home import StableCredentialHome
from nightshift.workers.container_session import SessionError
from nightshift.workers.snapshot import SourceFile
from nightshift.workers.stable_worker_guard import NativeMarkerEvidence

REAL_STDIO = module.codex._run_stdio


@pytest.fixture
def harness(tmp_path, monkeypatch):
    root = tmp_path / 'credentials'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(queue, 'CLAIM_DIR', tmp_path / 'claims')
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    git(worktree, 'init', '-q')
    git(worktree, 'config', 'user.name', 'Fixture')
    git(worktree, 'config', 'user.email', 'fixture@example.invalid')
    (worktree / 'a.txt').write_bytes(b'initial')
    git(worktree, 'add', 'a.txt')
    git(worktree, 'commit', '-qm', 'base')
    base = git(worktree, 'rev-parse', 'HEAD')
    (worktree / 'a.txt').write_bytes(b'changed')
    git(worktree, 'add', 'a.txt')
    git(worktree, 'commit', '-qm', 'candidate')
    sha = git(worktree, 'rev-parse', 'HEAD')
    claim = queue.Claim('example/repo', 7, git(worktree, 'branch', '--show-current'),
                        str(worktree), 'fixture', phase='reviewing')
    claim.write()
    recovery = tmp_path / 'recovery'
    claim._prepare_native('a' * 32, recovery)
    claim.path.chmod(0o600)
    marker = NativeMarkerEvidence(claim.path, 'a' * 32, worktree)
    state = SimpleNamespace(events=[], failure=None, root=root, marker=marker, kwargs=None, claim=claim)
    monkeypatch.setattr(sys, 'platform', 'darwin')
    async def version(binary, home, env, deadline):
        state.events.append('version')
        assert home != root / 'codex-home'
        assert env['HOME'] == env['CODEX_HOME'] == str(home)
        if state.failure == 'version': raise ValueError('synthetic secret')
    monkeypatch.setattr(module, '_check_version', version)

    class Session:
        def __init__(self, image, files, **kwargs):
            assert kwargs["readonly_source"] is True
            self.readonly_source_confirmed = state.failure != "writable"
            self.cleanup_succeeded = False
            self.deadline = kwargs['deadline']
            self.files = files
            assert kwargs['recovery_dir'] == recovery
        def __enter__(self):
            state.events.append('session')
            return self
        def finish(self):
            state.events.append('export')
            assert 'quiesce' in state.events
            if state.failure == 'export': raise SessionError('synthetic secret')
            return [SourceFile('a.txt', b'mutated' if state.failure == 'mutation' else b'changed')]
        def __exit__(self, *args):
            state.events.append('session-cleanup')
            self.cleanup_succeeded = state.failure != 'session-cleanup'
            if not self.cleanup_succeeded: raise SessionError('synthetic secret')
    monkeypatch.setattr(module, 'Session', Session)

    class Executor:
        def __init__(self, session, home):
            self.home = home
            self.socket_path = tmp_path / 'bridge.sock'
            self._thread = self._process = None
        def __enter__(self):
            state.events.append('executor')
            stub = SimpleNamespace(home=self.home, _config=(self.home / 'config.toml').read_text())
            for name, text in module._startup(stub, self).items():
                if name == 'config.toml': continue
                path = self.home / name
                path.write_text(text)
                path.chmod(0o600)
            if state.failure == 'startup': (self.home / 'environments.toml').write_text('include_local=true')
            return self
        def __exit__(self, kind, exc, tb):
            state.events.append('cancel' if exc else 'quiesce')
            if state.failure == 'quiesce': raise SessionError('synthetic secret')
            if state.failure == 'executor-live':
                self._thread = SimpleNamespace(is_alive=lambda: True)
                raise SessionError('synthetic secret')
    monkeypatch.setattr(module, 'NativeExecutor', Executor)
    async def run(request, argv, **kwargs):
        state.events.append('provider')
        state.kwargs = kwargs
        state.prompt = request.prompt
        assert request.role == 'review' and request.transcript_path is None
        assert argv == ['/synthetic/codex', 'app-server', '--stdio', '--strict-config']
        assert kwargs['external_executor'] is True and kwargs['admission'] is not None
        assert kwargs['config_validator'] is not None
        assert set(kwargs['env']) == {'PATH', 'HOME', 'CODEX_HOME'}
        with pytest.raises(SessionError):
            with StableCredentialHome(root): pass
        if state.failure == 'provider-exception': raise RuntimeError('synthetic secret')
        if state.failure == 'cancel': raise asyncio.CancelledError()
        if state.failure == 'lease-cleanup': os.mkfifo(kwargs['provider_cwd'] / 'pipe', 0o600)
        if state.failure == 'claim-change':
            claim.started_at = 'changed'
            claim.write()
        state.events.append('provider-stopped')
        return WorkerResult(status='auth_failed' if state.failure == 'admission' else 'succeeded',
            requested_model='explicit-model', observed_model='other' if state.failure == 'model' else 'explicit-model',
            thread_id='implementation' if state.failure == 'thread' else 'thread', turn_id='turn',
            usage={'inputTokens': 7}, reviewer_verdict=None if state.failure == 'verdict' else
            ReviewerVerdict('FAIL' if state.failure == 'fail-verdict' else 'PASS', (), ()))
    monkeypatch.setattr(module.codex, '_run_stdio', run)
    async def forbidden(*args, **kwargs): pytest.fail('inherited provider runner reached')
    monkeypatch.setattr(module.ChatGPTProvider, 'run', forbidden)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'changed')
    files = [SourceFile('a.txt', b'changed')]
    fp = snapshot.fingerprint(files)
    evidence = IsolatedVerificationResult(sha, 'true', 'sha256:' + 'b' * 64,
        status='succeeded', source_fingerprint=fp, final_fingerprint=fp, cleanup_succeeded=True,
        clauses=[ClauseResult(('true',), 'succeeded', 0, '', 0)])
    state.request = ReviewInput('review-id', base, sha, source,
        (FileChange('a.txt', SourceFile('a.txt', b'initial'), files[0]),), evidence,
        ApprovedTask('task', 'Repair', 'Approved fixture change'), ReviewPolicy('Check correctness'))
    state.call = lambda: module._review_stable_chatgpt(state.request, files,
        implementation_thread_id='implementation', model='explicit-model',
        verify_command='true', verification_image_id='sha256:' + 'b' * 64,
        credential_root=root, binary=Path('/synthetic/codex'),
        image_id='sha256:' + 'a' * 64, docker_host='unix:///synthetic/docker.sock',
        recovery_dir=recovery, expected_identity=ChatGPTIdentity('synthetic@example.invalid', 'synthetic-account'),
        native_marker=marker)
    return state


def test_stable_review_binds_exact_candidate_and_cleans_before_pass(harness):
    result = asyncio.run(harness.call())
    assert result.ok and result.candidate_sha == harness.request.candidate_sha
    assert result.review_id == 'review-id' and result.result.usage == {'inputTokens': 7}
    assert harness.events[-3:] == ['quiesce', 'export', 'session-cleanup']
    assert not (harness.root / 'attempt.json').exists()
    assert harness.marker.claim_path.exists()
    payload = json.loads(harness.prompt.split('\n', 1)[1])
    assert payload['candidate_sha'] == harness.request.candidate_sha
    assert payload['diff'][0]['after']['content_base64'] == 'Y2hhbmdlZA=='


@pytest.mark.parametrize('failure', ['writable', 'mutation', 'model', 'thread', 'verdict', 'fail-verdict',
    'startup', 'admission', 'provider-exception', 'quiesce', 'export', 'session-cleanup',
    'lease-cleanup', 'executor-live', 'claim-change'])
def test_failures_never_qualify_review(harness, failure):
    harness.failure = failure
    result = asyncio.run(harness.call())
    assert not result.ok
    assert 'synthetic secret' not in result.detail
    if failure == 'writable': assert 'provider' not in harness.events
    if failure in {'provider-exception', 'session-cleanup', 'lease-cleanup', 'executor-live'}:
        assert (harness.root / 'attempt.json').exists()


@pytest.mark.parametrize('field', ['phase', 'branch', 'diff', 'snapshot', 'sha', 'command', 'image', 'cleanup'])
def test_forged_review_binding_fails_before_credential_or_process_mutation(harness, field):
    from dataclasses import replace
    if field in {'phase', 'branch'}:
        setattr(harness.claim, field, 'implementing' if field == 'phase' else 'other')
        harness.claim.write()
    elif field == 'diff': harness.request = replace(harness.request, diff=())
    elif field == 'snapshot': (harness.request.source_path / 'a.txt').write_text('wrong')
    elif field == 'sha': harness.request = replace(harness.request, candidate_sha=harness.request.base_sha)
    elif field == 'command': harness.request.verification.command = 'false'
    elif field == 'image': harness.request.verification.image_id = 'wrong'
    elif field == 'cleanup': harness.request.verification.cleanup_succeeded = 1
    assert not asyncio.run(harness.call()).ok
    assert harness.events == [] and not list(harness.root.iterdir())


def test_real_json_rpc_stable_review_uses_structured_output_and_bound_admission(harness, tmp_path, monkeypatch):
    from test_chatgpt_admission import protocol
    binary = tmp_path / 'synthetic-codex'
    script = protocol().replace('scenario = sys.argv[1]', 'scenario = "external"')
    script = script.replace('if scenario == "review":', 'if scenario == "external":')
    script = script.replace('SYNTHETIC_EMAIL_PRIVATE@example.invalid', 'synthetic@example.invalid')
    script = script.replace('SYNTHETIC_ACCOUNT_PRIVATE', 'synthetic-account')
    binary.write_text('#!' + sys.executable + '\n' + script)
    binary.chmod(0o700)
    observed = []
    async def actual(request, argv, **kwargs):
        assert request.role == 'review'
        result = await REAL_STDIO(request, [str(binary)], **kwargs)
        observed.extend(json.loads(line) for line in
            (kwargs['provider_cwd'] / 'admission-methods.jsonl').read_text().splitlines())
        with pytest.raises(SessionError):
            with StableCredentialHome(harness.root): pass
        return result
    monkeypatch.setattr(module.codex, '_run_stdio', actual)
    monkeypatch.setattr(module.ChatGPTProvider, '_validate_configuration', lambda *args: None)
    result = asyncio.run(harness.call())
    assert result.ok, result.result.diagnostics
    assert observed.index('account/read') < observed.index('thread/start')
    assert observed.count('thread/start') == observed.count('turn/start') == 1


def test_cancellation_preserves_unconfirmed_provider_recovery(harness):
    harness.failure = 'cancel'
    with pytest.raises(asyncio.CancelledError): asyncio.run(harness.call())
    assert harness.events[-2:] == ['cancel', 'session-cleanup']
    assert (harness.root / 'attempt.json').exists()


def test_expiry_during_final_inspection_cannot_qualify_review(harness, monkeypatch):
    original = module._review_snapshot_unchanged
    clock = module.time.monotonic
    offset = [0]
    calls = []
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock() + offset[0])
    def delayed(*args):
        original(*args)
        calls.append(1)
        if len(calls) == 2: offset[0] = 2000
    monkeypatch.setattr(module, '_review_snapshot_unchanged', delayed)
    result = asyncio.run(harness.call())
    assert not result.ok and not result.fresh_context_confirmed
    assert result.result.usage == {'inputTokens': 7}


@pytest.mark.parametrize('kind', ['namespace', 'boolean-exit', 'float-exit', 'namespace-clause',
                                  'tuple-clauses', 'failed-status'])
def test_forged_or_malformed_verification_never_launches(harness, kind):
    from dataclasses import replace
    evidence = harness.request.verification
    if kind == 'namespace':
        evidence = SimpleNamespace(**{**evidence.__dict__, 'status': 'failed', 'clauses': [], 'ok': True})
    elif kind == 'boolean-exit':
        evidence.clauses[0] = replace(evidence.clauses[0], exit_code=False)
    elif kind == 'float-exit':
        evidence.clauses[0] = replace(evidence.clauses[0], exit_code=0.0)
    elif kind == 'namespace-clause':
        evidence.clauses[0] = SimpleNamespace(**evidence.clauses[0].__dict__, ok=True)
    elif kind == 'tuple-clauses': evidence.clauses = tuple(evidence.clauses)
    else: evidence.status = 'failed'
    harness.request = replace(harness.request, verification=evidence)
    assert not asyncio.run(harness.call()).ok
    assert harness.events == [] and not list(harness.root.iterdir())


def test_invalid_request_rejected_without_copy_or_field_hooks(harness):
    class Forged:
        def __deepcopy__(self, memo): pytest.fail('caller deepcopy executed')
        def __getattr__(self, name): pytest.fail('caller field accessed')
    harness.request = Forged()
    assert not asyncio.run(harness.call()).ok
    assert harness.events == []


@pytest.mark.parametrize('value', [False, 0, ''])
def test_falsey_nonbudget_rejected_before_any_operation(harness, monkeypatch, value):
    original = module._review_stable_chatgpt
    async def injected(*args, **kwargs):
        return await original(*args, **kwargs, budgets=value)
    monkeypatch.setattr(module, '_review_stable_chatgpt', injected)
    assert not asyncio.run(harness.call()).ok
    assert harness.events == [] and not list(harness.root.iterdir())

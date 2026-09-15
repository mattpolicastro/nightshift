"""Stable execution ordering with real credential leases and synthetic processes."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import queue
from nightshift.workers import stable_worker as module
from nightshift.workers.base import WorkerRequest, WorkerResult
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
    claim = queue.Claim('example/repo', 7, 'candidate/7', str(worktree), 'fixture')
    claim.write()
    recovery = tmp_path / 'recovery'
    claim._prepare_native('a' * 32, recovery)
    claim.path.chmod(0o600)
    marker = NativeMarkerEvidence(claim.path, 'a' * 32, worktree)
    state = SimpleNamespace(events=[], failure=None, root=root, marker=marker, kwargs=None)
    monkeypatch.setattr(sys, 'platform', 'darwin')
    async def version(binary, home, env, deadline):
        state.events.append('version')
        assert home != root / 'codex-home'
        assert env['HOME'] == env['CODEX_HOME'] == str(home)
        if state.failure == 'version': raise ValueError('synthetic secret')
    monkeypatch.setattr(module, '_check_version', version)

    class Session:
        def __init__(self, image, files, **kwargs):
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
            return [SourceFile('a.txt', b'changed')]
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
        assert argv == ['/synthetic/codex', 'app-server', '--stdio', '--strict-config']
        assert kwargs['external_executor'] is True and kwargs['admission'] is not None
        assert kwargs['config_validator'] is not None
        assert set(kwargs['env']) == {'PATH', 'HOME', 'CODEX_HOME'}
        with pytest.raises(SessionError):
            with StableCredentialHome(root): pass
        if state.failure == 'provider-exception': raise RuntimeError('synthetic secret')
        if state.failure == 'cancel': raise asyncio.CancelledError()
        if state.failure == 'lease-cleanup': os.mkfifo(kwargs['provider_cwd'] / 'pipe', 0o600)
        state.events.append('provider-stopped')
        return WorkerResult(status='auth_failed' if state.failure == 'admission' else 'succeeded',
            requested_model='explicit-model', observed_model='explicit-model', thread_id='thread', turn_id='turn')
    monkeypatch.setattr(module.codex, '_run_stdio', run)
    async def forbidden(*args, **kwargs): pytest.fail('inherited provider runner reached')
    monkeypatch.setattr(module.ChatGPTProvider, 'run', forbidden)
    state.request = WorkerRequest('implement', Path('/workspace'), 'synthetic task', 'explicit-model')
    state.call = lambda: module._run_stable_chatgpt(
        state.request,
        [SourceFile('a.txt', b'initial')], credential_root=root, binary=Path('/synthetic/codex'),
        image_id='sha256:' + 'a' * 64, docker_host='unix:///synthetic/docker.sock',
        recovery_dir=recovery, expected_identity=ChatGPTIdentity('synthetic@example.invalid', 'synthetic-account'),
        native_marker=marker)
    return state


def test_success_requires_ordered_shutdown_and_preserves_controller_marker(harness):
    result = asyncio.run(harness.call())
    assert result.ok and result.files == (SourceFile('a.txt', b'changed'),)
    assert harness.events == ['version', 'session', 'executor', 'provider', 'provider-stopped',
                              'quiesce', 'export', 'session-cleanup']
    assert not (harness.root / 'attempt.json').exists()
    assert not list((harness.root / 'codex-home').iterdir())
    assert harness.marker.claim_path.exists()


@pytest.mark.parametrize('failure', ['version', 'startup', 'admission', 'provider-exception',
                                    'quiesce', 'export', 'session-cleanup', 'lease-cleanup', 'executor-live'])
def test_failure_never_releases_candidate(harness, failure):
    harness.failure = failure
    result = asyncio.run(harness.call())
    assert not result.ok and result.files is None
    assert 'synthetic secret' not in repr(result)
    if failure in {'provider-exception', 'session-cleanup', 'lease-cleanup', 'executor-live'}:
        assert (harness.root / 'attempt.json').exists()
    if failure == 'admission':
        assert 'cancel' in harness.events and 'export' not in harness.events
        assert result.provider_stopped and result.lease_cleanup
    if failure == 'startup': assert 'provider' not in harness.events


def test_missing_durable_claim_prevents_any_namespace_or_process_mutation(harness):
    harness.marker.claim_path.unlink()
    result = asyncio.run(harness.call())
    assert not result.ok and harness.events == []
    assert not list(harness.root.iterdir())


def test_cancellation_cleans_executor_but_retains_uncertain_provider_recovery(harness):
    harness.failure = 'cancel'
    with pytest.raises(asyncio.CancelledError): asyncio.run(harness.call())
    assert harness.events[-2:] == ['cancel', 'session-cleanup']
    assert (harness.root / 'attempt.json').exists()


def test_real_synthetic_stdio_admission_and_turn_hold_stable_lease(harness, tmp_path, monkeypatch):
    from test_chatgpt_admission import protocol
    binary = tmp_path / 'synthetic-codex'
    # This executable speaks synthetic JSON-RPC and never contacts a provider.
    binary.write_text('#!' + sys.executable + '\n' + protocol()
        .replace('scenario = sys.argv[1]', 'scenario = "external"')
        .replace('SYNTHETIC_EMAIL_PRIVATE@example.invalid', 'synthetic@example.invalid')
        .replace('SYNTHETIC_ACCOUNT_PRIVATE', 'synthetic-account'))
    binary.chmod(0o700)
    observed = []
    async def actual(request, argv, **kwargs):
        with pytest.raises(SessionError):
            with StableCredentialHome(harness.root): pass
        result = await REAL_STDIO(request, [str(binary)], **kwargs)
        observed.extend(json.loads(line) for line in
            (kwargs['provider_cwd'] / 'admission-methods.jsonl').read_text().splitlines())
        # Still held after the provider has been reaped, before executor quiesce.
        with pytest.raises(SessionError):
            with StableCredentialHome(harness.root): pass
        return result
    monkeypatch.setattr(module.codex, '_run_stdio', actual)
    # Effective policy validation has dedicated protocol tests; this fixture's
    # config response is deliberately synthetic, while account/model checks run.
    monkeypatch.setattr(module.ChatGPTProvider, '_validate_configuration', lambda *args: None)
    result = asyncio.run(harness.call())
    assert result.ok, result.worker.diagnostics
    assert observed.index('account/read') < observed.index('thread/start')
    assert observed.index('account/rateLimits/read') < observed.index('thread/start')
    assert observed.index('model/list') < observed.index('thread/start')
    assert observed.count('thread/start') == observed.count('turn/start') == 1
    assert result.provider_stopped and result.executor_stopped
    assert not (harness.root / 'attempt.json').exists()


@pytest.mark.parametrize('field', ['transcript_path', 'normalized_transcript_path'])
def test_unowned_transcript_paths_rejected_before_mutation(harness, tmp_path, field):
    from dataclasses import replace
    harness.request = replace(harness.request, **{field: tmp_path / 'unowned.jsonl'})
    assert not asyncio.run(harness.call()).ok
    assert harness.events == [] and not list(harness.root.iterdir())
    assert not (tmp_path / 'unowned.jsonl').exists()

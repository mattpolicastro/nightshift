"""Offline live-review orchestration: identity, immutable policy and cleanup gates."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift.workers import reviewer, snapshot
from nightshift.workers.base import ReviewerVerdict, WorkerResult
from nightshift.workers.isolated_verification import IsolatedVerificationResult, ClauseResult


def fixture_input():
    files = [snapshot.SourceFile('a', b'candidate')]
    fingerprint = snapshot.fingerprint(files)
    evidence = IsolatedVerificationResult('a' * 40, 'true', 'sha256:' + 'b' * 64,
        status='succeeded', source_fingerprint=fingerprint, final_fingerprint=fingerprint,
        clauses=[ClauseResult(('true',), 'succeeded', 0, 'private raw diagnostic', 0)], cleanup_succeeded=True)
    request = SimpleNamespace(review_id='fresh-review', base_sha='c' * 40,
        candidate_sha='a' * 40, readonly_mount_required=True, diff=(), verification=evidence)
    return request, files


def mocks(monkeypatch, fault=None):
    state = {}
    class Session:
        def __init__(self, image, files, **kwargs):
            state['session'] = self
            assert kwargs['readonly_source'] is True
            self.files = files
            self.readonly_source_confirmed = fault != 'writable'
            self.cleanup_succeeded = False
        def __enter__(self): return self
        def __exit__(self, *a):
            self.cleanup_succeeded = fault != 'cleanup'
            if fault == 'cleanup': raise RuntimeError('cleanup unconfirmed')
        def finish(self):
            return [snapshot.SourceFile('a', b'modified')] if fault == 'mutation' else self.files
    class Executor:
        def __init__(self, session, home):
            state['home'] = home
            assert sorted(p.name for p in home.iterdir()) == ['config.toml']
            self.quiesced = False
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def quiesce(self):
            if fault == 'quiesce': raise RuntimeError('unfinished executor')
            self.quiesced = True
    async def run(request, argv, **kwargs):
        state['request'] = request
        state['env'] = kwargs['env']
        assert request.role == 'review'
        assert kwargs['provider_cwd'] == state['home']
        assert 'private raw diagnostic' not in request.prompt
        if fault == 'cancel': raise asyncio.CancelledError()
        return WorkerResult('succeeded', thread_id='new-thread', reviewer_verdict=(
            None if fault == 'malformed' else ReviewerVerdict('PASS', (), ())))
    monkeypatch.setattr(reviewer, 'Session', Session)
    monkeypatch.setattr(reviewer, 'NativeExecutor', Executor)
    monkeypatch.setattr(reviewer.codex, '_run_stdio', run)
    return state


def invoke(tmp_path, request, files, **kwargs):
    return asyncio.run(reviewer._run_isolated(request, files, image_id='sha256:'+'b'*64,
        docker_host='unix:///tmp/fixture.sock', recovery_dir=tmp_path/'recovery',
        provider_argv=['/fixture/codex', 'app-server', '--stdio'], provider_config='fixture=true',
        provider_env=kwargs.get('provider_env', {'PROVIDER_TOKEN': 'host-only'}), model='fixture'))


def test_fresh_home_and_bound_success(tmp_path, monkeypatch):
    monkeypatch.setenv('IMPLEMENTATION_SENTINEL', 'private implementation')
    state = mocks(monkeypatch)
    request, files = fixture_input()
    result = invoke(tmp_path, request, files)
    assert result.ok
    assert result.review_id == request.review_id
    assert result.candidate_sha == request.candidate_sha
    assert result.source_fingerprint == snapshot.fingerprint(files)
    assert not state['home'].exists()
    assert 'IMPLEMENTATION_SENTINEL' not in state['env']
    assert 'host-only' not in state['request'].prompt
    first_home = state['home']
    assert invoke(tmp_path, request, files).ok
    assert first_home != state['home']


@pytest.mark.parametrize('fault', ['writable', 'cleanup', 'mutation', 'quiesce', 'cancel', 'malformed'])
def test_failure_cannot_qualify_pass(tmp_path, monkeypatch, fault):
    state = mocks(monkeypatch, fault)
    request, files = fixture_input()
    result = invoke(tmp_path, request, files)
    assert not result.ok
    if fault == 'writable': assert 'request' not in state
    if fault == 'cleanup': assert not result.cleanup_succeeded
    if fault == 'cancel': assert result.result.status == 'interrupted'


@pytest.mark.parametrize('field,value', [('candidate_sha', 'd'*40),
    ('source_fingerprint', 'wrong'), ('final_fingerprint', 'wrong'), ('cleanup_succeeded', False)])
def test_wrong_evidence_blocks_container_and_provider(tmp_path, monkeypatch, field, value):
    state = mocks(monkeypatch)
    request, files = fixture_input()
    setattr(request.verification, field, value)
    assert not invoke(tmp_path, request, files).ok
    assert 'session' not in state
    assert 'request' not in state


def test_owned_home_override_rejected_before_container(tmp_path, monkeypatch):
    state = mocks(monkeypatch)
    request, files = fixture_input()
    assert not invoke(tmp_path, request, files, provider_env={'CODEX_HOME': '/implementation'}).ok
    assert 'session' not in state

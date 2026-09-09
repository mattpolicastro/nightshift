"""Stable lease composition, real lock exclusion and synthetic stdio shutdown."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from nightshift.workers import stable_metadata as module
from nightshift.workers.chatgpt_admission import ChatGPTIdentity
from nightshift.workers.container_session import SessionError
from nightshift.workers.credential_home import StableCredentialHome
from nightshift.workers.managed_qualification import _QualificationResult
from nightshift.workers.native_executor import NativeExecutor
from test_chatgpt_admission import protocol


def setup(tmp_path, monkeypatch):
    root = (tmp_path / 'credential-lease').resolve()
    root.mkdir(mode=0o700)
    monkeypatch.setattr(sys, 'platform', 'darwin')
    async def version(*a, **k):
        return None
    monkeypatch.setattr(module, '_check_version', version)
    async def forbidden(*a, **k):
        pytest.fail('metadata composition reached inherited model runner')
    monkeypatch.setattr(module.ChatGPTProvider, 'run', forbidden)
    monkeypatch.setattr(NativeExecutor, '__enter__', lambda *a: pytest.fail('metadata started tool executor'))
    return root


def identity():
    return ChatGPTIdentity('SYNTHETIC_EMAIL_PRIVATE@example.invalid', 'SYNTHETIC_ACCOUNT_PRIVATE')


def call(root, binary=Path('/synthetic/codex')):
    return module._qualify_stable_home(root, binary, 'explicit-model', expected_identity=identity())


def assert_empty(root):
    assert not (root / 'attempt.json').exists()
    assert (root / 'codex-home').is_dir()
    assert not list((root / 'codex-home').iterdir())


def test_exclusive_lock_is_held_until_metadata_provider_shutdown(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    async def exercise():
        started, finish = asyncio.Event(), asyncio.Event()
        state = []
        async def qualify(*args, **kwargs):
            state.append('provider-started')
            assert (root / 'attempt.json').exists()
            with pytest.raises(SessionError):
                with StableCredentialHome(root):
                    pytest.fail('concurrent lease acquired')
            started.set()
            await finish.wait()
            with pytest.raises(SessionError):
                with StableCredentialHome(root):
                    pytest.fail('lock released before shutdown')
            state.append('provider-stopped')
            return _QualificationResult('passed', 'explicit-model', provider_stopped=True)
        monkeypatch.setattr(module, '_qualify_managed_account', qualify)
        first = asyncio.create_task(call(root))
        await started.wait()
        second = await call(root)
        assert not second.passed
        assert state == ['provider-started']
        finish.set()
        assert (await first).passed
        assert state == ['provider-started', 'provider-stopped']
    asyncio.run(exercise())
    assert_empty(root)


def test_stable_namespace_inode_survives_clean_attempts(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    homes = []
    async def qualify(*args, **kwargs):
        homes.append((kwargs['provider_cwd'], kwargs['provider_cwd'].stat().st_ino))
        assert kwargs['env']['CODEX_HOME'] == str(kwargs['provider_cwd'])
        assert kwargs['external_executor'] is True
        assert (kwargs['provider_cwd'] / 'environments.toml').read_text() == module._REMOTE_ONLY
        return _QualificationResult('passed', 'explicit-model', provider_stopped=True)
    monkeypatch.setattr(module, '_qualify_managed_account', qualify)
    assert asyncio.run(call(root)).passed
    assert_empty(root)
    assert asyncio.run(call(root)).passed
    assert homes[0] == homes[1]
    assert_empty(root)


def test_uncertain_provider_shutdown_retains_recovery_state(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    async def qualify(*args, **kwargs):
        return _QualificationResult('protocol_error', 'explicit-model', provider_stopped=False)
    monkeypatch.setattr(module, '_qualify_managed_account', qualify)
    result = asyncio.run(call(root))
    assert not result.passed and not result.provider_stopped
    assert (root / 'attempt.json').exists()
    assert (root / 'codex-home' / 'config.toml').exists()
    assert not asyncio.run(call(root)).passed


def test_clean_metadata_rejection_can_release_lease(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    async def qualify(*args, **kwargs):
        return _QualificationResult('protocol_error', 'explicit-model', provider_stopped=True)
    monkeypatch.setattr(module, '_qualify_managed_account', qualify)
    assert not asyncio.run(call(root)).passed
    assert_empty(root)


def test_actual_synthetic_metadata_transport_never_starts_a_thread(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    binary = tmp_path / 'synthetic-codex'
    binary.write_text('#!' + sys.executable + '\n' + protocol().replace('scenario = sys.argv[1]', 'scenario = "external"'))
    binary.chmod(0o700)
    # Config validation itself has independent effective-layer tests; this
    # fixture exercises real provider lifetime inside the actual filesystem lease.
    monkeypatch.setattr(module.ChatGPTProvider, '_validate_configuration', lambda *a: None)
    original = module._qualify_managed_account
    observed = []
    async def record(*args, **kwargs):
        result = await original(*args, **kwargs)
        observed.extend(json.loads(line) for line in
            (kwargs['provider_cwd'] / 'admission-methods.jsonl').read_text().splitlines())
        with pytest.raises(SessionError):
            with StableCredentialHome(root):
                pytest.fail('lease released before provider returned')
        return result
    monkeypatch.setattr(module, '_qualify_managed_account', record)
    result = asyncio.run(call(root, binary))
    assert result.passed and result.provider_stopped and not result.execution_enabled
    assert 'model/list' in observed
    assert 'thread/start' not in observed and 'turn/start' not in observed
    assert_empty(root)


def test_lease_cleanup_failure_cannot_release_a_pass(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    async def qualify(*args, **kwargs):
        os.mkfifo(kwargs['provider_cwd'] / 'unexpected-pipe', 0o600)
        return _QualificationResult('passed', 'explicit-model', provider_stopped=True)
    monkeypatch.setattr(module, '_qualify_managed_account', qualify)
    result = asyncio.run(call(root))
    assert not result.passed
    assert result.provider_stopped
    assert (root / 'attempt.json').exists()

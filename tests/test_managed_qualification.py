"""Read-only managed metadata qualification through synthetic stdio only."""
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from nightshift.workers import codex
from nightshift.workers.base import WorkerBudgets
from nightshift.workers.chatgpt_admission import ChatGPTAdmission, ChatGPTIdentity
from nightshift.workers.managed_qualification import _qualify_managed_account
from test_chatgpt_admission import protocol
from test_codex_worker import request


def identity():
    return ChatGPTIdentity('SYNTHETIC_EMAIL_PRIVATE@example.invalid', 'SYNTHETIC_ACCOUNT_PRIVATE')


def invoke(tmp_path, *, inject_at='', expected_identity=None, config_validator=None,
           model='explicit-model', silent=False, exit_bad=False):
    script = protocol().replace('scenario = sys.argv[1]', '''scenario = sys.argv[1]
open('provider.pid','w').write(str(os.getpid()))
sys.stderr.write('SYNTHETIC_STDERR_PRIVATE')
''')
    if silent:
        script = script.replace('    elif method == "account/read":', '    elif method == "account/read":\n        time.sleep(30)')
    if exit_bad:
        script = script.replace('    elif method == "thread/start":', '        os._exit(17)\n    elif method == "thread/start":')
    fake = tmp_path / 'qualification-fake.py'
    fake.write_text(script)
    return asyncio.run(_qualify_managed_account(model, [sys.executable, str(fake), 'external'],
        env={'INJECT_AT': inject_at}, expected_identity=expected_identity or identity(),
        external_executor=True, provider_cwd=tmp_path,
        config_validator=config_validator or (lambda *args: None),
        budgets=WorkerBudgets(max_runtime_s=0.15 if silent else 3, interrupt_grace_s=0.01)))


def methods(tmp_path):
    return [json.loads(line) for line in (tmp_path / 'admission-methods.jsonl').read_text().splitlines()]


def assert_reaped(tmp_path):
    pid = int((tmp_path / 'provider.pid').read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_pass_is_metadata_only_without_threads_output_or_journals(tmp_path, capsys):
    result = invoke(tmp_path)
    assert result.passed
    assert result.execution_enabled is False
    assert result.provider_stopped is True
    assert methods(tmp_path) == ['initialize', 'initialized', 'config/read', 'configRequirements/read',
                                'account/read', 'account/rateLimits/read', 'model/list']
    assert set(asdict(result)) == {'status', 'requested_model', 'duration_s', 'provider_stopped'}
    assert 'PRIVATE' not in repr(result)
    assert capsys.readouterr() == ('', '')
    assert not (tmp_path / 'native.jsonl').exists()
    assert_reaped(tmp_path)


@pytest.mark.parametrize('at', ['config/read', 'configRequirements/read', 'account/read',
                                 'account/rateLimits/read', 'model/list'])
def test_account_races_cannot_create_thread_or_turn(tmp_path, at):
    result = invoke(tmp_path, inject_at=at)
    assert not result.passed
    assert 'thread/start' not in methods(tmp_path)
    assert 'turn/start' not in methods(tmp_path)
    assert_reaped(tmp_path)


@pytest.mark.parametrize('wrong', [ChatGPTIdentity('other@example.invalid', 'SYNTHETIC_ACCOUNT_PRIVATE'),
                                  ChatGPTIdentity('SYNTHETIC_EMAIL_PRIVATE@example.invalid', 'other-workspace')])
def test_identity_mismatch_rejected_without_model_execution(tmp_path, wrong):
    assert not invoke(tmp_path, expected_identity=wrong).passed
    assert 'model/list' not in methods(tmp_path)
    assert 'thread/start' not in methods(tmp_path)


@pytest.mark.parametrize('error', [ValueError, RuntimeError])
def test_config_rejection_cannot_read_account(tmp_path, error):
    def reject(*args):
        raise error('SYNTHETIC_SENSITIVE_DETAIL')
    result = invoke(tmp_path, config_validator=reject)
    assert not result.passed
    assert 'account/read' not in methods(tmp_path)
    assert 'SENSITIVE' not in repr(result)


def test_missing_model_cannot_create_thread(tmp_path):
    assert not invoke(tmp_path, model='not-listed').passed
    assert 'thread/start' not in methods(tmp_path)


def test_silent_read_times_out_and_reaps_provider(tmp_path):
    started = time.monotonic()
    result = invoke(tmp_path, silent=True)
    assert result.status == 'budget_exhausted'
    assert time.monotonic() - started < 2
    assert 'thread/start' not in methods(tmp_path)
    assert_reaped(tmp_path)


def test_unsuccessful_exit_after_metadata_is_not_a_pass(tmp_path):
    assert not invoke(tmp_path, exit_bad=True).passed
    assert 'thread/start' not in methods(tmp_path)
    assert_reaped(tmp_path)


@pytest.mark.parametrize('change', [{'expected_identity': None}, {'external_executor': False},
    {'external_executor': 1}, {'provider_cwd': Path('relative')}, {'config_validator': None}])
def test_missing_admission_guards_never_spawn(tmp_path, monkeypatch, change):
    async def forbidden(*a, **k):
        pytest.fail('invalid metadata qualification spawned a provider')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    kwargs = dict(env={}, expected_identity=identity(), external_executor=True,
                  provider_cwd=tmp_path, config_validator=lambda *a: None)
    kwargs.update(change)
    result = asyncio.run(_qualify_managed_account('model', ['unused'], **kwargs))
    assert result.status == 'protocol_error'


@pytest.mark.parametrize('missing', ['admission', 'validator', 'external', 'journal'])
def test_low_level_preflight_guards_cannot_be_bypassed(tmp_path, monkeypatch, missing):
    async def forbidden(*a, **k):
        pytest.fail('unsafe low-level preflight spawned a provider')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    req = request(tmp_path)
    if missing == 'journal':
        req = replace(req, transcript_path=tmp_path / 'forbidden.jsonl')
    result = asyncio.run(codex._run_stdio(req, ['unused'], env={}, preflight_only=True,
        external_executor=missing != 'external', provider_cwd=tmp_path,
        config_validator=None if missing == 'validator' else lambda *a: None,
        admission=None if missing == 'admission' else ChatGPTAdmission(identity())))
    assert not result.ok
    assert not (tmp_path / 'forbidden.jsonl').exists()

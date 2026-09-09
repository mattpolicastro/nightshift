"""Synthetic account/usage/catalog admission; never contact an account or model."""
import asyncio
import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from nightshift.workers.chatgpt_admission import ChatGPTAdmission
from nightshift.workers.codex import _run_stdio
from test_codex_worker import FAKE, request


def account():
    return {'requiresOpenaiAuth': True, 'account': {'type': 'chatgpt', 'email': None, 'planType': 'pro'}}


def limits():
    return {'rateLimits': {'limitId': 'codex', 'planType': 'pro',
        'primary': {'usedPercent': 25, 'windowDurationMins': 300, 'resetsAt': 123456},
        'secondary': {'usedPercent': 50}, 'credits': {'hasCredits': False, 'unlimited': False}}}


def test_included_usage_or_existing_managed_credits():
    gate = ChatGPTAdmission()
    gate.account(account())
    gate.rate_limits(limits())
    assert gate.usage_available
    for credits in ({'hasCredits': True, 'unlimited': False, 'balance': '2.5'},
                    {'hasCredits': False, 'unlimited': True}):
        data = limits()
        data['rateLimits']['primary']['usedPercent'] = 100
        data['rateLimits']['credits'] = credits
        gate.rate_limits(data)
        assert gate.usage_available


@pytest.mark.parametrize('change', [
    lambda data: data.update(account={'type': 'apiKey'}),
    lambda data: data.update(account=None),
    lambda data: data.update(requiresOpenaiAuth=False),
    lambda data: data.update(requiresOpenaiAuth=1),
    lambda data: data['account'].update(planType='unknown'),
    lambda data: data['account'].update(type='chatgptAuthTokens'),
])
def test_nonmanaged_or_unknown_account_rejected(change):
    data = account()
    change(data)
    with pytest.raises(ValueError):
        ChatGPTAdmission().account(data)


@pytest.mark.parametrize('change', [
    lambda data: data['primary'].update(usedPercent=100),
    lambda data: data['primary'].update(usedPercent=True),
    lambda data: data['primary'].update(usedPercent=-1),
    lambda data: data['primary'].update(usedPercent=101),
    lambda data: data.update(primary=None, secondary=None),
    lambda data: data.update(spendControlReached=True),
    lambda data: data.update(spendControlReached=0),
    lambda data: data.update(rateLimitReachedType='workspace_member_usage_limit_reached'),
    lambda data: data.update(rateLimitReachedType='unknown'),
    lambda data: data.update(planType='plus'),
    lambda data: data.update(limitId='unattributed'),
    lambda data: data.update(credits={'hasCredits': 1, 'unlimited': False}),
    lambda data: data.update(credits={'hasCredits': True, 'unlimited': False, 'balance': 'NaN'}),
    lambda data: data.update(credits={'hasCredits': True, 'unlimited': False, 'balance': '0'}),
])
def test_exhausted_or_malformed_usage_rejected(change):
    gate = ChatGPTAdmission()
    gate.account(account())
    data = limits()
    change(data['rateLimits'])
    with pytest.raises(ValueError):
        gate.rate_limits(data)


def test_reset_credits_are_not_spendable_allowance():
    gate = ChatGPTAdmission()
    gate.account(account())
    data = limits()
    data['rateLimits']['primary']['usedPercent'] = 100
    data['rateLimitResetCredits'] = {'availableCount': 99}
    with pytest.raises(ValueError):
        gate.rate_limits(data)


def protocol():
    script = FAKE.replace('    method = msg.get("method")', '''    method = msg.get("method")
    with open("admission-methods.jsonl", "a") as f: f.write(json.dumps(method)+"\\n")
    if method == os.environ.get("INJECT_AT"):
        if os.environ.get("INJECT_KIND") == "rate":
            send({"method":"account/rateLimits/updated","params":{"rateLimits":{"limitId":"codex","primary":{"usedPercent":100}}}})
        else:
            send({"method":"account/updated","params":{"authMode":"apikey","planType":"pro","secret":"SYNTHETIC_AUTH_PRIVATE"}})
''')
    return script.replace('    elif method == "thread/start":', '''    elif method == "config/read":
        send(dict(id=msg["id"],result={"config":{"marker":"SYNTHETIC_CONFIG_PRIVATE"},"layers":[],"origins":{}}))
    elif method == "configRequirements/read":
        send(dict(id=msg["id"],result={"requirements":None}))
    elif method == "account/read":
        assert msg["params"] == {"refreshToken":False}
        send(dict(id=msg["id"],result={"requiresOpenaiAuth":True,"account":{"type":"chatgpt","planType":"pro","email":"SYNTHETIC_EMAIL_PRIVATE@example.invalid"}}))
    elif method == "account/rateLimits/read":
        send(dict(id=msg["id"],result={"accountId":"SYNTHETIC_ACCOUNT_PRIVATE","rateLimits":{"limitId":"codex","planType":"pro","primary":{"usedPercent":25}}}))
    elif method == "model/list":
        assert msg["params"] == {"limit":100,"includeHidden":False}
        send(dict(id=msg["id"],result={"data":[{"id":"explicit-model","model":"explicit-model","hidden":False,"description":"SYNTHETIC_MODEL_PRIVATE","displayName":"Fixture","isDefault":True,"defaultReasoningEffort":"medium","supportedReasoningEfforts":[]}],"nextCursor":None}))
    elif method == "thread/start":''')


def invoke(tmp_path, inject_at='', inject_kind='account'):
    fake = tmp_path / 'admission-fake.py'
    fake.write_text(protocol())
    journal = tmp_path / 'native.jsonl'
    req = replace(request(tmp_path), cwd=Path('/workspace'), transcript_path=journal)
    result = asyncio.run(_run_stdio(req, [sys.executable, str(fake), 'external'],
        env={'INJECT_AT': inject_at, 'INJECT_KIND': inject_kind}, external_executor=True,
        provider_cwd=tmp_path, config_validator=lambda *args: None, admission=ChatGPTAdmission()))
    methods = [json.loads(line) for line in (tmp_path / 'admission-methods.jsonl').read_text().splitlines()]
    return result, methods, journal.read_text()


def test_admission_precedes_thread_and_private_responses_are_redacted(tmp_path):
    result, methods, raw = invoke(tmp_path)
    assert result.ok
    assert methods.index('account/read') < methods.index('account/rateLimits/read') < methods.index('model/list') < methods.index('thread/start')
    for secret in ('SYNTHETIC_CONFIG_PRIVATE', 'SYNTHETIC_EMAIL_PRIVATE', 'SYNTHETIC_ACCOUNT_PRIVATE', 'SYNTHETIC_MODEL_PRIVATE'):
        assert secret not in raw
    assert '[redacted]' in raw


@pytest.mark.parametrize('method', ['config/read', 'configRequirements/read', 'account/read',
    'account/rateLimits/read', 'model/list', 'thread/start'])
@pytest.mark.parametrize('kind', ['account', 'rate'])
def test_admission_races_stop_before_thread_or_turn(tmp_path, method, kind):
    result, methods, raw = invoke(tmp_path, method, kind)
    assert not result.ok
    assert 'turn/start' not in methods
    if method != 'thread/start':
        assert 'thread/start' not in methods
    assert 'SYNTHETIC_AUTH_PRIVATE' not in raw


def test_account_change_during_turn_disqualifies_result(tmp_path):
    result, methods, _ = invoke(tmp_path, 'turn/start')
    assert 'turn/start' in methods
    assert not result.ok


@pytest.mark.parametrize('case', ['missing', 'pagination_loop', 'oversize'])
def test_model_catalog_must_contain_requested_model_with_bounded_pages(case):
    gate = ChatGPTAdmission()
    calls = []
    async def rpc(method, params):
        calls.append(method)
        if method == 'account/read': return account()
        if method == 'account/rateLimits/read': return limits()
        if case == 'pagination_loop': return {'data': [], 'nextCursor': 'same'}
        if case == 'oversize': return {'data': [{}] * 101}
        return {'data': [], 'nextCursor': None}
    with pytest.raises(ValueError):
        asyncio.run(gate.preflight(rpc, 'missing-model'))
    assert calls.count('model/list') <= 2


def catalog_model():
    return {'id': 'catalog-id', 'model': 'requested-model', 'hidden': False,
            'description': 'Synthetic model', 'displayName': 'Fixture', 'isDefault': True,
            'defaultReasoningEffort': 'medium',
            'supportedReasoningEfforts': [{'reasoningEffort': 'medium', 'description': 'Medium'}]}


@pytest.mark.parametrize('conflict', ['duplicate', 'same_id_other_model', 'same_model_hidden'])
@pytest.mark.parametrize('split_pages', [False, True])
def test_duplicate_or_conflicting_catalog_identity_rejected(conflict, split_pages):
    first = catalog_model()
    second = copy.deepcopy(first)
    if conflict == 'same_id_other_model':
        second['model'] = 'other-model'
    elif conflict == 'same_model_hidden':
        second.update(id='other-id', hidden=True)
    pages = ([{'data': [first], 'nextCursor': 'next'}, {'data': [second], 'nextCursor': None}]
             if split_pages else [{'data': [first, second], 'nextCursor': None}])

    async def rpc(method, params):
        if method == 'account/read': return account()
        if method == 'account/rateLimits/read': return limits()
        return pages.pop(0)

    gate = ChatGPTAdmission()
    with pytest.raises(ValueError, match='catalog identity'):
        asyncio.run(gate.preflight(rpc, 'requested-model'))
    with pytest.raises(ValueError):
        gate.check_ready()


def test_requested_reasoning_effort_must_be_advertised():
    async def rpc(method, params):
        if method == 'account/read': return account()
        if method == 'account/rateLimits/read': return limits()
        return {'data': [catalog_model()], 'nextCursor': None}

    accepted = ChatGPTAdmission()
    asyncio.run(accepted.preflight(rpc, 'requested-model', 'medium'))
    accepted.check_ready()
    rejected = ChatGPTAdmission()
    with pytest.raises(ValueError, match='reasoning effort'):
        asyncio.run(rejected.preflight(rpc, 'requested-model', 'high'))


def test_catalog_accepts_bounded_future_reasoning_effort_names():
    entry = catalog_model()
    entry['defaultReasoningEffort'] = 'future-effort'
    entry['supportedReasoningEfforts'] = [
        {'reasoningEffort': 'future-effort', 'description': 'Synthetic future effort'}]
    async def rpc(method, params):
        if method == 'account/read': return account()
        if method == 'account/rateLimits/read': return limits()
        return {'data': [entry], 'nextCursor': None}

    gate = ChatGPTAdmission()
    asyncio.run(gate.preflight(rpc, 'requested-model', 'future-effort'))
    gate.check_ready()


def test_duplicate_reasoning_efforts_are_rejected():
    entry = catalog_model()
    entry['supportedReasoningEfforts'].append(copy.deepcopy(entry['supportedReasoningEfforts'][0]))
    async def rpc(method, params):
        if method == 'account/read': return account()
        if method == 'account/rateLimits/read': return limits()
        return {'data': [entry], 'nextCursor': None}

    with pytest.raises(ValueError, match='Duplicate model reasoning effort'):
        asyncio.run(ChatGPTAdmission().preflight(rpc, 'requested-model', 'medium'))

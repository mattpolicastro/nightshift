"""Generated ChatGPT policy only: synthetic config and no credential operations."""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift.workers import chatgpt_provider as module
from nightshift.workers.container_session import SessionError


def create(monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    identity = module.ChatGPTIdentity('synthetic-expected@example.invalid', 'synthetic-workspace')
    return module.ChatGPTProvider._for_synthetic_test(Path('/synthetic/codex'), 'explicit-model',
                                                     expected_identity=identity)


def evidence(obj):
    raw = copy.deepcopy(obj._expected)
    effective = copy.deepcopy(raw)
    effective['tools'] = {'web_search': None}
    effective['openai_base_url'] = None
    effective['chatgpt_base_url'] = 'https://chatgpt.com/backend-api/'
    name = {'type': 'user', 'file': str(obj.home / 'config.toml'), 'profile': None}
    def leaves(values, prefix=''):
        result = set()
        for key, value in values.items():
            result.update(leaves(value, prefix + key + '.') if isinstance(value, dict) else {prefix + key})
        return result
    return {'config': effective, 'layers': [{'name': name, 'config': raw, 'version': 'synthetic-version'}],
            'origins': {key: {'name': copy.deepcopy(name), 'version': 'synthetic-version'}
                        for key in leaves(raw) | {'features.network_proxy.enabled'}}}


def test_production_constructor_remains_blocked_without_stable_keyring_home():
    with pytest.raises(SessionError, match='stable keyring-home lifecycle is unqualified'):
        module.ChatGPTProvider(Path('/synthetic/codex'), 'model')


def test_generated_managed_policy_never_inherits_api_auth(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'SYNTHETIC_NEVER_INHERIT')
    with create(monkeypatch) as obj:
        assert obj._expected['model_provider'] == 'openai'
        assert obj._expected['forced_login_method'] == 'chatgpt'
        assert obj._expected['cli_auth_credentials_store'] == 'keyring'
        assert obj._expected['model_providers'] == {}
        assert set(obj._environment()) == {'PATH', 'HOME', 'CODEX_HOME'}
        assert 'SYNTHETIC_NEVER_INHERIT' not in obj._config
        assert 'api_key' not in obj._config.lower()
        obj._validate_configuration(evidence(obj), {'requirements': None})
        assert obj._configuration_confirmed


@pytest.mark.parametrize('key,value', [
    ('model_provider', 'custom'), ('model_providers', {'openai': {'env_key': 'OPENAI_API_KEY'}}),
    ('forced_login_method', 'api'), ('cli_auth_credentials_store', 'file'),
    ('cli_auth_credentials_store', 'auto'), ('openai_base_url', 'https://untrusted.invalid'),
    ('chatgpt_base_url', 'https://untrusted.invalid'),
])
def test_api_or_custom_provider_fallback_is_rejected(monkeypatch, key, value):
    with create(monkeypatch) as obj:
        response = evidence(obj)
        response['config'][key] = value
        with pytest.raises(ValueError):
            obj._validate_configuration(response, {'requirements': None})


def test_unsupported_platform_blocks_keyring_policy(monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'linux')
    with pytest.raises(SessionError, match='macOS'):
        module.ChatGPTProvider._for_synthetic_test(Path('/synthetic/codex'), 'model',
            expected_identity=module.ChatGPTIdentity('fixture@example.invalid', 'fixture-workspace'))


def test_bound_provider_uses_os_home_and_owned_codex_home(monkeypatch):
    monkeypatch.setenv('HOME', '/synthetic/untrusted-ambient-home')
    monkeypatch.setattr(module.pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir='/synthetic/os-home'))
    with create(monkeypatch) as obj:
        env = obj._environment()
        assert env == {'PATH': module.os.defpath, 'HOME': '/synthetic/os-home', 'CODEX_HOME': str(obj.home)}
        assert obj._expected['forced_chatgpt_workspace_id'] == 'synthetic-workspace'
        assert 'synthetic-expected@example.invalid' not in obj._config
        assert 'synthetic-workspace' not in repr(obj)
        admission = obj._admission()
        admission.account({'requiresOpenaiAuth': True, 'account': {'type': 'chatgpt',
            'email': 'synthetic-expected@example.invalid', 'planType': 'pro'}})
        with pytest.raises(ValueError, match='private expected identity'):
            admission.rate_limits({'accountId': 'different-workspace',
                'rateLimits': {'primary': {'usedPercent': 1}}})


@pytest.mark.parametrize('value', [None, 'different-workspace', ['synthetic-workspace']])
def test_effective_workspace_binding_must_match_private_expectation(monkeypatch, value):
    with create(monkeypatch) as obj:
        response = evidence(obj)
        response['config']['forced_chatgpt_workspace_id'] = value
        with pytest.raises(ValueError) as error:
            obj._validate_configuration(response, {'requirements': None})
        assert 'synthetic-workspace' not in str(error.value)


def test_missing_private_identity_cannot_construct_provider(monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    with pytest.raises(ValueError, match='private ChatGPT identity'):
        module.ChatGPTProvider._for_synthetic_test(Path('/synthetic/codex'), 'model', expected_identity=None)


def test_fresh_home_metadata_constructor_uses_same_private_policy(monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    identity = module.ChatGPTIdentity('fixture@example.invalid', 'fixture-workspace')
    obj = module.ChatGPTProvider._for_fresh_home_metadata_probe(
        Path('/synthetic/codex'), 'model', expected_identity=identity)
    assert obj._expected_identity is identity
    assert obj._used is False and obj._temporary is None


def test_fresh_home_metadata_policy_cannot_start_model_turn(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('metadata policy reached model execution')
    monkeypatch.setattr(module.FixtureProvider, 'run', forbidden)
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    policy = module.ChatGPTProvider._for_fresh_home_metadata_probe(
        Path('/synthetic/codex'), 'model', expected_identity=(
            module.ChatGPTIdentity('fixture@example.invalid', 'workspace')))
    with pytest.raises(SessionError, match='cannot start a model thread or turn'):
        asyncio.run(policy.run(None))


def test_fresh_home_metadata_policy_does_not_accept_stable_home(monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    with pytest.raises(TypeError):
        module.ChatGPTProvider._for_fresh_home_metadata_probe(
            Path('/synthetic/codex'), 'model', expected_identity=(
                module.ChatGPTIdentity('fixture@example.invalid', 'workspace')),
            stable_home=Path('/enrolled/profile'))

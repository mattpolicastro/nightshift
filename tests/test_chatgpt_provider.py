"""Generated ChatGPT policy only: synthetic config and no credential operations."""
import copy
from pathlib import Path

import pytest

from nightshift.workers import chatgpt_provider as module
from nightshift.workers.container_session import SessionError


def create(monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    return module.ChatGPTProvider._for_synthetic_test(Path('/synthetic/codex'), 'explicit-model')


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


def test_production_constructor_remains_blocked_without_keyring_binding():
    with pytest.raises(SessionError, match='keyring account binding is unqualified'):
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
        module.ChatGPTProvider._for_synthetic_test(Path('/synthetic/codex'), 'model')

"""Generated fixture policy and effective startup checks; no live provider."""
import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift.workers import provider_home as module
from nightshift.workers.container_session import SessionError
from nightshift.workers.native_executor import _LAUNCHER, _private_write


def provider():
    return module.FixtureProvider(Path('/fixture/codex'), 'gpt-5.4', 'http://127.0.0.1:12345/v1')


def evidence(obj):
    raw = copy.deepcopy(obj._expected)
    name = {'type': 'user', 'file': str(obj.home / 'config.toml'), 'profile': None}
    layer = {'name': name, 'config': raw, 'version': 'sha256:' + 'a' * 64}
    effective = copy.deepcopy(raw)
    effective['tools'] = {'web_search': None}
    normalized = effective['model_providers']['nightshift_fixture']
    for key in ('env_key_instructions', 'experimental_bearer_token', 'auth', 'aws',
                'query_params', 'http_headers', 'env_http_headers', 'stream_idle_timeout_ms',
                'websocket_connect_timeout_ms'):
        normalized[key] = None
    normalized['supports_standalone_web_search'] = False
    def flatten(value, prefix=''):
        output = set()
        for key, item in value.items():
            path = prefix + key
            output.update(flatten(item, path + '.') if isinstance(item, dict) else {path})
        return output
    origins = {key: {'name': copy.deepcopy(name), 'version': layer['version']}
               for key in flatten(raw) | {'features.network_proxy.enabled'}}
    return {'config': effective, 'origins': origins, 'layers': [layer,
        {'name': {'type': 'system', 'file': '/etc/codex/config.toml'}, 'config': {}, 'version': 'empty'}]}


def test_fresh_home_only_contains_generated_private_configuration(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'never-inherit')
    with provider() as first:
        assert [p.name for p in first.home.iterdir()] == ['config.toml']
        assert first.home.stat().st_mode & 0o777 == 0o700
        assert (first.home / 'config.toml').stat().st_mode & 0o777 == 0o600
        assert 'never-inherit' not in first._config
        assert first._expected['mcp_servers'] == first._expected['plugins'] == {}
        assert all(value is False for name, value in first._expected['features'].items()
                   if name != 'skip_host_skill_discovery')
        old = first.home
        with provider() as second:
            assert second.home != old
    assert not old.exists()


@pytest.mark.parametrize('url', ['https://api.openai.com/v1', 'http://localhost:123/v1',
    'http://127.0.0.1:123/v1?key=secret', 'http://user:pass@127.0.0.1:123/v1',
    'http://127.0.0.1/v1', 'http://127.0.0.1:123/v1#fragment'])
def test_nonfixture_endpoint_rejected(url):
    with pytest.raises(ValueError):
        module.FixtureProvider(Path('/fixture/codex'), 'model', url)


def test_subscription_cannot_read_credentials(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'do-not-use')
    with pytest.raises(SessionError, match='not qualified'):
        module.FixtureProvider.subscription()


def test_exact_controlled_configuration_and_origins_accepted():
    with provider() as obj:
        obj._validate_configuration(evidence(obj), {'requirements': None})
        assert obj._configuration_confirmed


@pytest.mark.parametrize('mutation', [
    lambda data: data.pop('layers'),
    lambda data: data['layers'][1]['config'].update({'mcp_servers': {'unowned': {}}}),
    lambda data: data['layers'][1]['name'].update(file='/different/config.toml'),
    lambda data: data['layers'][1]['name'].update(type='project'),
    lambda data: data['layers'][0]['name'].update(file='/interactive/config.toml'),
    lambda data: data['layers'][0]['config'].update({'instructions': 'unowned'}),
    lambda data: data['layers'][0].update(disabledReason='disabled'),
    lambda data: data['origins'].pop('features.apps'),
    lambda data: data['origins']['features.apps']['name'].update(type='mdm'),
    lambda data: data['origins']['features.apps'].update(version='different'),
    lambda data: data['config']['features'].update(apps=True),
    lambda data: data['config']['features'].update(unknown_tool=True),
    lambda data: data['config']['model_providers']['nightshift_fixture'].update(requires_openai_auth=True),
    lambda data: data['config'].update(skills={'paths': ['/interactive/skills']}),
])
def test_expanded_or_unowned_policy_rejected(mutation):
    with provider() as obj:
        data = evidence(obj)
        mutation(data)
        with pytest.raises(ValueError):
            obj._validate_configuration(data, {'requirements': None})
        assert not obj._configuration_confirmed


@pytest.mark.parametrize('requirements', [{}, {'requirements': {}}, {'requirements': {'allowedSandboxModes': ['read-only']}}])
def test_unknown_or_nonempty_requirements_rejected(requirements):
    with provider() as obj:
        with pytest.raises(ValueError):
            obj._validate_configuration(evidence(obj), requirements)


def bind(obj):
    socket_path = obj.home / 'not-created.sock'
    obj._executor = SimpleNamespace(socket_path=socket_path)
    _private_write(obj.home / 'nightshift-executor-launcher.py', _LAUNCHER)
    _private_write(obj.home / 'environments.toml', 'default="remote"\ninclude_local=false\n[[environments]]\n'
        'id="remote"\nprogram=' + json.dumps(sys.executable) + '\nargs=' +
        json.dumps([str(obj.home / 'nightshift-executor-launcher.py'), str(socket_path)]) + '\ninitialize_timeout_sec=5\n')


@pytest.mark.parametrize('mutation', ['local', 'launcher', 'profile'])
def test_launcher_or_profile_injection_rejected(mutation):
    with provider() as obj:
        bind(obj)
        obj._ready()
        if mutation == 'local':
            path = obj.home / 'environments.toml'
            path.write_text(path.read_text().replace('include_local=false', 'include_local=true'))
        elif mutation == 'launcher':
            (obj.home / 'nightshift-executor-launcher.py').write_text('unowned')
        else:
            (obj.home / 'skills').mkdir()
        with pytest.raises(SessionError):
            obj._ready()


@pytest.mark.parametrize('output,code', [('codex-cli 0.153.4', 0), ('codex-cli 999', 0), ('codex-cli 0.153.4', 1)])
def test_bounded_host_version_pin(tmp_path, output, code):
    binary = tmp_path / 'codex'
    binary.write_text('#!' + sys.executable + '\nimport sys\nprint(' + repr(output) + ')\nsys.exit(' + str(code) + ')\n')
    binary.chmod(0o700)
    action = module._check_version(binary, tmp_path, {'PATH': os.defpath}, time.monotonic() + 3)
    if output == 'codex-cli 0.153.4' and code == 0:
        asyncio.run(action)
    else:
        with pytest.raises(SessionError, match='version'):
            asyncio.run(action)


def test_provider_binding_and_home_cannot_be_reused():
    obj = provider()
    with obj:
        obj.attach(SimpleNamespace())
        with pytest.raises(SessionError, match='reused'):
            obj.attach(SimpleNamespace())
    with pytest.raises(SessionError, match='reused'):
        obj.__enter__()


def test_provider_run_is_single_attempt():
    with provider() as obj:
        bind(obj)
        obj._used = True
        with pytest.raises(SessionError, match='reused'):
            obj._ready()


@pytest.mark.parametrize('layer,key,value', [
    ('config', 'apps', 0), ('config', 'skip_host_skill_discovery', 1),
    ('raw', 'apps', 0), ('raw', 'skip_host_skill_discovery', 1),
])
def test_numeric_values_cannot_masquerade_as_policy_booleans(layer, key, value):
    with provider() as obj:
        data = evidence(obj)
        target = data['config'] if layer == 'config' else data['layers'][0]['config']
        target['features'][key] = value
        with pytest.raises(ValueError):
            obj._validate_configuration(data, {'requirements': None})


def test_boolean_cannot_masquerade_as_zero_retry_budget():
    with provider() as obj:
        data = evidence(obj)
        data['config']['model_providers']['nightshift_fixture']['request_max_retries'] = False
        with pytest.raises(ValueError):
            obj._validate_configuration(data, {'requirements': None})


@pytest.mark.parametrize('overflow_stream', ['stdout', 'stderr'])
def test_version_overflow_cancels_and_reaps_sibling_reader(tmp_path, monkeypatch, overflow_stream):
    state = {'cancelled': False, 'reaped': False}
    class Stream:
        def __init__(self, overflow):
            self.overflow = overflow
        async def read(self, amount):
            if self.overflow:
                return b'x' * amount
            try:
                await asyncio.Event().wait()
            finally:
                state['cancelled'] = True
    class Process:
        pid = 987654321
        stdout = Stream(overflow_stream == 'stdout')
        stderr = Stream(overflow_stream == 'stderr')
        async def wait(self):
            state['reaped'] = True
            return 0
    async def spawn(*args, **kwargs):
        return Process()
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(module.os, 'killpg', lambda *args: None)
    async def exercise():
        before = asyncio.all_tasks()
        with pytest.raises(SessionError, match='byte limit'):
            await module._check_version(Path('/fixture/codex'), tmp_path, {}, time.monotonic() + 3)
        assert asyncio.all_tasks() == before
        assert state == {'cancelled': True, 'reaped': True}
    asyncio.run(exercise())

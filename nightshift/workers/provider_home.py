"""Generated, one-attempt provider homes for synthetic native qualification.

No interactive profile path, arbitrary config, inherited environment, or real
credential input is accepted. Subscription authentication remains unsupported.
An empty /etc/codex/config.toml layer is the sole external-layer exception;
nonempty system settings and every other external layer fail before thread start.
The builtin skills namespace may exist, but no host capability roots are copied.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import signal
import time
from dataclasses import replace
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from . import codex
from .base import WorkerRequest, WorkerResult
from .container_session import SessionError
from .native_executor import NativeExecutor, _private_write, _LAUNCHER

FIXTURE_KEY = 'synthetic-fixture-key'
FEATURES = {name: False for name in ('apps', 'plugins', 'hooks', 'multi_agent', 'browser_use',
    'computer_use', 'shell_snapshot', 'view_image', 'image_generation', 'skill_search',
    'network_proxy', 'auth_elicitation', 'background_paginated_rollout_migration', 'mcp_2026_07_28',
    'memories', 'mentions_v2', 'remote_control', 'remote_plugin', 'tool_suggest')}
FEATURES['skip_host_skill_discovery'] = True


def _same(actual, expected):
    """JSON policy equality must not confuse booleans with numeric values."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_same(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _url(value):
    if not isinstance(value, str):
        raise ValueError('A literal loopback fixture endpoint is required')
    parsed = urlsplit(value)
    try:
        valid = (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                 and parsed.port is not None and 1 <= parsed.port <= 65535
                 and value == f'http://127.0.0.1:{parsed.port}/v1')
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Only http://127.0.0.1:port/v1 synthetic endpoints are supported')
    return value


async def _check_version(binary, home, env, deadline):
    process = await asyncio.create_subprocess_exec(str(binary), '--version', cwd=home, env=env,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True)
    async def bounded(stream, cap):
        data = bytearray()
        while chunk := await stream.read(cap + 1 - len(data)):
            data.extend(chunk)
            if len(data) > cap:
                raise SessionError('Provider version response exceeds its byte limit')
        return bytes(data)
    readers = [asyncio.create_task(bounded(process.stdout, 256)),
               asyncio.create_task(bounded(process.stderr, 16384))]
    try:
        async with asyncio.timeout(max(0, min(5, deadline - time.monotonic()))):
            stdout, _ = await asyncio.gather(*readers)
            code = await process.wait()
        if code != 0 or stdout.strip() != b'codex-cli 0.153.4':
            raise SessionError('Pinned provider version could not be confirmed')
    finally:
        try:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)


class FixtureProvider:
    def __init__(self, binary: Path, model: str, base_url: str):
        if not isinstance(binary, Path) or not binary.is_absolute():
            raise ValueError('An explicit absolute provider executable is required')
        if not isinstance(model, str) or not model.strip() or len(model) > 200 or any(ord(c) < 32 for c in model):
            raise ValueError('An explicit bounded model label is required')
        self.binary, self.model, self.base_url = binary, model, _url(base_url)
        self._temporary = None
        self._used = False
        self._attached = False
        self._executor = None
        self._configuration_confirmed = False
        self._closed = False

    @classmethod
    def subscription(cls, *args, **kwargs):
        raise SessionError('Subscription authentication is not qualified; no credentials were read')

    def __enter__(self):
        if self._temporary is not None or self._closed:
            raise SessionError('Provider home cannot be reused')
        self._temporary = tempfile.TemporaryDirectory(prefix='nightshift-fixture-provider-')
        self.home = Path(self._temporary.name).resolve()
        self.home.chmod(0o700)
        config = self._configuration()
        self._config = config
        self._expected = tomllib.loads(config)
        try:
            _private_write(self.home / 'config.toml', config)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _configuration(self):
        return ('model_provider="nightshift_fixture"\nmodel=' + json.dumps(self.model) + '\n'
            'web_search="disabled"\n[model_providers.nightshift_fixture]\nname="Synthetic fixture"\n'
            'base_url=' + json.dumps(self.base_url) + '\nwire_api="responses"\n'
            'env_key="NIGHTSHIFT_FAKE_KEY"\nrequires_openai_auth=false\nsupports_websockets=false\n'
            'request_max_retries=0\nstream_max_retries=0\n'
            '[tools]\nexperimental_request_user_input={enabled=false}\n[features]\n' +
            ''.join(key + '=' + str(value).lower() + '\n' for key, value in FEATURES.items()) +
            '[mcp_servers]\n[plugins]\n')

    def attach(self, session):
        if self._temporary is None or self._closed or self._attached:
            raise SessionError('Provider executor binding is unavailable or reused')
        self._attached = True
        self._executor = NativeExecutor(session, self.home)
        return self._executor

    def _validate_configuration(self, response, requirements):
        if not _same(requirements, {"requirements": None}):
            raise ValueError("Provider has configured or unknown requirements")
        if not isinstance(response, dict) or not isinstance(response.get('config'), dict):
            raise ValueError('Malformed effective provider configuration')
        layers = response.get('layers')
        if not isinstance(layers, list) or not layers:
            raise ValueError('Effective configuration layers are required')
        user_seen, system_seen = False, False
        user_layer = None
        for layer in layers:
            if (not isinstance(layer, dict) or not isinstance(layer.get('name'), dict)
                    or not isinstance(layer.get('version'), str) or layer.get('disabledReason') is not None):
                raise ValueError('Malformed or disabled provider configuration layer')
            name, values = layer['name'], layer.get('config')
            if name == {'type': 'system', 'file': '/etc/codex/config.toml'} and values == {} and not system_seen:
                system_seen = True
            elif (name in ({'type': 'user', 'file': str(self.home / 'config.toml')},
                           {'type': 'user', 'file': str(self.home / 'config.toml'), 'profile': None})
                  and _same(values, self._expected) and not user_seen):
                user_seen = True
                user_layer = layer
            else:
                raise ValueError('Unowned or altered provider configuration layer')
        if not user_seen:
            raise ValueError('Generated provider configuration layer is missing')
        origins = response.get('origins')
        def leaves(value, prefix=''):
            result = set()
            for key, item in value.items():
                path = prefix + key
                result.update(leaves(item, path + '.') if isinstance(item, dict) else {path})
            return result
        expected_origin = {'name': user_layer['name'], 'version': user_layer['version']}
        if (not isinstance(origins, dict) or set(origins) != leaves(self._expected) | {'features.network_proxy.enabled'}
                or any(not _same(value, expected_origin) for value in origins.values())):
            raise ValueError('Provider policy origins differ from the generated user layer')
        effective = response['config']
        for key in ('model', 'model_provider', 'web_search', 'features', 'mcp_servers', 'plugins'):
            if not _same(effective.get(key), self._expected[key]):
                raise ValueError('Effective provider configuration differs from generated policy')
        self._validate_model_provider(effective)
        # The pinned config/read view omits experimental_request_user_input;
        # its exact false value and origin are therefore checked in the raw layer.
        if not _same(effective.get('tools'), {'web_search': None}):
            raise ValueError('Effective tool configuration differs')
        for key in ('skills', 'instructions', 'developer_instructions', 'base_instructions',
                    'model_instructions_file', 'experimental_instructions_file', 'hooks',
                    'apps', 'profiles', 'profile', 'agents', 'marketplaces', 'orchestrator',
                    'model_catalog_json', 'notify', 'experimental_compact_prompt_file'):
            if effective.get(key) not in (None, {}, []):
                raise ValueError('Provider inherited instructions or capability configuration')
        self._configuration_confirmed = True

    def _validate_model_provider(self, effective):
        provider = dict(self._expected['model_providers']['nightshift_fixture'])
        provider.update({key: None for key in ('env_key_instructions', 'experimental_bearer_token',
            'auth', 'aws', 'query_params', 'http_headers', 'env_http_headers',
            'stream_idle_timeout_ms', 'websocket_connect_timeout_ms')})
        provider['supports_standalone_web_search'] = False
        if not _same(effective.get('model_providers'), {'nightshift_fixture': provider}):
            raise ValueError('Effective model provider differs from generated fixture policy')

    def _environment(self):
        return {"PATH": os.defpath, "HOME": str(self.home), "CODEX_HOME": str(self.home),
                "NIGHTSHIFT_FAKE_KEY": FIXTURE_KEY}

    def _admission(self):
        return None

    def _ready(self):
        if self._temporary is None or self._closed or self._used or self._executor is None:
            raise SessionError('Provider launch is unavailable or reused')
        expected = {'config.toml', 'environments.toml', 'nightshift-executor-launcher.py'}
        if {p.name for p in self.home.iterdir()} != expected:
            raise SessionError('Provider home contains unowned startup assets')
        for name in expected:
            info = (self.home / name).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise SessionError('Provider startup asset is not private and owned')
        if (self.home / 'config.toml').read_text() != self._config:
            raise SessionError('Generated provider configuration changed')
        if (self.home / 'nightshift-executor-launcher.py').read_text() != _LAUNCHER:
            raise SessionError('Generated executor launcher changed')
        environments = tomllib.loads((self.home / 'environments.toml').read_text())
        if (set(environments) != {'default', 'include_local', 'environments'}
                or environments['default'] != 'remote' or environments['include_local'] is not False
                or not isinstance(environments['environments'], list) or len(environments['environments']) != 1):
            raise SessionError('Provider does not have an exclusive remote executor')
        remote = environments['environments'][0]
        import sys
        expected_remote = {'id': 'remote', 'program': sys.executable,
            'args': [str(self.home / 'nightshift-executor-launcher.py'), str(self._executor.socket_path)],
            'initialize_timeout_sec': 5}
        if not _same(remote, expected_remote):
            raise SessionError('Provider executor binding changed')

    async def run(self, request: WorkerRequest) -> WorkerResult:
        self._ready()
        if request.model != self.model or request.cwd != Path('/workspace'):
            raise SessionError('Provider request differs from its bound model or source')
        self._used = True
        env = self._environment()
        deadline = time.monotonic() + request.budgets.max_runtime_s
        await _check_version(self.binary, self.home, env, deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SessionError('Provider version check exhausted the request deadline')
        request = replace(request, budgets=replace(request.budgets, max_runtime_s=remaining))
        result = await codex._run_stdio(request, [str(self.binary), 'app-server', '--stdio', '--strict-config'],
            env=env, external_executor=True, provider_cwd=self.home, config_validator=self._validate_configuration, admission=self._admission())
        if not self._configuration_confirmed and result.ok:
            result.status = 'protocol_error'
            result.diagnostics.append('Effective configuration was not confirmed')
        return result

    def __exit__(self, exc_type, exc, traceback):
        self._closed = True
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        return False

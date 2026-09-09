"""Offline owned subprocess/socket transport tests; no provider or Docker engine."""
import json
import os
import socket
import sys
import time
import tomllib
from types import SimpleNamespace

import pytest

from nightshift.workers.container_session import SessionError, BinaryResult
from nightshift.workers.native_executor import NativeExecutor


def fixture(tmp_path, script='import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n', **kwargs):
    docker = tmp_path / 'docker'
    docker.write_text('#!' + sys.executable + '\n' + script)
    docker.chmod(0o700)
    home = tmp_path / 'provider'
    home.mkdir(mode=0o700)
    session = SimpleNamespace(_entered=True, closed=False, _poisoned=False,
        _env={'PATH': str(tmp_path), 'HOME': str(tmp_path / 'empty'),
              'DOCKER_CONFIG': str(tmp_path / 'empty'), 'DOCKER_HOST': 'unix:///synthetic.sock'},
        name='owned-test-container', deadline=time.monotonic() + 3,
        _baseline_processes={1, 2}, _processes=lambda: {1, 2}, _healthy=lambda: None,
        _call=lambda *a, **k: BinaryResult('completed', 0, b'codex-cli 0.153.4\n', b'benign warning'))
    return NativeExecutor(session, home, **kwargs)


def exchange(executor, data=b'hello'):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(3)
        peer.connect(str(executor.socket_path))
        peer.sendall(data)
        peer.shutdown(socket.SHUT_WR)
        result = bytearray()
        while chunk := peer.recv(65536):
            result.extend(chunk)
        return bytes(result)


def test_owned_roundtrip_reaped_and_private_configuration(tmp_path):
    obj = fixture(tmp_path)
    with obj:
        config = tomllib.loads((obj.provider_home / 'environments.toml').read_text())
        assert config['include_local'] is False
        assert config['default'] == 'remote'
        assert config['environments'][0]['program'] == sys.executable
        assert 'docker' not in json.dumps(config)
        assert obj.socket_path.stat().st_mode & 0o777 == 0o600
        assert exchange(obj) == b'hello'
        obj.quiesce()
        assert obj.quiesced
        assert obj._process.poll() == 0
    assert not obj.session._poisoned
    assert not obj.socket_path.exists()


def test_fixed_credential_free_docker_argv(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'never-forward-this')
    obj = fixture(tmp_path)
    with obj:
        exchange(obj)
        argv = obj._argv()
        assert os.path.isabs(argv[0])
        assert argv[1:7] == ['exec', '-i', '--workdir', '/workspace', '--', obj.session.name]
        assert argv[7:9] == ['/usr/bin/env', '-i']
        assert argv[-4:] == ['/opt/codex/bin/codex', 'exec-server', '--listen', 'stdio']
        assert 'never-forward-this' not in str(argv)
        assert 'OPENAI_API_KEY' not in obj.session._env


def test_second_connection_cannot_restart_executor(tmp_path):
    obj = fixture(tmp_path)
    with obj:
        assert exchange(obj) == b'hello'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            with pytest.raises(OSError):
                peer.connect(str(obj.socket_path))
        obj.quiesce()


@pytest.mark.parametrize('script', [
    'import sys\nsys.stdin.buffer.read()\nsys.exit(7)\n',
    "import sys\nsys.stdin.buffer.read()\nsys.stderr.write('x'*1000)\n",
])
def test_failed_or_noisy_server_poison(tmp_path, script):
    obj = fixture(tmp_path, script=script, stderr_limit=10)
    with pytest.raises(SessionError):
        with obj:
            exchange(obj)
            obj.quiesce()
    assert obj.session._poisoned
    assert obj._process.poll() is not None


def test_transport_budget_poison(tmp_path):
    obj = fixture(tmp_path, max_transport_bytes=5)
    with pytest.raises(SessionError):
        with obj:
            try:
                exchange(obj, b'123456')
            except OSError:
                pass
            obj.quiesce()
    assert obj.session._poisoned


def test_exception_cancels_silent_server(tmp_path):
    obj = fixture(tmp_path, script='import time\ntime.sleep(30)\n')
    started = time.monotonic()
    with pytest.raises(RuntimeError, match='cancel'):
        with obj:
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            peer.connect(str(obj.socket_path))
            while obj._process is None:
                time.sleep(0.01)
            raise RuntimeError('cancel')
    peer.close()
    assert time.monotonic() - started < 2
    assert obj._process.poll() is not None
    assert obj.session._poisoned


def test_leftover_tool_process_disqualifies_quiescence(tmp_path):
    obj = fixture(tmp_path)
    with pytest.raises(SessionError, match='unfinished'):
        with obj:
            exchange(obj)
            obj.session._processes = lambda: {1, 2, 3}
            obj.quiesce()
    assert obj.session._poisoned


def test_existing_provider_configuration_never_overwritten(tmp_path):
    obj = fixture(tmp_path)
    path = obj.provider_home / 'environments.toml'
    path.write_text('existing')
    with pytest.raises(FileExistsError):
        obj.__enter__()
    assert path.read_text() == 'existing'
    assert obj._process is None


def test_wrong_runtime_version_cannot_attach(tmp_path):
    obj = fixture(tmp_path)
    obj.session._call = lambda *a, **k: BinaryResult('completed', 0, b'codex-cli 999\n')
    with pytest.raises(SessionError, match='version'):
        obj.__enter__()
    assert obj._process is None


def test_cleanup_failure_still_closes_bridge_and_signals_done(tmp_path, monkeypatch):
    obj = fixture(tmp_path)
    original = obj._kill
    def failed_cleanup():
        original()
        raise OSError('synthetic sensitive diagnostic must not escape')
    monkeypatch.setattr(obj, '_kill', failed_cleanup)
    with pytest.raises(SessionError) as error:
        with obj:
            exchange(obj)
            obj.quiesce()
    assert obj._done.is_set()
    assert obj.session._poisoned
    assert 'sensitive' not in str(error.value)
    assert not obj.socket_path.exists()


def test_cancel_before_connection_prevents_process_spawn(tmp_path):
    obj = fixture(tmp_path)
    with pytest.raises(RuntimeError):
        with obj:
            raise RuntimeError('cancel before provider')
    assert obj._process is None
    assert obj._done.is_set()
    assert obj.session._poisoned
    assert not obj.socket_path.exists()

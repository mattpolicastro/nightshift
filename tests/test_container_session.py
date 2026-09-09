"""Offline boundary controls and actual pipe streaming; no Docker daemon."""
import json
import os
import sys
import time

import pytest

from nightshift.workers import container_session as module
from nightshift.workers.snapshot import SourceFile


def session(tmp_path, **kwargs):
    return module.Session('sha256:' + 'a' * 64, [SourceFile('a', b'x')],
                          docker_host='unix:///tmp/test.sock', recovery_dir=tmp_path / 'recovery', **kwargs)


def active(tmp_path, monkeypatch):
    obj = session(tmp_path)
    obj._entered = True
    obj.deadline = time.monotonic() + 30
    obj._env = {}
    obj._baseline_processes = {1, 2}
    monkeypatch.setattr(obj, '_healthy', lambda: None)
    monkeypatch.setattr(obj, '_processes', lambda: {1, 2})
    return obj


def test_failed_command_is_repairable(tmp_path, monkeypatch):
    obj = active(tmp_path, monkeypatch)
    results = iter([module.BinaryResult('completed', 1), module.BinaryResult('completed', 0)])
    monkeypatch.setattr(obj, '_call', lambda *a, **k: next(results))
    assert obj.run(['false']).status == 'failed'
    assert obj.run(['true']).status == 'succeeded'
    assert not obj._poisoned


def test_cumulative_output_budget_poison(tmp_path, monkeypatch):
    obj = active(tmp_path, monkeypatch)
    obj.max_output_bytes = 5
    calls = []
    def call(args, **kwargs):
        calls.append(kwargs)
        return module.BinaryResult('completed' if len(calls) == 1 else 'output_exhausted', 0, b'abc' if len(calls) == 1 else b'de')
    monkeypatch.setattr(obj, '_call', call)
    assert obj.run(['echo']).status == 'succeeded'
    assert obj.run(['echo']).status == 'output_exhausted'
    assert calls[1]['combined_limit'] == 2
    with pytest.raises(module.SessionError):
        obj.run(['true'])


def test_record_precedes_mutation_and_ambiguous_create_retained(tmp_path, monkeypatch):
    obj = session(tmp_path)
    calls = []
    def call(args, env, deadline, **kwargs):
        calls.append(args)
        assert obj.record_path.stat().st_mode & 0o777 == 0o600
        assert json.loads(obj.record_path.read_text())['owner'] == obj.owner
        if args[:2] == ['volume', 'create']:
            return module.BinaryResult('timed_out', None)
        return module.BinaryResult('completed', 0)
    monkeypatch.setattr(module, '_binary', call)
    with pytest.raises(module.SessionError):
        obj.__enter__()
    assert obj.record_path.exists()
    assert not obj.cleanup_succeeded
    assert not any(c[0] == 'start' for c in calls)


@pytest.mark.parametrize('mode', [0o755, 0o777])
def test_public_recovery_directory_rejected(tmp_path, monkeypatch, mode):
    obj = session(tmp_path)
    obj.recovery_dir.mkdir(mode=mode)
    obj.recovery_dir.chmod(mode)
    monkeypatch.setattr(module, '_binary', lambda *a, **k: pytest.fail('Docker mutation'))
    with pytest.raises(module.SessionError):
        obj.__enter__()


def test_symlink_recovery_directory_rejected(tmp_path):
    obj = session(tmp_path)
    target = tmp_path / 'target'
    target.mkdir(mode=0o700)
    obj.recovery_dir.symlink_to(target, target_is_directory=True)
    with pytest.raises(module.SessionError):
        obj.__enter__()


def test_cleanup_error_blocks_candidate_and_shares_deadline(tmp_path, monkeypatch):
    obj = active(tmp_path, monkeypatch)
    obj._record()
    monkeypatch.setattr(obj, '_export', lambda: obj.files)
    deadlines = []
    def absent(kind, name, deadline):
        deadlines.append(deadline)
        raise module.SessionError('daemon unavailable')
    monkeypatch.setattr(obj, '_exists', absent)
    with pytest.raises(module.SessionError):
        obj.finish()
    with pytest.raises(module.SessionError):
        obj.close()
    assert len(set(deadlines)) == 1
    assert obj.record_path.exists()
    assert not obj.cleanup_succeeded


def test_cleanup_checks_owner_before_remove(tmp_path, monkeypatch):
    obj = active(tmp_path, monkeypatch)
    obj._record()
    monkeypatch.setattr(obj, '_exists', lambda *a: True)
    monkeypatch.setattr(obj, '_inspect', lambda *a, **k: {'Config': {'Labels': {module.OWNER_LABEL: 'other'}}})
    monkeypatch.setattr(obj, '_checked', lambda *a, **k: pytest.fail('must not remove other owner'))
    with pytest.raises(module.SessionError):
        obj.close()
    assert obj.record_path.exists()


def test_failed_directory_sync_restores_record(tmp_path, monkeypatch):
    obj = active(tmp_path, monkeypatch)
    obj._record()
    monkeypatch.setattr(obj, '_exists', lambda *a: False)
    def fail(path):
        raise OSError('failed persistence')
    monkeypatch.setattr(module, '_directory_sync', fail)
    with pytest.raises(module.SessionError):
        obj.close()
    assert obj.record_path.exists()
    assert not obj.cleanup_succeeded


def test_checkpoint_unpause_failure_poison(tmp_path, monkeypatch):
    obj = active(tmp_path, monkeypatch)
    monkeypatch.setattr(obj, '_export', lambda: obj.files)
    def fail(*a, **k):
        raise module.SessionError('unpause failed')
    monkeypatch.setattr(obj, '_checked', fail)
    with pytest.raises(module.SessionError):
        obj.checkpoint()
    assert obj._poisoned


def fake_docker(tmp_path, script):
    path = tmp_path / 'docker'
    path.write_text('#!' + sys.executable + '\n' + script)
    path.chmod(0o700)
    return {'PATH': str(tmp_path)}


def test_streaming_large_stdin_and_both_outputs(tmp_path):
    env = fake_docker(tmp_path, "import os\nwhile True:\n data=os.read(0,4096)\n if not data: break\n os.write(1,data)\n os.write(2,b'e')\n")
    data = b'abc' * 100000
    result = module._binary([], env, time.monotonic() + 5, input_bytes=data, stdout_limit=len(data), stderr_limit=1000)
    assert result.status == 'completed'
    assert result.stdout == data
    assert result.stderr and set(result.stderr) == {ord('e')}


def test_silent_timeout_is_bounded(tmp_path):
    env = fake_docker(tmp_path, 'import time\ntime.sleep(30)\n')
    start = time.monotonic()
    result = module._binary([], env, start + 0.1)
    assert result.status == 'timed_out'
    assert time.monotonic() - start < 3


def test_output_cap_stops_flood(tmp_path):
    env = fake_docker(tmp_path, "import os\nwhile True: os.write(1,b'x'*65536)\n")
    result = module._binary([], env, time.monotonic() + 5, stdout_limit=123)
    assert result.status == 'output_exhausted'
    assert result.stdout == b'x' * 123


def policy_data(obj):
    return {
        'Image': obj.image_id,
        'Config': {'Image': obj.image_id, 'Labels': {module.OWNER_LABEL: obj.owner},
                   'User': '1000:1000', 'WorkingDir': '/workspace',
                   'Entrypoint': ['/bin/sleep'], 'Cmd': ['2147483647'],
                   'Healthcheck': {'Test': ['NONE']},
                   'Env': [k + '=' + v for k, v in module.ENVIRONMENT.items()]},
        'State': {'Running': False, 'Paused': False},
        'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True,
                       'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
                       'PidsLimit': 64, 'Memory': 128 * 1024 * 1024,
                       'NanoCpus': 1_000_000_000, 'Init': True, 'IpcMode': 'private',
                       'LogConfig': {'Type': 'none'}, 'Tmpfs': module.SCRATCH.copy(),
                       'Mounts': [{'Type': 'volume', 'Source': obj.volume_name,
                                   'Target': '/workspace', 'VolumeOptions': {'NoCopy': True}}]},
        'Mounts': [{'Type': 'volume', 'Name': obj.volume_name, 'Destination': '/workspace', 'RW': True}]}


@pytest.mark.parametrize('section,key,value', [
    ('Config', 'Env', ['PATH=/bin', 'SECRET=unexpected']),
    ('HostConfig', 'Binds', ['/host:/escape']),
    ('HostConfig', 'NetworkMode', 'host'),
    ('HostConfig', 'Privileged', True),
    ('HostConfig', 'CapAdd', ['SYS_ADMIN']),
    ('HostConfig', 'PidMode', 'host'),
    ('HostConfig', 'Mounts', [{'Type': 'bind', 'Source': '/host', 'Target': '/workspace'}]),
])
def test_effective_container_policy_rejects_expansion(tmp_path, section, key, value):
    obj = session(tmp_path)
    data = policy_data(obj)
    assert obj._container_policy(data, running=False)
    data[section][key] = value
    assert not obj._container_policy(data, running=False)


def test_unexpected_effective_mount_rejected(tmp_path):
    obj = session(tmp_path)
    data = policy_data(obj)
    data['Mounts'].append({'Type': 'bind', 'Source': '/host', 'Destination': '/escape'})
    assert not obj._container_policy(data, running=False)


@pytest.mark.parametrize('key,value', [
    ('Labels', {module.OWNER_LABEL: 'another-owner'}),
    ('Driver', 'third-party'),
    ('Options', {'type': 'tmpfs', 'device': 'tmpfs', 'o': 'size=1g'}),
])
def test_effective_volume_policy_rejects_expansion(tmp_path, key, value):
    obj = session(tmp_path)
    data = {'Name': obj.volume_name, 'Driver': 'local', 'Scope': 'local',
            'Options': module.VOLUME_OPTIONS.copy(), 'Labels': {module.OWNER_LABEL: obj.owner}}
    assert obj._volume_policy(data)
    data[key] = value
    assert not obj._volume_policy(data)


def test_frozen_extra_process_rejects_before_export(tmp_path, monkeypatch):
    obj = active(tmp_path, monkeypatch)
    inventories = iter([{1, 2}, {1, 2, 3}])
    monkeypatch.setattr(obj, '_processes', lambda: next(inventories))
    calls = []
    monkeypatch.setattr(obj, '_checked', lambda args, **k: calls.append(args))
    monkeypatch.setattr(obj, '_inspect', lambda *a, **k: {
        'Config': {'Labels': {module.OWNER_LABEL: obj.owner}},
        'State': {'Paused': True, 'Running': True}})
    with pytest.raises(module.SessionError, match='paused container'):
        obj.checkpoint()
    assert [args[0] for args in calls] == ['pause']
    assert obj._poisoned


def test_initial_checkpoint_mismatch_rejects_and_cleans(tmp_path, monkeypatch):
    obj = session(tmp_path)
    monkeypatch.setattr(obj, '_checked', lambda *a, **k: module.BinaryResult('completed', 0))
    monkeypatch.setattr(obj, '_inspect', lambda *a, **k: {})
    monkeypatch.setattr(obj, '_volume_policy', lambda *a: True)
    monkeypatch.setattr(obj, '_container_policy', lambda *a, **k: True)
    monkeypatch.setattr(obj, '_healthy', lambda: None)
    monkeypatch.setattr(obj, '_processes', lambda: {1, 2})
    monkeypatch.setattr(obj, 'checkpoint', lambda: [SourceFile('a', b'wrong')])
    monkeypatch.setattr(obj, '_exists', lambda *a: False)
    with pytest.raises(module.SessionError, match='Imported source differs'):
        obj.__enter__()
    assert obj.cleanup_succeeded
    assert not obj.record_path.exists()

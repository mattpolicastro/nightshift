"""Synthetic filesystem and process-lock tests; no keyring, account, or models."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nightshift.workers import credential_home as module
from nightshift.workers.container_session import SessionError


@pytest.fixture
def root(tmp_path):
    path = tmp_path / 'private-root'
    path.mkdir(mode=0o700)
    return path


def assets(lease):
    expected = {'config.toml': 'model="synthetic"\n', 'environments.toml': 'include_local=false\n'}
    lease.write_config(expected['config.toml'])
    descriptor = os.open(lease.home / 'environments.toml', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(expected['environments.toml'])
    lease.validate_startup(expected)
    return expected


def test_two_attempts_keep_namespace_inode_and_remove_attempt_assets(root):
    identities = []
    for _ in range(2):
        with module.StableCredentialHome(root) as lease:
            identities.append((lease.home, lease.home.stat().st_ino))
            record = json.loads((root / 'attempt.json').read_text())
            assert set(record) == {'version', 'attempt', 'namespace'}
            assert record['namespace'] == 'codex-home'
            assert (root / 'attempt.json').stat().st_mode & 0o777 == 0o600
            assets(lease)
            runtime = lease.home / 'runtime'
            runtime.mkdir()
            (runtime / 'cache').write_bytes(b'SYNTHETIC_RUNTIME_ONLY')
            lease.confirm_stopped()
        assert lease.cleanup_succeeded
        assert list(lease.home.iterdir()) == []
        assert set(path.name for path in root.iterdir()) == {'.lock', 'codex-home'}
    assert identities[0] == identities[1]


def test_other_process_cannot_inspect_or_mutate_active_namespace(root):
    script = '''from pathlib import Path
import sys
from nightshift.workers.credential_home import StableCredentialHome
from nightshift.workers.container_session import SessionError
try:
    with StableCredentialHome(Path(sys.argv[1])):
        raise AssertionError("second process acquired active lease")
except SessionError:
    print("LOCKED")
'''
    with module.StableCredentialHome(root) as lease:
        assets(lease)
        before = (lease.home / 'config.toml').read_bytes()
        child = subprocess.run([sys.executable, '-c', script, str(root)], capture_output=True,
                               text=True, timeout=10, check=True)
        assert child.stdout == 'LOCKED\n' and child.stderr == ''
        assert (lease.home / 'config.toml').read_bytes() == before
        lease.confirm_stopped()


@pytest.mark.parametrize('entry', ['auth.json', 'config.toml', 'skills', 'unexpected'])
def test_stale_namespace_rejected_without_reading_or_deleting(root, entry):
    home = root / 'codex-home'
    home.mkdir(mode=0o700)
    stale = home / entry
    stale.write_bytes(b'SYNTHETIC_UNOWNED')
    with pytest.raises(SessionError, match='stale or unowned'):
        with module.StableCredentialHome(root):
            pytest.fail('stale namespace admitted')
    assert stale.read_bytes() == b'SYNTHETIC_UNOWNED'
    assert not (root / 'attempt.json').exists()


def test_stale_marker_prevents_next_attempt(root):
    (root / 'attempt.json').write_text('SYNTHETIC_RECOVERY')
    with pytest.raises(SessionError):
        with module.StableCredentialHome(root):
            pytest.fail('stale recovery admitted')
    assert (root / 'attempt.json').read_text() == 'SYNTHETIC_RECOVERY'
    assert not (root / 'codex-home').exists()


def test_unconfirmed_shutdown_preserves_recovery_and_blocks_reuse(root):
    with pytest.raises(SessionError, match='confirmed process shutdown'):
        with module.StableCredentialHome(root) as lease:
            assets(lease)
    assert not lease.cleanup_succeeded
    assert (root / 'attempt.json').exists()
    assert (root / 'codex-home' / 'config.toml').exists()
    with pytest.raises(SessionError):
        with module.StableCredentialHome(root):
            pytest.fail('unfinished attempt reused')


@pytest.mark.parametrize('kind', ['root_symlink', 'parent_symlink', 'home_symlink', 'lock_symlink',
                                  'root_mode', 'home_mode', 'lock_hardlink'])
def test_unsafe_root_namespace_or_lock_rejected(root, tmp_path, kind):
    chosen = root
    outside = tmp_path / 'outside'
    outside.mkdir(mode=0o700)
    if kind == 'root_symlink':
        chosen = tmp_path / 'root-link'
        chosen.symlink_to(root, target_is_directory=True)
    elif kind == 'parent_symlink':
        chosen = tmp_path / 'parent-link'
        chosen.symlink_to(tmp_path, target_is_directory=True)
        chosen = chosen / root.name
    elif kind == 'home_symlink':
        (root / 'codex-home').symlink_to(outside, target_is_directory=True)
    elif kind == 'lock_symlink':
        (root / '.lock').symlink_to(outside / 'untouched')
    elif kind == 'lock_hardlink':
        target = outside / 'existing'
        target.touch(mode=0o600)
        os.link(target, root / '.lock')
    elif kind == 'root_mode':
        root.chmod(0o755)
    else:
        (root / 'codex-home').mkdir(mode=0o755)
    with pytest.raises(SessionError):
        with module.StableCredentialHome(chosen):
            pytest.fail('unsafe namespace admitted')
    assert not (outside / 'untouched').exists()


def test_wrong_owner_rejected_before_namespace_creation(root, monkeypatch):
    actual = os.getuid()
    monkeypatch.setattr(module.os, 'getuid', lambda: actual + 1)
    with pytest.raises(SessionError, match='private and owned'):
        with module.StableCredentialHome(root):
            pytest.fail('other owner admitted')
    assert list(root.iterdir()) == []


def test_recovery_is_durable_before_namespace_mutation(root, monkeypatch):
    original = module.os.mkdir
    def mkdir(name, *args, **kwargs):
        if name == 'codex-home':
            assert (root / 'attempt.json').exists()
            raise OSError('synthetic creation failure')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(module.os, 'mkdir', mkdir)
    with pytest.raises(SessionError):
        with module.StableCredentialHome(root):
            pytest.fail('failed creation admitted')
    assert (root / 'attempt.json').exists()
    assert not (root / 'codex-home').exists()


@pytest.mark.parametrize('change', ['extra', 'bytes', 'mode', 'symlink', 'hardlink'])
def test_startup_assets_require_exact_bytes_names_private_files(root, tmp_path, change):
    with module.StableCredentialHome(root) as lease:
        expected = assets(lease)
        path = lease.home / 'environments.toml'
        if change == 'extra':
            (lease.home / 'auth.json').write_text('SYNTHETIC_NOT_ALLOWED')
        elif change == 'bytes':
            path.write_text('ALTERED')
        elif change == 'mode':
            path.chmod(0o644)
        else:
            path.unlink()
            outside = tmp_path / 'external'
            outside.write_text(expected['environments.toml'])
            outside.chmod(0o600)
            if change == 'symlink': path.symlink_to(outside)
            else: os.link(outside, path)
        with pytest.raises((SessionError, OSError)):
            lease.validate_startup(expected)
        lease.confirm_stopped()
    assert lease.cleanup_succeeded


def test_descriptor_asset_writer_rejects_unknown_or_duplicate_names(root):
    with module.StableCredentialHome(root) as lease:
        lease.write_asset('config.toml', 'model="fixture"\n')
        with pytest.raises(FileExistsError):
            lease.write_asset('config.toml', 'model="changed"\n')
        with pytest.raises(SessionError, match='startup asset'):
            lease.write_asset('auth.json', 'forbidden')
        assert (lease.home / 'config.toml').read_text() == 'model="fixture"\n'
        lease.confirm_stopped()


def test_cleanup_unlinks_runtime_symlink_without_following_it(root, tmp_path):
    outside = tmp_path / 'host-sentinel'
    outside.write_bytes(b'UNCHANGED')
    with module.StableCredentialHome(root) as lease:
        assets(lease)
        (lease.home / 'runtime-link').symlink_to(outside)
        lease.confirm_stopped()
    assert outside.read_bytes() == b'UNCHANGED' and lease.cleanup_succeeded


def test_cleanup_failure_retains_marker_and_releases_lock(root):
    with pytest.raises(SessionError, match='unsupported entry'):
        with module.StableCredentialHome(root) as lease:
            assets(lease)
            os.mkfifo(lease.home / 'unsupported')
            lease.confirm_stopped()
    assert (root / 'attempt.json').exists()
    assert not lease.cleanup_succeeded
    with pytest.raises(SessionError, match='stale or unowned'):
        with module.StableCredentialHome(root):
            pytest.fail('failed cleanup admitted')


@pytest.mark.parametrize('change', ['marker', 'lock', 'root_entry'])
def test_changed_control_state_is_not_removed_as_successful_cleanup(root, change):
    with pytest.raises(SessionError):
        with module.StableCredentialHome(root) as lease:
            assets(lease)
            if change == 'marker':
                (root / 'attempt.json').write_text('ALTERED_CONTROL')
            elif change == 'lock':
                (root / '.lock').unlink()
                (root / '.lock').touch(mode=0o600)
            else:
                (root / 'unowned').write_text('PRESERVE')
            lease.confirm_stopped()
    assert (root / 'attempt.json').exists()
    assert (root / 'codex-home' / 'config.toml').exists()
    assert not lease.cleanup_succeeded

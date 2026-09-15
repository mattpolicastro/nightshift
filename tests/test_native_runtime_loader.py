"""Protected runtime loading fixtures; account strings are synthetic, never logged."""
import json
import os
from dataclasses import asdict

import pytest

from nightshift import native_runtime_loader as module
from nightshift.native_dispatch import _NativeRuntime
from test_managed_task import values


@pytest.fixture
def records(values, tmp_path):
    from types import SimpleNamespace
    manifest_root = tmp_path / 'manifest'
    identity_root = tmp_path / 'identities'
    worktree_root = tmp_path / 'worktrees'
    for path in (manifest_root, identity_root, worktree_root): path.mkdir(mode=0o700)
    manifest = {key: str(value) if hasattr(value, 'as_posix') else value
                for key, value in values.items() if key != 'phases'}
    manifest.update(version=1, worktree_root=str(worktree_root), phases={
        name: {**phase, 'billing': 'subscription', 'budgets': asdict(phase['budgets'])}
        for name, phase in values['phases'].items()})
    path = manifest_root / 'runtime.json'
    identity = identity_root / (manifest['identity_reference'] + '.json')
    identity.write_text(json.dumps({'email': 'SYNTHETIC_PRIVATE_EMAIL@example.invalid',
                                    'account_id': 'SYNTHETIC_PRIVATE_ACCOUNT'}))
    identity.chmod(0o600)
    def write():
        path.write_text(json.dumps(manifest))
        path.chmod(0o600)
    write()
    return SimpleNamespace(manifest=manifest, path=path, identity=identity, identity_root=identity_root,
        write=write, load=lambda: module._load_managed_runtime(path, identity_root=identity_root))


def test_exact_manifest_resolves_separate_identity_without_activation(records, monkeypatch, capsys):
    monkeypatch.setenv('NIGHTSHIFT_NATIVE_ENABLED', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'SYNTHETIC_KEY_SHOULD_NOT_APPEAR')
    monkeypatch.setenv('CODEX_HOME', '/untrusted/profile')
    runtime = records.load()
    assert type(runtime) is _NativeRuntime and runtime.execution_enabled is False
    assert runtime.identity.matches_account('SYNTHETIC_PRIVATE_ACCOUNT')
    assert runtime.profile.implement.budgets.max_tool_calls == records.manifest['phases']['implement']['budgets']['max_tool_calls']
    assert runtime.profile.review.reasoning_effort == 'high'
    assert not capsys.readouterr().out
    assert 'SYNTHETIC' not in repr(runtime)


@pytest.mark.parametrize('change', [
    {'version': True}, {'version': 2}, {'enabled': True}, {'api_key': 'SYNTHETIC_SECRET'},
    {'base_url': 'https://custom.invalid'}, {'identity_reference': '../../escape'},
    {'identity_reference': 'missing'}, {'credential_root': []}])
def test_unknown_fields_versions_paths_or_references_fail_closed(records, change):
    records.manifest.update(change)
    records.write()
    with pytest.raises(Exception) as error: records.load()
    assert str(error.value) == 'Protected managed runtime configuration rejected'
    assert 'SYNTHETIC' not in str(error.value)


@pytest.mark.parametrize('field,value', [('auth', 'api_key'), ('provider', 'custom'),
    ('driver', 'claude-code'), ('billing', 'metered'), ('fallback', True)])
def test_both_phases_must_be_exact_managed_subscription(records, field, value):
    records.manifest['phases']['review'][field] = value
    records.write()
    with pytest.raises(Exception): records.load()


@pytest.mark.parametrize('kind', ['boolean', 'negative', 'unknown', 'missing', 'infinity'])
def test_budgets_are_explicit_exact_and_finite(records, kind):
    budgets = records.manifest['phases']['implement']['budgets']
    if kind == 'boolean': budgets['max_tool_calls'] = True
    elif kind == 'negative': budgets['max_runtime_s'] = -1
    elif kind == 'unknown': budgets['extra'] = 1
    elif kind == 'missing': del budgets['max_stream_bytes']
    else: budgets['max_runtime_s'] = float('inf')
    records.write()
    with pytest.raises(Exception): records.load()


@pytest.mark.parametrize('target', ['path', 'identity'])
@pytest.mark.parametrize('kind', ['readable', 'symlink', 'hardlink', 'fifo', 'duplicate', 'oversized'])
def test_record_permissions_type_size_and_duplicate_fields(records, tmp_path, target, kind):
    path = getattr(records, target)
    if kind == 'readable': path.chmod(0o644)
    elif kind in {'symlink', 'hardlink'}:
        saved = tmp_path / 'saved'
        path.rename(saved)
        if kind == 'symlink': path.symlink_to(saved)
        else: os.link(saved, path)
    elif kind == 'fifo': path.unlink(); os.mkfifo(path, 0o600)
    elif kind == 'oversized': path.write_bytes(b'x' * 65537)
    else:
        key = 'email' if target == 'identity' else 'version'
        text = path.read_text()
        path.write_text(text[:-1] + ',' + json.dumps(key) + ':null}')
    with pytest.raises(Exception): records.load()


def test_identity_schema_accepts_only_existing_email_account_fields(records):
    records.identity.write_text(json.dumps({'email': 'synthetic@example.invalid', 'account_id': 'synthetic',
                                          'access_token': 'SYNTHETIC_SECRET'}))
    with pytest.raises(Exception) as error: records.load()
    assert 'SYNTHETIC_SECRET' not in str(error.value)


def test_record_replaced_during_read_is_rejected(records, monkeypatch):
    original = module.os.read
    changed = []
    def replaced(fd, count):
        result = original(fd, count)
        if not changed:
            changed.append(True)
            records.path.unlink()
            records.write()
        return result
    monkeypatch.setattr(module.os, 'read', replaced)
    with pytest.raises(Exception): records.load()


def test_unprotected_parent_and_namespace_overlap_are_rejected(records):
    records.path.parent.chmod(0o755)
    with pytest.raises(Exception): records.load()
    records.path.parent.chmod(0o700)
    records.manifest['worktree_root'] = records.manifest['credential_root']
    records.write()
    with pytest.raises(Exception): records.load()


def test_public_constructor_and_loader_never_touch_private_paths(monkeypatch):
    def forbidden(*args): pytest.fail('public load reached disk')
    monkeypatch.setattr(module, '_record', forbidden)
    with pytest.raises(Exception): module.ManagedRuntimeLoader(enable=True)
    with pytest.raises(Exception): module.ManagedRuntimeLoader.load(force=True, enabled=True)


@pytest.mark.parametrize('field', sorted(module._MANIFEST))
def test_every_manifest_field_is_required(records, field):
    del records.manifest[field]
    records.write()
    with pytest.raises(Exception): records.load()


@pytest.mark.parametrize('field,value', [('phases', []), ('identity_reference', True),
    ('worktree_root', 1), ('binary_version', 1534), ('docker_host', {}),
    ('implementation_image_id', None), ('review_image_id', 1), ('verification_image_id', True)])
def test_manifest_field_type_mismatches_are_rejected(records, field, value):
    records.manifest[field] = value
    records.write()
    with pytest.raises(Exception): records.load()


@pytest.mark.parametrize('field', ['email', 'account_id'])
@pytest.mark.parametrize('value', ['', ' ', ' padded ', 'x'*513, '\ud800', True, None, 'private\nvalue'])
def test_identity_value_bounds_and_types_do_not_leak(records, field, value, capsys):
    record = json.loads(records.identity.read_text())
    record[field] = value
    records.identity.write_text(json.dumps(record))
    with pytest.raises(Exception) as error: records.load()
    assert str(error.value) == 'Protected managed runtime configuration rejected'
    assert str(records.path) not in str(error.value) and str(records.identity) not in str(error.value)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


def test_identity_file_replacement_during_read_rejected(records, monkeypatch):
    original = module.os.read
    inode = records.identity.stat().st_ino
    replaced = []
    def read(fd, count):
        result = original(fd, count)
        if not replaced and os.fstat(fd).st_ino == inode:
            replaced.append(True)
            content = records.identity.read_bytes()
            records.identity.unlink()
            records.identity.write_bytes(content)
            records.identity.chmod(0o600)
        return result
    monkeypatch.setattr(module.os, 'read', read)
    with pytest.raises(Exception): records.load()
    assert replaced


@pytest.mark.parametrize('target', ['path', 'identity'])
@pytest.mark.parametrize('change', ['symlink', 'mode', 'replacement'])
def test_parent_path_or_permissions_change_during_read_rejected(records, monkeypatch, target, change):
    path = getattr(records, target)
    inode = path.stat().st_ino
    original = module.os.read
    changed = []
    def read(fd, count):
        result = original(fd, count)
        if not changed and os.fstat(fd).st_ino == inode:
            changed.append(True)
            if change == 'mode': path.parent.chmod(0o755)
            else:
                renamed = path.parent.with_name(path.parent.name + '-replaced')
                path.parent.rename(renamed)
                if change == 'symlink':
                    path.parent.symlink_to(renamed, target_is_directory=True)
                else:
                    path.parent.mkdir(mode=0o700)
                    path.write_bytes((renamed/path.name).read_bytes())
                    path.chmod(0o600)
        return result
    monkeypatch.setattr(module.os, 'read', read)
    with pytest.raises(Exception): records.load()
    assert changed


def test_exact_loaded_bindings_ignore_ambient_provider_settings(records, monkeypatch):
    for key in ('OPENAI_BASE_URL', 'ANTHROPIC_API_KEY', 'NIGHTSHIFT_ENABLE_NATIVE', 'HOME'):
        monkeypatch.setenv(key, 'SYNTHETIC_AMBIENT_IGNORED')
    runtime = records.load()
    for field in ('binary_version', 'implementation_image_id', 'review_image_id',
                  'verification_image_id', 'docker_host', 'identity_reference'):
        assert getattr(runtime.profile, field) == records.manifest[field]
    for field in ('credential_root', 'binary', 'recovery_root'):
        assert str(getattr(runtime.profile, field)) == records.manifest[field]
    assert str(runtime.worktree_root) == records.manifest['worktree_root']
    for phase in ('implement', 'review'):
        expected = records.manifest['phases'][phase]
        observed = getattr(runtime.profile, phase)
        assert observed.model == expected['model']
        assert observed.reasoning_effort == expected['reasoning_effort']
        assert asdict(observed.budgets) == expected['budgets']
    assert 'SYNTHETIC_AMBIENT' not in repr(runtime)

"""Dormant profile/task validation; no provider process or credential reads."""
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from nightshift import queue
from nightshift.config import Repo
from nightshift.queue import Claim, Issue
from nightshift.workers import managed_task as module
from nightshift.workers.base import WorkerBudgets
from nightshift.workers.chatgpt_admission import ChatGPTIdentity
from nightshift.workers.review_context import ReviewPolicy


@pytest.fixture
def values(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    credential = tmp_path / 'credentials'
    recovery = tmp_path / 'recovery'
    credential.mkdir(mode=0o700)
    recovery.mkdir(mode=0o700)
    binary = tmp_path / 'codex'
    binary.write_text('synthetic executable; never run')
    binary.chmod(0o700)
    # Short path for macOS's Unix-domain socket length restriction.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='ns-profile-') as temporary:
        path = Path(temporary).resolve() / 'docker.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.bind(str(path))
            path.chmod(0o600)
            phase = {'driver': 'codex-app-server', 'auth': 'chatgpt', 'provider': 'openai',
                     'model': 'explicit-model', 'reasoning_effort': 'medium', 'budgets': WorkerBudgets()}
            yield dict(credential_root=credential, recovery_root=recovery, binary=binary,
                binary_version='0.153.4', identity_reference='operator-account',
                implementation_image_id='sha256:' + 'a' * 64, review_image_id='sha256:' + 'b' * 64,
                verification_image_id='sha256:' + 'c' * 64, docker_host='unix://' + str(path),
                phases={'implement': dict(phase), 'review': {**phase, 'model': 'review-model', 'reasoning_effort': 'high'}})


def inputs(profile, **changes):
    worktree = profile.recovery_root.parent / 'worktree'
    worktree.mkdir(exist_ok=True)
    claim = Claim('example/repo', 1, 'candidate/1', str(worktree), 'fixture')
    claim.write()
    claim.path.chmod(0o600)
    kwargs = dict(identity_reference='operator-account', identity=ChatGPTIdentity('private@example.invalid', 'private-workspace'),
        issue=Issue('example/repo', 1, 'Repair', 'Make the approved change.'), repo=Repo('example/repo', 'true'),
        claim=claim, review_policy=ReviewPolicy('Check correctness'))
    kwargs.update(changes)
    return module._build_inputs(profile, **kwargs)


def test_validated_inputs_remain_dormant_and_bounded(values):
    profile = module._validated_profile(**values)
    task = inputs(profile)
    assert not profile.execution_enabled and not task.execution_enabled
    assert task.request.cwd == Path('/workspace') and task.request.role == 'implement'
    assert task.request.reasoning_effort == 'medium' and profile.review.reasoning_effort == 'high'
    assert task.request.transcript_path is task.request.normalized_transcript_path is None
    payload = json.loads(task.request.prompt.split('\n', 1)[1])
    assert payload['approved_task']['task_id'] == 'example/repo#1'
    assert 'git worktree' not in task.request.prompt and 'Commit your work locally' not in task.request.prompt
    assert 'private@example.invalid' not in repr(task) and 'private-workspace' not in task.request.prompt
    with pytest.raises(Exception): module.ManagedNativeProfile(**values)


@pytest.mark.parametrize('change', [
    {'driver': 'claude-code'}, {'auth': 'api_key'}, {'provider': 'custom'},
    {'base_url': 'https://example.invalid'}, {'fallback': True}, {'api_key': 'synthetic'},
    {'model': ''}, {'reasoning_effort': 'automatic'}, {'budgets': False}])
def test_no_mixed_driver_custom_key_or_fallback_phase(values, change):
    values['phases']['review'].update(change)
    with pytest.raises(ValueError): module._validated_profile(**values)


@pytest.mark.parametrize('field,value', [('binary_version', 'latest'), ('review_image_id', 'image:latest'),
    ('docker_host', 'tcp://127.0.0.1:2375'), ('identity_reference', '/private/identity'),
    ('identity_reference', 'private@example.invalid')])
def test_ambiguous_profile_values_rejected(values, field, value):
    values[field] = value
    with pytest.raises(ValueError): module._validated_profile(**values)


def test_namespace_permissions_and_overlap_rejected(values):
    values['credential_root'].chmod(0o755)
    with pytest.raises(ValueError): module._validated_profile(**values)
    values['credential_root'].chmod(0o700)
    values['recovery_root'] = values['credential_root']
    with pytest.raises(ValueError): module._validated_profile(**values)


def test_reference_and_claim_identity_must_match(values):
    profile = module._validated_profile(**values)
    with pytest.raises(ValueError): inputs(profile, identity_reference='other')
    with pytest.raises(ValueError): inputs(profile, issue=Issue('other/repo', 1, 'Repair', 'Body'))
    with pytest.raises(ValueError): inputs(profile, claim=Claim('example/repo', 2, 'b', '/w', 'now'))
    with pytest.raises(ValueError): inputs(profile, issue=Issue('example/repo', 1, 'Repair', 'Body', revise=True))


def test_source_budget_rejects_oversized_issue(values):
    with pytest.raises(ValueError):
        inputs(module._validated_profile(**values), issue=Issue('example/repo', 1, 'Repair', 'x' * 32769))


@pytest.mark.parametrize('field,value', [('phase', 'implementing'), ('native_recovery', {}),
    ('revise', True), ('revise', 0), ('worktree', 'relative/worktree')])
def test_only_fresh_canonical_unprepared_claims_build_inputs(values, field, value):
    profile = module._validated_profile(**values)
    worktree = profile.recovery_root.parent / 'worktree'
    worktree.mkdir(exist_ok=True)
    claim = Claim('example/repo', 1, 'candidate/1', str(worktree), 'fixture')
    claim = replace(claim, **{field: value})
    with pytest.raises(ValueError): inputs(profile, claim=claim)


@pytest.mark.parametrize('kind', ['binary', 'credential_root', 'recovery_root', 'docker_host'])
def test_profile_rejects_replaceable_path_ancestors(values, tmp_path, kind):
    parent = tmp_path / 'replaceable'
    parent.mkdir()
    parent.chmod(0o777)
    if kind == 'docker_host':
        # No new socket is needed: move the fixture socket beneath unsafe parent.
        original = Path(values[kind][7:])
        target = parent / 'docker.sock'
        original.rename(target)
        values[kind] = 'unix://' + str(target)
    else:
        original = values[kind]
        target = parent / original.name
        original.rename(target)
        values[kind] = target
    with pytest.raises(ValueError, match='ancestor'):
        module._validated_profile(**values)


def test_nested_writable_ancestor_is_not_hidden_by_private_direct_parent(values, tmp_path):
    unsafe = tmp_path / 'unsafe'
    unsafe.mkdir()
    unsafe.chmod(0o777)
    private = unsafe / 'private'
    private.mkdir(mode=0o700)
    target = private / 'codex'
    values['binary'].rename(target)
    values['binary'] = target
    with pytest.raises(ValueError, match='ancestor'):
        module._validated_profile(**values)


def test_owned_private_directory_under_standard_sticky_tmp_is_supported(values):
    import tempfile
    with tempfile.TemporaryDirectory(prefix='ns-owned-', dir='/tmp') as temporary:
        root = Path(temporary).resolve()
        binary = root / 'codex'
        binary.write_text('synthetic executable; never run')
        binary.chmod(0o700)
        values['binary'] = binary
        assert module._validated_profile(**values).binary == binary


@pytest.mark.parametrize('kind', ['duplicate', 'nan', 'bool-number', 'float-number', 'int-revise', 'empty'])
def test_persisted_claim_shape_is_exact(values, monkeypatch, kind):
    profile = module._validated_profile(**values)
    original = module.os.read
    def malformed(descriptor, size):
        data = original(descriptor, size)
        record = json.loads(data)
        if kind == 'duplicate':
            return b'{"number":1,' + data[1:]
        if kind == 'nan':
            return data.replace(b'"number": 1', b'"number": NaN')
        if kind == 'bool-number': record['number'] = True
        if kind == 'float-number': record['number'] = 1.0
        if kind == 'int-revise': record['revise'] = 0
        if kind == 'empty': return b''
        return json.dumps(record).encode()
    monkeypatch.setattr(module.os, 'read', malformed)
    with pytest.raises(ValueError): inputs(profile)


def test_in_memory_claim_number_cannot_be_boolean_alias(values):
    profile = module._validated_profile(**values)
    worktree = profile.recovery_root.parent / 'worktree'
    worktree.mkdir()
    claim = Claim('example/repo', True, 'candidate/1', str(worktree), 'fixture')
    with pytest.raises(ValueError): inputs(profile, claim=claim)


def test_claim_path_replaced_during_read_is_rejected(values, monkeypatch):
    profile = module._validated_profile(**values)
    original = module.os.read
    def replaced(descriptor, size):
        data = original(descriptor, size)
        path = queue.claim_path('example/repo', 1)
        temporary = path.with_suffix('.replacement')
        temporary.write_bytes(data)
        temporary.chmod(0o600)
        temporary.replace(path)
        return data
    monkeypatch.setattr(module.os, 'read', replaced)
    with pytest.raises(ValueError, match='changed while read'): inputs(profile)

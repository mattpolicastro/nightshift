"""Dormant dispatch routing and recovery gates; no live provider or GitHub calls."""
import asyncio
from types import SimpleNamespace

import pytest

from nightshift import native_dispatch as module, task, vcs, queue
from nightshift.workers.native_task_lane import _RetainedTask
from test_native_task_lane import harness, values, repository


@pytest.fixture
def configured(harness):
    from nightshift.workers.chatgpt_admission import ChatGPTIdentity
    from nightshift.workers.review_context import ReviewPolicy
    runtime = module._validated_runtime(harness.profile, identity_reference='operator-account',
        identity=ChatGPTIdentity('private@example.invalid', 'private-workspace'), worktree_root=harness.root)
    call = lambda: module._dispatch_claimed(runtime, issue=harness.issue, repo=harness.repo,
        claim=harness.claim, repository=harness.repository, review_policy=ReviewPolicy('Check correctness'))
    return harness, runtime, call


def test_three_way_routing_never_turns_native_misconfiguration_into_legacy(configured):
    state, runtime, _ = configured
    assert module._dispatch_route('claude-code') is module._Route.LEGACY
    assert module._dispatch_route('codex-app-server', runtime) is module._Route.MANAGED_NATIVE
    for driver, value in [('codex-app-server', None), ('codex-app-server', {}),
                          ('codex-app-server', True), ('unknown', runtime), ('claude-code', runtime)]:
        assert module._dispatch_route(driver, value) is module._Route.BLOCKED


def test_public_dispatch_cannot_activate_with_typed_runtime_or_flags(configured, monkeypatch):
    _, runtime, _ = configured
    async def forbidden(*args, **kwargs): pytest.fail('public dispatch delegated')
    monkeypatch.setattr(module, '_dispatch_claimed', forbidden)
    for kwargs in ({}, {'runtime': runtime}, {'enable': True, 'force': True, 'runtime': runtime}):
        result = module.NativeDispatch().run(**kwargs)
        assert result.status == 'blocked' and not result.ready_for_shipping
    with pytest.raises(Exception): module._NativeRuntime()


def test_real_private_dispatch_to_owned_lane_keeps_candidate_and_never_ships(configured):
    state, runtime, call = configured
    result = asyncio.run(call())
    assert result.status == 'retained' and result.stage == 'complete'
    assert result.result.status == 'reviewed_pending_human' and not result.ready_for_shipping
    assert state.lock.exists() and state.worktree.exists()
    # Imported lane fixture forbids all task.run/release/remove/push/PR paths.
    assert state.calls == [('fetch', 'main'), ('implement', 'explicit-model'), ('review', 'review-model')]


@pytest.mark.parametrize('kind', ['orphan', 'unidentified', 'unsafe-root', 'native-marker', 'changed-claim'])
def test_recovery_blocks_before_lane_or_any_remote_call(configured, monkeypatch, kind):
    state, _, call = configured
    if kind == 'orphan': state.lock.write_text('')
    elif kind == 'unidentified':
        (state.claim.path.parent / 'example__repo#0009.json.native.lock').write_text('')
    elif kind == 'unsafe-root': state.claim.path.parent.chmod(0o777)
    elif kind == 'native-marker':
        state.claim._prepare_native('a' * 32, state.profile.recovery_root)
    else:
        state.claim.started_at = 'caller changed'
    async def forbidden(*args, **kwargs): pytest.fail('recovery evidence reached lane')
    monkeypatch.setattr(module, '_run_claimed_native', forbidden)
    result = asyncio.run(call())
    assert result.status in {'blocked', 'retained'} and not state.calls
    assert not state.worktree.exists()


def test_stale_runtime_cannot_fall_back_or_reach_lane(configured, monkeypatch):
    state, _, call = configured
    state.profile.credential_root.chmod(0o755)
    async def forbidden(*args, **kwargs): pytest.fail('stale runtime delegated')
    monkeypatch.setattr(module, '_run_claimed_native', forbidden)
    assert asyncio.run(call()).status == 'blocked'
    assert not state.calls


def test_untyped_runtime_has_no_field_or_boolean_hooks(configured):
    from nightshift.workers.review_context import ReviewPolicy
    state, _, _ = configured
    class Forged:
        def __bool__(self): pytest.fail('boolean hook executed')
        def __getattr__(self, name): pytest.fail('field hook executed')
    result = asyncio.run(module._dispatch_claimed(Forged(), issue=state.issue, repo=state.repo,
        claim=state.claim, repository=state.repository, review_policy=ReviewPolicy('Check correctness')))
    assert result.status == 'blocked' and not state.calls


def test_changed_persisted_claim_is_not_replaced_by_callers_stale_copy(configured, monkeypatch):
    import json
    state, _, call = configured
    data = json.loads(state.claim.path.read_text())
    data['started_at'] = 'different persisted owner'
    state.claim.path.write_text(json.dumps(data))
    async def forbidden(*args, **kwargs): pytest.fail('stale claim delegated')
    monkeypatch.setattr(module, '_run_claimed_native', forbidden)
    assert asyncio.run(call()).stage == 'claim_changed'
    assert not state.calls


def test_forged_profile_and_declared_version_cannot_build_runtime(configured):
    from dataclasses import replace
    state, runtime, _ = configured
    for profile in (SimpleNamespace(**state.profile.__dict__), replace(state.profile, binary_version='latest')):
        with pytest.raises(ValueError):
            module._validated_runtime(profile, identity_reference=runtime.identity_reference,
                identity=runtime.identity, worktree_root=runtime.worktree_root)


def test_environment_flags_never_enable_public_dispatch(configured, monkeypatch):
    _, runtime, _ = configured
    for key in ('NIGHTSHIFT_NATIVE_ENABLED', 'NIGHTSHIFT_ENABLE_NATIVE', 'CODEX_HOME',
                'OPENAI_API_KEY', 'OPENAI_BASE_URL'):
        monkeypatch.setenv(key, 'synthetic-enable-or-secret')
    result = module.NativeDispatch().run(runtime=runtime)
    assert result.status == 'blocked' and not result.ready_for_shipping
    assert 'synthetic-enable-or-secret' not in repr(result)


def test_cancellation_propagates_after_real_lane_retains_owned_state(configured):
    from nightshift import outcomes
    state, _, call = configured
    state.failure = 'cancel'
    with pytest.raises(asyncio.CancelledError): asyncio.run(call())
    assert state.lock.exists() and state.worktree.exists() and state.claim.path.exists()
    assert outcomes.tasks()[0]['native_retained']


def test_ambiguous_lane_exception_never_becomes_legacy_or_plain_validation_failure(configured, monkeypatch):
    _, _, call = configured
    async def failed(*args, **kwargs): raise RuntimeError('synthetic private diagnostic')
    monkeypatch.setattr(module, '_run_claimed_native', failed)
    result = asyncio.run(call())
    assert result.status == 'retained' and result.stage == 'native_handoff_ambiguous'
    assert 'synthetic private diagnostic' not in repr(result)


@pytest.mark.parametrize('returned', [None, {}, True, SimpleNamespace(status='retained', ready_for_shipping=True)])
def test_untyped_lane_result_is_retained_without_fallback(configured, monkeypatch, returned):
    state, _, call = configured
    async def malformed(*args, **kwargs): return returned
    monkeypatch.setattr(module, '_run_claimed_native', malformed)
    result = asyncio.run(call())
    assert type(result) is _RetainedTask
    assert result.status == 'retained' and result.stage == 'native_handoff_ambiguous'
    assert result.ready_for_shipping is False
    assert state.calls == []


def test_retained_recovery_precedes_runtime_filesystem_revalidation(configured, monkeypatch):
    state, _, call = configured
    state.lock.write_text('')
    def forbidden(*args, **kwargs): pytest.fail('retained evidence reached runtime filesystem validation')
    monkeypatch.setattr(module, '_validated_runtime', forbidden)
    result = asyncio.run(call())
    assert result.status == 'retained' and result.stage == 'recovery_required'
    assert not state.calls


@pytest.mark.parametrize('driver', ['claude-code', 'custom', 'openai', None, True])
def test_private_native_input_cannot_route_to_other_driver(configured, driver):
    from nightshift.workers.review_context import ReviewPolicy
    state, runtime, _ = configured
    result = asyncio.run(module._dispatch_claimed(runtime, issue=state.issue, repo=state.repo,
        claim=state.claim, repository=state.repository, review_policy=ReviewPolicy('Check correctness'), driver=driver))
    assert result.status == 'blocked' and result.ready_for_shipping is False
    assert not state.calls and not state.lock.exists() and not state.worktree.exists()


@pytest.mark.parametrize('protected_name', ['credential_root', 'recovery_root'])
@pytest.mark.parametrize('relation', ['equal', 'worktree-descendant', 'worktree-ancestor'])
def test_worktree_root_must_be_disjoint_from_protected_namespaces(
        configured, monkeypatch, protected_name, relation):
    state, runtime, _ = configured
    protected = getattr(state.profile, protected_name)
    if relation == 'equal': worktree_root = protected
    elif relation == 'worktree-descendant':
        worktree_root = protected / 'worktrees'
        worktree_root.mkdir(mode=0o700)
    else: worktree_root = protected.parent
    async def forbidden(*args, **kwargs): pytest.fail('overlapping profile reached lane')
    monkeypatch.setattr(module, '_run_claimed_native', forbidden)
    with pytest.raises(ValueError, match='disjoint'):
        module._validated_runtime(state.profile, identity_reference=runtime.identity_reference,
            identity=runtime.identity, worktree_root=worktree_root)
    assert not state.calls and not state.lock.exists() and not state.worktree.exists()


def test_dispatch_revalidates_namespace_separation_before_ownership(configured, monkeypatch):
    state, runtime, call = configured
    object.__setattr__(runtime, 'worktree_root', state.profile.credential_root)
    async def forbidden(*args, **kwargs): pytest.fail('stale overlapping runtime delegated')
    monkeypatch.setattr(module, '_run_claimed_native', forbidden)
    result = asyncio.run(call())
    assert result.status == 'blocked' and result.stage == 'native_validation_failed'
    assert not state.calls and not state.lock.exists() and not state.worktree.exists()

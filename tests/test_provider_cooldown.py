"""Cooldown and resume recovery with persisted runs and a deterministic clock."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from specloop import cli, loop, serve
from specloop.state import Run
from test_provider_retry_accounting import A, B, OTHER, TASK, context, restart, result


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(loop.time, "time", lambda: now[0])
    monkeypatch.setattr(loop.quota, "resume_at", lambda limits: now[0] + 100)
    return context(tmp_path, limit=1), now


@pytest.mark.scenario("provider-fallback/cooldown-eligibility/unavailable-profile-returns")
def test_unavailable_profile_returns_after_cooldown(recovery):
    ctx, now = recovery
    result(ctx, A, task=OTHER)
    result(ctx, A)
    result(ctx, A)
    assert ctx.run.state.exhausted[A.name] == now[0] + loop.INFRA_COOLDOWN
    result(ctx, B)
    deadline = ctx.run.state.exhausted[A.name]
    ctx = restart(ctx)
    now[0] = deadline - 1
    assert loop._pick(ctx, "implement") == B
    assert loop._infra_retries(ctx).count(ctx, TASK.key, A.name, "implement") == 2
    now[0] = deadline
    assert loop._pick(ctx, "implement") == A
    assert A.name not in Run(ctx.run.dir).state.exhausted
    # Both tasks get a new A allowance; B's partial allowance survives expiry/reload.
    ctx = restart(ctx)
    assert result(ctx, A).infra == 1
    assert result(ctx, A, task=OTHER).infra == 1
    result(ctx, B)
    assert loop._infra_retries(ctx).count(ctx, TASK.key, B.name, "implement") == 2
    assert B.name in ctx.run.state.exhausted
    assert ctx.run.state.status == "running"
    assert all(ts.attempts == 0 for ts in ctx.run.state.tasks.values())


@pytest.mark.scenario("provider-fallback/cooldown-eligibility/other-roles-and-restart-honor-availability")
def test_active_deadline_is_shared_across_roles_and_reloads(recovery):
    ctx, now = recovery
    ctx.m.chains["gate"] = (B,)
    result(ctx, A)
    result(ctx, A)
    deadline = ctx.run.state.exhausted[A.name]
    for _ in range(2):
        ctx = restart(ctx)
        for role in ("implement", "review", "gate"):
            assert loop._pick(ctx, role) == B
        assert Run(ctx.run.dir).state.exhausted == {A.name: deadline}
        assert loop._infra_retries(ctx).count(ctx, TASK.key, A.name, "review") == 2
        now[0] = deadline - 1
    now[0] = deadline
    assert loop._pick(ctx, "review") == A
    assert loop._pick(ctx, "gate") == B
    assert result(restart(ctx), A, role="review").infra == 1


@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/restart-retains-partial-allowance")
@pytest.mark.parametrize("single,previous,expected", [
    (True, None, 2), (False, None, 1), (False, "a", 2), (False, "b", 1),
])
def test_reload_retains_partial_and_legacy_allowances(tmp_path, single, previous, expected):
    ctx = context(tmp_path, single=single, limit=4)
    ctx.run.state.task(TASK.key).infra = 1
    if previous:
        ctx.run.event("reviewed", task=TASK.key, profile=previous)
    ctx = restart(ctx)
    assert result(ctx, A).infra == expected
    ctx = restart(ctx)
    assert result(ctx, A, role="review").infra == expected + 1
    if not single:
        assert result(ctx, B).infra == (2 if previous == "b" else 1)
    assert ctx.run.state.status == "running"


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/quota-only-behavior-remains-intact")
@pytest.mark.parametrize("history", ["legacy", "expired", "superseded"])
def test_quota_only_reloads_ignore_stale_causes_and_remain_uncapped(recovery, history):
    ctx, now = recovery
    ctx.run.state.task(TASK.key).auto_resumes = 2
    for _ in range(3):
        deadline = now[0] + 100
        ctx.run.state.exhausted = {A.name: deadline, B.name: deadline + 50, "other": now[0] + 1}
        if history != "legacy":
            ctx.run.event("unavailable", profile=A.name, until=now[0] - 1, detail="expired")
            if history == "superseded":
                ctx.run.event("unavailable", profile=A.name, until=deadline, detail="old cause")
            ctx.run.event("exhausted", profile=A.name, until=deadline)
            # Append order alone is insufficient: this event describes a different block.
            ctx.run.event("unavailable", profile=A.name, until=deadline + 500, detail="stale")
        ctx = restart(ctx)
        assert loop._pick(ctx, "implement") is None
        loop._all_exhausted(ctx, TASK, "implement")
        pause = ctx.run.state.pause
        assert pause.reason == "usage_limit" and pause.resume_at == deadline
        assert "usage-limited (a, b)" in pause.detail
        assert not serve.lift_due(ctx.run, deadline - 1, lambda message: None)
        now[0] = deadline
        assert serve.lift_due(ctx.run, now[0], lambda message: None)
        assert ctx.run.state.task(TASK.key).auto_resumes == 2
        assert loop._pick(restart(ctx), "implement") == A


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/mixed-blocks-retain-bounded-recovery")
@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/quota-only-behavior-remains-intact")
@pytest.mark.parametrize("latest,reason", [("exhausted", "usage_limit"), ("unavailable", "infra")])
def test_latest_matching_deadline_determines_cause(recovery, latest, reason):
    ctx, now = recovery
    deadline = now[0] + 100
    ctx.run.state.exhausted = {A.name: deadline, B.name: deadline + 50}
    earlier = "unavailable" if latest == "exhausted" else "exhausted"
    ctx.run.event(earlier, profile=A.name, until=deadline, detail="superseded", at=9999)
    ctx.run.event(latest, profile=A.name, until=deadline, detail="current", at=1)
    ctx.run.event(earlier, profile=A.name, until=now[0] - 1, detail="expired")
    ctx = restart(ctx)
    loop._all_exhausted(ctx, TASK, "implement")
    assert ctx.run.state.pause.reason == reason
    assert ctx.run.state.pause.resume_at == deadline
    assert "superseded" not in ctx.run.state.pause.detail
    assert "expired" not in ctx.run.state.pause.detail
    if reason == "infra":
        assert "a unavailable: current" in ctx.run.state.pause.detail
        assert "b usage-limited" in ctx.run.state.pause.detail


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/quota-only-behavior-remains-intact")
def test_new_quota_block_after_completed_unavailable_cooldown(recovery):
    ctx, now = recovery
    result(ctx, A)
    result(ctx, A)
    now[0] = ctx.run.state.exhausted[A.name]
    ctx = restart(ctx)
    assert loop._pick(ctx, "implement") == A
    ctx = restart(ctx)
    result(ctx, A, disposition="usage_limit")
    result(ctx, B, disposition="usage_limit")
    ctx = restart(ctx)
    loop._all_exhausted(ctx, TASK, "implement")
    assert ctx.run.state.pause.reason == "usage_limit"
    assert ctx.run.state.pause.resume_at == now[0] + 100
    assert loop._infra_retries(ctx).count(ctx, TASK.key, A.name, "implement") == 0


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/mixed-blocks-retain-bounded-recovery")
@pytest.mark.parametrize("quota_profile", [A, B])
def test_mixed_chain_reloads_keep_earliest_deadline_and_two_lift_cap(recovery, quota_profile):
    ctx, now = recovery
    ctx.m = replace(ctx.m, max_infra=0)
    offline = B if quota_profile == A else A
    for cycle in range(3):
        result(ctx, quota_profile, disposition="usage_limit")
        result(ctx, offline)
        ctx = restart(ctx)
        assert loop._pick(ctx, "review") is None
        loop._all_exhausted(ctx, TASK, "review")
        pause = ctx.run.state.pause
        assert pause.reason == "infra" and pause.resume_at == now[0] + 100
        assert f"{quota_profile.name} usage-limited" in pause.detail
        assert f"{offline.name} unavailable: offline" in pause.detail
        now[0] = pause.resume_at
        assert serve.lift_due(ctx.run, now[0], lambda message: None) == (cycle < 2)
        ctx = restart(ctx)
        assert loop._pick(ctx, "implement") == quota_profile
        assert ctx.run.state.exhausted == {offline.name: now[0] + 800}
        assert ctx.run.state.task(TASK.key).auto_resumes == min(cycle + 1, 2)
        now[0] += 800
        assert loop._pick(ctx, "implement") == A
    assert ctx.run.state.status == "paused"
    assert not serve.lift_due(ctx.run, now[0], lambda message: None)
    assert loop.run(ctx) == "paused"  # Eligibility alone cannot restart execution.


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/manual-resume-preserves-single-profile-recovery")
@pytest.mark.parametrize("single,quota_replaced", [(True, False), (False, False), (True, True)])
def test_early_manual_resume_reconciles_at_driver_entry(recovery, monkeypatch, single, quota_replaced):
    ctx, now = recovery
    ctx.m = replace(ctx.m, max_infra=0)
    ctx.m.chains["implement"] = (A,) if single else (A, B)
    ts = ctx.run.state.task(TASK.key)
    ts.auto_resumes = 2
    # Another task's allowance must survive a manual resume of this task.
    loop._infra_retries(ctx).record(ctx, OTHER.key, A.name, "review", 1)
    result(ctx, A)
    if not single:
        result(ctx, B)
    assert ctx.run.state.pause.reason == "infra"
    deadlines = dict(ctx.run.state.exhausted)
    if quota_replaced:
        # A newer quota cause may reuse the unavailable deadline before resume.
        ctx.run.event("exhausted", profile=A.name, until=deadlines[A.name])
    ctx.run.state.exhausted["unrelated-quota"] = now[0] + 50
    ctx = restart(ctx)
    assert loop._pick(ctx, "implement") is None
    assert not serve.lift_due(ctx.run, now[0], lambda message: None)
    ctx = restart(ctx)  # The real CLI creates this context before appending resumed.
    monkeypatch.setattr(cli, "_ctx", lambda run: ctx)
    monkeypatch.setattr(cli, "_sync", lambda ctx: None)
    assert cli._resume(SimpleNamespace(change="c", note="", no_run=True), ctx.run) == 0
    monkeypatch.setattr(ctx, "head", lambda: "head")
    monkeypatch.setattr(ctx, "tasks_text", lambda head: "## 1. Work\n- [ ] 1.1 First task\n")
    probe = single and not quota_replaced

    def step(ctx, task, head):
        assert loop._pick(ctx, "implement") == (A if probe else None)
        retries = loop._infra_retries(ctx)
        for profile in (A, B):
            assert retries.count(ctx, TASK.key, profile.name, "implement") == 0
        assert retries.count(ctx, OTHER.key, A.name, "review") == 1
        assert ctx.run.state.task(TASK.key).auto_resumes == 2
        assert ctx.run.state.exhausted["unrelated-quota"] == now[0] + 50
        assert Run(ctx.run.dir).state.exhausted == ctx.run.state.exhausted
        if probe:
            result(ctx, A)  # Fresh failure re-blocks A at the very same deadline.
            assert ctx.run.state.task(TASK.key).infra == 1
        else:
            loop._all_exhausted(ctx, TASK, "implement")
            assert ctx.run.state.pause.reason == ("usage_limit" if quota_replaced else "infra")

    monkeypatch.setattr(loop, "step", step)
    assert loop.run(ctx) == "paused"
    ctx = restart(ctx)
    assert loop._pick(ctx, "implement") is None
    assert all(ctx.run.state.exhausted[name] == until for name, until in deadlines.items())
    assert ctx.run.state.task(TASK.key).auto_resumes == 2

"""Regression coverage for retry-history validation and legacy attribution."""

import pytest

from specloop import agents, loop
from test_provider_retry_accounting import A, B, TASK, context, restart, result


@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/restart-retains-partial-allowance")
@pytest.mark.parametrize("bad_count", [None, "2", -1, True, False, 1.5, [], {}])
def test_invalid_retry_count_cannot_replace_active_pair_history(tmp_path, bad_count):
    ctx = context(tmp_path, limit=4)
    assert result(ctx, A).infra == 1
    ctx.run.event("infra_retry", task=TASK.key, role="implement", profile=A.name,
                  count=bad_count)

    ts = result(restart(ctx), A)

    assert ts.infra == 2
    assert ts.attempts == 0


@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/restart-retains-partial-allowance")
def test_empty_profile_history_cannot_discard_legacy_single_profile_count(tmp_path):
    ctx = context(tmp_path, single=True, limit=4)
    ctx.run.state.task(TASK.key).infra = 2
    ctx.run.event("infra_retry", task=TASK.key, role="implement", profile="", count=0)

    assert result(restart(ctx), A).infra == 3


@pytest.mark.scenario("provider-fallback/cooldown-eligibility/unavailable-profile-returns")
def test_expired_single_profile_cooldown_resets_unattributed_legacy_count(tmp_path, monkeypatch):
    ctx = context(tmp_path, single=True)
    ctx.run.state.task(TASK.key).infra = 2
    ctx.run.state.exhausted[A.name] = 101
    ctx = restart(ctx)
    monkeypatch.setattr(loop.time, "time", lambda: 100)
    assert loop._pick(ctx, "implement") is None

    monkeypatch.setattr(loop.time, "time", lambda: 101)
    assert loop._pick(ctx, "implement") == A
    assert A.name not in ctx.run.state.exhausted
    # The reset must survive another restart before the next failure is recorded.
    ctx = restart(ctx)
    ts = result(ctx, A)
    assert ts.infra == 1
    assert ts.attempts == 0
    assert ctx.run.state.status == "running"


@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/restart-retains-partial-allowance")
@pytest.mark.parametrize("previous,saved", [(None, 2), (A.name, 0)],
                         ids=["unattributed-count", "no-failures-to-attribute"])
def test_legacy_recovery_does_not_record_phantom_retries(tmp_path, previous, saved):
    ctx = context(tmp_path)
    ctx.run.state.task(TASK.key).infra = saved
    if previous is not None:
        ctx.run.event("implemented", task=TASK.key, profile=previous)
    ctx = restart(ctx)

    assert result(ctx, B).infra == 1
    # History must describe real task/profile counts, never a null profile or
    # an invented retry for a profile whose legacy failure count was zero.
    retries = [event for event in agents.read_jsonl(ctx.run.dir / "events.jsonl")
               if event.get("event") == "infra_retry"]
    assert [(event["task"], event["profile"], event["count"]) for event in retries] == [
        (TASK.key, B.name, 1),
    ]

"""Additive integration state and legacy run compatibility."""

import json

import pytest

from specloop import loop, state
from test_specloop_base_sync import advance_base, base_env


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/legacy-state")
@pytest.mark.parametrize("moved", [False, True])
@pytest.mark.parametrize("unknown_fields", [False, True])
def test_legacy_state_uses_fresh_base_and_preserves_run(base_env, moved, unknown_fields):
    ctx = base_env.ctx
    raw = {
        "change": "c", "status": "paused",
        "pause": {"reason": "requested", "detail": "keep pause", "resume_at": 123},
        "tasks": {"1.1": {"status": "passed", "attempts": 2}},
        "gates_passed": [1], "pr": "https://example.test/pull/17",
    }
    if unknown_fields:
        raw.update(future_state={"value": True})
        raw["pause"]["future_pause"] = True
        raw["tasks"]["1.1"]["future_task"] = True
    (ctx.run.dir / "state.json").write_text(json.dumps(raw))
    ctx.run = state.Run(ctx.run.dir, require_existing=True)
    assert ctx.run.state.base_sync == {}
    previous = ctx.run.state
    head = ctx.head()
    expected_base = advance_base(base_env) if moved else base_env.initial
    fetched = loop._fetch_publish_base(ctx, timeout=300)
    assert fetched == expected_base
    assert loop._is_ancestor(ctx.repo, fetched, head) is not moved
    merged = loop._merge_publish_base(ctx, fetched, head)
    assert merged and (merged != head) == moved
    ctx.run.save()
    ctx.run.reload()
    assert ctx.run.state == previous
    assert ctx.run.state.pause == state.Pause("requested", detail="keep pause", resume_at=123)
    assert ctx.run.state.tasks["1.1"] == state.TaskState(status="passed", attempts=2)
    assert ctx.run.state.pr == raw["pr"] and ctx.run.state.gates_passed == [1]


@pytest.mark.scenario("publish-base-sync/recover-pending-verification/legacy-state")
def test_base_sync_intent_round_trips_with_unknown_keys(base_env):
    run = base_env.ctx.run
    intent = {"base": "B", "previous_head": "H", "merged_head": "M", "verified_head": "",
              "receipts": ["final/base-sync/attempt-1"], "retry_verify": True, "future_key": {"v": 1}}
    run.state.base_sync = intent
    run.save()
    loaded = state.Run(run.dir, require_existing=True)
    assert loaded.state.base_sync == intent
    loaded.save()
    run.reload()
    assert run.state.base_sync == intent
    first, second = state.RunState("one"), state.RunState("two")
    first.base_sync["retry_verify"] = True
    assert second.base_sync == {}

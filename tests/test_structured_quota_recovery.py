"""Synthetic CLI telemetry through real admitted-work dispatch and recovery."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from nightshift.trace import RateLimit
from specloop import agents, cli, loop, quota, serve
from specloop.agents import claude
from specloop.agents.claude import run as claude_run
from specloop.state import Run
from test_claude_rate_limit import USAGE, final, limit
from test_provider_fallback_continuation import (
    CANDIDATE, FALLBACK, FINDINGS, HEAD, NOW, PRIMARY, SECTION_BASE, SKIP, TASK,
    driver, work_state,
)
from test_provider_retry_accounting import context
from test_provider_unavailable_adapters import provider


def telemetry_driver(env, monkeypatch, event, outcome="success"):
    ctx, now = env.ctx, [NOW]
    primary = replace(PRIMARY, adapter="claude")
    ctx.m = replace(ctx.m, chains={role: (primary, FALLBACK) for role in ctx.m.roles})
    monkeypatch.setattr(loop.time, "time", lambda: now[0])
    monkeypatch.setattr(claude, "run", claude_run)

    def spawn(argv, environ, call, **kwargs):
        env.calls.append(call)
        records = [{"type": "system", "subtype": "init", "session_id": "session"}]
        if call.profile == primary:
            output = ({"status": "ready", "summary": "done", "subtasks": []} if call.access == "write"
                      else {"verdict": "PASS", "prior": [], "findings": [], "needs_human": []})
            assert agents.validate(output, call.schema) is None
            records += [event, final(is_error=outcome == "error", structured_output=output)]
        else:
            assert call.profile == FALLBACK
            ctx.run.request_pause()  # Observe dispatch without accepting work.
            records += [final(is_error=True, errors="transport error")]
        call.transcript.write_text("\n".join(map(json.dumps, records)))
        call.transcript.with_suffix(".stderr").write_text("")
        return (None, "wall timeout") if outcome == "timeout" else (1 if outcome == "error" else 0, "")

    monkeypatch.setattr(claude, "spawn", spawn)
    ctx.agent = agents.run
    return ctx, now, primary


@pytest.mark.scenario("provider-fallback/structured-quota-deadlines/reset-time-drives-recovery")
@pytest.mark.scenario("provider-fallback/structured-quota-deadlines/unknown-and-stale-resets-retain-backoff")
@pytest.mark.parametrize("reset,chain,start", [
    (1600, "single", NOW), (1600, "fallback", NOW),
    (1600, "quota", NOW), (1600, "mixed", NOW), (4000, "mixed", NOW),
    (None, "single", NOW), ("invalid", "single", NOW),
    (1000, "single", NOW), (999, "single", NOW), (None, "single", 0),
])
def test_rate_limit_event_reset_time_drives_wait(driver, monkeypatch, reset, chain, start):
    event = limit("rejected", resetsAt=reset)
    if reset is None:
        del event["rate_limit_info"]["resetsAt"]
    ctx, now, primary = telemetry_driver(driver, monkeypatch, event)
    now[0] = start
    if chain == "single":
        ctx.m.chains["implement"] = (primary,)
    elif chain in ("quota", "mixed"):
        ctx.run.state.exhausted[FALLBACK.name] = start + 900
        ctx.run.event("unavailable" if chain == "mixed" else "exhausted",
                      profile=FALLBACK.name, until=start + 900, detail="prior block")
    ctx.run.state.task(TASK.key).auto_resumes = 2
    expected = reset + 60 if type(reset) is int and reset > start else start + 1800
    assert loop.run(ctx) == "paused"
    saved = Run(ctx.run.dir)
    assert saved.state.exhausted[primary.name] == expected
    events = list(agents.read_jsonl(ctx.run.dir / "events.jsonl"))
    exhausted = next(e for e in events if e["event"] == "exhausted" and e["profile"] == primary.name)
    assert exhausted["until"] == expected
    assert exhausted["rate_limit"] == dict(status="rejected", type="five_hour",
        resets_at=reset if type(reset) is int else 0, using_overage=False)
    implemented = next(e for e in events if e["event"] == "implemented")
    assert implemented["session_id"] == "session" and implemented["usage"] == USAGE
    assert exhausted["detail"] == implemented["detail"][:300]
    assert saved.state.tasks[TASK.key].attempts == 0
    if chain == "fallback":
        assert [c.profile for c in driver.calls] == [primary, FALLBACK]
        assert saved.state.pause.reason == "requested"
    else:
        assert [c.profile for c in driver.calls] == [primary]
        deadline = min(expected, start + 900) if chain in ("quota", "mixed") else expected
        assert saved.state.pause.resume_at == deadline
        assert saved.state.pause.reason == ("infra" if chain == "mixed" else "usage_limit")
        assert not serve.lift_due(saved, deadline - 1, driver.notices.append)
        now[0] = deadline
        assert serve.lift_due(saved, deadline, driver.notices.append) == (chain != "mixed")
        assert Run(ctx.run.dir).state.exhausted[primary.name] == expected


@pytest.mark.scenario("provider-fallback/structured-overage-hold/overage-pauses-despite-an-available-fallback")
@pytest.mark.scenario("provider-fallback/structured-overage-hold/overage-waits-for-human-recovery")
@pytest.mark.parametrize("role", ["implement", "review", "gate"])
@pytest.mark.parametrize("outcome", ["success", "error", "timeout"])
def test_overage_pauses_run(driver, monkeypatch, role, outcome):
    ctx, now, primary = telemetry_driver(driver, monkeypatch, limit("allowed", resetsAt=1600,
                                         isUsingOverage=True), outcome)
    key = "section:1" if role == "gate" else TASK.key
    ts = ctx.run.state.task(key)
    ts.attempts, ts.infra, ts.feedback, ts.auto_resumes = 1, 1, FINDINGS, 2
    if role == "review":
        ts.candidate, ts.candidate_base, ts.pending_review = CANDIDATE, HEAD, CANDIDATE
    if role == "gate":
        driver.text = driver.text.replace("[ ] 1.1", "[x] 1.1")
        ctx.run.state.section_base = {"1": SECTION_BASE}
        ctx.run.state.evidence_summary = {"1": "retained evidence"}
    monkeypatch.setattr(cli, "_ctx", lambda run: ctx)
    monkeypatch.setattr(cli, "_sync", lambda ctx: None)
    for cycle in range(2):
        before, infra = work_state(ctx.run.state.tasks[key]), ctx.run.state.tasks[key].infra
        assert loop.run(ctx) == "paused"
        ctx.run = Run(ctx.run.dir)
        saved = ctx.run.state
        assert saved.pause.reason == "overage" and saved.pause.resume_at == 0
        assert saved.pause.task == key
        assert all(value in saved.pause.detail for value in (key, role, primary.name))
        assert saved.exhausted == {SKIP.name: NOW + 2000} and saved.gates_passed == []
        assert work_state(saved.tasks[key]) == before and saved.tasks[key].infra == infra
        assert len(driver.calls) == cycle + 1 and driver.calls[-1].profile == primary
        events = list(agents.read_jsonl(ctx.run.dir / "events.jsonl"))
        overage = [e for e in events if e["event"] == "overage"][-1]
        assert (overage["task"], overage["role"], overage["profile"]) == (key, role, primary.name)
        assert overage["rate_limit"] == vars(RateLimit("allowed", "five_hour", 1600, True))
        results = [e for e in events if e["event"] in ("implemented", "reviewed", "gate_reviewed")]
        assert results[-1]["disposition"] == "usage_limit" and results[-1]["output"] is None
        assert results[-1]["session_id"] == "session" and results[-1]["usage"] == USAGE
        if role == "gate":
            assert saved.section_base == {"1": SECTION_BASE} and saved.evidence_summary == {"1": "retained evidence"}
        assert not {e["event"] for e in events} & {"infra_retry", "sealed", "landed", "gate_passed"}
        now[0] += 10000
        assert not serve.lift_due(ctx.run, now[0], driver.notices.append)
        assert loop.run(ctx) == "paused" and len(driver.calls) == cycle + 1
        if not cycle:
            assert cli._resume(SimpleNamespace(change="c", note="", no_run=True), ctx.run) == 0


@pytest.mark.scenario("provider-fallback/structured-quota-deadlines/unknown-and-stale-resets-retain-backoff")
@pytest.mark.parametrize("reached,used,expected", [(None, 100, 960), ("primary", 0, 960),
                                                (None, 0, 1800)])
def test_codex_windows_and_claude_precedence(reached, used, expected):
    limits = {"rateLimits": {"primary": {"resetsAt": 500, "usedPercent": used},
        "secondary": {"resetsAt": 900, "usedPercent": used}, "rateLimitReachedType": reached}}
    assert quota.resume_at(limits, now=0) == expected
    assert quota.resume_at(limits, now=900) == 2700
    assert quota.resume_at(None, now=0) == 1800
    for reset in (None, True, "500", 1.5, float("inf"), float("nan"), 0, -1, 100, 500):
        telemetry = RateLimit("rejected", "five_hour", reset, False)
        assert quota.resume_at(limits, now=100, rate_limit=telemetry) == (560 if reset == 500 else 1900)


@pytest.mark.scenario("provider-fallback/structured-quota-deadlines/unknown-and-stale-resets-retain-backoff")
@pytest.mark.parametrize("limits", [None, {"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": 1600}}}])
def test_legacy_adapter_quota_reads(provider, tmp_path, monkeypatch, limits):
    ctx, reads = context(tmp_path), []
    monkeypatch.setattr(loop.time, "time", lambda: NOW)
    monkeypatch.setattr(quota, "codex_limits", lambda home: reads.append(home) or limits)
    ctx.m.chains["implement"] = (provider.profile,)
    result = provider.run("quota", source="stderr")
    assert result.disposition == "usage_limit" and result.rate_limit is None
    assert loop._infra(ctx, TASK, ctx.run.state.task(TASK.key), result, provider.call, "implement")
    codex = provider.profile.adapter == "codex"
    assert reads == ([provider.call.home] if codex else [])
    deadline = 1660 if codex and limits else NOW + 1800
    saved = Run(ctx.run.dir).state
    assert saved.pause.reason == "usage_limit" and saved.pause.resume_at == deadline
    event = next(e for e in agents.read_jsonl(ctx.run.dir / "events.jsonl") if e["event"] == "exhausted")
    assert event == dict(at=NOW, event="exhausted", profile=provider.profile.name,
                         until=deadline, detail=result.detail[:300])

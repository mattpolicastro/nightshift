"""Offline provider transitions and compatibility with supervisor recovery."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from specloop import agents, cli, loop, messages, serve
from specloop.manifest import Profile
from specloop.state import Run
from test_provider_retry_accounting import A, B, TASK, context, restart
from test_specloop_messages import parse_build_hint


@pytest.fixture
def setup(tmp_path, monkeypatch):
    now, notices = [1000.0], []
    monkeypatch.setattr(loop.time, "time", lambda: now[0])
    monkeypatch.setattr(agents, "homes_dir", lambda: tmp_path / "fake-homes")
    ctx = context(tmp_path, limit=1)

    def notify(message):
        # Notifications must follow durable availability and task-state writes.
        saved = Run(ctx.run.dir).state
        assert saved.exhausted == ctx.run.state.exhausted
        assert saved.tasks == ctx.run.state.tasks
        notices.append(message)

    ctx.notify = notify
    return ctx, now, notices


def dispatch(ctx, disposition="infra", detail="offline", role="implement"):
    task, key = (None, "section:1") if role == "gate" else (TASK, TASK.key)
    call = loop._call(ctx, role, "read", "", ctx.run.dir, {}, ctx.run.dir / "call.jsonl", 1)
    assert call is not None
    ts = ctx.run.state.task(key)
    assert loop._infra(ctx, task, ts, agents.Result(disposition, detail=detail), call, role, key)
    return call.profile, ts


@pytest.mark.scenario("provider-fallback/unavailable-profile-fallback/infrastructure-retries-exhaust")
@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/fallback-has-its-own-allowance")
@pytest.mark.parametrize("limit", [0, 1, 3])
@pytest.mark.parametrize("role", ["implement", "review", "gate"])
def test_infra_exhaustion_moves_role_to_next_profile(setup, limit, role):
    ctx, now, notices = setup
    ctx.m = replace(ctx.m, max_infra=limit)
    key = "section:1" if role == "gate" else TASK.key
    ctx.run.state.task(key).attempts = 2
    detail = "backend offline\n" + "diagnostic " * 40
    for count in range(1, limit + 2):
        profile, ts = dispatch(ctx, detail=detail, role=role)
        assert profile == A and ts.attempts == 2
        if count <= limit:
            assert ts.infra == count and not ctx.run.state.exhausted and not notices
    assert ts.infra == 0 and ctx.run.state.status == "running"
    assert ctx.run.state.pause is None
    assert Run(ctx.run.dir).state.exhausted == {"a": now[0] + 900}
    event = list(agents.read_jsonl(ctx.run.dir / "events.jsonl"))[-1]
    assert event == dict(at=now[0], event="unavailable", task=key, role=role, profile="a",
                         until=now[0] + 900, disposition="infra", detail=detail[:300])
    assert notices == [messages.fyi("c", f"a unavailable: backend offline; {role} continues on b")]
    for count in range(1, limit + 1):
        profile, ts = dispatch(ctx, role=role)
        assert profile == B and ts.infra == count and ts.attempts == 2
        assert ctx.run.state.status == "running"
    profile, ts = dispatch(ctx, role=role)
    assert profile == B and ts.infra == limit + 1 and ts.attempts == 2
    assert ctx.run.state.pause.reason == "infra"


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/single-profile-retains-infra-pause")
@pytest.mark.scenario("run-actions/canonical-command-hints/messages-and-status-agree")
@pytest.mark.parametrize("disposition", ["infra", "unavailable"])
def test_single_profile_chain_still_pauses_on_infra(setup, disposition):
    ctx, now, notices = setup
    ctx.m.chains["implement"] = (A,)
    detail = "provider offline\n" + "retained full pause detail " * 20
    ts = ctx.run.state.task(TASK.key)
    ts.attempts = 2
    for cycle in range(3):
        if cycle:
            assert loop._pick(ctx, "implement") == A
        for _ in range(2 if disposition == "infra" else 1):
            dispatch(ctx, disposition, detail)
        pause = Run(ctx.run.dir).state.pause
        assert Run(ctx.run.dir).state.tasks[TASK.key] == ts
        assert pause.reason == "infra" and pause.detail == detail
        assert pause.resume_at == now[0] + 900
        assert ts.infra == (2 if disposition == "infra" else 0) and ts.attempts == 2
        assert notices[-1].kind == messages.ACTION
        assert notices[-1].command == "nightshift resume c"
        hint = parse_build_hint(notices[-1].command)
        assert hint.cmd == "resume" and hint.change == "c" and hint.note == ""
        assert hint.no_run is False
        assert not serve.lift_due(ctx.run, pause.resume_at - 1, notices.append)
        now[0] = pause.resume_at
        assert serve.lift_due(ctx.run, now[0], notices.append) == (cycle < 2)
    assert ts.auto_resumes == 2 and ctx.run.state.status == "paused"
    events = list(agents.read_jsonl(ctx.run.dir / "events.jsonl"))
    assert any(e.get("disposition") == disposition for e in events if e["event"] == "unavailable")
    assert sum(e["event"] == "infra_retry" and e["count"] > 0 for e in events) == (
        6 if disposition == "infra" else 0)


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/single-profile-retains-infra-pause")
@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/manual-resume-preserves-single-profile-recovery")
@pytest.mark.parametrize("no_run", [False, True])
@pytest.mark.parametrize("single,disposition,role", [(True, "infra", "implement"),
    (True, "unavailable", "implement"), (True, "usage_limit", "implement"),
    (False, "infra", "implement"), (True, "infra", "review")])
def test_manual_resume_reconciles_provider_deadline(setup, monkeypatch, single, disposition, role, no_run):
    ctx, now, _ = setup
    ctx.m = replace(ctx.m, max_infra=0)
    ctx.m.chains[role] = (A,) if single else (A, B)
    monkeypatch.setattr(loop.quota, "resume_at", lambda limits: now[0] + 900)
    ts = ctx.run.state.task(TASK.key)
    ts.auto_resumes = 2
    dispatch(ctx, disposition)
    if role == "review":
        # Implementation [A, B] succeeds on B, then review [A] is blocked by A's outage.
        call = loop._call(ctx, "implement", "read", "", ctx.run.dir, {}, ctx.run.dir / "b.jsonl", 1)
        assert call.profile == B
        assert not loop._infra(ctx, TASK, ts, agents.Result("ok"), call, "implement")
        ctx.run.event("implemented", task=TASK.key, profile="b", disposition="ok")
        ts.pending_review = "candidate"
        assert loop._pick(ctx, role) is None
        loop._all_exhausted(ctx, TASK, role)
        assert ctx.run.state.pause.reason == "infra"
        assert ctx.run.state.pause.resume_at == now[0] + 900
        assert ts.attempts == 0
    if not single:
        dispatch(ctx, disposition)
    ctx.run.state.exhausted["unrelated-quota"] = now[0] + 500
    ctx = restart(ctx)
    assert loop._pick(ctx, role) is None  # Restart alone cannot clear a block.
    deadlines = dict(ctx.run.state.exhausted)
    ctx = restart(ctx)  # The CLI constructs its context before recording resumed.
    monkeypatch.setattr(cli, "_ctx", lambda run: ctx)
    monkeypatch.setattr(cli, "_sync", lambda ctx: None)
    monkeypatch.setattr(ctx, "head", lambda: "head")
    monkeypatch.setattr(ctx, "tasks_text", lambda head: "## 1. Work\n- [ ] 1.1 First task\n")
    should_probe = single and disposition != "usage_limit"

    def step(ctx, task, head):
        assert loop._pick(ctx, role) == (A if should_probe else None)
        assert loop._infra_retries(ctx).count(ctx, TASK.key, A.name, role) == 0
        assert ctx.run.state.task(TASK.key).auto_resumes == 2
        assert ctx.run.state.exhausted["unrelated-quota"] == deadlines["unrelated-quota"]
        assert Run(ctx.run.dir).state.exhausted == ctx.run.state.exhausted
        if should_probe:
            assert A.name not in ctx.run.state.exhausted
            if role == "review":
                assert ctx.run.state.task(TASK.key).pending_review == "candidate"
            profile, ts = dispatch(ctx, role=role)
            assert profile == A and ts.infra == 1 and ts.attempts == 0
        else:
            assert ctx.run.state.exhausted == deadlines
            loop._all_exhausted(ctx, TASK, role)

    monkeypatch.setattr(loop, "step", step)
    assert cli._resume(SimpleNamespace(change="c", note="", no_run=no_run), ctx.run) == 0
    if no_run:
        assert loop.run(ctx) == "paused"
    ctx = restart(ctx)
    # A new failure at the same clock time supersedes the historical manual resume.
    assert ctx.run.state.task(TASK.key).auto_resumes == 2
    assert loop._pick(ctx, role) is None
    assert ctx.run.state.exhausted == deadlines
    if should_probe:
        assert not serve.lift_due(ctx.run, now[0] + 900, lambda message: None)


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/all-unavailable-use-earliest-retry")
def test_all_profiles_unavailable_pauses_with_earliest_retry(setup):
    ctx, now, notices = setup
    ctx.m = replace(ctx.m, max_infra=0)
    unrelated = Profile("other", "claude", "m")
    ctx.m.chains["review"] = (unrelated,)
    ctx.run.state.exhausted["other"] = 1200
    ctx.run.event("unavailable", profile="other", until=1200, detail="unrelated")
    dispatch(ctx, detail="A offline")
    now[0] += 100
    dispatch(ctx, detail="B offline")
    pause = ctx.run.state.pause
    assert pause.reason == "infra" and pause.resume_at == 1900
    assert "a unavailable: A offline" in pause.detail and "b unavailable: B offline" in pause.detail
    assert "other" not in pause.detail
    assert ctx.run.state.task(TASK.key).attempts == 0
    assert [notice.kind for notice in notices] == [messages.FYI, messages.ACTION]
    assert Run(ctx.run.dir).state.pause == pause


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/mixed-blocks-retain-bounded-recovery")
@pytest.mark.parametrize("quota_first", [True, False])
def test_mixed_blocks_keep_infra_recovery_cap(setup, monkeypatch, quota_first):
    ctx, now, notices = setup
    ctx.m = replace(ctx.m, max_infra=0)
    monkeypatch.setattr(loop.quota, "resume_at", lambda limits: now[0] + 100)
    for cycle in range(3):
        if cycle:
            # The next outage occurs after both profiles' previous windows ended.
            now[0] += 900
        dispatch(ctx, "usage_limit" if quota_first else "infra")
        dispatch(ctx, "infra" if quota_first else "usage_limit")
        pause = ctx.run.state.pause
        assert pause.reason == "infra" and pause.resume_at == now[0] + 100
        assert Run(ctx.run.dir).state.pause == pause
        assert ("a usage-limited" if quota_first else "b usage-limited") in pause.detail
        assert ("b unavailable: offline" if quota_first else "a unavailable: offline") in pause.detail
        now[0] = pause.resume_at
        assert serve.lift_due(ctx.run, now[0], notices.append) == (cycle < 2)
    ts = ctx.run.state.task(TASK.key)
    assert ts.attempts == 0 and ts.auto_resumes == 2
    assert loop._pick(ctx, "implement") == (A if quota_first else B)
    assert ctx.run.state.status == "paused"  # Eligibility cannot lift a spent pause.


@pytest.mark.scenario("provider-fallback/compatible-all-blocked-pauses/quota-only-behavior-remains-intact")
@pytest.mark.parametrize("history", ["legacy", "expired-unavailable", "latest-matching"])
def test_quota_only_recovery_stays_uncapped(setup, monkeypatch, history):
    ctx, now, notices = setup
    monkeypatch.setattr(loop.quota, "resume_at", lambda limits: now[0] + 200)
    ts = ctx.run.state.task(TASK.key)
    ts.auto_resumes = 2
    for _ in range(3):
        until = now[0] + 100
        ctx.run.state.exhausted["b"] = until
        if history != "legacy":
            ctx.run.event("unavailable", profile="b", until=now[0] - 1, detail="old auth failure")
            if history == "latest-matching":
                ctx.run.event("unavailable", profile="b", until=until, detail="superseded")
            ctx.run.event("exhausted", profile="b", until=until)
            ctx.run.event("unavailable", profile="b", until=until + 999, detail="different deadline")
        dispatch(ctx, "usage_limit")
        pause = ctx.run.state.pause
        assert pause.reason == "usage_limit" and pause.resume_at == until
        assert Run(ctx.run.dir).state.pause == pause
        assert "usage-limited (a, b)" in pause.detail
        assert notices[-1].kind == messages.FYI
        now[0] = until
        assert serve.lift_due(ctx.run, now[0], notices.append)
        now[0] += 200
    assert ts.auto_resumes == 2 and ts.attempts == 0

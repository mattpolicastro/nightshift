"""Counter tests inject profile changes; fallback routing is a separate concern."""

import pytest

from specloop import agents, loop, serve, tasks
from specloop.manifest import Manifest, Profile
from specloop.state import Run


TASK, OTHER = tasks.parse("## 1. Work\n- [ ] 1.1 First task\n- [ ] 1.2 Second task\n")
A, B = Profile("a", "claude", "m"), Profile("b", "claude", "m")


def context(tmp_path, *, limit=2, single=False):
    chain = (A,) if single else (A, B)
    roles = {role: A for role in ("implement", "review", "gate")}
    manifest = Manifest(tmp_path, "c", "spec/c", "main", "", roles, (), (), (),
                        max_infra=limit, chains={role: chain for role in roles})
    return loop.Ctx(Run(tmp_path / "run"), manifest, {})


def result(ctx, profile=A, task=TASK, role="implement", disposition="infra", key=""):
    ts = ctx.run.state.task(task.key if task else key)
    call = agents.Call(profile, "read", "", ctx.run.dir, {}, ctx.run.dir / "call.jsonl",
                       60, 60, ctx.run.dir / "fake-home")
    handled = loop._infra(ctx, task, ts, agents.Result(disposition, detail="offline"), call, role, key)
    assert handled == (disposition in ("infra", "usage_limit"))
    return ts


def restart(ctx):
    ctx.run.save()
    return loop.Ctx(Run(ctx.run.dir), ctx.m, {})


@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/fallback-has-its-own-allowance")
@pytest.mark.parametrize("limit", [0, 1, 3])
def test_fallback_budget_is_independent(tmp_path, limit):
    ctx = context(tmp_path, limit=limit)
    for _ in range(limit + 1):
        ts = result(ctx)
    assert ts.infra == limit + 1 and ts.attempts == 0
    assert ctx.run.state.pause.reason == "infra"
    # Select B without a resume/reset, isolating accounting from fallback routing.
    ctx.run.state.status, ctx.run.state.pause = "running", None
    assert loop._infra_retries(ctx).count(ctx, TASK.key, B.name, "implement") == 0
    for count in range(1, limit + 1):
        ts = result(ctx, B)
        assert ts.infra == count and ctx.run.state.status == "running"
    ts = result(ctx, B)
    assert ts.infra == limit + 1 and ts.attempts == 0
    assert ctx.run.state.pause.reason == "infra"


@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/tasks-and-roles-preserve-counter-identity")
def test_tasks_roles_and_sections_keep_their_identity(tmp_path):
    ctx = context(tmp_path, limit=4)
    assert result(ctx).infra == 1
    assert result(ctx, B).infra == 1
    result(ctx, B, disposition="ok")
    assert result(ctx, A, role="review").infra == 2
    assert result(ctx, A, task=OTHER).infra == 1
    assert result(ctx, A, task=None, role="gate", key="section:1").infra == 1
    ctx = restart(ctx)
    assert result(ctx, A).infra == 3
    assert result(ctx, B, role="review").infra == 2
    assert result(ctx, A, task=None, role="gate", key="section:1").infra == 2
    assert all(ts.attempts == 0 for ts in ctx.run.state.tasks.values())


@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/restart-retains-partial-allowance")
def test_restart_replays_append_order_and_ignores_bad_lines(tmp_path):
    ctx = context(tmp_path)
    result(ctx)
    ctx.run.event("infra_retry", task=TASK.key, role="review", profile="a", count=2, at=0)
    with (ctx.run.dir / "events.jsonl").open("a") as log:
        log.write('broken\n[]\nnull\n{"event":"unknown"}\n'
                  '{"event":"infra_retry","task":[],"count":8}\n'
                  '{"event":"infra_retry","task":"x","profile":"a","count":"8"}\n')
    before = (ctx.run.dir / "events.jsonl").read_text()
    ctx = restart(ctx)
    assert result(ctx, B).infra == 1
    assert ctx.run.state.status == "running"
    assert result(ctx, A).infra == 3
    assert ctx.run.state.pause.reason == "infra"
    assert (ctx.run.dir / "events.jsonl").read_text().startswith(before)


@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/restart-retains-partial-allowance")
def test_restart_keeps_the_remaining_retry(tmp_path):
    ctx = context(tmp_path)
    result(ctx)
    ctx = restart(ctx)
    assert result(ctx).infra == 2
    assert ctx.run.state.status == "running"
    assert result(ctx).infra == 3
    assert ctx.run.state.pause.reason == "infra"


@pytest.mark.scenario("provider-fallback/task-and-profile-retry-allowance/restart-retains-partial-allowance")
@pytest.mark.parametrize("single,previous,expected", [(True, None, 3), (False, None, 1),
                                                       (False, "a", 3), (False, "b", 1)])
def test_legacy_scalar_attribution(tmp_path, single, previous, expected):
    ctx = context(tmp_path, single=single, limit=5)
    ctx.run.state.task(TASK.key).infra = 2
    if previous:
        ctx.run.event("reviewed", task=TASK.key, profile=previous)
    ctx = restart(ctx)
    # Selection initializes history before this new call can overwrite legacy attribution.
    loop._pick(ctx, "implement")
    ctx.run.event("implemented", task=TASK.key, profile="a")
    assert result(ctx).infra == expected
    if previous == "b":
        assert result(restart(ctx), B).infra == 3


@pytest.mark.parametrize("single,expected", [(True, 3), (False, 1)])
def test_legacy_resume_then_unattributed_call(tmp_path, single, expected):
    ctx = context(tmp_path, single=single, limit=5)
    ctx.run.event("pause", task=TASK.key, reason="infra")
    ctx.run.event("resumed")
    ctx.run.event("implemented", task=TASK.key, profile="a")
    ctx.run.event("reviewed", task=TASK.key)  # Latest call no longer identifies a profile.
    ctx.run.state.task(TASK.key).infra = 2
    assert result(restart(ctx)).infra == expected


@pytest.mark.parametrize("resume,reason,expected", [("resumed", "infra", 1),
                                                   ("auto_resumed", "infra", 1),
                                                   ("auto_resumed", "usage_limit", 2)])
def test_resume_reconciles_at_driver_entry(tmp_path, monkeypatch, resume, reason, expected):
    ctx = context(tmp_path, limit=5)
    result(ctx, A)
    result(ctx, B)
    result(ctx, A, task=OTHER)
    ctx.run.pause(reason, TASK.key, resume_at=1)
    # Like the CLI, construct the context before writing the resume event.
    ctx = restart(ctx)
    if resume == "auto_resumed":
        assert serve.lift_due(ctx.run, 1, lambda text: None)
    else:
        ctx.run.state.task(TASK.key).infra = 0
        ctx.run.event("resumed")
    ctx.run.state.status = "running"
    monkeypatch.setattr(ctx, "head", lambda: "head")
    monkeypatch.setattr(ctx, "tasks_text", lambda head: "## 1. Work\n- [ ] 1.1 First task\n")

    def step(ctx, task, head):
        assert result(ctx, A).infra == expected
        assert result(ctx, B, role="review").infra == expected
        assert result(ctx, A, task=OTHER).infra == 2
        ctx.run.state.status = "done"

    monkeypatch.setattr(loop, "step", step)
    assert loop.run(ctx) == "done"


def test_expired_profile_resets_only_its_allowance_and_survives_restart(tmp_path, monkeypatch):
    ctx = context(tmp_path, limit=4)
    monkeypatch.setattr(loop.time, "time", lambda: 100)
    result(ctx, A)
    result(ctx, B)
    ctx.run.state.exhausted[A.name] = 101
    assert loop._pick(ctx, "review") == B
    monkeypatch.setattr(loop.time, "time", lambda: 101)
    assert loop._pick(ctx, "review") == A
    ctx = restart(ctx)
    assert result(ctx, A).infra == 1
    assert result(ctx, B).infra == 2


def test_usage_fallback_mirrors_the_selected_profile_count(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    monkeypatch.setattr(loop.quota, "resume_at", lambda limits: float("inf"))
    result(ctx, A)
    assert result(ctx, A, disposition="usage_limit").infra == 0
    assert result(ctx, B).infra == 1


def test_history_is_cached_between_calls(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    result(ctx)
    monkeypatch.setattr(agents, "read_jsonl", lambda path: pytest.fail("history replayed within driver"))
    result(ctx, B)
    assert result(ctx, A).infra == 2

"""Drive real retry loops with scripted providers and no external work."""

import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from specloop import agents, loop, messages, tasks, verify
from specloop.agents import claude, codex
from specloop.manifest import Manifest, Profile
from specloop.state import Run


NOW = 1000.0
HEAD, CANDIDATE, SECTION_BASE = "branch-head", "verified-candidate", "section-base"
TASKS = "## 1. Core\n- [ ] 1.1 Connect the components\n## 2. Docs\n- [ ] 2.1 Document them\n"
TASK = tasks.parse(TASKS)[0]
PRIMARY = Profile("primary", "codex", "model")
SKIP = Profile("already-limited", "claude", "model")
FALLBACK = Profile("fallback", "claude", "model")
LAST = Profile("last", "claude", "model")
DETAIL = "provider connection failed\n" + "retained diagnostic " * 30
FINDINGS = "- [integration:components must connect] Preserve this unresolved finding."


def unexpected(*args, **kwargs):
    pytest.fail(f"unexpected external work or work advancement: {args!r}")


def work_state(ts):
    """Only sessions and infrastructure accounting may change on these calls."""
    return {key: value for key, value in asdict(ts).items() if key not in ("sessions", "infra")}


@pytest.fixture
def driver(tmp_path, monkeypatch):
    monkeypatch.setattr(loop.time, "time", lambda: NOW)
    monkeypatch.setattr(agents, "homes_dir", lambda: tmp_path / "empty-provider-homes")
    chain = (PRIMARY, SKIP, FALLBACK, LAST)
    roles = ("implement", "review", "gate")
    manifest = Manifest(
        tmp_path, "c", "spec/c", "main", "", {role: PRIMARY for role in roles},
        ("out/**",), ("openspec/**",), (), max_infra=1,
        chains={role: chain for role in roles}, phase_gates=(1,), gate_review="agent",
    )
    ctx = loop.Ctx(Run(tmp_path / "c"), manifest, {}, agent=unexpected)
    env = SimpleNamespace(ctx=ctx, text=TASKS, calls=[], notices=[], clones=[], extracts=[], diffs=[])
    monkeypatch.setattr(ctx, "head", lambda: HEAD)
    monkeypatch.setattr(ctx, "tasks_text", lambda head: env.text)
    monkeypatch.setattr(loop.openspec, "scenarios", lambda repo, change: {})

    def clone(ctx, head, work):
        env.clones.append(head)
        work.mkdir(parents=True)
        (work / "revision").write_text(head)

    def extract(repo, revision, checkout):
        env.extracts.append(revision)
        checkout.mkdir(parents=True)
        (checkout / "revision").write_text(revision)

    def git(repo, *args):
        assert args[0] == "diff"
        env.diffs.append(args[1:])
        return b"diff --git a/out/component b/out/component\n+connected\n"

    def chmod(argv, **kwargs):
        assert argv[:3] == ["chmod", "-R", "u+w"]
        return SimpleNamespace(returncode=0)

    def notify(message):
        saved = Run(ctx.run.dir).state
        assert saved.exhausted == ctx.run.state.exhausted
        assert saved.tasks == ctx.run.state.tasks
        env.notices.append(message)

    ctx.notify = notify
    monkeypatch.setattr(loop, "_clone", clone)
    monkeypatch.setattr(loop, "git", git)
    monkeypatch.setattr(verify, "extract", extract)
    monkeypatch.setattr(loop.subprocess, "run", chmod)
    # Fail closed if a provider failure ever reaches verification, decision,
    # landing, task rewriting, publication, or a real provider process.
    for owner, names in (
        (loop, ("_land", "_failed", "_rewrite_tasks", "finals")),
        (loop.seal, ("seal",)), (verify, ("run_steps",)),
        (loop.review, ("decide",)), (agents, ("spawn",)),
        (codex, ("run",)), (claude, ("run",)),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, unexpected)
    monkeypatch.setattr(agents.urllib.request, "urlopen", unexpected)
    ctx.run.state.exhausted[SKIP.name] = NOW + 2000
    ctx.run.event("exhausted", profile=SKIP.name, until=NOW + 2000, detail="existing quota limit")
    ctx.run.save()
    return env


def drive_to_fallback(env, role, disposition, detail, dispatch=None):
    """Observe the next actual dispatch, then stop at an explicit boundary.

    The fallback returns its first ordinary infra result. This both tests its
    independent retry allowance and ensures no successful verdict can hide an
    accidental pass/landing during the failed primary calls.
    """
    ctx = env.ctx
    key = "section:1" if role == "gate" else TASK.key
    ts = ctx.run.state.task(key)
    before = work_state(ts)
    initial_sessions = ts.sessions
    primary_calls = ctx.m.max_infra + 1 if disposition == "infra" else 1
    expected = [ctx.m.chains[role][0]] * primary_calls + [FALLBACK]

    def agent(call):
        index = len(env.calls)
        assert index < len(expected), "driver dispatched unexpected extra work"
        assert call.profile == expected[index]
        assert call.access == ("write" if role == "implement" else "read")
        assert ctx.run.state.status == "running" and ctx.run.state.pause is None
        assert ctx.run.state.gates_passed == []
        assert work_state(ts) == before
        assert Run(ctx.run.dir).state.tasks[key] == ts
        env.calls.append(call)
        if call.profile == FALLBACK:
            assert ctx.run.state.exhausted[PRIMARY.name] == NOW + 900
            assert ts.infra == 0
            assert env.notices == [messages.fyi(
                "c", f"primary unavailable: {messages.first_line(detail)}; {role} continues on fallback")]
            ctx.run.request_pause()
        else:
            assert PRIMARY.name not in ctx.run.state.exhausted
            assert ts.infra == index
            assert env.notices == []
        if dispatch is not None:
            return dispatch(call)
        return agents.Result(
            "infra" if call.profile == FALLBACK else disposition,
            detail="fallback's first retry" if call.profile == FALLBACK else detail,
        )

    ctx.agent = agent
    assert loop.run(ctx) == "paused"
    assert [call.profile for call in env.calls] == expected
    saved = Run(ctx.run.dir).state
    assert saved.pause.reason == "requested"  # Only the test's explicit stop.
    assert saved.tasks == ctx.run.state.tasks
    assert work_state(saved.tasks[key]) == before
    assert saved.tasks[key].infra == 1
    assert saved.tasks[key].sessions == initial_sessions + len(expected)
    assert saved.exhausted == {SKIP.name: NOW + 2000, PRIMARY.name: NOW + 900}
    assert saved.gates_passed == []
    assert set(saved.tasks) == {key}
    events = list(agents.read_jsonl(ctx.run.dir / "events.jsonl"))
    assert [event for event in events if event["event"] == "unavailable"] == [dict(
        at=NOW, event="unavailable", task=key, role=role, profile=PRIMARY.name,
        until=NOW + 900, disposition=disposition, detail=detail[:300],
    )]
    retries = [(event["profile"], event["count"]) for event in events if event["event"] == "infra_retry"]
    assert retries == ([(PRIMARY.name, count) for count in range(1, primary_calls + 1)]
                       if disposition == "infra" else []) + [(FALLBACK.name, 1)]
    assert not {event["event"] for event in events} & {
        "attempt_failed", "landed", "gate_passed", "sealed", "reverify",
    }
    assert [event["reason"] for event in events if event["event"] == "pause"] == ["requested"]
    return events


@pytest.mark.scenario("provider-fallback/unavailable-profile-fallback/offline-local-backend-falls-back")
@pytest.mark.scenario("provider-fallback/unavailable-profile-fallback/infrastructure-retries-exhaust")
def test_offline_backend_retries_then_dispatches_first_usable_implementer(driver, monkeypatch):
    env, probes, adapter_calls = driver, [], []
    local = replace(PRIMARY, base_url="http://127.0.0.1:11434/v1")
    env.ctx.m.chains["implement"] = (local, SKIP, FALLBACK, LAST)

    def offline(url, **kwargs):
        probes.append(url)
        raise URLError("scripted local backend is offline")

    def fallback(call):
        adapter_calls.append(call.profile)
        assert call.profile == FALLBACK
        return agents.Result("infra", detail="fallback's first retry")

    # Exercise the unchanged shared dispatcher and reachability loop; replace
    # only the actual HTTP I/O and the provider invocation.
    monkeypatch.setattr(agents.urllib.request, "urlopen", offline)
    monkeypatch.setattr(claude, "run", fallback)
    detail = f"{local.base_url} unreachable"
    events = drive_to_fallback(env, "implement", "infra", detail, agents.run)
    assert probes == [f"http://127.0.0.1:11434{path}" for path in ("/api/tags", "/v1/models", "/health")] * 2
    assert adapter_calls == [FALLBACK]
    assert env.clones == [HEAD] * 3 and env.extracts == []
    assert env.ctx.run.state.section_base == {"1": HEAD}
    assert env.ctx.run.state.tasks[TASK.key].attempts == 0
    assert [(event["task"], event["attempt"]) for event in events if event["event"] == "implement"] == [
        (TASK.key, 1), (TASK.key, 1), (TASK.key, 1),
    ]
    contexts = [(call.cwd.parent / "context" / "task.md").read_text() for call in env.calls]
    assert len(set(contexts)) == 1 and TASK.title() in contexts[0]
    assert env.text == TASKS


@pytest.mark.scenario("provider-fallback/unavailable-profile-fallback/review-and-gate-work-continue")
@pytest.mark.scenario("provider-fallback/unavailable-profile-fallback/infrastructure-retries-exhaust")
@pytest.mark.parametrize("disposition", ["infra", "unavailable"])
def test_pending_candidate_review_continues_without_reimplementation(driver, disposition):
    env = driver
    ts = env.ctx.run.state.task(TASK.key)
    ts.candidate, ts.candidate_base, ts.pending_review = CANDIDATE, HEAD, CANDIDATE
    ts.attempts, ts.sessions, ts.feedback = 1, 2, FINDINGS
    receipts = env.ctx.run.attempt_dir(TASK.key, 1) / "verify" / "receipts.json"
    receipts.parent.mkdir()
    receipt = verify.Receipt("tests", ["scripted-tests"], 0, 0, True, 0.1, "tests passed")
    receipts.write_text(json.dumps([asdict(receipt)]))
    env.ctx.run.event("verified", task=TASK.key, sha=CANDIDATE, passed=True)
    env.ctx.run.save()

    events = drive_to_fallback(env, "review", disposition, DETAIL)
    count = len(env.calls)
    assert env.clones == [] and env.extracts == [CANDIDATE] * count
    assert env.diffs == [(HEAD, CANDIDATE)] * count
    assert [(event["task"], event["sha"]) for event in events if event["event"] == "review"] == [
        (TASK.key, CANDIDATE),
    ] * count
    assert not any(event["event"] == "implement" for event in events)
    assert sum(event["event"] == "verified" for event in events) == 1
    for call in env.calls:
        context = call.cwd.parent / "context"
        assert (call.cwd / "revision").read_text() == CANDIDATE
        assert (context / "prior-review.md").read_text() == FINDINGS + "\n"
        assert (context / "receipts.md").read_text() == verify.summary([receipt]) + "\n"
        assert TASK.title() in (context / "task.md").read_text()
        assert str(context / "prior-review.md") in call.prompt
    assert env.text == TASKS


@pytest.mark.scenario("provider-fallback/unavailable-profile-fallback/review-and-gate-work-continue")
@pytest.mark.scenario("provider-fallback/unavailable-profile-fallback/infrastructure-retries-exhaust")
@pytest.mark.parametrize("disposition", ["infra", "unavailable"])
def test_section_gate_continues_with_existing_findings(driver, disposition):
    env = driver
    env.text = TASKS.replace("[ ] 1.1", "[x] 1.1")
    ts = env.ctx.run.state.task("section:1")
    ts.attempts, ts.feedback = 1, FINDINGS  # Fix round completed; gate still pending.
    env.ctx.run.state.section_base = {"1": SECTION_BASE}
    env.ctx.run.state.evidence_summary = {"1": "section evidence retained"}
    env.ctx.run.save()

    events = drive_to_fallback(env, "gate", disposition, DETAIL)
    count = len(env.calls)
    assert env.clones == [] and env.extracts == [HEAD] * count
    assert env.diffs == [(SECTION_BASE, HEAD)] * count
    assert [(event["section"], event["head"]) for event in events if event["event"] == "gate_review"] == [
        (1, HEAD),
    ] * count
    assert not any(event["event"] == "implement" for event in events)
    sections = []
    for call in env.calls:
        context = call.cwd.parent / "context"
        sections.append((context / "section.md").read_text())
        assert (call.cwd / "revision").read_text() == HEAD
        assert (context / "prior-review.md").read_text() == FINDINGS + "\n"
        assert (context / "evidence.md").read_text() == "section evidence retained\n"
        assert "Review of section 1: Core" in call.prompt
        assert "second review" in call.prompt
    assert len(set(sections)) == 1 and "[x] 1.1 Connect the components" in sections[0]
    assert env.text == TASKS.replace("[ ] 1.1", "[x] 1.1")

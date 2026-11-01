"""Synthetic CLI diagnostics through the real adapters and fallback driver."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from specloop import agents, loop, messages, tasks
from specloop.agents import claude, codex
from specloop.manifest import Manifest, Profile
from specloop.state import Run


SESSION = "synthetic-session"
USAGE = {"input_tokens": 17, "output_tokens": 3}
AUTH = [
    "You are logged out", "Logged-out session", "Not logged in",
    "Login required", "Authentication is required", "Please log in",
    "You must sign in", "Invalid authentication token", "Expired auth token",
    "OAuth token has expired", "Invalid access token", "Refresh token is invalid",
    "Token has expired", "HTTP 401: Unauthorized", "HTTP 403: Forbidden", "401", "403",
]
PLANS = [
    f"Your {noun} is {status}"
    for noun in ("plan", "subscription")
    for status in ("inactive", "expired", "cancelled", "canceled")
] + ["Inactive plan", "Expired subscription", "Cancelled plan", "Subscription has been canceled"]


@pytest.fixture(params=["codex", "claude"])
def provider(request, tmp_path, monkeypatch):
    """Only process I/O is synthetic; never use an operator's adapter home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(agents, "homes_dir", lambda: tmp_path / "homes")
    adapter = {"codex": codex, "claude": claude}[request.param]
    profile = Profile("primary", request.param, "synthetic-model")
    default_call = agents.Call(profile, "write", "prompt", tmp_path, {"type": "object"},
                               tmp_path / "call.jsonl", 60, 30, tmp_path / "homes" / "primary")

    def invoke(diagnostic, *, source="response", outcome="failed", timeout="", metadata=True,
               usage=USAGE, extra=None, call=None):
        call = call or default_call
        call.home.mkdir(parents=True, exist_ok=True)
        (call.home / "auth.json").write_text("{}")  # Prevent Codex's host-login symlink.
        failed = outcome == "failed"
        output = {"status": "blocked" if outcome == "blocked" else "ready",
                  "summary": diagnostic, "subtasks": []}

        def spawn(argv, env, actual, stdin=None):
            assert actual == call and stdin == call.prompt
            assert env["CODEX_HOME" if adapter is codex else "CLAUDE_CONFIG_DIR"] == str(call.home)
            records = []
            if adapter is codex:
                if metadata:
                    records.append({"type": "thread.started", "thread_id": SESSION})
                    records.append({"type": "turn.completed", "usage": usage})
                if failed and source != "stderr":
                    error = ({"type": "turn.failed", "error": {"message": diagnostic}}
                             if source == "alternate" else {"type": "error", "message": diagnostic})
                    records.append({**error, **(extra or {})})
                if not failed and outcome != "missing":
                    Path(argv[argv.index("-o") + 1]).write_text(
                        diagnostic if outcome == "invalid" else json.dumps(output))
            else:
                if metadata:
                    records.append({"type": "system", "subtype": "init", "session_id": SESSION})
                if outcome != "missing" and (source != "stderr" or metadata):
                    final = {"type": "result", "subtype": "error_during_execution" if failed else "success",
                             "is_error": failed and source != "alternate",
                             "result": diagnostic if source != "stderr" else "",
                             "structured_output": diagnostic if outcome == "invalid" else output}
                    if source == "stderr":
                        final.pop("structured_output")
                    elif source == "alternate":
                        final.pop("result")
                        final.pop("structured_output")
                        final["errors"] = [diagnostic]
                    if metadata:
                        final["usage"] = usage
                    final.update(extra or {})
                    records.append(final)
            actual.transcript.parent.mkdir(parents=True, exist_ok=True)
            actual.transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
            actual.transcript.with_suffix(".stderr").write_text(diagnostic if source == "stderr" else "")
            return (None if timeout else 1 if failed else 0), timeout

        monkeypatch.setattr(adapter, "spawn", spawn)
        return agents.run(call)

    return SimpleNamespace(run=invoke, profile=profile, call=default_call)


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/authentication-and-plan-diagnostics")
@pytest.mark.parametrize("diagnostic", AUTH + PLANS)
@pytest.mark.parametrize("source", ["response", "alternate", "stderr"])
@pytest.mark.parametrize("prefix", ["", "Request failed:\n"])
def test_authentication_and_plan_diagnostics(provider, diagnostic, source, prefix):
    diagnostic = prefix + diagnostic.upper()  # Both providers must match case-insensitively.
    result = provider.run(diagnostic, source=source)
    assert result.disposition == "unavailable"
    assert result.session == SESSION and result.usage == USAGE
    assert result.output is None
    retained = diagnostic if source == "stderr" else json.dumps(diagnostic)[1:-1]
    assert result.detail.startswith("exit 1: ") and retained in result.detail


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/authentication-and-plan-diagnostics")
def test_stderr_authentication_without_session_or_usage(provider):
    result = provider.run("Login required", source="stderr", metadata=False)
    assert result == agents.Result("unavailable", detail=(
        "exit 1: " + ("{}" if provider.profile.adapter == "claude" else "") + "Login required"))


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/usage-matching-retains-priority")
@pytest.mark.parametrize("quota", ["usage limit", "rate limit", "quota", "429", "adapter-specific"])
@pytest.mark.parametrize("auth", ["", "; Invalid OAuth token", "; Subscription is expired",
                                "\nLogin required"])
@pytest.mark.parametrize("source", ["response", "stderr"])
def test_usage_matching_retains_priority(provider, quota, auth, source):
    if quota == "adapter-specific":
        quota = "too many requests" if provider.profile.adapter == "codex" else "limit reached"
    diagnostic = quota + auth
    if provider.profile.adapter == "claude" and source == "response":
        source = "alternate"
    result = provider.run(diagnostic, source=source)
    assert result.disposition == "usage_limit"
    assert result.session == SESSION and result.usage == USAGE
    assert result.output is None
    retained = diagnostic if source == "stderr" else json.dumps(diagnostic)[1:-1]
    assert result.detail.startswith("exit 1: ") and retained in result.detail


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/usage-matching-retains-priority")
def test_usage_diagnostic_keeps_existing_stderr_truncation(provider):
    diagnostic = "transport context " * 150 + "quota exceeded; Invalid token" + " trailing context" * 20
    result = provider.run(diagnostic, source="stderr", metadata=False)
    retained = diagnostic[-600:] if provider.profile.adapter == "codex" else "{}" + diagnostic[-800:]
    assert result == agents.Result("usage_limit", detail="exit 1: " + retained)


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/other-outcomes-retain-their-meaning")
@pytest.mark.parametrize("diagnostic", [
    "Connection reset by peer", "Upstream returned 500", "Transport failed after 1401 ms",
    "Error 4030", "Request id 94019", "Request id 94039", "code401 code403",
    "Token counting failed", "Unable to read plan file", "Invalid execution plan",
    "Subscription lookup timed out", "Expired cache entry", "Invalid tokenization setting",
])
@pytest.mark.parametrize("source", ["response", "stderr"])
def test_unrelated_diagnostics_stay_infra(provider, diagnostic, source):
    result = provider.run(diagnostic, source=source)
    assert result.disposition == "infra"
    assert diagnostic in result.detail
    assert result.session == SESSION and result.usage == USAGE


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/other-outcomes-retain-their-meaning")
@pytest.mark.parametrize("count", [400, 401, 403, 404])
@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_usage_metadata_numbers_do_not_mark_transport_failure_unavailable(provider, count, field):
    usage = {**USAGE, field: count}
    result = provider.run("Connection reset by peer", usage=usage)
    assert result.disposition == "infra"
    assert "Connection reset by peer" in result.detail
    assert result.session == SESSION and result.usage == usage


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/other-outcomes-retain-their-meaning")
@pytest.mark.parametrize("extra", [
    {"num_turns": 403}, {"session_id": "401"},
    {"structured_output": {"summary": "Invalid token; subscription is expired"}},
])
def test_non_diagnostic_fields_do_not_mark_transport_failure_unavailable(provider, extra):
    result = provider.run("Connection reset by peer", extra=extra)
    assert result.disposition == "infra"
    assert "Connection reset by peer" in result.detail
    assert result.session == SESSION and result.usage == USAGE


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/other-outcomes-retain-their-meaning")
@pytest.mark.parametrize("timeout", ["wall timeout", "idle timeout"])
def test_timeout_takes_priority_over_all_diagnostics(provider, timeout):
    result = provider.run("401 invalid token; quota exceeded", source="stderr", timeout=timeout)
    assert result == agents.Result("infra", session=SESSION, detail=timeout, usage=USAGE)


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/other-outcomes-retain-their-meaning")
@pytest.mark.parametrize("outcome,expected", [("ok", "ok"), ("invalid", "invalid_output"),
                                              ("missing", "invalid_output")])
def test_successful_and_invalid_output_keep_their_meaning(provider, outcome, expected):
    # A missing Claude result has always been infra, unlike missing Codex final JSON.
    if outcome == "missing" and provider.profile.adapter == "claude":
        expected = "infra"
    diagnostic = "Plan for token handling: report 401, invalid OAuth token, expired subscription and quota"
    result = provider.run(diagnostic, outcome=outcome)
    assert result.disposition == expected and result.session == SESSION
    assert result.usage == ({} if outcome == "missing" and provider.profile.adapter == "claude" else USAGE)
    if expected == "ok":
        assert result.output["summary"] == diagnostic
    else:
        assert result.output is None and result.detail


@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/other-outcomes-retain-their-meaning")
def test_successful_json_that_fails_schema_validation_stays_invalid(provider):
    call = replace(provider.call, schema={"type": "object", "required": ["missing_field"]})
    result = provider.run("401 invalid token; inactive plan", outcome="ok", call=call)
    assert result.disposition == "invalid_output"
    assert result.detail == "$: missing missing_field"
    assert result.output["summary"] == "401 invalid token; inactive plan"
    assert result.session == SESSION and result.usage == USAGE


@pytest.mark.scenario("provider-fallback/unavailable-profile-fallback/authentication-bypasses-retries")
@pytest.mark.parametrize("diagnostic", ["Not logged in", "OAuth token has expired", "Subscription is inactive",
                                      "Request failed:\n401", "Request failed:\nLogin required",
                                      "Request failed:\nSubscription is inactive"])
@pytest.mark.parametrize("source", ["response", "alternate", "stderr"])
def test_auth_failure_marks_profile_unavailable_immediately(provider, diagnostic, source, tmp_path, monkeypatch):
    now, notices, calls, results = 1000.0, [], [], []
    monkeypatch.setattr(loop.time, "time", lambda: now)
    # Ctx normally gathers credential values for sealing; no sealing occurs here.
    monkeypatch.setattr(loop.seal, "credential_values", lambda secrets: [])
    primary, fallback = provider.profile, replace(provider.profile, name="fallback")
    manifest = Manifest(tmp_path, "c", "spec/c", "main", "", {"implement": primary},
                        ("out/**",), ("openspec/**",), (), max_infra=3,
                        chains={"implement": (primary, fallback)})
    ctx = loop.Ctx(Run(tmp_path / "run"), manifest, {})
    text = "## 1. Core\n- [ ] 1.1 Connect the components\n"
    task = tasks.parse(text)[0]
    monkeypatch.setattr(ctx, "head", lambda: "head")
    monkeypatch.setattr(ctx, "tasks_text", lambda head: text)
    monkeypatch.setattr(loop.openspec, "scenarios", lambda repo, change: {})
    monkeypatch.setattr(loop, "_clone", lambda ctx, head, work: work.mkdir(parents=True))

    def unavailable_event():
        return dict(at=now, event="unavailable", task=task.key, role="implement", profile=primary.name,
                    until=now + 900, disposition="unavailable", detail=results[0].detail[:300])

    def assert_persisted():
        saved = Run(ctx.run.dir).state
        assert saved.status == "running" and saved.pause is None
        assert saved.exhausted == {primary.name: now + 900}
        assert saved.tasks[task.key].infra == saved.tasks[task.key].attempts == 0
        events = list(agents.read_jsonl(ctx.run.dir / "events.jsonl"))
        assert [e for e in events if e["event"] == "unavailable"] == [unavailable_event()]
        assert not any(e["event"] == "infra_retry" for e in events)

    def notify(message):
        if not notices:
            assert_persisted()  # Durability precedes the fallback notification.
        notices.append(message)

    def dispatch(call):
        calls.append(call.profile)
        assert calls == [primary] or calls == [primary, fallback], "unexpected retry or extra dispatch"
        if call.profile == primary:
            result = provider.run(diagnostic, source=source, call=call)
            assert result.disposition == "unavailable"
        else:
            assert_persisted()
            assert notices == [messages.fyi("c", f"primary unavailable: {messages.first_line(results[0].detail)}; "
                                           "implement continues on fallback")]
            # A successful structured response deliberately stops this synthetic task.
            result = provider.run("fixture stop", outcome="blocked", call=call)
        results.append(result)
        return result

    ctx.agent, ctx.notify = dispatch, notify
    assert loop.run(ctx) == "paused"
    assert calls == [primary, fallback]
    saved = Run(ctx.run.dir).state
    assert saved.pause.reason == "blocked" and saved.pause.detail == "fixture stop"
    assert saved.exhausted == {primary.name: now + 900}
    assert saved.tasks[task.key].infra == saved.tasks[task.key].attempts == 0
    assert saved.tasks[task.key].sessions == 2
    events = list(agents.read_jsonl(ctx.run.dir / "events.jsonl"))
    assert [e for e in events if e["event"] == "unavailable"] == [unavailable_event()]
    assert not any(e["event"] == "infra_retry" for e in events)
    assert [(e["profile"], e["disposition"], e["session_id"], e["usage"])
            for e in events if e["event"] == "implemented"] == [
        (primary.name, "unavailable", SESSION, USAGE), (fallback.name, "ok", SESSION, USAGE),
    ]

"""Synthetic telemetry through the shared decoder and the real Claude adapter."""

import json
from pathlib import Path

import pytest

from nightshift import trace
from specloop import agents
from specloop.agents import claude
from specloop.manifest import Profile


USAGE = {"input_tokens": 17, "output_tokens": 3}


def limit(status="allowed", **fields):
    return {"type": "rate_limit_event", "rate_limit_info": {
        "status": status, "rateLimitType": "five_hour", "resetsAt": 1800000000,
        "isUsingOverage": False, **fields}}


def final(**fields):
    return {"type": "result", "subtype": "success", "usage": USAGE,
            "structured_output": {"answer": "done"}, **fields}


@pytest.fixture
def invoke(tmp_path, monkeypatch):
    def run(events, *, stderr="", timeout="", schema=None):
        call = agents.Call(Profile("test", "claude", "synthetic"), "read", "prompt", tmp_path,
                           schema or {"type": "object"}, tmp_path / "trace.jsonl", 60, 30,
                           tmp_path / "home")
        records = [{"type": "system", "subtype": "init", "session_id": "session"}, *events]
        transcript = "\n".join(map(json.dumps, records))

        def spawn(*args, **kwargs):
            call.transcript.write_text(transcript)
            call.transcript.with_suffix(".stderr").write_text(stderr)
            return (None if timeout else 1 if any(e.get("is_error") for e in events) else 0), timeout

        monkeypatch.setattr(claude, "spawn", spawn)
        result = agents.run(call)
        assert result.session == "session"
        terminal = next((e for e in reversed(events) if e.get("type") == "result"), {})
        assert result.usage == terminal.get("usage", {})
        historical = trace.parse(transcript)
        if historical is not None:
            assert historical.rate_limit == result.rate_limit
        else:
            assert not any(e.get("type") == "result" for e in events)
        return result
    return run


@pytest.mark.scenario("provider-fallback/shared-rate-limit-telemetry/telemetry-survives-incomplete-output")
@pytest.mark.parametrize("position", [0, 1, 2, None])
@pytest.mark.parametrize("statuses", [("rejected", "allowed"), ("allowed", "rejected")])
def test_last_event_in_stream_order(invoke, position, statuses):
    events = [limit(statuses[0], isUsingOverage=True), limit(statuses[1], rateLimitType="seven_day")]
    if position is not None:
        events.insert(position, final())
    result = invoke(events)
    assert result.rate_limit == trace.RateLimit(statuses[1], "seven_day", 1800000000, False)
    expected = "usage_limit" if statuses[1] == "rejected" else "infra" if position is None else "ok"
    assert result.disposition == expected


@pytest.mark.scenario("provider-fallback/shared-rate-limit-telemetry/telemetry-survives-incomplete-output")
@pytest.mark.parametrize("name", ["worker1", "worker3b"])
def test_historical_trace_fixture_retains_snapshot(name):
    transcript = (Path(__file__).parent / "fixtures" / f"{name}.jsonl").read_text()
    result = trace.parse(transcript)
    assert result is not None
    assert result.rate_limit == trace.RateLimit("allowed", "five_hour", 1785705600, False)
    incomplete = "\n".join(line for line in transcript.splitlines()
                           if json.loads(line).get("type") != "result")
    assert trace.parse(incomplete) is None


@pytest.mark.scenario("provider-fallback/shared-rate-limit-telemetry/malformed-telemetry-is-bounded")
@pytest.mark.parametrize("payload", [None, False, 1, "quota", [], ["rejected"]])
def test_non_mapping_payload_does_not_erase_snapshot(invoke, payload):
    event = {"type": "rate_limit_event", "rate_limit_info": payload}
    assert trace.parse_rate_limit(event) is None
    result = invoke([limit(), final(), event])
    assert result.disposition == "ok"
    assert result.rate_limit == trace.RateLimit("allowed", "five_hour", 1800000000, False)


@pytest.mark.scenario("provider-fallback/shared-rate-limit-telemetry/malformed-telemetry-is-bounded")
@pytest.mark.parametrize("bad", [None, True, False, "1800000000", 1.5, 1.0, 0, -1,
                                 float("inf"), float("-inf"), float("nan"), [], {}])
def test_malformed_fields_normalize(invoke, bad):
    event = limit(status=[], rateLimitType=123, resetsAt=bad, isUsingOverage="true")
    result = invoke([event, final(is_error=True)])
    assert result.disposition == "infra"
    assert result.rate_limit == trace.RateLimit("unknown", "unknown", 0, False)
    assert trace.parse_rate_limit({"type": "rate_limit_event", "rate_limit_info": {}}) == result.rate_limit
    assert trace.parse_rate_limit({"type": "result", "rate_limit_info": event["rate_limit_info"]}) is None


@pytest.mark.scenario("provider-fallback/shared-rate-limit-telemetry/malformed-telemetry-is-bounded")
def test_metadata_survives_schema_validation(invoke):
    result = invoke([limit(), final()], schema={"type": "object", "required": ["missing"]})
    assert result.disposition == "invalid_output" and result.detail == "$: missing missing"
    assert result.output == {"answer": "done"}
    assert result.rate_limit == trace.RateLimit("allowed", "five_hour", 1800000000, False)


@pytest.mark.scenario("provider-fallback/shared-rate-limit-telemetry/malformed-telemetry-is-bounded")
@pytest.mark.parametrize("value", [True, False, 1, "true", "false", None, [], {}])
def test_only_literal_true_means_overage(invoke, value):
    result = invoke([limit(isUsingOverage=value), final()])
    assert result.rate_limit.using_overage is (value is True)
    assert result.disposition == ("usage_limit" if value is True else "ok")


@pytest.mark.scenario("provider-fallback/structured-claude-usage-classification/agent-prose-cannot-exhaust-a-profile")
@pytest.mark.parametrize("word", ["quota", "rate limit", "usage limit", "limit reached", "429"])
@pytest.mark.parametrize("field", ["result", "structured_output", "usage", "session_id", "errors"])
def test_agent_text_mentioning_quota_is_not_usage_limit(invoke, word, field):
    # Nested error objects are not explicit string provider diagnostics either.
    value = {"summary": word} if field in ("structured_output", "errors", "usage") else word
    event = final(is_error=True, **{field: value})
    if field == "usage":
        event["usage"]["input_tokens"] = 429
    result = invoke([event])
    assert result.disposition == "infra" and result.output is None
    assert word in result.detail and result.rate_limit is None


@pytest.mark.scenario("provider-fallback/structured-claude-usage-classification/rejection-is-authoritative")
@pytest.mark.scenario("provider-fallback/structured-claude-usage-classification/provider-diagnostics-remain-a-fallback")
@pytest.mark.parametrize("outcome", ["success", "failed", "missing"])
@pytest.mark.parametrize("timeout", ["", "wall timeout", "idle timeout"])
@pytest.mark.parametrize("status,overage", [("rejected", False), ("allowed", True)])
def test_structured_limit_and_timeout_precedence(invoke, outcome, timeout, status, overage):
    events = [limit(status, isUsingOverage=overage)]
    if outcome != "missing":
        events.append(final(is_error=outcome == "failed", result="ordinary text"))
    result = invoke(events, timeout=timeout, stderr="quota; Login required")
    assert result.disposition == ("infra" if timeout and not overage else "usage_limit")
    assert result.output is None and result.rate_limit.using_overage is overage
    if timeout and not overage:
        assert result.detail == timeout


@pytest.mark.scenario("provider-fallback/structured-claude-usage-classification/allowed-and-warning-events-do-not-exhaust")
@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/authentication-and-plan-diagnostics")
@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/other-outcomes-retain-their-meaning")
@pytest.mark.parametrize("status", ["allowed", "allowed_warning"])
@pytest.mark.parametrize("fields,diagnostic,expected", [
    ({}, "quota; Invalid token", "ok"),
    ({"structured_output": None}, "quota", "invalid_output"),
    ({"is_error": True}, "quota", "infra"),
    ({"is_error": True}, "quota; Login required", "unavailable"),
    ({"is_error": True, "result": "quota; Subscription is expired"}, "", "unavailable"),
])
def test_advisory_status_preserves_ordinary_outcomes(invoke, status, fields, diagnostic, expected):
    result = invoke([limit(status), final(**fields)], stderr=diagnostic)
    assert result.disposition == expected and result.rate_limit.status == status


@pytest.mark.scenario("provider-fallback/structured-claude-usage-classification/provider-diagnostics-remain-a-fallback")
@pytest.mark.scenario("provider-fallback/unavailable-failure-classification/usage-matching-retains-priority")
@pytest.mark.parametrize("status", [None, "future_status"])
@pytest.mark.parametrize("errors,stderr,expected", [
    ("quota; Invalid token", "", "usage_limit"),
    ([{}, 429, "rate limit\nLogin required"], "", "usage_limit"),
    ([], "context " * 200 + "429; Subscription is expired", "usage_limit"),
    ([{"message": "quota"}, 429], "transport error 1401", "infra"),
    (None, "quota" + "x" * 800, "infra"),
])
def test_provider_diagnostic_fallback(invoke, status, errors, stderr, expected):
    event = final(is_error=True, errors=errors)
    result = invoke(([limit(status)] if status else []) + [event], stderr=stderr)
    assert result.disposition == expected and result.output is None
    assert result.detail == "exit 1: " + json.dumps(event)[:800] + stderr[-800:]

"""Adapter tests use synthetic or already-redacted streams; no model invocation."""
import json
import signal
from pathlib import Path
from unittest.mock import Mock

import pytest

from nightshift import config, worker
from nightshift.workers.claude import ClaudeWorker, normalize


def stream(*events, returncode=0):
    return worker.Run(returncode, "\n".join(json.dumps(event) for event in events))


def terminal(**fields):
    return {"type": "result", "subtype": "success", "is_error": False,
            "result": "Done", **fields}


def test_missing_usage_models_and_billing_stay_unknown():
    adapted = normalize(stream(terminal()), requested_model="sonnet")
    assert adapted.result.ok
    assert adapted.result.requested_model == "sonnet"
    assert adapted.result.observed_model is None
    assert adapted.result.runtime_version is None
    assert adapted.result.output_tokens is None
    assert adapted.result.usage == {}
    assert adapted.native_usage is None and adapted.model_usage is None
    assert adapted.legacy.output_tokens == 0  # Preserved only in the legacy view.


def test_reported_usage_preserves_units_zero_and_additive_fields():
    usage = {"input_tokens": 10, "output_tokens": 0, "cache_read_input_tokens": 20,
             "cache_creation_input_tokens": 5, "future_counter": {"unknown_unit": 7}}
    model_usage = {"actual-model": {"outputTokens": 0, "costBasis": "unknown"}}
    adapted = normalize(stream(terminal(usage=usage, modelUsage=model_usage,
                                        total_cost_usd=123, duration_ms=1250)))
    assert adapted.result.output_tokens == 0
    assert adapted.result.usage == {"inputTokens": 10, "outputTokens": 0,
                                    "cachedInputTokens": 20, "cacheWriteInputTokens": 5}
    assert "totalTokens" not in adapted.result.usage
    assert adapted.native_usage == usage
    assert adapted.model_usage == model_usage
    assert adapted.result.duration_s == 1.25
    assert adapted.result.observed_model is None  # Accounting keys don't prove the primary model.
    assert "costUSD" not in adapted.result.usage


def test_observed_model_is_derived_from_actual_messages_only():
    header = {"type": "system", "subtype": "init", "model": "alias", "claude_code_version": "test-version"}
    message = {"type": "assistant", "message": {"model": "actual-model", "content": []}}
    result = normalize(stream(header, message, terminal()), requested_model="alias").result
    assert result.observed_model == "actual-model"
    assert result.runtime_version == "test-version"
    second = {"type": "assistant", "message": {"model": "another-model", "content": []}}
    adapted = normalize(stream(message, second, terminal()))
    assert adapted.result.observed_model is None
    assert adapted.observed_models == ("actual-model", "another-model")


def test_tool_requests_never_become_completed_verification():
    event = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}}]}}
    adapted = normalize(stream(event, terminal(result="pytest passed")))
    assert adapted.requested_commands == ("pytest",)
    assert adapted.result.commands == []


@pytest.mark.parametrize(("returncode", "fields", "status"), [
    (1, {}, "failed"), (-signal.SIGTERM, {}, "interrupted"),
    (-signal.SIGSEGV, {}, "failed"),
    (1, {"subtype": "error_max_turns", "is_error": True}, "budget_exhausted"),
    (0, {"subtype": "error_max_turns"}, "budget_exhausted"),
    (1, {"api_error_status": 401, "is_error": True}, "auth_failed"),
    (1, {"api_error_status": 429, "is_error": True}, "rate_limited"),
    (0, {"subtype": "future_success"}, "protocol_error"),
    (0, {"is_error": None}, "protocol_error"),
    (0, {"is_error": True}, "failed"),
])
def test_terminal_and_process_failure_mapping(returncode, fields, status):
    adapted = normalize(stream(terminal(**fields), returncode=returncode))
    assert adapted.result.status == status
    assert not adapted.result.ok
    if returncode != 0:
        assert not adapted.legacy.ok


def test_missing_terminal_and_malformed_legacy_stream_fail_closed():
    assert normalize(worker.Run(0, "ordinary diagnostic\n")).result.status == "protocol_error"
    assert normalize(stream({"type": "assistant", "message": None}, terminal())).result.status == "protocol_error"
    assert normalize(worker.Run(1, "ordinary diagnostic\n")).result.status == "failed"


def test_plain_verdict_is_preserved_without_inventing_structured_findings():
    adapted = normalize(stream(terminal(result="VERDICT: PASS")), role="review")
    assert adapted.legacy_verdict is True
    assert adapted.result.reviewer_verdict is None
    assert normalize(stream(terminal(result="VERDICT: FAIL")), role="review").legacy_verdict is False
    assert normalize(stream(terminal(result="No verdict")), role="review").legacy_verdict is None


@pytest.mark.parametrize("name", ["worker1", "worker3b"])
def test_redacted_fixtures_preserve_legacy_result(name):
    path = Path(__file__).parent / "fixtures" / f"{name}.jsonl"
    if not path.exists():
        pytest.fail(f"Missing fixture {name}")
    raw = worker.Run(0, path.read_text())
    adapted = normalize(raw)
    assert adapted.legacy == worker.parse_result(raw)
    assert adapted.result.text == adapted.legacy.text
    assert adapted.result.commands == []
    assert adapted.result.output_tokens == adapted.legacy.output_tokens


@pytest.mark.parametrize("role", ["implement", "review"])
def test_invocation_preserves_explicit_legacy_routing(tmp_path, monkeypatch, role):
    impl, review = Mock(return_value=stream(terminal())), Mock(return_value=stream(terminal()))
    monkeypatch.setattr(worker, "implement", impl)
    monkeypatch.setattr(worker, "review", review)
    endpoint = config.DEFAULT_ENDPOINT
    transcript = tmp_path / "native.jsonl"
    adapted = ClaudeWorker().run(tmp_path, "Task", role=role, model="explicit-model", max_turns=17,
                                 endpoint=endpoint, context_tokens=123,
                                 foreign_auth_envs=("OTHER_KEY",), transcript=transcript)
    selected, other = (impl, review) if role == "implement" else (review, impl)
    selected.assert_called_once_with(tmp_path, "Task", model="explicit-model", max_turns=17,
                                     endpoint=endpoint, context_tokens=123,
                                     foreign_auth_envs=("OTHER_KEY",), transcript=transcript)
    other.assert_not_called()
    assert adapted.result.ok


def test_invocation_failure_is_typed_without_dumping_sensitive_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "implement", Mock(side_effect=OSError("synthetic-private-detail")))
    result = ClaudeWorker().run(tmp_path, "Task", role="implement", model="explicit", max_turns=1).result
    assert result.status == "failed"
    assert "synthetic-private-detail" not in str(result.diagnostics)


@pytest.mark.parametrize("kwargs", [{"role": "plan"}, {"max_turns": 0}, {"max_turns": True}, {"model": ""}])
def test_invalid_invocation_cannot_start_worker(tmp_path, monkeypatch, kwargs):
    invoke = Mock()
    monkeypatch.setattr(worker, "implement", invoke)
    options = {"role": "implement", "model": "explicit", "max_turns": 1, **kwargs}
    with pytest.raises(ValueError):
        ClaudeWorker().run(tmp_path, "Task", **options)
    invoke.assert_not_called()

"""Synthetic HTTPS fixtures only. Never access a credential store or provider."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from nightshift.workers import openai_smoke as smoke

KEY = "synthetic-dedicated-key"


def good(**values):
    return {"status": "completed", "error": None, "model": "reported-model",
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": smoke.MARKER}]}], **values}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv(smoke.KEY_NAME, KEY)
    connection = Mock()
    connection.getresponse.return_value.status = 200
    connection.getresponse.return_value.read.return_value = json.dumps(good()).encode()
    factory = Mock(return_value=connection)
    monkeypatch.setattr(smoke.http.client, "HTTPSConnection", factory)
    return factory, connection


def test_exact_request_has_no_tools_fixed_prompt_and_no_proxy(isolated, monkeypatch):
    factory, connection = isolated
    monkeypatch.setenv("HTTPS_PROXY", "https://untrusted.invalid")
    report = smoke.run("chosen-model")
    assert report["status"] == "succeeded"
    assert report["requested_model"] == "chosen-model" and report["observed_model"] == "reported-model"
    assert report["usage"]["output_tokens"] is None
    assert factory.call_count == connection.request.call_count == 1
    assert factory.call_args.args == ("api.openai.com",)
    assert factory.call_args.kwargs["port"] == 443
    assert factory.call_args.kwargs["timeout"] == 30
    args, kwargs = connection.request.call_args
    assert args == ("POST", "/v1/responses")
    assert json.loads(kwargs["body"]) == {"model": "chosen-model", "input": "Reply with exactly NIGHTSHIFT_OPENAI_OK and nothing else.",
                                         "tools": [], "tool_choice": "none", "store": False, "max_output_tokens": 1024}
    assert kwargs["headers"]["Authorization"] == "Bearer " + KEY
    connection.getresponse.return_value.read.assert_called_once_with(1024 * 1024 + 1)
    assert KEY not in json.dumps(report)
    connection.close.assert_called_once()


@pytest.mark.parametrize(("status", "expected"), [(301, "redirect_rejected"), (307, "redirect_rejected"),
    (401, "auth_failed"), (403, "auth_failed"), (429, "rate_limited"), (500, "http_error")])
def test_http_errors_do_not_read_bodies_retry_or_follow_redirects(isolated, status, expected):
    _, connection = isolated
    response = connection.getresponse.return_value
    response.status = status
    response.read.return_value = KEY.encode()
    assert smoke.run("chosen")["status"] == expected
    response.read.assert_not_called()
    assert connection.request.call_count == 1


@pytest.mark.parametrize(("exception", "expected"), [(TimeoutError(KEY), "socket_timeout"), (OSError(KEY), "transport_error")])
def test_transport_exceptions_never_leak_secret(isolated, exception, expected):
    _, connection = isolated
    connection.request.side_effect = exception
    result = smoke.run("chosen")
    assert result["status"] == expected
    assert KEY not in json.dumps(result)
    assert connection.request.call_count == 1


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"{" + b"a" * smoke.RESPONSE_LIMIT])
def test_invalid_and_oversized_response_fail(isolated, body):
    isolated[1].getresponse.return_value.read.return_value = body
    assert smoke.run("chosen")["status"] in {"protocol_error", "response_too_large"}


@pytest.mark.parametrize("replacement", [
    {"status": "incomplete"}, {"error": {"message": KEY}}, {"output": []},
    {"output": [{"type": "function_call", "arguments": KEY}]},
    {"output": [{"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "refusal", "refusal": KEY}]}]},
    {"output": [{"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": smoke.MARKER + "\n"}]}]},
])
def test_only_exact_completed_assistant_marker_succeeds(isolated, replacement):
    isolated[1].getresponse.return_value.read.return_value = json.dumps(good(**replacement)).encode()
    result = smoke.run("chosen")
    assert result["status"] != "succeeded"
    assert KEY not in json.dumps(result)


def test_numeric_usage_only_and_model_not_inferred(isolated):
    body = good(model=KEY, usage={"input_tokens": 12, "output_tokens": 0, "total_tokens": True,
                                 "input_tokens_details": {"cached_tokens": -1},
                                 "output_tokens_details": {"reasoning_tokens": 3}})
    isolated[1].getresponse.return_value.read.return_value = json.dumps(body).encode()
    report = smoke.run("chosen")
    assert report["observed_model"] is None
    assert report["usage"] == {"input_tokens": 12, "output_tokens": 0, "total_tokens": None,
                                "cached_input_tokens": None, "reasoning_output_tokens": 3}
    assert KEY not in json.dumps(report)


def test_no_personal_key_fallback(isolated, monkeypatch):
    monkeypatch.delenv(smoke.KEY_NAME)
    monkeypatch.setenv("OPENAI_API_KEY", "personal-secret")
    assert smoke.run("chosen")["status"] == "missing_or_invalid_dedicated_credential"
    isolated[0].assert_not_called()


def test_private_file_only_explicitly_selected(tmp_path, monkeypatch, isolated):
    target = tmp_path / ".config" / "nightshift" / "env"
    target.parent.mkdir(parents=True)
    target.write_text("export OTHER_KEY='ignored'\nexport NIGHTSHIFT_OPENAI_API_KEY='file-dedicated'\n")
    target.chmod(0o600)
    assert smoke.load_key() == KEY
    assert smoke.load_key(use_nightshift_env=True) == "file-dedicated"
    target.chmod(0o640)
    assert smoke.run("chosen", use_nightshift_env=True)["status"] == "credential_file_not_private"
    isolated[0].assert_not_called()


def test_duplicate_and_shell_syntax_are_never_evaluated(tmp_path):
    target = tmp_path / ".config" / "nightshift" / "env"
    target.parent.mkdir(parents=True)
    target.write_text("NIGHTSHIFT_OPENAI_API_KEY=$(touch /tmp/should-never-execute)\n")
    target.chmod(0o600)
    with pytest.raises(smoke.CredentialError):
        smoke.load_key(use_nightshift_env=True)
    target.write_text("NIGHTSHIFT_OPENAI_API_KEY=one\nNIGHTSHIFT_OPENAI_API_KEY=two\n")
    with pytest.raises(smoke.CredentialError):
        smoke.load_key(use_nightshift_env=True)


def test_cli_never_accepts_or_echoes_secret_arguments(capsys):
    with pytest.raises(SystemExit):
        smoke.main(["--model", "chosen", "--api-key", KEY])
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err


def test_cli_outputs_only_safe_summary(capsys):
    assert smoke.main(["--model", "chosen"]) == 0
    output = capsys.readouterr().out
    assert KEY not in output and smoke.MARKER not in output
    assert json.loads(output)["status"] == "succeeded"



def test_fifo_credential_source_is_rejected_without_blocking(tmp_path, isolated):
    import os
    target = tmp_path / ".config" / "nightshift" / "env"
    target.parent.mkdir(parents=True)
    os.mkfifo(target, 0o600)
    assert smoke.run("chosen", use_nightshift_env=True)["status"] == "credential_file_not_private"
    isolated[0].assert_not_called()

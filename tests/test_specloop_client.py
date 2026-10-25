from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import subprocess

import pytest

from nightshift.specloop_client import SpecloopError, call
from specloop import cli as alias


ALIAS_NOTICE = "specloop has been renamed to nightshift; use nightshift instead.\n"


@pytest.mark.scenario("nightshift-cli/alias-routed-daemon-callers/daemon-calls-parse-json-despite-notice")
@pytest.mark.parametrize("command", ["propose", "admit", "revise", "publish"])
@pytest.mark.parametrize("outcome,code", [
    ("ok", 0), ("needs_input", 2), ("rejected", 1), ("refused", 1), ("error", 1),
])
def test_daemon_client_parses_actual_alias_output(monkeypatch, tmp_path, command, outcome, code):
    manifest = str(tmp_path / "manifest with spaces.toml")
    feedback = tmp_path / "feedback with spaces.json"
    feedback.write_text(json.dumps(['Keep "café"; $(literal)\nnext line']))
    change, round_id = "owner__sample-issue-7-add-a-gate", "round-7"
    args = {
        "propose": ["propose", manifest, "--change", change, "--idea-file", str(feedback)],
        "admit": ["admit", manifest],
        "revise": ["revise", change, "--feedback", str(feedback),
                   "--feedback-format", "json", "--round-id", round_id],
        "publish": ["publish", change],
    }[command]
    document = {"version": 1, **({"error": "build failed"} if outcome == "error"
                               else {"outcome": outcome})}
    handled, invoked = [], []

    def handler(parsed):
        assert parsed.cmd == command and parsed.json is True
        if command in {"propose", "admit"}:
            assert parsed.manifest == manifest
        if command != "admit":
            assert parsed.change == change
        if command == "propose":
            assert parsed.idea_file == str(feedback)
        if command == "revise":
            assert (parsed.feedback, parsed.feedback_format, parsed.round_id) == (
                str(feedback), "json", round_id)
        handled.append(parsed)
        parsed.document.clear()
        parsed.document.update(document)
        return code

    def runner(argv, **kwargs):
        assert argv == ["specloop", *args, "--json"]
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = alias.main(argv[1:])
        assert result == code
        assert stdout.getvalue() == json.dumps(document) + "\n"
        assert stderr.getvalue() == ALIAS_NOTICE
        invoked.append(argv)
        return subprocess.CompletedProcess(argv, result, stdout.getvalue(), stderr.getvalue())

    monkeypatch.setattr(alias, command, handler)
    if outcome == "error":
        with pytest.raises(SpecloopError, match="specloop: build failed"):
            call(args, runner=runner)
    else:
        assert call(args, runner=runner) == document
    assert len(handled) == len(invoked) == 1
    assert json.loads(feedback.read_text()) == ['Keep "café"; $(literal)\nnext line']


@pytest.mark.parametrize("args", [
    ["status"], ["propose", "/manifest with spaces.toml", "--idea", "literal $(echo nope)"],
    ["admit", "/manifest.toml"], ["revise", "change", "--feedback", "/feedback.md"],
    ["publish", "change"],
])
def test_cli_invocation_is_argv_with_json(args):
    result = {"version": 1, "outcome": "ok"}

    def runner(argv, **kwargs):
        assert argv == ["specloop", *args, "--json"]
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "progress diagnostics")

    assert call(args, runner=runner) == result


@pytest.mark.parametrize("outcome,code", [("needs_input", 2), ("rejected", 1), ("refused", 1)])
def test_structured_nonzero_outcomes_reach_the_caller(outcome, code):
    result = {"version": 1, "outcome": outcome, "questions": ["Which format?"]}
    assert call(["propose"], runner=lambda *a, **k: subprocess.CompletedProcess(
        a[0], code, json.dumps(result), "diagnostic"
    )) == result


@pytest.mark.parametrize("stdout,code", [
    ("not json", 0), ("", 1), ('{"version":1}\n{"version":1}', 0),
    ('[]', 0), ('{"version":2}', 0), ('{"version":true}', 0), ('{}', 0),
    ('{"version":1,"error":"unknown change"}', 1),
    ('{"version":1,"outcome":"drafted"}', 1),
])
def test_protocol_or_process_failure_is_not_an_empty_success(stdout, code):
    with pytest.raises(SpecloopError):
        call(["status"], runner=lambda *a, **k: subprocess.CompletedProcess(a[0], code, stdout, "failed"))


def test_missing_executable_is_reported():
    def runner(*a, **k):
        raise FileNotFoundError("specloop")

    with pytest.raises(SpecloopError, match="could not run specloop"):
        call(["status"], runner=runner)

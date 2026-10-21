import json
import subprocess

import pytest

from nightshift.specloop_client import SpecloopError, call


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

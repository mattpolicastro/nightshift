"""Offline regressions for landing through the current Python interpreter."""

from importlib import import_module
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_land import (
    CHECKS, CONFIRM_MERGED, FINISH, FINISHED_HEAD, MAIN, MAIN_REF, PR, RUN,
    STATUS, URL, VIEW, FakeClock, assert_guarded_merge, config_path,
    expect_before_finish, expect_initial_run, expect_ready_to_merge, invoke,
    invoke_ci, merge_command, no_live_processes, scripted_runner, status_rows,
)


@pytest.fixture
def without_console_script(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert shutil.which("specloop") is None
    assert import_module("specloop.cli") is not None

    def forbidden_lookup(*args, **kwargs):
        pytest.fail("landing must not look up a console script")

    monkeypatch.setattr(shutil, "which", forbidden_lookup)


@pytest.mark.scenario("reviewed-pr-landing/discovery-through-the-interpreter/console-script-absent")
@pytest.mark.scenario("reviewed-pr-landing/finish-through-the-interpreter/eligible-finish-uses-the-current-interpreter")
@pytest.mark.parametrize("kind", ["direct", "finish", "done"])
def test_land_runs_specloop_through_its_interpreter(
    config_path, scripted_runner, without_console_script, capsys, kind,
):
    clock = FakeClock()
    # A small CI budget also exercises the remaining-budget cap on status reads.
    head = expect_ready_to_merge(scripted_runner, kind=kind, timeout=7)
    scripted_runner.expect(merge_command(head), {})
    scripted_runner.expect(
        CONFIRM_MERGED, {"number": 78, "url": URL, "state": "MERGED"},
    )
    assert invoke_ci(config_path, scripted_runner, clock, timeout=7) == 0
    output = capsys.readouterr()
    assert output.out == f"Merged {URL}\n" and output.err == ""
    assert clock.sleeps == []
    assert_guarded_merge(scripted_runner, head)
    module_calls = [(argv, options) for argv, options in scripted_runner.calls
                    if argv[0] != "gh"]
    status_calls = [(argv, options) for argv, options in module_calls if "status" in argv]
    assert len(status_calls) == (4 if kind == "finish" else 3)
    assert all(argv == [sys.executable, "-m", "specloop.cli", "status", "--json"]
               for argv, _ in status_calls)
    assert [options["timeout"] for _, options in status_calls] == (
        [30, 30, 30, 7] if kind == "finish" else [30, 30, 7]
    )
    finish_calls = [(argv, options) for argv, options in module_calls if "finish" in argv]
    assert len(module_calls) == len(status_calls) + len(finish_calls)
    assert len(finish_calls) == (1 if kind == "finish" else 0)
    if finish_calls:
        assert finish_calls == [(
            [sys.executable, "-m", "specloop.cli", "finish", RUN["change"]],
            {"capture_output": True, "text": True, "check": False, "timeout": 300},
        )]


@pytest.mark.scenario("reviewed-pr-landing/discovery-through-the-interpreter/module-invocation-fails")
@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("response, reason", [
    (subprocess.CompletedProcess(STATUS, 1, "", "No module named specloop.cli"),
     "No module named specloop.cli (exit 1)"),
    (subprocess.CompletedProcess(STATUS, 2, '{"version": 1, "runs": []}', "status failed"),
     "status failed (exit 2)"),
    (subprocess.CompletedProcess(STATUS, 0, "{broken", ""), "malformed JSON response"),
    (subprocess.CompletedProcess(STATUS, 0, "[]", ""), "expected a JSON object"),
    (subprocess.TimeoutExpired(STATUS, 30), "command timed out after 30s"),
])
def test_land_refuses_interpreter_discovery_failure(
    config_path, scripted_runner, without_console_script, capsys, refresh, response, reason,
):
    scripted_runner.expect(VIEW, PR).expect(MAIN, MAIN_REF)
    if refresh:
        # Even a previously established direct PR must refresh discovery safely.
        scripted_runner.expect(STATUS, status_rows())
    scripted_runner.expect(STATUS, response)
    assert invoke(config_path, scripted_runner, URL) == 1
    assert capsys.readouterr().err == f"land: run discovery: {reason}\n"
    assert [argv for argv, _ in scripted_runner.calls] == (
        [VIEW, MAIN] + ([STATUS] if refresh else []) + [STATUS]
    )


@pytest.mark.scenario("reviewed-pr-landing/finish-through-the-interpreter/eligible-finish-uses-the-current-interpreter")
@pytest.mark.parametrize("failure, reason", [
    ("module", "No module named specloop.cli (exit 1)"),
    ("exit", "push failed (exit 2)"),
    ("timeout", "command timed out after 300s"),
    ("discovery", "run discovery: No module named specloop.cli"),
    ("missing", "run identity changed"),
    ("replacement", "run identity changed"),
    ("paused", "expected the same run done"),
    ("head", "does not equal remote PR head"),
])
def test_land_refuses_interpreter_finish_failure(
    config_path, scripted_runner, without_console_script, capsys, failure, reason,
):
    expect_initial_run(scripted_runner, PR, RUN)
    expect_before_finish(scripted_runner, PR)
    response = {
        "module": subprocess.CompletedProcess(FINISH, 1, "", "No module named specloop.cli"),
        "exit": subprocess.CompletedProcess(FINISH, 2, "", "push failed"),
        "timeout": subprocess.TimeoutExpired(FINISH, 300),
    }.get(failure, subprocess.CompletedProcess(FINISH, 0, "Finished", ""))
    scripted_runner.expect(FINISH, response, timeout=300)
    if failure in {"discovery", "missing", "replacement", "paused", "head"}:
        done = {**RUN, "status": "done", "pause": None, "head": FINISHED_HEAD}
        refreshed = {
            "discovery": subprocess.CompletedProcess(STATUS, 1, "", "No module named specloop.cli"),
            "missing": status_rows(),
            "replacement": status_rows({**done, "change": "another-change"}),
            "paused": status_rows(RUN),
            "head": status_rows(done),
        }[failure]
        scripted_runner.expect(STATUS, refreshed)
        if failure == "head":
            scripted_runner.expect(VIEW, PR)
    assert invoke(config_path, scripted_runner, URL) == 1
    assert reason in capsys.readouterr().err
    calls = [argv for argv, _ in scripted_runner.calls]
    assert calls.count(FINISH) == 1
    assert CHECKS not in calls
    assert not any("merge" in argv for argv in calls)


@pytest.mark.scenario("reviewed-pr-landing/landing-interpreter-prerequisites/operator-prepares-the-environment")
def test_landing_documents_interpreter_prerequisites():
    guide = (Path(__file__).resolve().parents[1] / "docs" / "landing.md").read_text()
    prerequisites = " ".join(
        guide.split("## Prerequisites and flags\n", 1)[1].split("| Argument |", 1)[0].split()
    )
    assert "`uv` and `gh` available on PATH" in prerequisites
    assert "interpreter running `nightshift.land` must be able to import `specloop.cli`" in prerequisites
    assert "`specloop` console script need not be on PATH" in prerequisites
    assert "virtual-environment activation is not required" in prerequisites
    assert "exactly one case-insensitive enrollment for the repository" in prerequisites
    assert "Authenticate `gh` as the operator with permission" in prerequisites
    assert "read and edit the PR and issue comments, update the branch, and merge" in prerequisites
    assert "repository's protection rules" in prerequisites
    assert "`-m specloop.cli status --json` can read the applicable runs, including for worker repositories" in prerequisites

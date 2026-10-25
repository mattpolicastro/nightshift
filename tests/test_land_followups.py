"""Offline regressions for landing through the current Python interpreter."""

from importlib import import_module
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_land import (
    BASE, CHECKS, CONFIRM_MERGED, FINISH, FINISHED_HEAD, HEAD, MAIN, MAIN_REF, PR, RUN,
    STATUS, UPDATE, UPDATED_COMMIT, UPDATED_HEAD, UPDATED_PR, URL, VIEW, FakeClock,
    assert_guarded_merge, ci_check, config_path, expect_before_finish, expect_ci_entry,
    expect_ci_poll, expect_initial_run, expect_ready_to_merge, expect_round_end,
    expect_update_request, invoke, invoke_ci, merge_command, merge_commit,
    no_live_processes, scripted_runner, status_rows,
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
    assert "`-m specloop.cli status --json` can read the applicable runs" in prerequisites


PARSER_ERROR = "unexpected end of JSON input"


@pytest.mark.scenario("reviewed-pr-landing/narrow-uncertain-update-responses/parser-error-after-accepted-update")
@pytest.mark.scenario("reviewed-pr-landing/narrow-uncertain-update-responses/silent-update-result")
@pytest.mark.scenario("reviewed-pr-landing/narrow-uncertain-update-responses/existing-transport-loss")
@pytest.mark.scenario("reviewed-pr-landing/uncertain-update-observation-preserves-gates/proven-update-proceeds-through-fresh-gates")
@pytest.mark.parametrize("response", [
    subprocess.CompletedProcess(UPDATE, 1, "", PARSER_ERROR),
    subprocess.CompletedProcess(UPDATE, 2, '{"message":', PARSER_ERROR),
    subprocess.CompletedProcess(UPDATE, 1, "", ""),
    subprocess.CompletedProcess(UPDATE, 7, "", ""),
    subprocess.CompletedProcess(UPDATE, 7, None, None),
    *[subprocess.CompletedProcess(UPDATE, 1, "", detail) for detail in
      ["EOF", "connection reset", "connection lost", "broken pipe", "timed out", "timeout"]],
    subprocess.TimeoutExpired(UPDATE, 30), OSError("connection lost"),
])
@pytest.mark.parametrize("fresh_ci", ["pass", "fail", "parser-error"])
def test_land_reconciles_branch_update_with_unparseable_response(
    config_path, scripted_runner, capsys, response, fresh_ci,
):
    clock = FakeClock()
    fresh_base = "e" * 40
    # Green checks for H must not authorize the eventual replacement head.
    expect_ci_entry(scripted_runner)
    expect_ci_poll(scripted_runner, [ci_check()])
    expect_round_end(scripted_runner, base=fresh_base)
    scripted_runner.expect(MAIN, {**MAIN_REF, "object": {"type": "commit", "sha": BASE}})
    expect_update_request(scripted_runner, response, base=fresh_base)
    scripted_runner.expect(VIEW, PR).expect(VIEW, UPDATED_PR)
    scripted_runner.expect(UPDATED_COMMIT, merge_commit(HEAD, fresh_base))
    if fresh_ci == "parser-error":
        scripted_runner.expect(VIEW, UPDATED_PR)
        scripted_runner.expect(CHECKS, subprocess.CompletedProcess(CHECKS, 1, "", PARSER_ERROR))
        scripted_runner.expect(VIEW, UPDATED_PR)
    else:
        expect_ci_poll(scripted_runner, [ci_check(fresh_ci)], pr=UPDATED_PR,
                       code=1 if fresh_ci == "fail" else 0)
    if fresh_ci == "pass":
        expect_round_end(scripted_runner, pr=UPDATED_PR, base=fresh_base)
        scripted_runner.expect(merge_command(UPDATED_HEAD), {})
        scripted_runner.expect(CONFIRM_MERGED, {"number": 78, "url": URL, "state": "MERGED"})
    assert invoke_ci(config_path, scripted_runner, clock) == (0 if fresh_ci == "pass" else 1)
    output = capsys.readouterr()
    calls = [argv for argv, _ in scripted_runner.calls]
    assert [argv for argv in calls if "PUT" in argv] == [UPDATE]
    assert calls.count(CHECKS) == 2 and clock.sleeps == [10]
    if fresh_ci == "pass":
        assert output.out == f"No human checks listed\nMerged {URL}\n" and output.err == ""
        assert_guarded_merge(scripted_runner, UPDATED_HEAD)
    else:
        assert ("CI check unit tests: fail" if fresh_ci == "fail"
                else "CI checks: malformed JSON response") in output.err
        assert not any("merge" in argv for argv in calls)


def assert_prompt_update_refusal(config_path, runner, capsys, code, stdout, stderr):
    clock = FakeClock()
    expect_ci_entry(runner, stale=True)
    expect_update_request(runner, subprocess.CompletedProcess(UPDATE, code, stdout, stderr))
    assert invoke_ci(config_path, runner, clock) == 1
    detail = (stderr or stdout or "request rejected").strip()
    assert capsys.readouterr().err == f"land: branch update: {detail} (exit {code})\n"
    calls = [argv for argv, _ in runner.calls]
    assert calls[calls.index(UPDATE):] == [UPDATE]  # No post-PUT reads or mutations.
    assert calls.count(UPDATE) == 1 and CHECKS not in calls
    assert clock.sleeps == []


@pytest.mark.scenario("reviewed-pr-landing/definite-update-failures-refuse-promptly/json-error-takes-precedence")
@pytest.mark.parametrize("document", [
    {"message": "expected_head_sha does not match"}, {"message": "merge conflict"},
    {"message": PARSER_ERROR + " EOF"}, {}, [], None, False, 0, "unrelated",
])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_land_refuses_branch_update_json_error(
    config_path, scripted_runner, capsys, document, stream,
):
    stdout, stderr = ((json.dumps(document), PARSER_ERROR + " EOF") if stream == "stdout"
                      else ("", json.dumps(document)))
    assert_prompt_update_refusal(config_path, scripted_runner, capsys, 2, stdout, stderr)


@pytest.mark.scenario("reviewed-pr-landing/definite-update-failures-refuse-promptly/http-error-takes-precedence")
@pytest.mark.parametrize("status", [100, 200, 302, 403, 409, 422, 503, 599])
@pytest.mark.parametrize("diagnostic", ["(HTTP {status})", "hTtP/1.1\t{status}: " + PARSER_ERROR + " EOF"])
def test_land_refuses_branch_update_http_error_without_waiting(
    config_path, scripted_runner, capsys, status, diagnostic,
):
    assert_prompt_update_refusal(config_path, scripted_runner, capsys, 2, "",
                                 diagnostic.format(status=status))


@pytest.mark.scenario("reviewed-pr-landing/definite-update-failures-refuse-promptly/other-failures-do-not-wait")
@pytest.mark.parametrize("code, stdout, stderr", [
    (1, "", "permission denied"), (1, "", "authentication required"),
    (1, "", "please run gh auth login"), (4, "", ""), (4, "", PARSER_ERROR + " EOF"),
    (2, "{broken", ""), (2, "", "unknown failure"),
    (1, " ", ""), (1, "", "\n"), (1, "\t", " "),
    (1, "", "Unexpected end of JSON input"), (1, PARSER_ERROR, ""),
])
def test_land_refuses_other_update_failures(config_path, scripted_runner, capsys, code, stdout, stderr):
    assert_prompt_update_refusal(config_path, scripted_runner, capsys, code, stdout, stderr)


@pytest.mark.scenario("reviewed-pr-landing/uncertain-update-observation-preserves-gates/unexpected-parents-refuse")
@pytest.mark.parametrize("commit", [
    merge_commit(UPDATED_HEAD), merge_commit(BASE, HEAD), merge_commit(HEAD, BASE, FINISHED_HEAD),
    merge_commit(HEAD, FINISHED_HEAD), merge_commit(HEAD, BASE, head=FINISHED_HEAD),
    {"sha": UPDATED_HEAD, "parents": [HEAD, BASE]}, {},
    subprocess.CompletedProcess(UPDATED_COMMIT, 0, "{broken", ""),
    subprocess.CompletedProcess(UPDATED_COMMIT, 1, "", "commit unavailable"),
])
def test_land_uncertain_update_requires_exact_parents(config_path, scripted_runner, capsys, commit):
    clock = FakeClock()
    expect_ci_entry(scripted_runner, stale=True)
    expect_update_request(scripted_runner, subprocess.CompletedProcess(UPDATE, 2, "", PARSER_ERROR))
    scripted_runner.expect(VIEW, UPDATED_PR).expect(UPDATED_COMMIT, commit)
    assert invoke_ci(config_path, scripted_runner, clock) == 1
    error = capsys.readouterr().err
    assert f"branch update: {PARSER_ERROR} (exit 2); branch update reconciliation failed:" in error
    assert "branch update provenance refused" in error or "branch update parent inspection" in error
    calls = [argv for argv, _ in scripted_runner.calls]
    assert calls[calls.index(UPDATE):] == [UPDATE, VIEW, UPDATED_COMMIT]
    assert calls.count(UPDATE) == 1 and CHECKS not in calls and clock.sleeps == []


@pytest.mark.scenario("reviewed-pr-landing/uncertain-update-observation-preserves-gates/observation-cannot-establish-the-update")
@pytest.mark.parametrize("observation, reason", [
    (subprocess.CompletedProcess(VIEW, 1, "", "read unavailable"), "read unavailable"),
    (subprocess.CompletedProcess(VIEW, 0, "{broken", ""), "malformed JSON"),
    ({**PR, "headRefOid": "bad"}, "malformed PR metadata"),
    ({**PR, "state": "CLOSED"}, "PR must be open"),
    ({**PR, "isDraft": True}, "PR became draft"),
])
def test_land_uncertain_update_observation_refuses(
    config_path, scripted_runner, capsys, observation, reason,
):
    clock = FakeClock()
    expect_ci_entry(scripted_runner, stale=True)
    expect_update_request(scripted_runner, subprocess.CompletedProcess(UPDATE, 7, "", ""))
    scripted_runner.expect(VIEW, observation)
    assert invoke_ci(config_path, scripted_runner, clock) == 1
    error = capsys.readouterr().err
    assert "branch update: request rejected (exit 7); branch update reconciliation failed:" in error
    assert reason in error and clock.sleeps == []
    calls = [argv for argv, _ in scripted_runner.calls]
    assert calls[calls.index(UPDATE):] == [UPDATE, VIEW] and calls.count(UPDATE) == 1


@pytest.mark.scenario("reviewed-pr-landing/uncertain-update-observation-preserves-gates/observation-cannot-establish-the-update")
@pytest.mark.parametrize("expiry", ["unchanged", "read", "parents"])
def test_land_uncertain_update_keeps_original_deadline(config_path, scripted_runner, capsys, expiry):
    clock = FakeClock()
    moved = "e" * 40
    expect_ci_entry(scripted_runner, timeout=25)
    expect_ci_poll(scripted_runner, [ci_check("pending")], code=8, timeout=25)
    expect_ci_poll(scripted_runner, [ci_check()], timeout=15)
    expect_round_end(scripted_runner, base=moved, timeout=15)
    scripted_runner.expect(MAIN, {**MAIN_REF, "object": {"type": "commit", "sha": moved}}, timeout=15)

    def response_after(seconds, response):
        def respond():
            clock.now += seconds
            return response
        return respond

    expect_update_request(scripted_runner, response_after(
        2, subprocess.CompletedProcess(UPDATE, 2, "", PARSER_ERROR)), base=moved, timeout=15)
    if expiry == "read":
        scripted_runner.expect(VIEW, response_after(13, UPDATED_PR), timeout=13)
    elif expiry == "parents":
        scripted_runner.expect(VIEW, response_after(1, UPDATED_PR), timeout=13)
        scripted_runner.expect(UPDATED_COMMIT, response_after(12, merge_commit(HEAD, moved)), timeout=12)
    else:
        scripted_runner.expect(VIEW, response_after(1, PR), timeout=13)
        scripted_runner.expect(VIEW, PR, timeout=2)
    assert invoke_ci(config_path, scripted_runner, clock, timeout=25) == 1
    error = capsys.readouterr().err
    assert f"branch update: {PARSER_ERROR} (exit 2); branch update reconciliation failed:" in error
    assert "shared CI deadline expired" in error
    assert clock.now == 125 and clock.sleeps == ([10, 10, 2] if expiry == "unchanged" else [10])
    calls = [argv for argv, _ in scripted_runner.calls]
    assert calls.count(UPDATE) == 1 and calls.count(CHECKS) == 2
    assert not any("merge" in argv for argv in calls)

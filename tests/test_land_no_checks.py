"""Offline registration-delay regressions, preserving the real landing gates."""

import json
import subprocess

import pytest

from nightshift import land
from test_land import (
    CHECKS, CONFIRM_MERGED, FINISHED_HEAD, HEAD, PR, REPO, RUN, URL,
    VIEW, FakeClock, assert_guarded_merge, ci_check, config_path, expect_ci_entry,
    expect_ci_poll, expect_finish_gate, expect_initial_run, expect_round_end,
    expect_round_start, expect_round_update, invoke_ci, merge_command,
    no_live_processes, scripted_runner,
)


MISSING = subprocess.CompletedProcess(CHECKS, 1, "", "no checks reported on branch archive")


def expect_response(runner, response=MISSING, *, pr=PR, timeout=30):
    runner.expect(VIEW, pr, timeout=timeout)
    runner.expect(CHECKS, response, timeout=timeout)
    if not isinstance(response, Exception) and response.returncode in (0, 1, 8):
        runner.expect(VIEW, pr, timeout=timeout)


def expect_finished_entry(runner, timeout=30):
    expect_initial_run(runner, PR, RUN)
    expect_finish_gate(runner, row=RUN, ci=False)
    expect_round_start(runner, head=FINISHED_HEAD, timeout=timeout)
    return {**PR, "headRefOid": FINISHED_HEAD}


@pytest.mark.scenario("reviewed-pr-landing/ci-registration-delay/checks-register-after-push")
@pytest.mark.scenario("reviewed-pr-landing/bounded-ci-wait/pending-then-green")
def test_land_waits_while_no_checks_are_reported_yet(config_path, scripted_runner, capsys):
    clock = FakeClock()
    pr = expect_finished_entry(scripted_runner)
    expect_response(scripted_runner, pr=pr)
    expect_ci_poll(scripted_runner, [ci_check("pending")], code=8, pr=pr)
    expect_ci_poll(scripted_runner, [ci_check(), ci_check("skipping")], pr=pr)
    done = {**RUN, "status": "done", "pause": None, "head": FINISHED_HEAD}
    expect_round_end(scripted_runner, pr=pr, row=done)
    scripted_runner.expect(merge_command(FINISHED_HEAD), {})
    scripted_runner.expect(CONFIRM_MERGED, {"number": 78, "url": URL, "state": "MERGED"})
    assert invoke_ci(config_path, scripted_runner, clock) == 0
    output = capsys.readouterr()
    assert output.out.endswith(f"Merged {URL}\n") and output.err == ""
    assert clock.sleeps == [10, 10]
    assert_guarded_merge(scripted_runner, FINISHED_HEAD)


@pytest.mark.scenario("reviewed-pr-landing/ci-registration-delay/no-checks-before-deadline")
@pytest.mark.scenario("reviewed-pr-landing/bounded-ci-wait/shared-deadline-expires")
def test_land_refuses_when_no_checks_appear_before_deadline(config_path, scripted_runner, capsys):
    clock = FakeClock()
    pr = expect_finished_entry(scripted_runner, timeout=25)
    for remaining in (25, 15, 5):
        expect_response(scripted_runner, pr=pr, timeout=remaining)
    assert invoke_ci(config_path, scripted_runner, clock, timeout=25) == 1
    assert capsys.readouterr().err == (
        f"land: no CI checks were reported for head {FINISHED_HEAD} within 25s; "
        "inspect checks and rerun land\n"
    )
    assert clock.now == 125 and clock.sleeps == [10, 10, 5]
    assert sum(argv == CHECKS for argv, _ in scripted_runner.calls) == 3
    assert not any("merge" in argv for argv, _ in scripted_runner.calls)


@pytest.mark.scenario("reviewed-pr-landing/ci-registration-delay/other-responses-retain-validation")
@pytest.mark.scenario("reviewed-pr-landing/bounded-ci-wait/failing-ci-refused")
@pytest.mark.parametrize("after_missing", [False, True])
@pytest.mark.parametrize("code, stdout, stderr, reason", [
    (0, "", "no checks reported", "malformed JSON"),
    (8, "", "no checks reported", "malformed JSON"),
    (2, "", "no checks reported", "no checks reported (exit 2)"),
    (1, " ", "no checks reported", "malformed JSON"),
    (1, "\n", "no checks reported", "malformed JSON"),
    (1, "broken", "no checks reported", "malformed JSON"),
    (1, "", "No checks reported", "malformed JSON"),
    (1, "", "unavailable", "malformed JSON"),
    (1, "", None, "malformed JSON"),
    (1, "[]", "no checks reported", "no checks reported (exit 1)"),
    (1, json.dumps([ci_check()]), "no checks reported", "no checks reported (exit 1)"),
    (1, "{}", "no checks reported", "expected a JSON array"),
    (1, "[{}]", "no checks reported", "malformed check result"),
    (1, json.dumps([{**ci_check(), "bucket": "unknown"}]), "no checks reported",
     "unknown classification"),
    *[(1, json.dumps([ci_check(bucket)]), "no checks reported", f"unit tests: {bucket}")
      for bucket in ("fail", "cancel")],
])
def test_registration_predicate_boundaries(
    config_path, scripted_runner, capsys, after_missing, code, stdout, stderr, reason,
):
    clock = FakeClock()
    expect_ci_entry(scripted_runner)
    if after_missing:
        expect_response(scripted_runner)
    expect_response(scripted_runner, subprocess.CompletedProcess(CHECKS, code, stdout, stderr))
    assert invoke_ci(config_path, scripted_runner, clock) == 1
    error = capsys.readouterr().err
    assert reason in error and "within" not in error
    assert clock.sleeps == ([10] if after_missing else [])
    assert not any("merge" in argv for argv, _ in scripted_runner.calls)


@pytest.mark.scenario("reviewed-pr-landing/ci-registration-delay/head-changes-during-registration")
@pytest.mark.parametrize("position", ["before", "after", "next-poll"])
def test_registration_head_drift(config_path, scripted_runner, capsys, position):
    clock = FakeClock()
    expect_ci_entry(scripted_runner)
    if position == "next-poll":
        expect_response(scripted_runner)
    if position == "after":
        scripted_runner.expect(VIEW, PR).expect(CHECKS, MISSING)
    scripted_runner.expect(VIEW, {**PR, "headRefOid": FINISHED_HEAD})
    assert invoke_ci(config_path, scripted_runner, clock) == 1
    assert capsys.readouterr().err == (
        f"land: PR head changed during CI: expected {HEAD}, found {FINISHED_HEAD}; "
        "review the current head and rerun land\n"
    )
    assert clock.sleeps == ([10] if position == "next-poll" else [])
    assert not any("merge" in argv for argv, _ in scripted_runner.calls)


@pytest.mark.scenario("reviewed-pr-landing/ci-registration-delay/other-responses-retain-validation")
@pytest.mark.scenario("reviewed-pr-landing/bounded-ci-wait/shared-deadline-expires")
@pytest.mark.parametrize("checks, code", [([], 0), ([ci_check("pending")], 8),
                                         ([ci_check("skipping")], 0)])
def test_ordinary_response_clears_registration_wait(
    config_path, scripted_runner, capsys, checks, code,
):
    clock = FakeClock()
    expect_ci_entry(scripted_runner, timeout=15)
    expect_response(scripted_runner, timeout=15)
    expect_ci_poll(scripted_runner, checks, code=code, timeout=5)
    assert invoke_ci(config_path, scripted_runner, clock, timeout=15) == 1
    assert capsys.readouterr().err == (
        "land: shared CI deadline expired; inspect checks and rerun land\n"
    )
    assert clock.sleeps == [10, 5] and clock.now == 115


@pytest.mark.scenario("reviewed-pr-landing/ci-registration-delay/no-checks-before-deadline")
@pytest.mark.scenario("reviewed-pr-landing/bounded-ci-wait/shared-deadline-expires")
@pytest.mark.parametrize("waiting", ["missing", "pending", "unclassified"])
def test_registration_rounds_and_commands_share_budget(
    config_path, scripted_runner, capsys, waiting,
):
    clock = FakeClock()
    moved = "e" * 40
    expect_ci_entry(scripted_runner, timeout=25)
    expect_response(scripted_runner, timeout=25)
    expect_ci_poll(scripted_runner, [ci_check()], timeout=15)
    expect_round_end(scripted_runner, base=moved, timeout=15)
    pr = expect_round_update(scripted_runner, base=moved, round_base=moved, timeout=15)
    # The update's final command consumes time before the new head's wait begins.
    argv, response, command_timeout = scripted_runner.steps.pop()

    def slow_update_read():
        clock.now += 3
        return response

    scripted_runner.expect(argv, slow_update_read, timeout=command_timeout)
    if waiting == "unclassified":
        def slow_head_read():
            clock.now += 12
            return pr
        scripted_runner.expect(VIEW, slow_head_read, timeout=12)
    else:
        for remaining in (12, 2):
            if waiting == "missing":
                expect_response(scripted_runner, pr=pr, timeout=remaining)
            else:
                expect_ci_poll(scripted_runner, [ci_check("pending")], code=8,
                               pr=pr, timeout=remaining)
    assert invoke_ci(config_path, scripted_runner, clock, timeout=25) == 1
    reason = (f"no CI checks were reported for head {pr['headRefOid']} within 25s"
              if waiting == "missing" else "shared CI deadline expired")
    assert capsys.readouterr().err == f"land: {reason}; inspect checks and rerun land\n"
    assert clock.now == 125
    assert clock.sleeps == ([10] if waiting == "unclassified" else [10, 10, 2])
    assert not any("merge" in argv for argv, _ in scripted_runner.calls)


@pytest.mark.scenario("reviewed-pr-landing/ci-registration-delay/other-responses-retain-validation")
@pytest.mark.parametrize("failure", ["timeout", "refusal", "draft", "metadata"])
def test_registration_does_not_hide_other_failures(
    config_path, scripted_runner, capsys, failure,
):
    clock = FakeClock()
    expect_ci_entry(scripted_runner, timeout=15)
    expect_response(scripted_runner, timeout=15)

    def failed_read():
        if failure in ("timeout", "refusal"):
            clock.now += 5
            return (subprocess.TimeoutExpired(VIEW, 5) if failure == "timeout"
                    else land.Refusal("head read refused"))
        return {**PR, **({"isDraft": True} if failure == "draft" else {"headRefOid": None})}

    scripted_runner.expect(VIEW, failed_read, timeout=5)
    assert invoke_ci(config_path, scripted_runner, clock, timeout=15) == 1
    reason = {"timeout": "command timed out", "refusal": "head read refused",
              "draft": "PR became draft", "metadata": "malformed PR metadata"}[failure]
    error = capsys.readouterr().err
    assert reason in error and "no CI checks were reported" not in error
    assert clock.sleeps == [10]


@pytest.mark.scenario("reviewed-pr-landing/ci-registration-delay/no-checks-before-deadline")
def test_registration_helper_uses_entry_budget_when_timeout_omitted(scripted_runner):
    clock = FakeClock()
    operations = land.Operations(scripted_runner, clock.sleep, clock.monotonic, deadline=102.5)
    response = subprocess.CompletedProcess(CHECKS, 1, "", "prefix: no checks reported (yet)\n")
    expect_response(scripted_runner, response, timeout=2.5)
    with pytest.raises(land.Refusal, match=f"head {HEAD} within 2.5s; inspect checks and rerun land"):
        land.wait_for_ci(land.Identity(REPO, 78), HEAD, operations)
    assert clock.sleeps == [2.5]


@pytest.mark.scenario("reviewed-pr-landing/ci-registration-delay/no-checks-before-deadline")
@pytest.mark.parametrize("established", [False, True])
def test_deadline_requires_an_established_registration_poll(
    config_path, scripted_runner, capsys, established,
):
    clock = FakeClock()
    budget = 15 if established else 5
    expect_ci_entry(scripted_runner, timeout=budget)
    if established:
        expect_response(scripted_runner, timeout=budget)

    def slow_checks():
        clock.now += 5
        return MISSING

    scripted_runner.expect(VIEW, PR, timeout=5).expect(CHECKS, slow_checks, timeout=5)
    assert invoke_ci(config_path, scripted_runner, clock, timeout=budget) == 1
    reason = (f"no CI checks were reported for head {HEAD} within 15s"
              if established else "shared CI deadline expired")
    assert capsys.readouterr().err == f"land: {reason}; inspect checks and rerun land\n"
    assert clock.sleeps == ([10] if established else [])

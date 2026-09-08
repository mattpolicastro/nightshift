"""State-machine tests.

These cover the branches that are expensive or impossible to reach by running
real agents: a truncated worker, a reviewer that never rendered a verdict, and
a second FAIL after a retry. Each is a decision function called directly.
"""

from __future__ import annotations

import pytest

from nightshift import task, trace
from nightshift.task import Step


def result(**kw) -> trace.Result:
    base = dict(
        ok=True,
        stop_reason="end_turn",
        turns=40,
        duration_s=100.0,
        cost_usd=1.0,
        output_tokens=100,
        cache_read_tokens=1000,
        text="",
    )
    base.update(kw)
    return trace.Result(**base)


# --- after_implement -----------------------------------------------------


def test_healthy_run_proceeds_to_review():
    assert (
        task.after_implement(
            result(), escalated=False, committed=True, verified=True
        )
        is None
    )


def test_no_result_event_escalates():
    d = task.after_implement(None, escalated=False, committed=True, verified=True)
    assert d.step is Step.ESCALATE
    assert "no result event" in d.reason


def test_truncated_run_is_a_failure_not_a_partial_success():
    """A run cut off at --max-turns left an artefact, not a piece of work."""
    d = task.after_implement(
        result(turns_exhausted=True), escalated=False, committed=True, verified=True
    )
    assert d.step is Step.ESCALATE
    assert "truncated" in d.reason


def test_truncation_is_checked_before_committed_work():
    """A truncated run that happened to commit must still not proceed."""
    d = task.after_implement(
        result(stop_reason="max_tokens", turns_exhausted=True),
        escalated=False,
        committed=True,
        verified=True,
    )
    assert d.step is Step.ESCALATE and "truncated" in d.reason


def test_escalation_is_checked_before_shortfalls():
    """A deliberate stop must not be reported as 'committed nothing'."""
    d = task.after_implement(
        result(), escalated=True, committed=False, verified=False
    )
    assert d.step is Step.ESCALATE
    assert d.reason == "worker escalated"


def test_committing_nothing_escalates():
    d = task.after_implement(
        result(), escalated=False, committed=False, verified=True
    )
    assert d.step is Step.ESCALATE
    assert "without committing" in d.reason


def test_turn_exhaustion_reads_as_truncation_not_as_committing_nothing():
    """`subtype` carries turn exhaustion; `stop_reason` does not.

    Issue #23 ran out of turns and reported `stop_reason: "tool_use"` — which
    describes its last message, not the run — so `truncated` was False and the
    escalation read "worker committed nothing". That points a human at the
    issue when the fault is the run's size.
    """
    d = task.after_implement(
        result(subtype="error_max_turns", turns=101),
        escalated=False,
        committed=False,
        verified=True,
    )
    assert "truncated at 101 turns" in d.reason


def test_committing_without_verifying_escalates():
    """AGENTS.md makes this explicit so the incentive is never commit-and-hope."""
    d = task.after_implement(
        result(), escalated=False, committed=True, verified=False
    )
    assert d.step is Step.ESCALATE
    assert "no verify run" in d.reason


# --- after_review --------------------------------------------------------


def test_pass_ships():
    d = task.after_review(result(), True, attempt=1, max_attempts=2)
    assert d.step is Step.SHIP


def test_first_fail_retries():
    d = task.after_review(result(), False, attempt=1, max_attempts=2)
    assert d.step is Step.RETRY_REVIEW


def test_second_fail_escalates_rather_than_spending_more():
    d = task.after_review(result(), False, attempt=2, max_attempts=2)
    assert d.step is Step.ESCALATE
    assert "2 attempts" in d.reason


def test_missing_verdict_is_not_a_pass():
    """The gate's most dangerous failure: absence of FAIL read as approval."""
    d = task.after_review(result(), None, attempt=1, max_attempts=2)
    assert d.step is Step.ESCALATE
    assert d.step is not Step.SHIP


def test_missing_verdict_from_truncation_says_so():
    d = task.after_review(
        result(turns_exhausted=True), None, attempt=1, max_attempts=2
    )
    assert "truncated" in d.reason


def test_reviewer_with_no_result_event_escalates():
    d = task.after_review(None, None, attempt=1, max_attempts=2)
    assert d.step is Step.ESCALATE


def test_single_attempt_config_never_retries():
    d = task.after_review(result(), False, attempt=1, max_attempts=1)
    assert d.step is Step.ESCALATE


# --- ran_verification ----------------------------------------------------

VERIFY = "pnpm -r typecheck && pnpm -r test && pnpm -r build && pnpm format:check"


def test_verification_detected_from_executed_commands():
    r = result(commands=["git status", "pnpm -r test 2>&1 | tail -60"])
    assert task.ran_verification(r, VERIFY)


def test_verification_not_inferred_from_prose():
    """A worker that says it tested and one that tested read the same in prose."""
    r = result(text="I ran pnpm -r test and everything passed.", commands=["git status"])
    assert not task.ran_verification(r, VERIFY)


def test_verification_ignores_a_near_miss_command():
    r = result(commands=["pnpm install --frozen-lockfile"])
    assert not task.ran_verification(r, VERIFY)


@pytest.mark.parametrize(
    "transcript,expected",
    [("worker1.jsonl", True), ("worker3b.jsonl", False)],
)
def test_verification_against_real_transcripts(transcript, expected, request):
    """worker1 ran the verify chain; worker3b escalated without verifying."""
    path = (
        request.config.rootpath / "tests" / "fixtures" / transcript
    )
    if not path.exists():
        pytest.skip(f"fixture {transcript} not vendored")
    r = trace.parse(path.read_text())
    assert task.ran_verification(r, VERIFY) is expected

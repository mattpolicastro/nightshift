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
    assert "no successful host verification" in d.reason


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


def test_partial_or_piped_verification_is_not_the_complete_chain():
    r = result(commands=["git status", "pnpm -r test 2>&1 | tail -60"])
    assert not task.ran_verification(r, VERIFY)


def test_exact_full_chain_is_only_diagnostic():
    assert task.ran_verification(result(commands=[VERIFY]), VERIFY)


def test_failed_implementation_with_commit_does_not_reach_review():
    d = task.after_implement(result(ok=False), escalated=False,
                             committed=True, verified=True)
    assert d.step is Step.ESCALATE
    assert "failed" in d.reason


@pytest.mark.parametrize("changes", [{"ok": False}, {"turns_exhausted": True}])
def test_failed_or_truncated_reviewer_cannot_ship_with_pass(changes):
    d = task.after_review(result(**changes), True, attempt=1, max_attempts=2)
    assert d.step is Step.ESCALATE



def test_verification_not_inferred_from_prose():
    """A worker that says it tested and one that tested read the same in prose."""
    r = result(text="I ran pnpm -r test and everything passed.", commands=["git status"])
    assert not task.ran_verification(r, VERIFY)


def test_verification_ignores_a_near_miss_command():
    r = result(commands=["pnpm install --frozen-lockfile"])
    assert not task.ran_verification(r, VERIFY)


@pytest.mark.parametrize(
    "transcript,expected",
    [("worker1.jsonl", False), ("worker3b.jsonl", False)],
)
def test_verification_against_real_transcripts(transcript, expected, request):
    """Legacy partial/piped requests are not complete host verification."""
    path = (
        request.config.rootpath / "tests" / "fixtures" / transcript
    )
    if not path.exists():
        pytest.skip(f"fixture {transcript} not vendored")
    r = trace.parse(path.read_text())
    assert task.ran_verification(r, VERIFY) is expected


@pytest.mark.parametrize("failure", ["missing_ref", "timeout", "io"])
def test_final_candidate_inspection_failure_preserves_unpushed_worktree(tmp_path, monkeypatch, failure):
    import json
    import subprocess
    from nightshift import verification, vcs, worker
    from nightshift.config import Config, Repo
    from nightshift.queue import Claim, Issue

    monkeypatch.setattr(task.queue, "comments_of", lambda *a: [])
    monkeypatch.setattr(Claim, "advance", lambda *a: None)
    monkeypatch.setattr(task.outcomes, "record", lambda *a, **k: None)
    for name in ("fetch", "install", "add_worktree"):
        monkeypatch.setattr(vcs, name, lambda *a, **k: None)
    monkeypatch.setattr(vcs, "has_commits", lambda *a: True)
    removed, pushed = [], []
    monkeypatch.setattr(vcs, "remove_worktree", lambda *a, **k: removed.append(a))
    monkeypatch.setattr(vcs, "push", lambda *a, **k: pushed.append(a))
    event = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                        "num_turns": 1, "result": "VERDICT: PASS"})
    for phase in ("implement", "review"):
        monkeypatch.setattr(worker, phase, lambda *a, **k: worker.Run(0, event))
    checked = verification.VerificationResult("verified-sha", "true", clauses=[
        verification.CommandResult(("true",), 0, 0)])
    monkeypatch.setattr(verification, "run", lambda *a, **k: checked)

    def failed_inspection(*args, **kwargs):
        if failure == "missing_ref":
            raise subprocess.CalledProcessError(128, ["git", "rev-parse"])
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["git", "status"], 30)
        raise OSError("I/O error")

    monkeypatch.setattr(verification, "unchanged", failed_inspection)
    report = task.run(Config(repos=[], worktree_root=tmp_path), Repo("owner/repo", "true"),
                      tmp_path, Issue("owner/repo", 1, "Task", "Task"),
                      Claim("owner/repo", 1, "candidate", str(tmp_path), "now"))
    assert report.step is Step.ESCALATE
    assert "unable to inspect candidate" in report.reason
    assert not removed and not pushed



@pytest.mark.parametrize("failure", ["save", "phase", "push_runtime", "push_timeout", "push_io", "save_record", "push_record"])
def test_evidence_and_shipping_failures_retain_unpushed_candidate(tmp_path, monkeypatch, caplog, failure):
    import json
    import subprocess
    from nightshift import verification, vcs, worker
    from nightshift.config import Config, Repo
    from nightshift.queue import Claim, Issue, Phase

    fail_record = failure.endswith("_record")
    failure = {"save_record": "save", "push_record": "push_runtime"}.get(failure, failure)
    recording_failed = [False]

    def record(*args, **kwargs):
        if recording_failed[0]:
            raise OSError("synthetic failure-record persistence error")

    monkeypatch.setattr(task.queue, "comments_of", lambda *a: [])
    monkeypatch.setattr(task.outcomes, "record", record)
    for name in ("fetch", "install", "add_worktree"):
        monkeypatch.setattr(vcs, name, lambda *a, **k: None)
    monkeypatch.setattr(vcs, "has_commits", lambda *a: True)
    removed, pushed, reviewed, opened = [], [], [], []
    monkeypatch.setattr(vcs, "remove_worktree", lambda *a, **k: removed.append(a))
    monkeypatch.setattr(vcs, "open_pr", lambda *a, **k: opened.append(a))
    event = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "num_turns": 1, "result": "VERDICT: PASS"})
    monkeypatch.setattr(worker, "implement", lambda *a, **k: worker.Run(0, event))

    def review(*args, **kwargs):
        reviewed.append(True)
        return worker.Run(0, event)

    monkeypatch.setattr(worker, "review", review)
    checked = verification.VerificationResult("verified-sha", "true", clauses=[
        verification.CommandResult(("true",), 0, 0)])
    monkeypatch.setattr(verification, "run", lambda *a, **k: checked)
    monkeypatch.setattr(verification, "unchanged", lambda *a, **k: True)

    def advance(claim, phase):
        if failure == "phase" and phase is Phase.SHIPPING:
            raise OSError("synthetic claim write failure")

    monkeypatch.setattr(Claim, "advance", advance)

    def push(*args, **kwargs):
        pushed.append(True)
        recording_failed[0] = fail_record
        if failure == "push_timeout":
            raise subprocess.TimeoutExpired(["git", "push"], 30)
        if failure == "push_io":
            raise OSError("synthetic push IO failure")
        raise RuntimeError("synthetic push refusal")

    monkeypatch.setattr(vcs, "push", push)
    if failure == "save":
        def save(*args, **kwargs):
            recording_failed[0] = fail_record
            raise OSError("synthetic evidence write failure")
        monkeypatch.setattr(verification, "save", save)

    report = task.run(Config(repos=[], worktree_root=tmp_path), Repo("owner/repo", "true"),
                      tmp_path, Issue("owner/repo", 1, "Task", "Task"),
                      Claim("owner/repo", 1, "candidate", str(tmp_path), "now"),
                      transcript_dir=tmp_path / "evidence")
    assert report.step is Step.ESCALATE
    assert "candidate retained" in report.reason
    assert not removed and not opened
    assert bool(pushed) == failure.startswith("push_")
    assert bool(reviewed) == (failure != "save")
    if failure == "save":
        assert "verification evidence" in report.reason
    elif failure == "phase":
        assert "shipping phase" in report.reason
    else:
        assert "push candidate" in report.reason

    if fail_record:
        assert "could not record retained candidate escalation" in caplog.text

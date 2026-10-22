import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pytest

from nightshift import touchpoints
from nightshift.touchpoints import collect_events, collect_history, collect_intake, collect_report, main, render_table


def put(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.mark.scenario("touchpoint-report/front-door-attribution/issues-and-prs-remain-distinct")
def test_repository_issue_and_pr_identity(tmp_path):
    for repo, pr in (("alpha", 28), ("beta", 45)):
        change = f"{repo}-issue-8-feature"
        put(tmp_path, f"proposals/owner__{repo}/8.json", dict(change=change))
        for kind in ("admissions", "revisions"):
            put(tmp_path, f"{kind}/owner__{repo}/{pr}.json", dict(change=change, head=repo))
    history = collect_history(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    assert [(row["repo"], row["issue"]) for row in history["changes"]] == [("owner/alpha", 8), ("owner/beta", 8)]
    for row, pr in zip(history["changes"], (28, 45)):
        assert [r["number"] for r in row["records"]] == [8, pr, pr]
        assert all(r["change"] == row["change"] and r["repo"] == row["repo"] for r in row["records"])


@pytest.mark.scenario("touchpoint-report/front-door-attribution/missing-proposal-and-ambiguous-run")
def test_missing_proposal_ambiguous_run_and_unsafe_names(tmp_path, monkeypatch):
    change = "sample-issue-8-feature"
    for owner in ("one", "two"):
        put(tmp_path, f"admissions/{owner}__sample/28.json", dict(change=change))
    put(tmp_path, f"runs/{change}/events.jsonl", dict(event="human_accept", at=1))
    put(tmp_path, "revisions/one__sample/45.json", dict(change="../escape"))
    put(tmp_path, "revisions/one__sample/46.json", dict(change="unattributable"))
    read = Path.read_text
    def guarded(path, *args, **kwargs):
        assert path.name != "events.jsonl", "ambiguous or unsafe run must not be read"
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    history = collect_history(tmp_path, tmp_path / "runs")
    assert len(history["changes"]) == 2
    for row in history["changes"]:
        assert row["issue"] == 8 and row["drafted_at"] is None
        assert row["drafted_at_source"] is None and row["warnings"]
        assert row["events_path"] is None and len(row["records"]) == 1
    assert len(history["errors"]) == 3
    assert all(any(term in error["message"] for error in history["errors"])
               for term in ("unsafe", "ambiguous run", "no front-door"))


def test_retained_jobs_and_numeric_receipts_only(tmp_path):
    change = "custom-safe_name.1"
    put(tmp_path, "proposals/o__r/8.jobs/old/request.json", dict(args=["propose", "manifest", "--change", change]))
    put(tmp_path, "proposals/o__r/8.jobs/old/result.json", dict(result=dict(outcome="needs_input")))
    put(tmp_path, "revisions/o__r/28.jobs/new/request.json", dict(args=["revise", change, "--feedback", "/never/read"]))
    for name in ("8.feedback.json", "idea.json", "8.tmp", "0.json"):
        put(tmp_path, f"proposals/o__r/{name}", ["ignored"])
    history = collect_history(tmp_path, tmp_path / "runs")
    assert not history["errors"] and len(history["changes"]) == 1
    row = history["changes"][0]
    assert (row["repo"], row["issue"], row["change"]) == ("o/r", 8, change)
    assert row["events_path"] == tmp_path / "runs" / change / "events.jsonl"
    assert [(r["command"], r["job"], r["retained"]) for r in row["records"]] == [("propose", "old", True), ("revise", "new", True)]
    assert row["records"][0]["result"] == dict(outcome="needs_input")
    assert row["records"][1]["result"] is None


@pytest.mark.parametrize("directory,extra", [
    ("o__r", {"repo": "other/r"}), ("o__r", {"issue": 9}),
    ("o__r", {"issue": True}), ("o__r__ambiguous", {}), ("o___r", {}),
])
def test_conflicting_or_ambiguous_identity(tmp_path, directory, extra):
    put(tmp_path, f"proposals/{directory}/8.json", dict(change="safe", **extra))
    history = collect_history(tmp_path, tmp_path / "runs")
    assert not history["changes"] and len(history["errors"]) == 1


def test_explicit_repository_disambiguates_directory(tmp_path):
    put(tmp_path, "proposals/o__r__suffix/8.json", dict(repo="o/r__suffix", issue=8, change="safe"))
    history = collect_history(tmp_path, tmp_path / "runs")
    assert not history["errors"] and history["changes"][0]["repo"] == "o/r__suffix"


@pytest.mark.parametrize("change", ["", ".", "..", "/absolute", "a/b", "a\\b", "bad\x00name"])
def test_unsafe_change_never_becomes_a_run_path(tmp_path, change):
    put(tmp_path, "proposals/o__r/8.json", dict(change=change))
    history = collect_history(tmp_path, tmp_path / "runs")
    assert not history["changes"] and len(history["errors"]) == 1
    assert "unsafe" in history["errors"][0]["message"]


def test_normalized_job_and_snapshot_preserve_join_evidence(tmp_path):
    change = "r-issue-8-feature"
    result = dict(outcome="admitted", commit="abc", future_field=True)
    put(tmp_path, "admissions/o__r/28.jobs/j/request.json",
        dict(args=["admit", "manifest", "--change", change, "--head", "abc"]))
    put(tmp_path, "admissions/o__r/28.jobs/j/result.json", dict(result=result))
    put(tmp_path, "admissions/o__r/28.json", dict(change=change, head="abc", job="j", result=result))
    history = collect_history(tmp_path, tmp_path / "runs")
    assert not history["errors"] and len(history["changes"]) == 1
    records = history["changes"][0]["records"]
    assert len(records) == 2 and {r["retained"] for r in records} == {False, True}
    assert all((r["command"], r["head"], r["job"], r["result"], r["number"]) ==
               ("admit", "abc", "j", result, 28) for r in records)


@pytest.mark.parametrize("change", ["other-issue-8-title", "r-issue-0-title",
                                    "r-issue-08-title", "r-issue-8-", "r-issue-8-Title",
                                    "r-issue-8-" + "x" * 41])
def test_fallback_requires_exact_producer_name(tmp_path, change):
    put(tmp_path, "admissions/o__r/28.json", dict(change=change))
    history = collect_history(tmp_path, tmp_path / "runs")
    assert not history["changes"] and len(history["errors"]) == 1
    assert "no front-door" in history["errors"][0]["message"]


@pytest.mark.parametrize("command", [[], {}, None, True, 3])
def test_invalid_command_preserves_healthy_sibling(tmp_path, command):
    bad = put(tmp_path, "proposals/o__r/8.json", dict(change="bad", command=command))
    put(tmp_path, "proposals/o__r/9.json", dict(change="healthy"))
    history = collect_history(tmp_path, tmp_path / "runs")
    assert [row["issue"] for row in history["changes"]] == [9]
    assert history["errors"] == [dict(path=str(bad), line=None,
                                     message="ValueError: command must be a string")]


@pytest.mark.parametrize("operation,relative", [
    ("stat", "proposals/o__r"), ("stat", "proposals/o__r/8.jobs/bad"),
    ("iterdir", "proposals"), ("iterdir", "proposals/o__r"),
    ("iterdir", "proposals/o__r/8.jobs"),
])
def test_discovery_permission_errors_preserve_siblings(tmp_path, monkeypatch, operation, relative):
    put(tmp_path, "proposals/o__r/8.jobs/bad/request.json", dict(args=["propose", "m", "--change", "bad"]))
    put(tmp_path, "proposals/o__r/9.jobs/good/request.json", dict(args=["propose", "m", "--change", "good"]))
    put(tmp_path, "proposals/o__s/10.json", dict(change="sibling"))
    put(tmp_path, "admissions/o__t/28.json", dict(change="t-issue-11-feature"))
    denied, original = tmp_path / relative, getattr(Path, operation)

    def guarded(path, *args, **kwargs):
        if path == denied:
            raise PermissionError("fixture access denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, guarded)
    history = collect_history(tmp_path, tmp_path / "runs")
    expected = ([9] if relative.endswith(("/bad", ".jobs")) else [])
    expected += ([10] if relative != "proposals" else []) + [11]
    assert [row["issue"] for row in history["changes"]] == expected
    assert history["errors"] == [dict(path=str(denied), line=None,
                                     message="PermissionError: fixture access denied")]


def intake_job(root, kind, job, command, outcome=None, *, number=None, head=None, **extra):
    number = number or (8 if kind == "proposals" else 28)
    change = "r-issue-8-feature"
    args = ["revise", change] if command == "revise" else [command, "manifest", "--change", change]
    if head is not None:
        args += ["--head", head]
    base = f"{kind}/o__r/{number}.jobs/{job}"
    put(root, f"{base}/request.json", dict(args=args))
    result = dict(outcome=outcome, **extra) if outcome is not None else None
    if result is not None:
        put(root, f"{base}/result.json", dict(result=result))
    return dict(change=change, command=command, job=job, head=head, result=result)


def event_log(root, events):
    path = root / "runs/r-issue-8-feature/events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deliberately decreasing timestamps: append order supplies attribution.
    path.write_text("".join(json.dumps(dict(at=100 - index, **event)) + "\n"
                            for index, event in enumerate(events)))
    return path


@pytest.mark.scenario("touchpoint-report/intake-round-accounting/each-intake-kind-counted-once")
@pytest.mark.scenario("touchpoint-report/human-event-accounting/pauses-resumes-and-accepts-by-reason")
def test_touchpoints_count_each_kind(tmp_path):
    rounds = [
        ("proposals", "q", "propose", "needs_input", dict(questions=["a", "b", "c"])),
        ("revisions", "q", "propose", "needs_input", dict(questions=["a", "b"])),
        ("revisions", "s", "propose", "drafted", dict(commit="spec")),
        ("revisions", "b", "revise", "revised", dict(commit="build")),
        ("admissions", "no", "admit", "refused", {}),
        ("admissions", "yes", "admit", "admitted", {}),
    ]
    for kind, job, command, outcome, extra in rounds:
        receipt = intake_job(tmp_path, kind, job, command, outcome, **extra)
        put(tmp_path, f"{kind}/o__r/{8 if kind == 'proposals' else 28}.json", receipt)
    event_log(tmp_path, [
        dict(event="revised", commit="build"),
        dict(event="pause", reason="requested"), dict(event="resumed"),
        dict(event="pause", reason="blocked"), dict(event="resumed", note=""),
        dict(event="pause", reason="stuck"), dict(event="human_accept"),
        dict(event="pause", reason="human_checks"),
        dict(event="pause", reason="ready_to_publish"), dict(event="arbiter_accept"),
        dict(event="acceptance"), dict(event="auto_resumed"),
    ])
    history = collect_events(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    assert row["counts"] == dict(drafter_questions=2, spec_revisions=2, build_revisions=1,
                               human_pauses=4, manual_resumes=2, human_accepts=1,
                               admission_refusals=1, total=13,
                               pauses_by_reason=dict(blocked=1, human_checks=1, requested=1, stuck=1),
                               resumes_by_reason=dict(blocked=1, requested=1))
    assert row["build_commits"] == {"build"} and not row["warnings"]


@pytest.mark.parametrize("kind,command,outcome,expected", [
    ("proposals", "propose", "drafted", {}),
    ("proposals", "propose", "needs_input", dict(drafter_questions=1)),
    ("proposals", "propose", "rejected", {}),
    ("revisions", "propose", "drafted", dict(spec_revisions=1)),
    ("revisions", "propose", "needs_input", dict(drafter_questions=1, spec_revisions=1)),
    ("revisions", "propose", "rejected", dict(spec_revisions=1)),
    ("revisions", "revise", "revised", dict(build_revisions=1)),
    ("revisions", "revise", "refused", dict(build_revisions=1)),
    ("admissions", "admit", "admitted", {}),
    ("admissions", "admit", "refused", dict(admission_refusals=1)),
])
def test_intake_terminal_outcomes(tmp_path, kind, command, outcome, expected):
    intake_job(tmp_path, kind, "job", command, outcome, commit="abc")
    history = collect_intake(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    counts = history["changes"][0]["counts"]
    nonzero = dict(expected, total=sum(expected.values())) if expected else {}
    assert {key: value for key, value in counts.items() if value} == nonzero


@pytest.mark.scenario("touchpoint-report/retained-intake-history/retained-jobs-survive-snapshot-replacement")
@pytest.mark.parametrize("kind,command,outcome,field", [
    ("proposals", "propose", "needs_input", "drafter_questions"),
    ("admissions", "admit", "refused", "admission_refusals"),
])
@pytest.mark.parametrize("reference", [True, False])
@pytest.mark.parametrize("ambiguous", [True, False])
def test_independent_jobs_and_replaced_snapshots(tmp_path, kind, command, outcome, field, reference, ambiguous):
    for job in ("first", "latest"):
        # Different heads distinguish admission snapshots; commit distinguishes proposals.
        marker = "same" if ambiguous else job
        receipt = intake_job(tmp_path, kind, job, command, outcome, head=marker, commit=marker)
    if not reference:
        receipt.pop("job")
    number = 8 if kind == "proposals" else 28
    put(tmp_path, f"{kind}/o__r/{number}.json", receipt)
    put(tmp_path, "revisions/o__r/29.json",
        dict(change=receipt["change"], command="propose", result=dict(outcome="rejected")))
    history = collect_intake(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    assert row["counts"][field] == 2 and row["counts"]["spec_revisions"] == 1
    assert row["counts"]["total"] == 3
    assert any("earlier rounds may have been overwritten" in warning for warning in row["warnings"])
    assert any("Ambiguous receipt" in warning for warning in row["warnings"]) == (ambiguous and not reference)


@pytest.mark.parametrize("difference", ["head", "commit", "outcome", "command", "number", "job"])
def test_snapshot_matching_requires_compatible_evidence(tmp_path, difference):
    receipt = intake_job(tmp_path, "admissions", "job", "admit", "refused", head="head", commit="commit")
    receipt.pop("job")
    kind, number = "admissions", 28
    if difference in ("head", "job"):
        receipt[difference] = "other"
    elif difference in ("commit", "outcome"):
        receipt["result"][difference] = "other" if difference == "commit" else "admitted"
    elif difference == "command":
        kind, receipt["command"] = "revisions", "revise"
    else:
        number = 29
    put(tmp_path, f"{kind}/o__r/{number}.json", receipt)
    history = collect_intake(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    assert row["counts"]["total"] == (1 if difference == "outcome" else 2)
    assert any("Unmatched legacy snapshot" in warning for warning in row["warnings"])


@pytest.mark.scenario("touchpoint-report/incomplete-intake-rounds/incomplete-and-empty-rounds")
def test_incomplete_and_empty_rounds(tmp_path):
    for command, number in (("propose", 28), ("revise", 29)):
        put(tmp_path, f"revisions/o__r/{number}.json",
            dict(change="r-issue-8-feature", command=command, result=dict(outcome="empty")))
    intake_job(tmp_path, "revisions", "pending", "revise")
    receipt = intake_job(tmp_path, "admissions", "failed", "admit")
    put(tmp_path, "admissions/o__r/28.json", receipt)
    error_path = put(tmp_path, "admissions/o__r/28.jobs/failed/result.json", dict(error="worker failed"))
    intake_job(tmp_path, "revisions", "refused", "revise", "refused")
    event_log(tmp_path, [dict(event="revised", commit="event-only")])
    history = collect_events(tmp_path, tmp_path / "runs")
    row, = history["changes"]
    assert row["counts"]["build_revisions"] == row["counts"]["total"] == 2
    assert row["counts"]["admission_refusals"] == 0 and row["build_commits"] == {"event-only"}
    assert any("Pending intake request" in warning and "pending/request.json" in warning
               for warning in row["warnings"])
    assert len(history["errors"]) == 1 and history["errors"][0]["path"] == str(error_path)
    assert "wrapper" in history["errors"][0]["message"]


def test_successful_build_results_share_commit_identity(tmp_path):
    for job, commit in (("first", "a"), ("repeat", "a"), ("next", "b")):
        intake_job(tmp_path, "revisions", job, "revise", "revised", commit=commit)
    history = collect_intake(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    assert row["counts"]["build_revisions"] == 2 and row["build_commits"] == {"a", "b"}


@pytest.mark.parametrize("state", ["pending", "completed", "failed"])
def test_undelivered_receipt_uses_retained_job_state(tmp_path, state):
    receipt = intake_job(tmp_path, "admissions", "job", "admit")
    put(tmp_path, "admissions/o__r/28.json", receipt)
    if state != "pending":
        wrapper = dict(result=dict(outcome="refused")) if state == "completed" else dict(error="failed")
        put(tmp_path, "admissions/o__r/28.jobs/job/result.json", wrapper)
    history = collect_intake(tmp_path, tmp_path / "runs")
    row, = history["changes"]
    assert row["counts"]["total"] == (1 if state == "completed" else 0)
    assert len(history["errors"]) == (1 if state == "failed" else 0)
    assert sum("Pending intake request" in warning for warning in row["warnings"]) == (state == "pending")


@pytest.mark.parametrize("extra", [dict(outcome=True), dict(outcome={}), dict(outcome="admitted"),
                                    dict(commit=None), dict(commit=[]), dict(commit=" ")])
def test_invalid_intake_results_preserve_completed_siblings(tmp_path, extra):
    intake_job(tmp_path, "revisions", "good", "revise", "refused")
    intake_job(tmp_path, "revisions", "bad", "revise", "revised", commit="abc")
    bad = put(tmp_path, "revisions/o__r/28.jobs/bad/result.json",
              dict(result=dict(dict(outcome="revised", commit="abc"), **extra)))
    history = collect_intake(tmp_path, tmp_path / "runs")
    assert history["changes"][0]["counts"]["total"] == 1
    assert len(history["errors"]) == 1 and history["errors"][0]["path"] == str(bad)


@pytest.mark.scenario("touchpoint-report/retained-intake-history/build-evidence-counted-once")
def test_build_receipt_result_and_events_share_commit_identity(tmp_path):
    receipt = intake_job(tmp_path, "revisions", "build", "revise", "revised", commit="a")
    put(tmp_path, "revisions/o__r/28.json", receipt)
    event_log(tmp_path, [dict(event="revised", commit=commit) for commit in ("a", "b", "b")])
    history = collect_events(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    assert row["counts"]["build_revisions"] == row["counts"]["total"] == 2
    assert row["build_commits"] == {"a", "b"}


@pytest.mark.parametrize("clearing", ["revised", "human_accept", "resumed", "auto_resumed", "finished", "aborted"])
def test_event_resolution_clears_resume_attribution(tmp_path, clearing):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    event_log(tmp_path, [dict(event="pause", reason="requested"),
                         dict(event=clearing, commit="a"), dict(event="resumed")])
    history = collect_events(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    assert row["counts"]["human_pauses"] == (clearing != "auto_resumed")
    assert row["counts"]["manual_resumes"] == 1 + (clearing == "resumed")
    assert row["counts"]["resumes_by_reason"] == (
        dict(requested=1, unknown=1) if clearing == "resumed" else dict(unknown=1))
    assert any("Unpaired manual resume" in warning for warning in row["warnings"])


@pytest.mark.parametrize("bad", ['{"event":', '{"event":"revised","at":1}',
                                  '{"event":"pause","at":true,"reason":"blocked"}'])
def test_bad_event_preserves_siblings_without_stale_reason(tmp_path, bad):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    path = event_log(tmp_path, [dict(event="pause", reason="blocked")])
    with path.open("a") as stream:
        stream.write(bad + '\n{"event":"resumed","at":2}\n')
    history = collect_events(tmp_path, tmp_path / "runs")
    row, = history["changes"]
    assert row["counts"]["human_pauses"] == row["counts"]["manual_resumes"] == 1
    assert row["counts"]["resumes_by_reason"] == dict(unknown=1)
    assert len(history["errors"]) == 1
    assert history["errors"][0]["path"] == str(path) and history["errors"][0]["line"] == 2


@pytest.mark.scenario("touchpoint-report/partial-failure-isolation/unreadable-records-are-not-fatal")
def test_file_error_sorts_before_first_line_error_and_preserves_read_events(tmp_path, monkeypatch):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    path = event_log(tmp_path, [dict(event="human_accept")])
    path.write_bytes(b'{"event":\n' + path.read_bytes())
    original_open = Path.open

    @contextmanager
    def interrupted_read():
        with original_open(path, "rb") as stream:
            def lines():
                yield from stream
                raise OSError("fixture event log read interrupted")

            yield lines()

    def guarded_open(source, *args, **kwargs):
        if source == path:
            return interrupted_read()
        return original_open(source, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    history = collect_events(tmp_path, tmp_path / "runs")
    row, = history["changes"]
    assert row["counts"]["human_accepts"] == row["counts"]["total"] == 1
    file_error, line_error = history["errors"]
    assert file_error == dict(path=str(path), line=None,
                              message="OSError: fixture event log read interrupted")
    assert line_error["path"] == str(path) and line_error["line"] == 1
    assert line_error["message"].startswith("JSONDecodeError:")
    # File-level errors precede line 1 even when message order would reverse them.
    assert line_error["message"] < file_error["message"]


def test_identical_events_are_distinct_and_unknown_events_do_not_clear_pause(tmp_path):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    path = event_log(tmp_path, [dict(event="pause", reason="requested"),
                              dict(event="future_kind", extra=True), dict(event="resumed")])
    path.write_text(path.read_text() * 2)
    history = collect_events(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    assert row["counts"]["human_pauses"] == row["counts"]["manual_resumes"] == 2
    assert row["counts"]["pauses_by_reason"] == row["counts"]["resumes_by_reason"] == dict(requested=2)


def test_replay_classifies_automatic_and_manually_resolved_pauses(tmp_path):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    infra = dict(event="pause", reason="infra", task="1", resume_at=10)
    automatic = dict(event="auto_resumed", reason="infra", task="1")
    event_log(tmp_path, [
        infra, automatic, infra, automatic, infra, dict(event="resumed"), infra,
        dict(event="pause", reason="usage_limit", resume_at=10),
        dict(event="pause", reason="usage_limit", resume_at=10), dict(event="human_accept"),
        dict(event="pause", reason="future", resume_at=10), dict(event="resumed"),
        dict(event="pause", reason="legacy"), dict(event="auto_resumed"),
        dict(event="pause", reason="infra", resume_at=10),
        dict(event="pause", reason="future", resume_at=10),
    ])
    history = collect_events(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    counts = row["counts"]
    assert counts["human_pauses"] == 4 and counts["manual_resumes"] == 2
    assert counts["human_accepts"] == 1 and counts["total"] == 7
    assert counts["pauses_by_reason"] == dict(future=1, infra=2, usage_limit=1)
    assert counts["resumes_by_reason"] == dict(future=1, infra=1)
    assert len(row["warnings"]) == 1 and "Unclassified scheduled pause (future)" in row["warnings"][0]


def test_missing_event_log_is_only_an_error_for_an_admitted_change(tmp_path):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    assert not collect_events(tmp_path, tmp_path / "runs")["errors"]
    intake_job(tmp_path, "admissions", "admit", "admit", "admitted")
    history = collect_events(tmp_path, tmp_path / "runs")
    assert len(history["errors"]) == 1
    assert history["errors"][0]["path"].endswith("r-issue-8-feature/events.jsonl")
    assert history["changes"][0]["counts"]["total"] == 0


@pytest.mark.scenario("touchpoint-report/automatic-pause-classification/automatic-recovery-and-exhausted-infra")
def test_infra_allowance_is_per_task_and_survives_manual_resume(tmp_path):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    infra = dict(event="pause", reason="infra", task="1", resume_at=10)
    other = dict(infra, task="2")
    path = event_log(tmp_path, [
        infra, dict(event="auto_resumed", reason="infra", task="1"),
        other, dict(event="auto_resumed"),  # Legacy resolution inherits task/reason.
        infra, dict(event="auto_resumed"),
        infra, dict(event="resumed", note=""), infra,
        # Usage recoveries must not consume task 2's remaining infra allowance.
        dict(event="pause", reason="usage_limit", task="2", resume_at=10),
        dict(event="auto_resumed", reason="usage_limit", task="2"), other,
        dict(event="pause", reason="usage_limit", task="2", resume_at=10),
        dict(event="pause", reason="new unscheduled reason", resume_at=0),
        dict(event="pause", reason="new scheduled reason", resume_at=10),
    ])
    history = collect_events(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    counts = row["counts"]
    assert counts["human_pauses"] == 3
    assert counts["manual_resumes"] == 1 and counts["human_accepts"] == 0
    assert counts["total"] == 4
    assert counts["pauses_by_reason"] == {"infra": 2, "new unscheduled reason": 1}
    assert counts["resumes_by_reason"] == {"infra": 1}
    warning, = row["warnings"]
    assert "Unclassified scheduled pause (new scheduled reason)" in warning
    assert "unknown scheduling policy" in warning and f"{path}:15" in warning


@pytest.mark.scenario("touchpoint-report/automatic-pause-classification/manual-resolution-establishes-a-human-wait")
@pytest.mark.parametrize("reason", ["usage_limit", "future reason", "infra"])
@pytest.mark.parametrize("resolution", ["resumed", "human_accept"])
@pytest.mark.parametrize("task", ["1", ""])
def test_manual_resolution_counts_scheduled_wait_once(tmp_path, reason, resolution, task):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    event_log(tmp_path, [
        dict(event="pause", reason=reason, task=task, resume_at=10),
        dict(event=resolution),
        dict(event="pause", reason="legacy"), dict(event="auto_resumed"),
        dict(event="pause", reason="infra", resume_at=10),
    ])
    history = collect_events(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    counts = row["counts"]
    assert counts["human_pauses"] == 1 and counts["pauses_by_reason"] == {reason: 1}
    assert counts["manual_resumes"] == (resolution == "resumed")
    assert counts["human_accepts"] == (resolution == "human_accept")
    assert counts["resumes_by_reason"] == ({reason: 1} if resolution == "resumed" else {})
    assert counts["total"] == 2 and not row["warnings"]


@pytest.mark.scenario("touchpoint-report/event-reason-attribution/broken-attribution-stays-explicit")
@pytest.mark.parametrize("clearing", [None, "revised", "human_accept", "malformed"])
def test_unpaired_resume_warns_with_unknown_reason(tmp_path, clearing):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    events = [] if clearing is None else [dict(event="pause", reason="blocked")]
    if clearing not in (None, "malformed"):
        events.append(dict(event=clearing, commit="revision"))
    path = event_log(tmp_path, events)
    with path.open("a") as stream:
        if clearing == "malformed":
            stream.write('{"event":\n')
        stream.write('{"event":"resumed","at":1}\n')
    history = collect_events(tmp_path, tmp_path / "runs")
    row, = history["changes"]
    counts = row["counts"]
    assert counts["manual_resumes"] == 1 and counts["resumes_by_reason"] == {"unknown": 1}
    assert counts["human_pauses"] == (clearing is not None)
    assert counts["human_accepts"] == (clearing == "human_accept")
    assert counts["build_revisions"] == (clearing == "revised")
    assert len(history["errors"]) == (clearing == "malformed")
    warning, = row["warnings"]
    assert "Unpaired manual resume; reason unknown" in warning
    assert f"{path}:{1 if clearing is None else 3}" in warning


@pytest.mark.scenario("touchpoint-report/event-reason-attribution/broken-attribution-stays-explicit")
@pytest.mark.parametrize("bad", [b'{"event":', b'\xff',
                                  b'{"extra":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}',
                                  b'{"event":"pause","at":true,"reason":"infra"}'])
def test_broken_history_invalidates_infra_allowance_but_manual_resolution_counts(tmp_path, bad):
    intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    infra = dict(event="pause", reason="infra", task="1", resume_at=10)
    automatic = dict(event="auto_resumed", reason="infra", task="1")
    path = event_log(tmp_path, [infra, automatic, infra, automatic, infra])
    with path.open("ab") as stream:
        stream.write(bad + b"\n")
        # Neither a manual intervention nor more automatic recoveries repairs
        # missing history. Fresh paired manual resolution still establishes a wait.
        for event in [dict(event="resumed"), infra, dict(event="human_accept"),
                      infra, dict(event="resumed"),
                      infra, automatic, infra, automatic, infra]:
            stream.write((json.dumps(dict(at=1, **event)) + "\n").encode())
    history = collect_events(tmp_path, tmp_path / "runs")
    row, = history["changes"]
    counts = row["counts"]
    assert counts["human_pauses"] == 2 and counts["pauses_by_reason"] == {"infra": 2}
    assert counts["manual_resumes"] == 2 and counts["resumes_by_reason"] == {"infra": 1, "unknown": 1}
    assert counts["human_accepts"] == 1 and counts["total"] == 5
    error, = history["errors"]
    assert error["path"] == str(path) and error["line"] == 6
    assert len(row["warnings"]) == 3
    uncertain = [warning for warning in row["warnings"] if "Unclassified scheduled pause (infra)" in warning]
    assert len(uncertain) == 2
    assert all("broken event history; recovery allowance is uncertain" in warning for warning in uncertain)
    assert any(f"{path}:5;" in warning for warning in uncertain)
    assert any(f"{path}:16;" in warning for warning in uncertain)
    assert any("Unpaired manual resume; reason unknown" in warning for warning in row["warnings"])


def dated_put(root, relative, data, timestamp):
    path = put(root, relative, data)
    stamp = datetime.fromisoformat(timestamp).timestamp()
    os.utime(path, (stamp, stamp))
    return path


@pytest.mark.scenario("touchpoint-report/draft-date-selection/inclusive-cutoff-selects-whole-changes")
def test_since_uses_earliest_proposal_request_and_keeps_whole_history(tmp_path):
    times = ["2026-10-20T23:59:59+00:00", "2026-10-21T00:00:00+00:00", "2026-10-21T00:00:01+00:00"]
    for issue, timestamp in enumerate(times, 1):
        change = f"r-issue-{issue}-feature"
        # Lexical job order and current receipt dates must not select the latest attempt.
        for job, stamp, outcome in (("z-first", timestamp, "needs_input"),
                                     ("a-latest", "2026-10-25T00:00:00+00:00", "drafted")):
            base = f"proposals/o__r/{issue}.jobs/{job}"
            dated_put(tmp_path, f"{base}/request.json",
                      dict(args=["propose", "manifest", "--change", change]), stamp)
            put(tmp_path, f"{base}/result.json", dict(result=dict(outcome=outcome)))
        dated_put(tmp_path, f"proposals/o__r/{issue}.json",
                  dict(change=change, job="a-latest", result=dict(outcome="drafted")),
                  "2026-10-26T00:00:00+00:00")
        dated_put(tmp_path, f"revisions/o__r/{issue + 20}.json",
                  dict(change=change, command="propose", result=dict(outcome="needs_input")),
                  "2026-10-27T00:00:00+00:00")
        path = put(tmp_path, f"runs/{change}/events.jsonl", dict(event="human_accept", at=1))
        # Errors belonging to excluded changes remain visible.
        if issue == 1:
            path.write_text(path.read_text() + '\n{"event":')
    report = collect_report(tmp_path, tmp_path / "runs", since=date(2026, 10, 21))
    assert report["since"] == "2026-10-21"
    assert [row["issue"] for row in report["changes"]] == [2, 3]
    assert [row["drafted_at"] for row in report["changes"]] == times[1:]
    for row in report["changes"]:
        assert row["drafted_at_source"] == "request_mtime"
        assert any("approximate" in warning and "request_mtime" in warning for warning in row["warnings"])
        assert row["counts"]["drafter_questions"] == 2
        assert row["counts"]["spec_revisions"] == row["counts"]["human_accepts"] == 1
        assert row["counts"]["total"] == 4
    assert report["totals"]["changes"] == 2 and report["totals"]["total"] == 8
    error, = report["errors"]
    assert error["path"].endswith("r-issue-1-feature/events.jsonl") and error["line"] == 2
    unfiltered = collect_report(tmp_path, tmp_path / "runs")
    assert unfiltered["totals"]["total"] == 12
    assert unfiltered["changes"][0]["drafted_at"] == times[0]


@pytest.mark.scenario("touchpoint-report/draft-date-selection/inclusive-cutoff-selects-whole-changes")
@pytest.mark.parametrize("bad_result", [[], False, "invalid", {"outcome": "invalid"}])
@pytest.mark.parametrize("has_receipt", [False, True])
def test_malformed_result_preserves_initial_draft_attempt(tmp_path, bad_result, has_receipt):
    change = "r-issue-8-feature"
    dated_put(tmp_path, "proposals/o__r/8.jobs/first/request.json",
              dict(args=["propose", "manifest", "--change", change]),
              "2026-10-20T00:00:00+00:00")
    result_path = put(tmp_path, "proposals/o__r/8.jobs/first/result.json",
                      dict(result=bad_result))
    if has_receipt:
        dated_put(tmp_path, "proposals/o__r/8.json",
                  dict(change=change, result=dict(outcome="needs_input")),
                  "2026-10-22T00:00:00+00:00")
    report = collect_report(tmp_path, tmp_path / "runs")
    row, = report["changes"]
    assert row["drafted_at"] == "2026-10-20T00:00:00+00:00"
    assert row["drafted_at_source"] == "request_mtime"
    assert row["counts"]["total"] == row["counts"]["drafter_questions"] == int(has_receipt)
    error, = report["errors"]
    assert error["path"] == str(result_path) and error["line"] is None
    assert error["message"].startswith("ValueError:")
    filtered = collect_report(tmp_path, tmp_path / "runs", since=date(2026, 10, 21))
    assert filtered["changes"] == []
    assert filtered["totals"]["changes"] == filtered["totals"]["total"] == 0
    assert filtered["errors"] == report["errors"]


@pytest.mark.scenario("touchpoint-report/draft-date-selection/approximate-missing-and-invalid-dates")
def test_receipt_date_estimate_and_unknown_date_filtering(tmp_path):
    # Argument rejection belongs to the command tests in task 3.2.
    dated_put(tmp_path, "proposals/o__r/8.json",
              dict(change="r-issue-8-feature", result=dict(outcome="needs_input")),
              "2026-10-20T19:00:00-05:00")
    missing = dated_put(tmp_path, "admissions/o__r/29.json",
                        dict(change="r-issue-9-feature", result=dict(outcome="refused")),
                        "2026-10-30T00:00:00+00:00")
    put(tmp_path, "runs/r-issue-9-feature/events.jsonl", dict(event="human_accept", at=2_000_000_000))
    report = collect_report(tmp_path, tmp_path / "runs")
    estimated, unknown = report["changes"]
    assert estimated["drafted_at"] == "2026-10-21T00:00:00+00:00"
    assert estimated["drafted_at_source"] == "receipt_mtime"
    assert any("approximate" in warning and "receipt_mtime" in warning for warning in estimated["warnings"])
    assert unknown["drafted_at"] is unknown["drafted_at_source"] is None
    assert any("draft date unknown" in warning.lower() for warning in unknown["warnings"])
    assert not report["errors"] and report["totals"]["total"] == 3
    filtered = collect_report(tmp_path, tmp_path / "runs", since=date(2026, 10, 21))
    assert filtered["changes"] == [estimated]
    assert filtered["totals"]["changes"] == filtered["totals"]["total"] == 1
    error, = filtered["errors"]
    assert error["path"] == str(missing) and error["line"] is None
    assert all(text in error["message"] for text in ("--since", "draft date unknown", "excluded"))


@pytest.mark.parametrize("outcome", [None, "needs_input"])
def test_draft_attempt_date_survives_without_success_or_current_receipt(tmp_path, outcome):
    intake_job(tmp_path, "proposals", "first", "propose", outcome)
    request = tmp_path / "proposals/o__r/8.jobs/first/request.json"
    os.utime(request, (0, 0))
    report = collect_report(tmp_path, tmp_path / "runs", since=date(1970, 1, 1))
    row, = report["changes"]
    assert row["drafted_at"] == "1970-01-01T00:00:00+00:00"
    assert row["drafted_at_source"] == "request_mtime"
    assert row["counts"]["total"] == (outcome == "needs_input")
    assert not report["errors"]


@pytest.mark.scenario("touchpoint-report/versioned-report-output/table-and-json-agree")
@pytest.mark.scenario("touchpoint-report/versioned-report-output/observed-history-does-not-claim-merge")
def test_touchpoints_json_document(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    nightshift_root, runs = tmp_path / ".nightshift", tmp_path / ".specloop/runs"
    fixtures = [("z/r", 8, "aborted"), ("a/r", 10, "active"), ("a/r", 2, "finished")]
    for repo, issue, state in fixtures:
        change = f"r-issue-{issue}-{state}"
        dated_put(nightshift_root, f"proposals/{repo.replace('/', '__')}/{issue}.json",
                  dict(change=change, result=dict(outcome="needs_input")), "2026-10-21T00:00:00+00:00")
        events = [dict(event=state, at=1)]
        if state == "finished":
            put(nightshift_root, "revisions/a__r/22.json",
                dict(change=change, command="propose", result=dict(outcome="needs_input")))
            events += [dict(event="human_accept", at=2), dict(event="revised", at=3, commit="post-finish")]
        else:
            for reason in (["zebra", "alpha"] if state == "active" else ["zebra"]):
                events += [dict(event="pause", reason=reason, at=2), dict(event="resumed", at=3)]
        path = put(runs, f"{change}/events.jsonl", {})
        path.write_text("".join(json.dumps(event) + "\n" for event in events))
    report = collect_report(nightshift_root, runs)
    encoded = json.dumps(report)
    assert encoded == json.dumps(collect_report(nightshift_root, runs))
    assert json.loads(encoded) == report
    assert set(report) == {"version", "since", "coverage", "merge_boundary", "limitations", "changes", "totals", "errors"}
    assert type(report["version"]) is int and report["version"] == 1
    assert report["since"] is None and not report["errors"]
    assert report["coverage"] == "observed_local_history" and report["merge_boundary"] == "unknown"
    limits = " ".join(report["limitations"])
    assert all(term in limits for term in ("approximate", "overwritten or removed", "merge time is unavailable",
                                          "sum of category observations", "both categories"))
    assert [(row["repo"], row["issue"]) for row in report["changes"]] == [("a/r", 2), ("a/r", 10), ("z/r", 8)]
    scalar = ("drafter_questions", "spec_revisions", "build_revisions", "human_pauses",
              "manual_resumes", "human_accepts", "admission_refusals")
    expected = [(2, 1, 1, 0, 0, 1, 0), (1, 0, 0, 2, 2, 0, 0), (1, 0, 0, 1, 1, 0, 0)]
    for row, values in zip(report["changes"], expected):
        assert set(row) == {"repo", "issue", "change", "drafted_at", "drafted_at_source", "counts", "warnings"}
        assert set(row["counts"]) == {*scalar, "total", "pauses_by_reason", "resumes_by_reason"}
        assert tuple(row["counts"][field] for field in scalar) == values
        assert row["counts"]["total"] == sum(values)
        assert row["drafted_at"] == "2026-10-21T00:00:00+00:00" and row["warnings"]
    totals = report["totals"]
    assert set(totals) == {"changes", *report["changes"][0]["counts"]}
    assert totals["changes"] == 3 and totals["total"] == 13
    assert tuple(totals[field] for field in scalar) == (4, 1, 1, 3, 3, 1, 0)
    assert totals["pauses_by_reason"] == totals["resumes_by_reason"] == {"alpha": 1, "zebra": 2}
    for counts in [totals, *(row["counts"] for row in report["changes"])]:
        assert all(type(counts[field]) is int for field in (*scalar, "total"))
        for field, counter in (("pauses_by_reason", "human_pauses"), ("resumes_by_reason", "manual_resumes")):
            assert list(counts[field]) == sorted(counts[field])
            assert sum(counts[field].values()) == counts[counter]

    outputs = {}
    for args in ([], ["--json"]):
        for _ in range(2):
            assert main(args) == 0  # Approximation/history warnings are not errors.
            captured = capsys.readouterr()
            assert captured.err == ""
            key = tuple(args)
            assert captured.out == outputs.setdefault(key, captured.out)
    assert json.loads(outputs[("--json",)]) == report  # Rejects extra stdout documents/logging.
    table = outputs[()]
    assert table == render_table(report) + "\n"
    lines = table.splitlines()
    assert re.split(r"  +", lines[0]) == [
        "Repository", "Issue", "Change", "Questions", "Spec revisions", "Build revisions",
        "Pauses", "Resumes", "Accepts", "Refusals", "Total",
    ]
    for line, row in zip(lines[2:5], report["changes"]):
        cells = re.split(r"  +", line)
        assert cells[:3] == [row["repo"], str(row["issue"]), row["change"]]
        assert list(map(int, cells[3:])) == [row["counts"][field] for field in (*scalar, "total")]
    total_cells = re.split(r"  +", lines[5])
    assert total_cells[:3] == ["TOTAL", "-", "3 changes"]
    assert list(map(int, total_cells[3:])) == [totals[field] for field in (*scalar, "total")]
    labels = [f"{row['repo']} #{row['issue']} {row['change']}" for row in report["changes"]]
    for label, counts in zip([*labels, "TOTAL"], [*(row["counts"] for row in report["changes"]), totals]):
        reason_line = next(line for line in lines if line.startswith(f"  {label}: pauses="))
        pauses, resumes = reason_line.split(": pauses=", 1)[1].split("; resumes=")
        assert json.loads(pauses) == counts["pauses_by_reason"]
        assert json.loads(resumes) == counts["resumes_by_reason"]
    assert "observed_local_history" in table and "merge boundary: unknown" in table
    assert all(limitation in table for limitation in report["limitations"])
    assert all(warning in table for row in report["changes"] for warning in row["warnings"])
    assert table.endswith("Errors:\n  None\n")


def test_report_diagnostics_sort_by_line_before_message(tmp_path):
    put(tmp_path, "proposals/o__r/8.json",
        dict(change="r-issue-8-feature", result=dict(outcome="drafted")))
    path = event_log(tmp_path, [
        dict(event="revised", commit=42),
        dict(event="human_accept"),
        dict(event="pause", reason=42),
    ])
    report = collect_report(tmp_path, tmp_path / "runs")
    # Message order opposes line order, so replacing line numbers with zero
    # must not silently reorder diagnostics from the same file.
    errors = report["errors"]
    assert [(error["path"], error["line"]) for error in errors] == [
        (str(path), 1), (str(path), 3),
    ]
    assert errors[0]["message"] > errors[1]["message"]
    assert report["totals"]["human_accepts"] == 1


def test_empty_report_has_all_zero_totals(tmp_path):
    report = collect_report(tmp_path / "absent", tmp_path / "runs")
    assert not report["changes"] and not report["errors"]
    assert report["totals"] == dict(changes=0, drafter_questions=0, spec_revisions=0, build_revisions=0,
                                     human_pauses=0, manual_resumes=0, human_accepts=0, admission_refusals=0,
                                     pauses_by_reason={}, resumes_by_reason={}, total=0)
    assert not list(tmp_path.iterdir())


def module_cli(home, *args):
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(repo), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-m", "nightshift.touchpoints", *args],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.scenario("touchpoint-report/local-report-entry-point/empty-local-history")
@pytest.mark.parametrize("existing_roots", [False, True])
@pytest.mark.parametrize("args", [[], ["--json"]])
def test_empty_history_module_entry_point(tmp_path, existing_roots, args):
    if existing_roots:
        for kind in ("proposals", "admissions", "revisions"):
            (tmp_path / ".nightshift" / kind).mkdir(parents=True)
        (tmp_path / ".specloop/runs").mkdir(parents=True)
    before = sorted(tmp_path.rglob("*"))
    result = module_cli(tmp_path, *args)
    assert result.returncode == 0 and result.stderr == ""
    report = collect_report(tmp_path / ".nightshift", tmp_path / ".specloop/runs")
    assert report["changes"] == report["errors"] == []
    for field, value in report["totals"].items():
        assert value == ({} if field.endswith("_by_reason") else 0)
    if args:
        assert json.loads(result.stdout) == report
    else:
        assert result.stdout == render_table(report) + "\n"
        total = re.split(r"  +", result.stdout.splitlines()[2])
        assert total[:3] == ["TOTAL", "-", "0 changes"]
        assert list(map(int, total[3:])) == [0] * 8
        assert result.stdout.startswith("Repository")
    assert sorted(tmp_path.rglob("*")) == before


@pytest.mark.scenario("touchpoint-report/draft-date-selection/approximate-missing-and-invalid-dates")
@pytest.mark.parametrize("value", ["2026-02-29", "2026-13-01", "2026-04-31", "0000-01-01",
                                   "20261021", "2026-W43-3", "2026-1-01", "2026-10-21T00:00:00", ""])
def test_invalid_date_is_rejected_before_collection(tmp_path, monkeypatch, capsys, value):
    monkeypatch.setenv("HOME", str(tmp_path))
    def forbidden(*args, **kwargs):
        pytest.fail("invalid arguments must not collect local records")
    monkeypatch.setattr(touchpoints, "collect_report", forbidden)
    with pytest.raises(SystemExit) as exc:
        main(["--json", "--since", value])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "YYYY-MM-DD" in captured.err
    assert not list(tmp_path.iterdir())


@pytest.mark.scenario("touchpoint-report/draft-date-selection/approximate-missing-and-invalid-dates")
def test_since_module_entry_point_and_unknown_date_exit(tmp_path):
    state = tmp_path / ".nightshift"
    dated_put(state, "proposals/o__r/8.json",
              dict(change="r-issue-8-feature", result=dict(outcome="needs_input")),
              "2026-10-20T19:00:00-05:00")
    put(state, "admissions/o__r/29.json",
        dict(change="r-issue-9-feature", result=dict(outcome="refused")))
    unfiltered = module_cli(tmp_path, "--json")
    assert unfiltered.returncode == 0 and unfiltered.stderr == ""
    estimated, unknown = json.loads(unfiltered.stdout)["changes"]
    assert estimated["drafted_at"] == "2026-10-21T00:00:00+00:00"
    assert estimated["drafted_at_source"] == "receipt_mtime" and estimated["warnings"]
    assert unknown["drafted_at"] is unknown["drafted_at_source"] is None
    filtered = module_cli(tmp_path, "--json", "--since", "2026-10-21")
    assert filtered.returncode == 1 and filtered.stderr == ""
    report = json.loads(filtered.stdout)
    assert report["since"] == "2026-10-21" and report["changes"] == [estimated]
    assert report["totals"]["total"] == 1
    assert "draft date unknown" in report["errors"][0]["message"]
    invalid = module_cli(tmp_path, "--json", "--since", "2026-02-30")
    assert invalid.returncode == 2 and invalid.stdout == "" and "YYYY-MM-DD" in invalid.stderr


@pytest.mark.parametrize("args,code", [(["--help"], 0), (["--unknown"], 2), (["--since"], 2)])
def test_argparse_help_and_usage_do_not_collect(tmp_path, monkeypatch, capsys, args, code):
    monkeypatch.setenv("HOME", str(tmp_path))
    def forbidden(*args, **kwargs):
        pytest.fail("help and invalid arguments must not collect records")
    monkeypatch.setattr(touchpoints, "collect_report", forbidden)
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == code
    captured = capsys.readouterr()
    assert "usage:" in (captured.out if code == 0 else captured.err)
    assert (captured.err if code == 0 else captured.out) == ""
    if args == ["--help"]:
        help_text = " ".join(captured.out.split())
        assert "--since YYYY-MM-DD" in help_text
        assert "include changes drafted on or after this date (UTC, approximate)" in help_text


def test_partial_report_module_exit_and_diagnostics_in_both_formats(tmp_path):
    state = tmp_path / ".nightshift"
    put(state, "proposals/o__r/8.json", dict(change="r-issue-8-feature", result=dict(outcome="needs_input")))
    bad = put(state, "proposals/o__r/9.json", {})
    bad.write_text('{"private": "do not echo",')
    events = put(tmp_path, ".specloop/runs/r-issue-8-feature/events.jsonl", {})
    events.write_text('{"event":"human_accept","at":1}\nBAD\n{"event":"human_accept","at":2}\n')
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in tmp_path.rglob("*") if path.is_file()}
    encoded, table = module_cli(tmp_path, "--json"), module_cli(tmp_path)
    assert encoded.returncode == table.returncode == 1
    assert encoded.stderr == table.stderr == ""
    report = json.loads(encoded.stdout)
    assert report["totals"]["human_accepts"] == 2 and report["totals"]["total"] == 3
    assert len(report["errors"]) == 2
    assert table.stdout == render_table(report) + "\n"
    for error in report["errors"]:
        assert error["message"] in table.stdout
    assert f"{events}:2:" in table.stdout and f"{bad}:" in table.stdout
    assert "do not echo" not in encoded.stdout + table.stdout
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.scenario("touchpoint-report/partial-failure-isolation/unreadable-records-are-not-fatal")
def test_unreadable_records_are_reported_not_fatal(tmp_path, monkeypatch, capsys):
    state, runs = tmp_path / ".nightshift", tmp_path / ".specloop/runs"
    change = "r-issue-8-feature"
    put(state, "proposals/o__r/8.json", dict(change=change, result=dict(outcome="needs_input")))
    malformed = put(state, "proposals/o__r/9.json", {})
    malformed.write_text('{"private": "do not echo",')
    invalid = put(state, "proposals/o__r/10.json", dict(change="invalid", issue=True))
    denied_receipt = put(state, "proposals/o__r/11.json", dict(change="denied"))
    for job in ("request_denied", "result_denied", "bad_head"):
        intake_job(state, "admissions", job, "admit", "refused", head=" " if job == "bad_head" else "abc")
    base = state / "admissions/o__r/28.jobs"
    denied_request, denied_result = base / "request_denied/request.json", base / "result_denied/result.json"
    bad_head = base / "bad_head/request.json"
    put(state, "admissions/o__r/29.json", dict(change="r-issue-12-missing", result=dict(outcome="admitted")))
    missing = runs / "r-issue-12-missing/events.jsonl"
    put(state, "proposals/o__r/13.json", dict(change="r-issue-13-denied", result=dict(outcome="drafted")))
    denied_events = put(runs, "r-issue-13-denied/events.jsonl", dict(event="human_accept", at=1))
    events = put(runs, f"{change}/events.jsonl", {})
    events.write_bytes(
        b'{"event":"human_accept","at":1}\nBAD\n'
        b'{"event":"human_accept","at":2}\n'
        b'{"event":"pause","reason":"blocked","at":true}\n'
        b'\xff\n{"event":"human_accept","at":3}\n{"event":')
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in tmp_path.rglob("*") if path.is_file()}
    entries = sorted(tmp_path.rglob("*"))
    original = Path.open
    denied = {denied_receipt, denied_request, denied_result, denied_events}

    def guarded(path, *args, **kwargs):
        if path in denied:
            raise PermissionError("fixture access denied")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setenv("HOME", str(tmp_path))
        patch.setattr(Path, "open", guarded)
        assert main(["--json"]) == 1
        encoded = capsys.readouterr()
        report = json.loads(encoded.out)
        assert main([]) == 1
        table = capsys.readouterr()
        assert encoded.err == table.err == ""
        assert table.out == render_table(report) + "\n"
        assert report["totals"]["drafter_questions"] == 1
        assert report["totals"]["human_accepts"] == 3
        assert report["totals"]["total"] == 4
        expected = {(str(path), None) for path in (*denied, malformed, invalid, bad_head, missing)}
        expected |= {(str(events), line) for line in (2, 4, 5, 7)}
        assert {(error["path"], error["line"]) for error in report["errors"]} == expected
        assert len(report["errors"]) == len(expected)
        for error in report["errors"]:
            location = error["path"] + (f":{error['line']}" if error["line"] else "")
            assert error["message"] and f"{location}: {error['message']}" in table.out
        assert all(error["message"].startswith("PermissionError:") for error in report["errors"]
                   if Path(error["path"]) in denied)
        assert "do not echo" not in encoded.out + table.out
        assert not any("Pending intake request" in warning
                       for row in report["changes"] for warning in row["warnings"])
    assert sorted(tmp_path.rglob("*")) == entries
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in entries if path.is_file()}


@pytest.mark.scenario("touchpoint-report/partial-failure-isolation/discovery-and-compatibility")
@pytest.mark.parametrize("failure", ["permission", "partial", "disappeared"])
def test_discovery_failures_and_forward_compatible_records(tmp_path, monkeypatch, failure):
    change = "r-issue-8-feature"
    put(tmp_path, "proposals/o__r/8.json", dict(change=change, future_field={"nested": [True]},
                                             result=dict(outcome="needs_input", future_result=42)))
    receipt = intake_job(tmp_path, "admissions", "refused", "admit", "refused", future_result=[])
    put(tmp_path, "admissions/o__r/28.json", dict(receipt, future_receipt=False))
    event_log(tmp_path, [dict(event="pause", reason="requested", future_pause=[]),
                         dict(event="future_kind", future_event={"new": True}), dict(event="resumed")])
    blocked = tmp_path / ("proposals/o__r" if failure == "disappeared" else "proposals")
    original = Path.iterdir

    def guarded(path):
        if path == blocked:
            if failure == "partial":
                yield from original(path)
            if failure == "disappeared":
                raise FileNotFoundError("fixture directory disappeared")
            raise PermissionError("fixture discovery denied")
        else:
            yield from original(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "iterdir", guarded)
        report = collect_report(tmp_path, tmp_path / "runs")
    error, = report["errors"]
    assert error["path"] == str(blocked) and error["line"] is None
    assert error["message"].startswith("FileNotFoundError:" if failure == "disappeared" else "PermissionError:")
    row, = report["changes"]
    assert row["counts"]["admission_refusals"] == 1
    assert row["counts"]["human_pauses"] == row["counts"]["manual_resumes"] == 1
    assert row["counts"]["resumes_by_reason"] == {"requested": 1}
    assert row["counts"]["total"] == 3 + (failure == "partial")
    healthy = collect_report(tmp_path, tmp_path / "runs")
    assert not healthy["errors"] and healthy["totals"]["total"] == 4


@pytest.mark.scenario("touchpoint-report/partial-failure-isolation/discovery-and-compatibility")
@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("operation,relative", [
    ("iterdir", "proposals"), ("stat", "proposals/o__r"),
    ("open", "proposals/o__r/8.json"), ("open", "proposals/o__r/8.jobs/draft/result.json"),
    ("open", "runs/r-issue-8-feature/events.jsonl"),
])
def test_process_control_exceptions_propagate(tmp_path, monkeypatch, exception, operation, relative):
    receipt = intake_job(tmp_path, "proposals", "draft", "propose", "drafted")
    put(tmp_path, "proposals/o__r/8.json", receipt)
    event_log(tmp_path, [dict(event="human_accept")])
    original = getattr(Path, operation)

    def guarded(path, *args, **kwargs):
        if path == tmp_path / relative:
            raise exception("fixture interruption")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, guarded)
    with pytest.raises(exception, match="fixture interruption"):
        collect_report(tmp_path, tmp_path / "runs")


@pytest.mark.scenario("touchpoint-report/partial-failure-isolation/unreadable-records-are-not-fatal")
@pytest.mark.parametrize("relative", ["proposals/o__r/9.json", "proposals/o__r/8.jobs/bad/result.json"])
@pytest.mark.parametrize("payload", [b'\xff', b'{"private":"do not echo",',
                                     b'{"extra":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'])
def test_corrupt_json_preserves_healthy_rounds(tmp_path, relative, payload):
    receipt = intake_job(tmp_path, "proposals", "bad", "propose", "needs_input")
    # An embedded request result must never substitute for an unreadable result.json.
    put(tmp_path, "proposals/o__r/8.jobs/bad/request.json",
        dict(args=["propose", "m", "--change", receipt["change"]], result=receipt["result"]))
    put(tmp_path, "proposals/o__r/8.json", dict(change=receipt["change"], result=dict(outcome="needs_input")))
    bad = put(tmp_path, relative, {})
    bad.write_bytes(payload)
    report = collect_report(tmp_path, tmp_path / "runs")
    error, = report["errors"]
    assert error["path"] == str(bad) and error["line"] is None
    assert report["totals"]["drafter_questions"] == 1
    assert not any("Pending intake request" in warning for warning in report["changes"][0]["warnings"])

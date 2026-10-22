import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from nightshift.touchpoints import collect_events, collect_history, collect_intake


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

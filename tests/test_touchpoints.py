import json
from pathlib import Path

import pytest

from nightshift.touchpoints import collect_history, collect_intake


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


@pytest.mark.scenario("touchpoint-report/intake-round-accounting/each-intake-kind-counted-once")
def test_each_intake_kind_counted_once(tmp_path):
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
    # Matching revised-event union is exercised by task 2.1's event replay tests.
    history = collect_intake(tmp_path, tmp_path / "runs")
    assert not history["errors"]
    row, = history["changes"]
    assert row["counts"] == dict(drafter_questions=2, spec_revisions=2, build_revisions=1,
                               human_pauses=0, manual_resumes=0, human_accepts=0,
                               admission_refusals=1, pauses_by_reason={}, resumes_by_reason={}, total=6)
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
    # Task 2.1 adds the successful event without a receipt to this intake count.
    history = collect_intake(tmp_path, tmp_path / "runs")
    row, = history["changes"]
    assert row["counts"]["build_revisions"] == row["counts"]["total"] == 1
    assert row["counts"]["admission_refusals"] == 0 and not row["build_commits"]
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

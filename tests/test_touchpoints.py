import json
from pathlib import Path

import pytest

from nightshift.touchpoints import collect_history


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

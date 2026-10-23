from datetime import date
from pathlib import Path

import pytest

from nightshift.touchpoints import collect_history, collect_report
from test_touchpoints import assert_offline_output_pair, put, snapshot_local_tree


@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/qualified-fallback-recovers-without-a-proposal")
@pytest.mark.parametrize("prefix", ["owner__sample", "sample"])
@pytest.mark.parametrize("storage", ["receipt", "job", "pending_job"])
@pytest.mark.parametrize("kind,command,outcome,counts", [
    ("admissions", "admit", "refused", {"admission_refusals": 1}),
    ("revisions", "propose", "needs_input", {"spec_revisions": 1, "drafter_questions": 1}),
    ("revisions", "revise", "refused", {"build_revisions": 1}),
])
def test_owner_qualified_touchpoints_recover_without_proposal(
        tmp_path, prefix, storage, kind, command, outcome, counts):
    change = f"{prefix}-issue-7-add-a-gate"
    result = dict(outcome=outcome)
    if storage == "receipt":
        path = put(tmp_path, f"{kind}/owner__sample/28.json",
                   dict(change=change, command=command, result=result))
    else:
        args = ["revise", change] if command == "revise" else [command, "manifest", "--change", change]
        path = put(tmp_path, f"{kind}/owner__sample/28.jobs/j/request.json", dict(args=args))
        if storage == "job":
            put(path.parent, "result.json", dict(result=result))
    events = put(tmp_path, f"runs/{change}/events.jsonl", dict(event="human_accept", at=1))
    before = snapshot_local_tree(tmp_path)
    history = collect_history(tmp_path, tmp_path / "runs")
    assert history["errors"] == [] and not (tmp_path / "proposals").exists()
    row, = history["changes"]
    assert (row["repo"], row["issue"], row["change"]) == ("owner/sample", 7, change)
    assert row["events_path"] == events
    record, = row["records"]
    assert (record["number"], record["path"]) == (28, path)
    assert record["retained"] == (storage != "receipt")
    report = collect_report(tmp_path, tmp_path / "runs")
    assert report["errors"] == []
    row, = report["changes"]
    assert row["drafted_at"] is row["drafted_at_source"] is None
    assert any("Missing proposal" in warning for warning in row["warnings"])
    assert any("Draft date unknown" in warning for warning in row["warnings"])
    for field, count in counts.items():
        assert row["counts"][field] == (0 if storage == "pending_job" else count)
    assert row["counts"]["human_accepts"] == 1
    assert row["counts"]["total"] == 1 + (0 if storage == "pending_job" else sum(counts.values()))
    filtered = collect_report(tmp_path, tmp_path / "runs", since=date(2026, 1, 1))
    assert filtered["changes"] == [] and filtered["totals"]["total"] == 0
    assert filtered["errors"] == [dict(path=str(path), line=None, message=(
        f"ValueError: Cannot apply --since 2026-01-01 to {change}: draft date unknown; change excluded"))]
    assert snapshot_local_tree(tmp_path) == before


@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/both-conventions-retain-isolated-attribution")
def test_both_conventions_remain_isolated_offline(tmp_path):
    state, runs = tmp_path / ".nightshift", tmp_path / ".specloop/runs"
    legacy = "x-issue-8-add-a-gate"
    for owner in ("a", "b"):
        change = f"{owner}__x-issue-7-add-a-gate"
        put(state, f"admissions/{owner}__x/28.json",
            dict(change=change, result=dict(outcome="refused")))
        put(state, f"revisions/{owner}__x/29.json",
            dict(change=legacy, command="propose", result=dict(outcome="needs_input")))
        put(runs, f"{change}/events.jsonl", dict(event="human_accept", at=1))
    shared = put(runs, f"{legacy}/events.jsonl", dict(event="human_accept", at=1))
    # Reporting must not load enrollment or attempt to validate these manifests.
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("invalid = [")
    missing = tmp_path / "missing.toml"
    config = tmp_path / "config.toml"
    config.write_text("".join(
        f'[[repos]]\nname = "{owner}/x"\nengine = "specloop"\nmanifest = "{manifest}"\n'
        for owner, manifest in (("a", invalid), ("b", missing))))
    before = snapshot_local_tree(tmp_path)
    history = collect_history(state, runs)
    assert [(r["repo"], r["issue"]) for r in history["changes"]] == [
        ("a/x", 7), ("a/x", 8), ("b/x", 7), ("b/x", 8)]
    for row in history["changes"]:
        assert row["events_path"] == (None if row["issue"] == 8 else runs / row["change"] / "events.jsonl")
    # The existing helper audits network/process/write attempts and verifies the
    # complete JSON/table schema, deterministic output and unchanged file bytes.
    report = assert_offline_output_pair(tmp_path, exit_code=1, denied=(shared, config, invalid, missing))
    assert report["errors"] == [dict(path=str(shared), line=None, message=(
        "ValueError: ambiguous run name belongs to multiple rows; events not read"))]
    assert report["totals"]["total"] == 8
    for row in report["changes"]:
        assert row["counts"]["total"] == 2
        assert row["counts"]["human_accepts"] == (1 if row["issue"] == 7 else 0)
        assert row["counts"]["admission_refusals"] == (1 if row["issue"] == 7 else 0)
        assert row["counts"]["spec_revisions"] == row["counts"]["drafter_questions"] == (row["issue"] == 8)
    assert snapshot_local_tree(tmp_path) == before


@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/fallback-requires-exact-repository-and-suffix")
@pytest.mark.parametrize("qualified", [False, True])
def test_fallback_requires_literal_repository_and_valid_suffix(tmp_path, monkeypatch, qualified):
    repo = "owner/widget.js_tools-x"
    prefix = "owner__widget.js_tools-x" if qualified else "widget.js_tools-x"
    valid = f"{prefix}-issue-7-add-a-gate"
    boundary = f"{prefix}-issue-8-" + "x" * 40
    for pr, change in ((28, valid), (29, boundary)):
        put(tmp_path, f"admissions/owner__widget.js_tools-x/{pr}.json", dict(repo=repo, change=change))
        put(tmp_path, f"runs/{change}/events.jsonl", dict(event="human_accept", at=1))
    invalid = [
        ("other__widget.js_tools-x-issue-7-title", {}, "no front-door"),
        (f"{prefix.replace('.', 'X')}-issue-7-title", {}, "no front-door"),
        (f"{prefix.upper()}-issue-7-title", {}, "no front-door"),
        *[(f"{prefix}-issue-{suffix}", {}, "no front-door") for suffix in (
            "0-title", "07-title", "7-", "7-Title", "7-two--words", "7-title-",
            "7-under_score", "7-" + "x" * 41)],
        (f"../{valid}", {}, "unsafe change"),
        (valid, {"issue": 9}, "issue conflicts with joined change identity"),
        (valid, {"repo": "other/widget.js_tools-x"}, "repository conflicts"),
        (valid, {"repo": "owner/widget/js_tools-x"}, "invalid repository"),
    ]
    expected = {}
    for pr, (change, extra, diagnostic) in enumerate(invalid, 30):
        path = put(tmp_path, f"admissions/owner__widget.js_tools-x/{pr}.json", dict(change=change, **extra))
        expected[str(path)] = diagnostic
    ambiguous = put(tmp_path, "admissions/owner__widget__extra/80.json",
                    dict(change="owner__widget__extra-issue-7-title"))
    expected[str(ambiguous)] = "ambiguous repository directory"
    original = Path.open
    opened = []

    def guarded(path, *args, **kwargs):
        if path.name == "events.jsonl":
            assert path.parent.name in (valid, boundary), "invalid identity must never read events"
            opened.append(path.parent.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    report = collect_report(tmp_path, tmp_path / "runs")
    assert [(r["repo"], r["issue"], r["change"]) for r in report["changes"]] == [
        (repo, 7, valid), (repo, 8, boundary)]
    assert sorted(opened) == sorted([valid, boundary])
    assert len(report["errors"]) == len(expected)
    assert {error["path"] for error in report["errors"]} == set(expected)
    assert all(expected[error["path"]] in error["message"] for error in report["errors"])
    assert report["totals"]["human_accepts"] == 2


@pytest.mark.scenario("touchpoint-report/producer-naming-compatibility/fallback-requires-exact-repository-and-suffix")
@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_proposal_identity_precedes_name_inference(tmp_path, retained, ambiguous):
    change = "owner__sample-issue-7-add-a-gate"
    for issue in ([9, 10] if ambiguous else [9]):
        if retained:
            put(tmp_path, f"proposals/owner__sample/{issue}.jobs/j/request.json",
                dict(args=["propose", "manifest", "--change", change]))
        else:
            put(tmp_path, f"proposals/owner__sample/{issue}.json", dict(change=change))
    receipt = put(tmp_path, "admissions/owner__sample/28.json", dict(change=change, issue=9))
    history = collect_history(tmp_path, tmp_path / "runs")
    assert [r["issue"] for r in history["changes"]] == ([9, 10] if ambiguous else [9])
    if ambiguous:
        assert len(history["errors"]) == 2
        assert any(e["path"] == str(receipt) and "ambiguous proposal identity" in e["message"]
                   for e in history["errors"])
        assert all(r["events_path"] is None for r in history["changes"])
    else:
        assert history["errors"] == []
        assert len(history["changes"][0]["records"]) == 2
        assert history["changes"][0]["warnings"] == []

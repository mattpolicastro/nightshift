import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import timings, touchpoints
from specloop import propose, state
from test_state_roots import tree_snapshot


def history(root, change):
    directory = root / "runs" / change
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps(dict(
        change=change, status="paused", pause=dict(reason="infra", resume_at=1))))
    (directory / "events.jsonl").write_text(
        '{"event":"implement","task":"1.1","at":10}\n'
        '{"event":"implemented","task":"1.1","at":30,"profile":"p"}\n'
        '{"event":"pause","at":40,"reason":"requested"}\n'
        '{"event":"resumed","at":60}\n')
    return directory


@pytest.mark.scenario("state-migration/all-state-consumers-agree/reports-and-deploy-see-both-roots")
def test_reports_and_proposal_context_share_mixed_roots(tmp_path, monkeypatch):
    canonical, legacy = state.state_root(), state.legacy_root()
    linked = history(canonical, "a-linked")
    old = history(legacy, "b-legacy")
    other = history(legacy, "c-other-repository")
    (legacy / "runs" / linked.name).symlink_to(linked, target_is_directory=True)
    for number, (run, repo) in enumerate(((linked, "one/repo"), (old, "one/repo"), (other, "two/repo")), 1):
        receipt = canonical / "proposals" / repo.replace("/", "__") / f"{number}.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(dict(change=run.name, result=dict(outcome="needs_input"))))
    (canonical / "pause").write_text("held")
    before = tree_snapshot(canonical, legacy)
    report = timings.collect_report()
    assert [row["change"] for row in report["changes"]] == [linked.name, old.name, other.name]
    assert report["phases"]["implement"] == dict(count=3, total_seconds=60, median_seconds=20)
    assert report["profiles"][0]["implementation_attempts"] == 3
    assert report["errors"] == []
    report = touchpoints.collect_report()
    assert [row["repo"] for row in report["changes"]] == ["one/repo", "one/repo", "two/repo"]
    assert report["totals"]["changes"] == report["totals"]["human_pauses"] == 3
    assert report["totals"]["manual_resumes"] == 3 and report["totals"]["total"] == 9
    assert report["errors"] == []
    # Explicit roots still see only their own history.
    assert len(timings.collect_report(canonical / "runs")["changes"]) == 1
    explicit = touchpoints.collect_report(canonical, canonical / "runs")
    assert explicit["totals"]["human_pauses"] == 1
    assert explicit["errors"] == []  # Unadmitted drafts may have no event log.
    source = tmp_path / "source"
    source.mkdir()
    (source / "shared.py").touch()
    monkeypatch.setattr(propose.manifest, "load", lambda path: SimpleNamespace(
        repo=source if path.parent != other else tmp_path / "other", branch="main", change=path.parent.name))
    reads = []

    def tasks(repo, *args):
        reads.append(repo.parent.name)
        return b"- [ ] 1.1 Update `shared.py`\n"

    monkeypatch.setattr(propose, "git", tasks)
    assert propose.in_flight(source, "new") == {"shared.py": [linked.name, old.name]}
    assert reads == [linked.name, old.name]
    (tmp_path / "other").mkdir()
    (tmp_path / "other/shared.py").touch()
    assert propose.in_flight(tmp_path / "other", "new") == {"shared.py": [other.name]}
    assert tree_snapshot(canonical, legacy) == before


@pytest.mark.scenario("state-migration/all-state-consumers-agree/reports-and-deploy-see-both-roots")
def test_conflicting_run_reports_error_without_hiding_healthy_history():
    canonical, legacy = state.state_root(), state.legacy_root()
    history(canonical, "conflict")
    history(legacy, "conflict")
    history(legacy, "healthy")
    receipts = canonical / "proposals/o__r"
    receipts.mkdir(parents=True)
    for number, change in enumerate(("conflict", "healthy"), 1):
        (receipts / f"{number}.json").write_text(json.dumps(dict(change=change)))
    before = tree_snapshot(canonical, legacy)
    timing = timings.collect_report()
    assert [row["change"] for row in timing["changes"]] == ["healthy"]
    assert len(timing["errors"]) == 1 and "conflicting run directories" in timing["errors"][0]
    touchpoint = touchpoints.collect_report()
    assert touchpoint["totals"]["human_pauses"] == 1
    assert len(touchpoint["errors"]) == 1
    assert "conflicting run directories" in touchpoint["errors"][0]["message"]
    assert timings.collect_report(canonical / "runs")["errors"] == []
    explicit = touchpoints.collect_report(canonical, canonical / "runs")
    assert explicit["errors"] == [] and explicit["totals"]["human_pauses"] == 1
    assert tree_snapshot(canonical, legacy) == before


@pytest.mark.scenario("state-migration/all-state-consumers-agree/reports-and-deploy-see-both-roots")
@pytest.mark.scenario("build-timing-report/read-only-collection/readable-history-survives-bad-input")
@pytest.mark.parametrize("file_root", ["canonical", "legacy"])
def test_timing_file_conflict_retains_diagnostic(file_root, capsys):
    canonical, legacy = state.state_root(), state.legacy_root()
    blocked_root, readable_root = (canonical, legacy) if file_root == "canonical" else (legacy, canonical)
    readable = history(readable_root, "conflict")
    readable.joinpath("state.json").unlink()  # Event-only history must also survive discovery.
    history(blocked_root, "healthy")
    blocked = blocked_root / "runs/conflict"
    blocked.write_text("not a run directory")
    before = tree_snapshot(canonical, legacy)

    assert timings.main(["--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert [row["change"] for row in report["changes"]] == ["healthy"]
    assert report["phases"]["implement"] == dict(count=1, total_seconds=20, median_seconds=20)
    assert len(report["errors"]) == 1
    diagnostic = report["errors"][0]
    assert "conflicting run directories" in diagnostic
    assert str(blocked) in diagnostic and str(readable) in diagnostic
    assert captured.err == diagnostic + "\n"
    explicit = timings.collect_report(readable_root / "runs")
    assert [row["change"] for row in explicit["changes"]] == ["conflict"]
    assert explicit["errors"] == []
    assert timings.collect_report(change="healthy")["errors"] == []
    assert tree_snapshot(canonical, legacy) == before


@pytest.mark.scenario("build-timing-report/read-only-collection/readable-history-survives-bad-input")
def test_default_timing_read_isolates_bad_history(monkeypatch, capsys):
    good = history(state.legacy_root(), "healthy") / "events.jsonl"
    good.write_text(good.read_text().replace('\n', '\nBAD\n', 1))
    # Events remain reportable even if state.json is no longer available.
    good.with_name("state.json").unlink()
    bad = history(state.state_root(), "unreadable") / "events.jsonl"
    before = tree_snapshot(state.state_root(), state.legacy_root())
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == bad:
            raise PermissionError("fixture denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert timings.main(["--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == f"{bad}: fixture denied\n"
    report = json.loads(captured.out)
    assert [row["change"] for row in report["changes"]] == ["healthy"]
    assert report["phases"]["implement"] == dict(count=1, total_seconds=20, median_seconds=20)
    assert tree_snapshot(state.state_root(), state.legacy_root()) == before


@pytest.mark.scenario("build-timing-report/read-only-collection/empty-history")
@pytest.mark.parametrize("existing", [False, True])
def test_default_timing_empty_history(tmp_path, monkeypatch, capsys, existing):
    roots = (tmp_path / "canonical", tmp_path / "legacy")
    for variable, root in zip(("NIGHTSHIFT_STATE_ROOT", "NIGHTSHIFT_LEGACY_STATE_ROOT"), roots):
        monkeypatch.setenv(variable, str(root))
        if existing:
            (root / "runs").mkdir(parents=True)
    before = tree_snapshot(*roots)
    assert timings.main(["--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["changes"] == report["profiles"] == report["errors"] == []
    assert len(report["phases"]) == 6
    assert all(row == dict(count=0, total_seconds=0, median_seconds=None)
               for row in report["phases"].values())
    assert tree_snapshot(*roots) == before

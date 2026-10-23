import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from nightshift.timings import collect_report, main, render_table


def event(kind, at, task="1.1", **extra):
    return dict(event=kind, at=at, task=task, **extra)


@pytest.fixture
def logs(tmp_path):
    def write(name, *events):
        path = tmp_path / name / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(e if isinstance(e, str) else json.dumps(e) for e in events) + "\n")
        return path
    return write


@pytest.mark.scenario("build-timing-report/phase-durations/six-phase-summaries")
@pytest.mark.scenario("build-timing-report/phase-durations/retries-and-separate-tasks")
def test_phase_durations_from_event_pairs(tmp_path, logs):
    logs("simple", *(event(kind, at) for kind, at in (
        ("admitted", 0), ("implement", 10), ("implemented", 30), ("sealed", 35),
        ("verified", 45), ("review", 50), ("reviewed", 55), ("landed", 60),
        ("evidence", 90), ("pause", 100), ("resumed", 160), ("implement", 180))))
    phases = collect_report(tmp_path)["phases"]
    assert phases == {name: dict(count=count, total_seconds=total, median_seconds=middle)
                      for name, count, total, middle in (
                          ("implement", 1, 20, 20), ("verify", 1, 10, 10), ("review", 1, 5, 5),
                          ("evidence_gate", 1, 30, 30), ("paused_wait", 1, 60, 60),
                          ("slot_wait", 2, 30, 15))}
    logs("retries", event("implement", 0), event("implement", 5, "other"),
         event("implement", 10), "bad json", event("implemented", 20),
         event("implemented", 95, "other"), event("implemented", 100),
         event("implement", 120), event("implemented", 110), event("implemented", 130),
         event("implemented", 140, "unmatched"), event("implement", 150, ""),
         event("implemented", 160, ""), event("implement", 170, None),
         event("implemented", 180, None), event("landed", 190),
         event("landed", 200, "other"), event("evidence", 230), event("evidence", 240),
         event("pause", 250), event("auto_resumed", 260))
    phases = collect_report(tmp_path)["phases"]
    assert phases["implement"] == dict(count=3, total_seconds=120, median_seconds=20)
    assert phases["evidence_gate"] == dict(count=2, total_seconds=60, median_seconds=30)
    assert phases["paused_wait"] == dict(count=1, total_seconds=60, median_seconds=60)
    # A completed retry adds another sample; pending starts never cross runs.
    logs("retry", event("implemented", 190), event("implement", 190), event("implement", 200),
         event("implemented", 210), event("implement", 220), event("implemented", 240))
    assert collect_report(tmp_path)["phases"]["implement"] == dict(
        count=5, total_seconds=150, median_seconds=20)


@pytest.mark.scenario("build-timing-report/phase-durations/retries-and-separate-tasks")
@pytest.mark.parametrize("phase,start,end", [
    ("verify", "sealed", "verified"),
    ("review", "review", "reviewed"),
])
def test_task_phase_pairs_remain_separate(tmp_path, logs, phase, start, end):
    logs("interleaved", event(start, 0, "1.1"), event(start, 5, "1.2"),
         event(end, 20, "1.1"), event(end, 95, "1.2"))

    assert collect_report(tmp_path)["phases"][phase] == dict(
        count=2, total_seconds=110, median_seconds=55)


@pytest.mark.scenario("build-timing-report/phase-durations/six-phase-summaries")
@pytest.mark.parametrize("pause_task,resume_task", [({}, {}), ({"task": "1.1"}, {"task": "1.2"})])
def test_paused_wait_pairs_by_run(tmp_path, logs, pause_task, resume_task):
    # Run-level waits pair even without task keys or when the task changes.
    logs("paused", dict(event="pause", at=100, **pause_task),
         dict(event="resumed", at=160, **resume_task))

    assert collect_report(tmp_path)["phases"]["paused_wait"] == dict(
        count=1, total_seconds=60, median_seconds=60)


@pytest.mark.scenario("build-timing-report/change-summaries/build-and-wait-hours")
def test_per_change_build_and_wait_hours(tmp_path, logs):
    logs("complete", event("admitted", 0), event("landed", 1), event("landed", 2, "2.1"),
         event("landed", 3), *(event("attempt_failed", i) for i in (4, 5, 6)),
         event("published", 7200), event("published", 10800), event("finished", 18000),
         event("admitted", 19000), event("finished", 20000))
    logs("no-publication", event("admitted", 0), event("finished", 18000))
    logs("no-finish", event("admitted", 0), event("published", 7200))
    logs("no-admission", event("finished", 0), event("published", 7200), event("finished", 18000))
    logs("inverted", event("admitted", 100), event("published", 90), event("finished", 80))
    rows = collect_report(tmp_path)["changes"]
    assert [r["change"] for r in rows] == sorted(r["change"] for r in rows)
    by_name = {r["change"]: r for r in rows}
    assert by_name["complete"] == dict(change="complete", tasks_landed=2, failed_attempts=3,
                                       build_hours=2, publication_wait_hours=3)
    for name, build, wait in (("no-publication", None, None), ("no-finish", 2, None),
                              ("no-admission", None, 3), ("inverted", None, None)):
        assert (by_name[name]["build_hours"], by_name[name]["publication_wait_hours"]) == (build, wait)


@pytest.mark.scenario("build-timing-report/profile-summaries/review-failure-rates")
def test_review_failure_rate_by_profile(tmp_path, logs):
    logs("profiles", *(event("reviewed", i, profile=profile, disposition=disposition, output=output)
                       for i, (profile, disposition, output) in enumerate([
                           ("alpha", "ok", {"verdict": "FAIL"}), ("alpha", "infra", {"verdict": "FAIL"}),
                           ("alpha", "ok", {"verdict": "PASS"}), ("alpha", "ok", None),
                           ("beta", "ok", {"verdict": "FAIL"}), ("beta", "ok", {"verdict": "FAIL"})])),
         event("implemented", 10, profile="gamma"), event("implemented", 11, profile="gamma"),
         event("reviewed", 12), event("implemented", 13, profile=""))
    assert collect_report(tmp_path)["profiles"] == [
        dict(profile=name, implementation_attempts=implement, review_attempts=review,
             review_failures=failures, review_failure_rate=rate)
        for name, implement, review, failures, rate in (
            ("alpha", 0, 4, 1, .25), ("beta", 0, 2, 2, 1),
            ("gamma", 2, 0, 0, None), ("unknown", 1, 1, 0, 0))]


@pytest.mark.scenario("build-timing-report/read-only-collection/readable-history-survives-bad-input")
def test_unreadable_run_is_reported_not_fatal(tmp_path, logs, monkeypatch, capsys):
    good = logs("healthy", event("implement", 10), "{broken", "[]", "null",
                *({"event": "implemented", "at": at} for at in (True, None, "20", float("nan"), float("inf"))),
                {"event": 1, "at": 20}, {"at": 20}, event("future-event", 15), event("implemented", 30))
    bad = logs("unreadable", event("implement", 0), event("implemented", 100))
    before = {p: p.read_bytes() for p in (good, bad)}
    read = Path.read_text

    def guarded(path, *args, **kwargs):
        if path == bad:
            raise PermissionError("fixture access denied")
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    report = collect_report(tmp_path)
    assert report["errors"] == [f"{bad}: fixture access denied"]
    assert [r["change"] for r in report["changes"]] == ["healthy"]
    assert report["phases"]["implement"] == dict(count=1, total_seconds=20, median_seconds=20)
    assert report["profiles"][0]["implementation_attempts"] == 1
    for args in ([], ["--json"]):
        assert main(args, runs_root=tmp_path) == 0
        captured = capsys.readouterr()
        assert captured.err == f"{bad}: fixture access denied\n"
        assert (json.loads(captured.out) == report if args else captured.out == render_table(report) + "\n")
    assert collect_report(tmp_path, change="healthy")["errors"] == []
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.scenario("build-timing-report/read-only-collection/empty-history")
def test_empty_history(tmp_path, capsys):
    absent = tmp_path / "absent"
    for root in (absent, tmp_path):
        report = collect_report(root)
        assert report["changes"] == report["profiles"] == report["errors"] == []
        assert set(report["phases"]) == {"implement", "verify", "review", "evidence_gate", "slot_wait", "paused_wait"}
        assert all(row == dict(count=0, total_seconds=0, median_seconds=None)
                   for row in report["phases"].values())
        for args in ([], ["--json"]):
            assert main(args, runs_root=root) == 0
            captured = capsys.readouterr()
            assert captured.err == ""
            assert (json.loads(captured.out) == report if args else captured.out == render_table(report) + "\n")
    assert not absent.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.scenario("build-timing-report/filtered-report-output/date-and-change-filters")
def test_since_and_change_filters(tmp_path, logs, capsys):
    since = date(2026, 10, 23)
    cutoff = datetime(2026, 10, 23, tzinfo=timezone.utc).timestamp()
    for name, admitted in (("before", cutoff - 1), ("boundary", cutoff), ("after", cutoff + 1)):
        logs(name, event("implement", cutoff - 10), event("admitted", admitted),
             event("implemented", cutoff + 10, profile=name), event("landed", cutoff + 20),
             event("attempt_failed", cutoff + 25), event("published", cutoff + 3600),
             event("resumed", cutoff + 7200), event("admitted", cutoff + 8000))
    logs("no-admission", event("implemented", cutoff + 10, profile="missing"))
    assert len(collect_report(tmp_path)["changes"]) == 4
    report = collect_report(tmp_path, since=since)
    assert [row["change"] for row in report["changes"]] == ["after", "boundary"]
    assert [row["profile"] for row in report["profiles"]] == ["after", "boundary"]
    assert report["phases"]["implement"] == dict(count=2, total_seconds=40, median_seconds=20)
    selected = collect_report(tmp_path, since=since, change="boundary")
    assert selected == collect_report(tmp_path, change="boundary")
    assert selected["changes"][0] == dict(change="boundary", tasks_landed=1, failed_attempts=1,
                                         build_hours=1, publication_wait_hours=None)
    assert main(["--since", "2026-10-23", "--change", "boundary", "--json"], runs_root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == selected
    empty = collect_report(tmp_path / "absent")
    for name in ("before", "no-admission", "bound", "unmatched"):
        assert collect_report(tmp_path, since=since, change=name) == empty
    for invalid in ("2026-02-30", "20261023", "2026-W43-5", "not-a-date"):
        with pytest.raises(SystemExit) as exc:
            main(["--since", invalid], runs_root=tmp_path)
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == "" and "YYYY-MM-DD" in captured.err


@pytest.mark.scenario("build-timing-report/filtered-report-output/text-and-json-agree")
def test_json_output_shape(tmp_path, logs, capsys):
    logs("example", event("admitted", 0), event("implement", 1),
         event("implemented", 21.5, profile="builder"), event("landed", 30),
         event("reviewed", 40, profile="critic", disposition="ok", output={"verdict": "FAIL"}),
         event("reviewed", 50, profile="critic"), event("published", 3600))
    bad = tmp_path / "unreadable" / "events.jsonl"
    bad.mkdir(parents=True)
    report = collect_report(tmp_path)
    assert main(["--json"], runs_root=tmp_path) == 0
    output = capsys.readouterr()
    parsed = json.loads(output.out)
    assert parsed == report and set(parsed) == {"phases", "changes", "profiles", "errors"}
    assert parsed["phases"]["implement"] == dict(count=1, total_seconds=20.5, median_seconds=20.5)
    assert parsed["changes"] == [dict(change="example", tasks_landed=1, failed_attempts=0,
                                     build_hours=1, publication_wait_hours=None)]
    assert parsed["profiles"] == [dict(profile=name, implementation_attempts=impl, review_attempts=rev,
                                      review_failures=fail, review_failure_rate=rate)
                                  for name, impl, rev, fail, rate in (("builder", 1, 0, 0, None),
                                                                    ("critic", 0, 2, 1, .5))]
    assert len(parsed["errors"]) == 1 and str(bad) in parsed["errors"][0]
    assert output.err == parsed["errors"][0] + "\n"
    assert main([], runs_root=tmp_path) == 0
    text = capsys.readouterr()
    assert text.err == output.err and text.out == render_table(parsed) + "\n"
    table, note = text.out.split("\n\n")
    lines = [line.split() for line in table.splitlines()]
    assert lines.pop(0) == ["Kind", "Name", "Metric", "Value", "Unit"]
    expected = []
    for kind, rows in (("phase", parsed["phases"].items()),
                       ("change", ((r["change"], r) for r in parsed["changes"])),
                       ("profile", ((r["profile"], r) for r in parsed["profiles"]))):
        for name, row in rows:
            for metric, value in row.items():
                if metric in {"change", "profile"}:
                    continue
                unit = {"total_seconds": "seconds", "median_seconds": "seconds", "build_hours": "hours",
                        "publication_wait_hours": "hours", "review_failure_rate": "%"}.get(metric, "count")
                expected.append([kind, name, metric, "-" if value is None else
                                 str(value * 100 if unit == "%" else value), unit])
    assert lines == expected
    assert "Local-history estimates" in note

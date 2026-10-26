"""Pause age is read from history without changing the status or stored state."""

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift.cli import main

from specloop import cli, state, tasks
from specloop.state import Run
from test_specloop_status_json import (
    TASKS, admitted, assert_status_error, home, status_document,
)


@pytest.mark.scenario("specloop-interface/pause-event-timestamp/latest-pause-event-reported")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("since", [0, 1800000000.125])
def test_status_reports_pause_since(admitted, capsys, named, since):
    run, _ = admitted("paused")
    run.pause("requested", detail="Review this change")
    run.event("pause", at=1900000000, reason="infra")
    run.event("resumed", at=1900000001)
    run.event("pause", at=since, reason="requested")
    # The latest pause is outside the five displayed context events, and its
    # time is smaller than both an earlier pause and the later non-pause events.
    for offset in range(6):
        run.event("published", at=1900000002 + offset)

    entry = status_document(capsys, *(["paused"] if named else []))["runs"][0]
    assert entry["pause"] == {
        "reason": "requested", "task": "", "detail": "Review this change",
        "auto_resumes_remaining": 0, "since": since,
    }
    assert "error" not in entry
    assert len(entry["context"]["recent"]) == 5
    assert all(event["text"] == "published to its pull request"
               for event in entry["context"]["recent"])


@pytest.mark.scenario("specloop-interface/pause-event-timestamp/unavailable-pause-time-is-null")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("error_row", [False, True])
@pytest.mark.parametrize("log", ["missing", "empty", "unreadable", "undecodable", "no-pause"])
def test_pause_since_is_null_without_events(admitted, monkeypatch, capsys, named, error_row, log):
    run, _ = admitted("paused")
    run.pause("requested", resume_at=1900000000)
    path = run.dir / "events.jsonl"
    if log == "missing":
        path.unlink()
    elif log == "empty":
        path.write_text("")
    elif log == "unreadable":
        read_text = Path.read_text

        def denied(candidate, *args, **kwargs):
            if candidate == path:
                raise PermissionError("event log read denied")
            return read_text(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
    elif log == "undecodable":
        # A valid pause elsewhere in an undecodable log is still unavailable.
        with path.open("ab") as stream:
            stream.write(b"\xff\n")
    else:
        path.write_text('\n{"event":"resumed","at":1800000000}\n'
                        '{"at":1700000000}\n{"event":"Pause","at":1600000000}\n')
    if error_row:
        (run.dir / "state.json").write_text("{")
    before = path.read_bytes() if path.exists() else None

    entry = status_document(capsys, *(["paused"] if named else []))["runs"][0]
    if error_row:
        assert_status_error(entry, "paused", "JSONDecodeError", since=None)
    else:
        assert entry["pause"] == {
            "reason": "requested", "task": "", "detail": "",
            "auto_resumes_remaining": 0, "since": None,
        }
        assert entry["status"] == "paused"
        assert entry["tasks"] == {"done": 1, "total": 3}
        assert entry["action"]["actions"] == ["resume", "abort"]
        assert "error" not in entry
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.scenario("specloop-interface/pause-event-timestamp/unavailable-pause-time-is-null")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("timestamp", [
    {}, {"at": None}, {"at": True}, {"at": False}, {"at": "1800000000"},
    {"at": []}, {"at": {}}, {"at": float("nan")},
    {"at": float("inf")}, {"at": float("-inf")},
], ids=["missing", "null", "true", "false", "string", "array", "object", "nan", "inf", "-inf"])
def test_latest_pause_with_invalid_time_does_not_borrow_older_time(admitted, capsys, named, timestamp):
    run, _ = admitted("paused")
    run.pause("requested")
    with (run.dir / "events.jsonl").open("a") as stream:
        stream.write(json.dumps({"event": "pause", **timestamp}) + "\n")
        stream.write('{"event":"published","at":1900000000}\n')

    entry = status_document(capsys, *(["paused"] if named else []))["runs"][0]
    assert entry["pause"]["since"] is None
    assert "error" not in entry
    assert entry["action"]["actions"] == ["resume", "abort"]


@pytest.mark.scenario("specloop-interface/pause-event-timestamp/damaged-lines-do-not-hide-a-pause")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("damage", [
    "not json\n", '{"event":"pause","at":',
    'null\n[]\n42\ntrue\n"pause"\n',
    '{"event":"pause",broken}\nnull\n{"event":',
], ids=["malformed", "partial", "non-objects", "mixed"])
def test_damaged_lines_do_not_hide_pause(admitted, capsys, named, damage):
    run, _ = admitted("paused")
    run.pause("requested")
    run.event("pause", at=1800000000.75)
    path = run.dir / "events.jsonl"
    with path.open("a") as stream:
        stream.write(damage)
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    entry = status_document(capsys, *(["paused"] if named else []))["runs"][0]
    assert entry["pause"]["since"] == 1800000000.75
    assert "error" not in entry
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.scenario("specloop-interface/pause-event-timestamp/damaged-lines-do-not-hide-a-pause")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("error_row", [False, True])
def test_deeply_nested_record_does_not_hide_pause(admitted, monkeypatch, capsys, named, error_row):
    run, _ = admitted("paused")
    run.pause("requested")
    run.event("pause", at=1800000000.75)
    path = run.dir / "events.jsonl"
    # Valid JSON can exceed the decoder's recursion limit, independently of
    # malformed syntax. It must not break normal or synthetic pause lookup.
    nested_record = "[" * 2000 + "0" + "]" * 2000
    with path.open("a") as stream:
        stream.write(nested_record + "\n")
    if error_row:
        (run.dir / "state.json").write_text("{")
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    # Newer Python C decoders can read this depth. Use the Python scanner for
    # this record so the Python 3.12 recursion failure is exercised everywhere.
    decoder = json.JSONDecoder()
    decoder.scan_once = json.scanner.py_make_scanner(decoder)
    loads = json.loads

    def decode_record(source, *args, **kwargs):
        if source == nested_record:
            return decoder.decode(source)
        return loads(source, *args, **kwargs)

    monkeypatch.setattr(json, "loads", decode_record)

    entry = status_document(capsys, *(["paused"] if named else []))["runs"][0]
    if error_row:
        assert_status_error(entry, "paused", "JSONDecodeError", since=1800000000.75)
    else:
        assert entry["pause"]["since"] == 1800000000.75
        assert "error" not in entry
        assert entry["action"]["actions"] == ["resume", "abort"]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.scenario("specloop-interface/pause-event-timestamp/timestamp-preserves-status-compatibility")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("since", [0, 1800000000.25, None])
def test_pause_timestamp_preserves_status_and_is_read_only(admitted, tmp_path, monkeypatch, capsys, named, since):
    runs = {}
    key = tasks.parse(TASKS)[1].key
    # Create out of order; include terminal and running null pauses.
    for name in ("z-running", "m-broken", "a-paused", "done", "aborted"):
        run, _ = admitted(name)
        if name in ("a-paused", "m-broken"):
            run.pause("infra", key, "Worker unavailable", resume_at=1900000000)
        elif name in ("done", "aborted"):
            run.state.status = name
            run.save()
        runs[name] = run
    before_entries = {entry["change"]: entry for entry in status_document(capsys)["runs"]}
    broken = runs["m-broken"]
    (broken.dir / "state.json").write_text("{")
    broken.event("pause", at=1900000000)
    broken.event("pause", at=since)
    with (broken.dir / "events.jsonl").open("a") as stream:
        stream.write('{"event":')

    def snapshot():
        # All files and directories include state, events, refs and reflogs in
        # both source and bare repositories, detecting new files as well.
        return {
            path.relative_to(tmp_path): (path.stat().st_mtime_ns,
                                         path.read_bytes() if path.is_file() else None)
            for path in tmp_path.rglob("*")
        }

    before = snapshot()
    loaded = []
    loads = []
    load = Run._load

    def track_load(run):
        loads.append(run.dir.name)
        result = load(run)
        loaded.append((result, asdict(result)))
        return result

    def no_mutation(*args, **kwargs):
        pytest.fail("status must not mutate state or create an execution context")

    monkeypatch.setattr(Run, "_load", track_load)
    for method in ("save", "event", "pause", "lock"):
        monkeypatch.setattr(Run, method, no_mutation)
    monkeypatch.setattr(state.RunState, "task", no_mutation)
    monkeypatch.setattr(cli, "_ctx", no_mutation)

    for names in ([[name] for name in sorted(runs)] if named else [[]]):
        document = status_document(capsys, *names)
        assert [entry["change"] for entry in document["runs"]] == (names or sorted(runs))
        for entry in document["runs"]:
            name = entry["change"]
            if name == "m-broken":
                assert_status_error(entry, name, "JSONDecodeError", since=since)
            else:
                assert entry == before_entries[name]
                if name == "a-paused":
                    assert entry["pause"]["since"] == 1800000000
                    assert entry["pause"]["auto_resumes_remaining"] is None
                    assert entry["action"]["actions"] == ["resume", "abort"]
                else:
                    assert entry["pause"] is None
                    assert "action" not in entry
    assert loads == sorted(runs)  # No second attempt to load broken state.
    assert all(asdict(result) == original for result, original in loaded)
    assert snapshot() == before


@pytest.mark.scenario("specloop-interface/pause-event-timestamp/timestamp-preserves-status-compatibility")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("status", ["running", "done", "aborted"])
def test_retained_non_null_pauses_include_since(admitted, capsys, named, status):
    run, _ = admitted("retained")
    run.pause("requested")
    run.state.status = status
    run.save()

    entry = status_document(capsys, *(["retained"] if named else []))["runs"][0]
    assert entry["status"] == status
    assert entry["pause"]["since"] == 1800000000
    assert "action" not in entry


@pytest.mark.scenario("specloop-interface/pause-event-timestamp/timestamp-preserves-status-compatibility")
@pytest.mark.parametrize("named", [False, True])
def test_text_status_does_not_look_up_pause_since(admitted, monkeypatch, capsys, named):
    run, head = admitted("paused")
    run.pause("requested", detail="Review this change")
    manifest = cli._manifest(run)
    monkeypatch.setattr(cli, "_ctx", lambda run: SimpleNamespace(
        m=manifest, repo=run.dir / "repo.git", head=lambda: head,
    ))

    def no_lookup(*args):
        pytest.fail("text status must not look up pause.since")

    monkeypatch.setattr(cli, "_pause_since", no_lookup)
    assert main(["status", *(["paused"] if named else [])]) == 0
    output = capsys.readouterr()
    key = tasks.parse(TASKS)[1].key
    assert output.err == ""
    assert output.out == (
        "supervisor: not running\n"
        "paused: paused\n"
        "  paused: requested\n"
        "    Review this change\n"
        f"  tasks: 1/3 done on spec/paused @ {head[:12]}\n"
        f"  next: [{key}] 1.2 Stuck task\n"
        '  · pause {"reason": "requested", "task": "", "detail": "Review this change", "resume_at": 0}\n'
    )


@pytest.mark.scenario("specloop-interface/pause-event-timestamp/timestamp-preserves-status-compatibility")
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("error_row", [False, True])
@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(7)])
def test_pause_lookup_propagates_interrupts(admitted, monkeypatch, capsys, named, error_row, error):
    run, _ = admitted("paused")
    run.pause("requested")
    if error_row:
        (run.dir / "state.json").write_text("{")
    read_text = Path.read_text

    def interrupted(path, *args, **kwargs):
        if path == run.dir / "events.jsonl":
            raise error
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", interrupted)
    with pytest.raises(type(error)) as caught:
        main(["status", "--json", *(["paused"] if named else [])])
    assert caught.value is error
    output = capsys.readouterr()
    assert output.out == output.err == ""

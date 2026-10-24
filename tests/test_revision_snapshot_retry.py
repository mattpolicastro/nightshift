"""Saved build selections survive attempts; replacement selects a new identity."""

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from nightshift import specloop_job as jobs
from revision_result_fixtures import revision_success
from test_revision_feedback_consumption import read, receipt_path
from test_revision_feedback_recovery import feedback_streams, restart, tick
from test_revision_job_identity import stopped
from test_specloop_build_revision import setup as build_setup
from test_specloop_compaction import snapshot

pytestmark = pytest.mark.real_trust


@pytest.fixture
def attempts(monkeypatch, feedback_streams):
    launched = []
    monkeypatch.setattr(jobs, "_children", {})

    def launch(argv, **kwargs):
        assert feedback_streams and all(stream.closed for stream in feedback_streams)
        directory = Path(argv[-2])
        request = json.loads((directory / "request.json").read_text())
        source = Path(request["args"][3])
        assert source.is_absolute()
        assert json.loads(source.read_text())
        launched.append((directory, request, source))
        return SimpleNamespace(poll=lambda: None)

    return launched, launch


def key(saved):
    value = UUID(saved["round_id"])
    assert saved["revision_key_version"] == 1 and value.version == 4
    assert value.hex == saved["round_id"]
    return value.hex


@pytest.mark.scenario("pr-revision/stable-build-round-identity/ordinary-retry-keeps-the-snapshot")
@pytest.mark.parametrize("hold", ["live", "stopped"])
def test_cached_refusal_preserves_hold(build_setup, tmp_path, attempts, hold):
    launched, launch = attempts
    tick(build_setup, tmp_path, background=True, launcher=launch)
    path, directory = receipt_path(tmp_path), launched[0][0]
    jobs.save(path, {**read(tmp_path), "result": {"version": 1, "outcome": "refused"}})
    build_setup[1].inline[0]["body"] = "Changed feedback cannot bypass the hold"
    with (directory / "lock").open() as lock:
        if hold == "live":
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:
            stopped(directory)
        before = snapshot(path.parent)
        assert tick(build_setup, tmp_path, module=restart(), background=True, launcher=launch) == []
        assert snapshot(path.parent) == before
    assert len(launched) == 1 and not build_setup[1].comments


@pytest.mark.scenario("pr-revision/stable-build-round-identity/ordinary-retry-keeps-the-snapshot")
@pytest.mark.parametrize("error", [None, "command failed"])
@pytest.mark.parametrize("new_event", [False, True])
@pytest.mark.parametrize("expired", [False, True])
def test_detached_retry_uses_original_selection(
    build_setup, tmp_path, monkeypatch, attempts, error, new_event, expired,
):
    _, gh, commands = build_setup
    launched, launch = attempts
    path = receipt_path(tmp_path)
    jobs.save(path, {**read(tmp_path), "future": {"keep": [1, 2]}})
    tick(build_setup, tmp_path, background=True, launcher=launch)
    original = read(tmp_path)
    directory, request, source = launched[0]
    retained = source.read_bytes()
    if error:
        jobs.finalize(directory, {"error": error})
    monkeypatch.setattr(jobs.time, "time", lambda: request["deadline_at"] + (1 if expired else -1))
    with pytest.raises(RuntimeError, match=error or "exited without a result"):
        tick(build_setup, tmp_path, module=restart(), background=True, launcher=launch)
    detached = read(tmp_path)
    assert detached == {k: v for k, v in original.items() if k != "job"}
    history = snapshot(directory)
    gh.inline[0]["body"] = "Edited after the original attempt"
    gh.inline.append({**gh.inline[0], "id": 4, "body": "Later independent feedback"})
    if new_event:
        gh.label_event("agent:revise", "unlabeled")
        gh.label_event("agent:revise", "labeled")
    latest_event = gh.events[-1]["id"]
    jobs._children.clear()
    tick(build_setup, tmp_path, module=restart(), background=True, launcher=launch)
    retry = read(tmp_path)
    assert key(retry) == key(original)
    assert retry == {**detached, "job": launched[1][0].name, "round_job": launched[1][0].name}
    assert len(launched) == 2 and launched[1][0] != directory
    assert launched[1][1]["round_id"] == request["round_id"] == key(original)
    assert launched[1][2] != source and launched[1][2].read_bytes() == retained
    assert snapshot(directory) == history and source.read_bytes() == retained
    assert not path.with_suffix(".release.lock").exists()
    expected = revision_success(launched[1][1]["args"], commands.result)
    jobs.finalize(launched[1][0], {"result": expected})
    tick(build_setup, tmp_path, module=restart())
    completed = read(tmp_path)
    assert completed["result"] == expected and completed["delivered"]
    assert completed["consumed_feedback_ids"] == sorted(
        {*original["consumed_feedback_ids"], *original["feedback_ids"]})
    assert ("agent:revise" in gh.labels) == new_event
    assert len(gh.comments) == 1
    assert gh.comments[0]["body"].endswith(
        f"<!-- nightshift:revise:{original['change']}:{key(original)} -->")
    original_comment = gh.comments[0].copy()
    if new_event:
        tick(build_setup, tmp_path, module=restart())
        remaining = read(tmp_path)
        assert key(remaining) != key(original) and remaining["event"] == latest_event
        assert remaining["feedback_ids"] == ["inline:4"]
        assert remaining["future"] == original["future"]
        assert gh.comments[0] == original_comment and len(gh.comments) == 2
    tick(build_setup, tmp_path, module=restart())
    assert len(launched) == 2


@pytest.mark.scenario("pr-revision/stable-build-round-identity/prepared-snapshot-survives-launch-failure")
@pytest.mark.parametrize("background", [False, True])
def test_prepared_snapshot_survives_dispatch_and_retry_input_failure(
    build_setup, tmp_path, monkeypatch, attempts, background,
):
    _, gh, commands = build_setup
    launched, launch = attempts

    def fail(*args, **kwargs):
        if background:
            launch(*args, **kwargs)
        else:
            argv = args[0]
            assert json.loads(Path(argv[4]).read_text()) == read(tmp_path)["feedback"]
            assert argv[argv.index("--round-id") + 1] == key(read(tmp_path))
        raise OSError("dispatch failed")

    with pytest.raises(OSError if background else RuntimeError, match="dispatch failed"):
        tick(build_setup, tmp_path, background=background,
             **({"launcher": fail} if background else {"specloop_runner": fail}))
    saved = read(tmp_path)
    assert key(saved) and "job" not in saved and "result" not in saved
    assert ("round_job" in saved) == background
    history = snapshot(receipt_path(tmp_path).parent)
    gh.inline[0]["body"] = "Changed after preparation"
    module = restart()
    with monkeypatch.context() as patch:
        patch.setattr(module, "_build_revision_input",
                      lambda *a: (_ for _ in ()).throw(OSError("input failed")))
        with pytest.raises(OSError, match="input failed"):
            tick(build_setup, tmp_path, module=module)
    assert snapshot(receipt_path(tmp_path).parent) == history
    assert not gh.comments
    tick(build_setup, tmp_path, module=restart())
    completed = read(tmp_path)
    for field in ("round_id", "event", "feedback_ids", "feedback", "future"):
        if field in saved:
            assert completed[field] == saved[field]
    assert completed["result"]["round_id"] == key(saved) and completed["delivered"]
    assert len(gh.comments) == 1
    inputs = list(receipt_path(tmp_path).with_suffix(".jobs").glob("feedback-*.json"))
    assert len(inputs) == 2 and inputs[0].read_bytes() == inputs[1].read_bytes()


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/mixed-legacy-and-keyed-evidence-is-refused")
@pytest.mark.parametrize("partial", ["request", "receipt", "argv"])
@pytest.mark.parametrize("released", [False, True])
def test_partial_legacy_conversion_cannot_redispatch(build_setup, tmp_path, attempts, partial, released):
    launched, launch = attempts
    tick(build_setup, tmp_path, background=True, launcher=launch)
    directory, request, _ = launched[0]
    if released:
        stopped(directory)
        assert jobs.release_job(tmp_path, directory.name) == "released"
    else:
        with pytest.raises(RuntimeError, match="exited without a result"):
            tick(build_setup, tmp_path)
    saved = read(tmp_path)
    if partial == "request":
        request.pop("round_id")
    elif partial == "argv":
        index = request["args"].index("--round-id")
        del request["args"][index:index + 2]
    else:
        saved.pop("revision_key_version")
        saved.pop("round_job")
        saved["round_id"] = str(saved["event"])
    jobs.save(directory / "request.json", request)
    jobs.save(receipt_path(tmp_path), saved)
    before = snapshot(receipt_path(tmp_path).parent)
    with pytest.raises(ValueError, match="inconsistent"):
        tick(build_setup, tmp_path, module=restart(), background=True, launcher=launch)
    assert snapshot(receipt_path(tmp_path).parent) == before
    assert len(launched) == 1 and not build_setup[1].comments
    assert read(tmp_path)["consumed_feedback_ids"] == saved["consumed_feedback_ids"]


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/concurrent-provenance-stays-current")
@pytest.mark.parametrize("writer", ["snapshot", "successor", "release"])
def test_stale_retry_defers_to_current_receipt(build_setup, tmp_path, monkeypatch, attempts, writer):
    launched, launch = attempts
    tick(build_setup, tmp_path, background=True, launcher=launch)
    directory = launched[0][0]
    with pytest.raises(RuntimeError, match="exited without a result"):
        tick(build_setup, tmp_path)
    module = restart()
    boundary = jobs.revision_attempt
    current = None

    @contextmanager
    def interleave(path, receipt):
        nonlocal current
        current = dict(receipt)
        if writer == "snapshot":
            current.update(round_id="a" * 32, feedback=["successor feedback"])
            current.pop("round_job")
            jobs.save(path, current)
        else:
            with boundary(path, receipt):
                jobs.start(path, current, launched[0][1]["args"], launcher=launch)
            if writer == "release":
                stopped(launched[1][0])
                assert jobs.release_job(tmp_path, launched[1][0].name) == "released"
            current = read(tmp_path)
        with boundary(path, receipt) as state:
            yield state

    monkeypatch.setattr(jobs, "revision_attempt", interleave)
    history = snapshot(directory)
    pending = []
    assert tick(build_setup, tmp_path, module=module, background=True, launcher=launch,
                on_pending=lambda *args: pending.append(args)) == []
    assert pending and read(tmp_path) == current
    assert len(launched) == (1 if writer == "snapshot" else 2) and snapshot(directory) == history
    assert receipt_path(tmp_path).with_suffix(".release.lock").exists() == (writer == "release")


@pytest.mark.scenario("pr-revision/stable-build-round-identity/legacy-rounds-keep-existing-semantics")
@pytest.mark.parametrize("damage", [
    {"revision_key_version": True}, {"revision_key_version": 2},
    {"round_id": ""}, {"round_id": "../bad"}, {"round_job": None},
])
def test_malformed_prepared_key_preserves_history(build_setup, tmp_path, damage):
    def fail(*args, **kwargs):
        raise OSError("injected")
    with pytest.raises(RuntimeError, match="injected"):
        tick(build_setup, tmp_path, specloop_runner=fail)
    path = receipt_path(tmp_path)
    jobs.save(path, {**read(tmp_path), **damage})
    before = snapshot(path.parent)
    with pytest.raises(ValueError):
        tick(build_setup, tmp_path, module=restart())
    assert snapshot(path.parent) == before and not build_setup[1].comments


@pytest.mark.scenario("pr-revision/stable-build-round-identity/legacy-rounds-keep-existing-semantics")
@pytest.mark.parametrize("next_stage", ["empty", "spec"])
def test_replacement_clears_build_metadata(build_setup, tmp_path, attempts, next_stage):
    _, gh, commands = build_setup
    launched, launch = attempts
    tick(build_setup, tmp_path, background=True, launcher=launch)
    directory, request, source = launched[0]
    jobs.finalize(directory, {"result": revision_success(request["args"], commands.result)})
    tick(build_setup, tmp_path)
    original = read(tmp_path)
    retained = source.read_bytes()
    gh.label_event("agent:revise", "labeled")
    event = gh.events[-1]["id"]
    if next_stage == "spec":
        commands.runs = []
        commands.result = {"version": 1, "outcome": "drafted", "commit": original["result"]["commit"],
                           "branch": f"spec/{original['change']}"}
        gh.inline.append({**gh.inline[0], "id": 4, "body": "New spec feedback"})
    tick(build_setup, tmp_path, module=restart())
    replacement = read(tmp_path)
    assert replacement["event"] == event and replacement["round_id"] == str(event)
    assert not {"revision_key_version", "round_job", "job"}.intersection(replacement)
    assert replacement["delivered"] and source.read_bytes() == retained
    assert set(original["consumed_feedback_ids"]) <= set(replacement["consumed_feedback_ids"])
    assert gh.comments[-1]["body"].endswith(
        f"<!-- nightshift:revise:{original['change']}:{event} -->")
    if next_stage == "spec":
        assert replacement["feedback_ids"] == ["inline:4"]
        assert "--round-id" not in commands.calls[-1]
    else:
        # An empty round must not make older keyed attempts look partially converted.
        gh.inline.append({**gh.inline[0], "id": 4, "body": "New build feedback"})
        gh.label_event("agent:revise", "labeled")
        tick(build_setup, tmp_path, module=restart(), background=True, launcher=launch)
        assert key(read(tmp_path)) != key(original) and len(launched) == 2
        assert read(tmp_path)["feedback_ids"] == ["inline:4"]

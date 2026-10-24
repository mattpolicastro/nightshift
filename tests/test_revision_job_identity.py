"""Round identity survives attempt failures, coordinated retry and compaction."""
import fcntl
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

import pytest

from nightshift import specloop_job as jobs
from test_specloop_compaction import snapshot
from test_specloop_jobs import identity


@pytest.fixture
def revision(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_children", {})
    monkeypatch.setattr(jobs, "reap", lambda: None)
    path = tmp_path / "revisions" / "owner__sample" / "7.json"
    feedback = path.with_suffix(".jobs") / "feedback-123.json"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(json.dumps(["Keep this exact feedback ☃"]))
    receipt = dict(command="revise", change="sample", event=123, round_id="round-1",
                   revision_key_version=1, feedback=json.loads(feedback.read_text()),
                   feedback_state_version=1, feedback_ids=["conversation:1"],
                   consumed_feedback_ids=[])
    jobs.save(path, receipt)
    args = ["revise", "sample", "--feedback", str(feedback), "--feedback-format", "json",
            "--round-id", receipt["round_id"]]
    return path, receipt, args, feedback


def launch(revision, **kwargs):
    path, receipt, args, _ = revision
    jobs.start(path, receipt, args, launcher=kwargs.pop("launcher", lambda *a, **k: object()),
               clock=kwargs.pop("clock", lambda: 100), **kwargs)
    return path.with_suffix(".jobs") / receipt["job"]


def stopped(directory):
    jobs.save(directory / "process.json", identity(directory))
    jobs.request_stop(directory, verify_identity=lambda: identity(directory))
    jobs.finish_stop(directory)


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/key-and-attempt-are-saved-before-launch")
@pytest.mark.scenario("background-job-control/durable-command-timing/timing-survives-restart")
@pytest.mark.parametrize("failure", [False, True])
def test_prelaunch_identity_and_failure(revision, failure):
    path, receipt, args, feedback = revision
    original, seen = dict(receipt), []

    def launcher(argv, **kwargs):
        directory = Path(argv[-2])
        seen.append(directory)
        assert json.loads(path.read_text()) == receipt == dict(
            original, job=directory.name, round_job=directory.name)
        assert json.loads((directory / "request.json").read_text()) == dict(
            stage="revisions", args=args, started_at=100, timeout_seconds=1800,
            deadline_at=1900, round_id="round-1")
        if failure:
            raise OSError("launch failed")
        return object()

    if failure:
        with pytest.raises(OSError, match="launch failed"):
            launch(revision, launcher=launcher)
    else:
        launch(revision, launcher=launcher)
    assert len(seen) == 1 and feedback.read_text() == json.dumps(original["feedback"])
    expected = dict(original, round_job=seen[0].name)
    if not failure:
        expected["job"] = seen[0].name
    assert json.loads(path.read_text()) == receipt == expected
    jobs._children.clear()
    before = snapshot(path.parent)
    rows, errors = jobs.list_jobs(path.parents[2])
    assert not errors and len(rows) == 1 and rows[0].id == seen[0].name
    assert rows[0].command == tuple(args) and rows[0].state == "failed"
    assert (rows[0].started_at, rows[0].timeout_seconds, rows[0].deadline_at) == (100, 1800, 1900)
    with jobs.revision_attempt(path, receipt) as state:
        assert state == ("retry" if failure else "pending")
    assert snapshot(path.parent) == before
    assert not path.with_suffix(".release.lock").exists()


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/crashed-attempt-detaches-normally")
@pytest.mark.parametrize("now", [101, 2000])
@pytest.mark.parametrize("error", [None, "CLI failed"])
def test_crash_detaches_without_deadline_hold(revision, monkeypatch, now, error):
    path, receipt, _, _ = revision
    directory = launch(revision)
    if error:
        jobs.finalize(directory, {"error": error})
    before = snapshot(directory)
    monkeypatch.setattr(jobs.time, "time", lambda: now)
    expected = {k: v for k, v in receipt.items() if k != "job"}
    with pytest.raises(jobs.specloop_client.SpecloopError, match=error or "exited without a result"):
        jobs.collect(path, receipt)
    assert json.loads(path.read_text()) == receipt == expected
    assert snapshot(directory) == before and not (directory / "collected.json").exists()
    with jobs.revision_attempt(path, receipt) as state:
        assert state == "retry"
        successor = launch(revision, clock=lambda: now)
    assert successor != directory and receipt["round_job"] == receipt["job"] == successor.name
    assert json.loads((successor / "request.json").read_text())["deadline_at"] == now + 1800
    assert receipt["round_id"] == "round-1" and receipt["consumed_feedback_ids"] == []
    assert snapshot(directory) == before


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/mixed-legacy-and-keyed-evidence-is-refused")
@pytest.mark.parametrize("damage", ["request-key", "argv-key", "receipt-key", "receipt-version",
                                   "receipt-attempt", "receipt-all", "wrong-key", "wrong-change",
                                   "feedback", "boolean-version", "bad-attempt", "missing-request"])
@pytest.mark.parametrize("detachment", [None, "crash", "release"])
def test_mixed_evidence_never_becomes_legacy(revision, damage, detachment):
    path, receipt, _, feedback = revision
    directory = launch(revision)
    if detachment == "crash":
        with pytest.raises(jobs.specloop_client.SpecloopError):
            jobs.collect(path, receipt)
    elif detachment == "release":
        stopped(directory)
        assert jobs.release_job(path.parents[2], directory.name) == "released"
        receipt.pop("job")
    request = json.loads((directory / "request.json").read_text())
    if damage == "request-key":
        request.pop("round_id")
    elif damage == "argv-key":
        request["args"] = request["args"][:-2]
    elif damage.startswith("receipt-"):
        for key in {"receipt-key": ["round_id"], "receipt-version": ["revision_key_version"],
                    "receipt-attempt": ["round_job"],
                    "receipt-all": ["round_id", "round_job", "revision_key_version"]}[damage]:
            receipt.pop(key)
    elif damage == "wrong-key":
        request["round_id"] = "another-key"
    elif damage == "wrong-change":
        request["args"][1] = "another-change"
    elif damage == "feedback":
        feedback.write_text('["changed"]')
    elif damage == "boolean-version":
        receipt["revision_key_version"] = True
    elif damage == "bad-attempt":
        receipt["round_job"] = "../elsewhere"
    jobs.save(directory / "request.json", request)
    if damage == "missing-request":
        (directory / "request.json").unlink()
    jobs.save(path, receipt)
    before = snapshot(path.parent)
    with pytest.raises((ValueError, OSError)):
        with jobs.revision_attempt(path, receipt):
            pytest.fail("inconsistent evidence authorized redispatch")
    with pytest.raises((ValueError, OSError)):
        launch(revision, launcher=lambda *a, **k: pytest.fail("invalid attempt launched"))
    assert snapshot(path.parent) == before
    if detachment is None:
        jobs.finalize(directory, {"result": {"version": 1, "outcome": "revised", "commit": "abc"}})
        before = snapshot(path.parent)
        # Collection is not a replacement/replay decision; provenance gates
        # those decisions above, not acceptance of an already cached result.
        assert jobs.collect(path, receipt) == {"version": 1, "outcome": "revised", "commit": "abc"}
        after = snapshot(path.parent)
        assert {p: data for p, data in after.items() if p.name != "collected.json"} == before
        before = snapshot(path.parent)
    assert snapshot(path.parent) == before and receipt["consumed_feedback_ids"] == []


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/concurrent-provenance-stays-current")
@pytest.mark.parametrize("inspect", [False, True])
@pytest.mark.parametrize("history", [False, True])
def test_first_attempt_defers_competing_writer_before_receipt_save(revision, monkeypatch, inspect, history):
    path, receipt, args, feedback = revision
    if history:
        launch(revision)
        with pytest.raises(jobs.specloop_client.SpecloopError):
            jobs.collect(path, receipt)
        receipt.pop("round_job")
        receipt["round_id"] = args[-1] = "new-snapshot"
        jobs.save(path, receipt)
    stale, save = dict(receipt), jobs.save

    def competitor():
        with pytest.raises(jobs.ReceiptChanged):
            with jobs.revision_attempt(path, stale) if inspect else nullcontext():
                launch((path, stale, args, feedback))

    def interleave(target, data):
        if target == path and "job" in data:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(competitor).result(timeout=5)
        save(target, data)

    monkeypatch.setattr(jobs, "save", interleave)
    with jobs.revision_attempt(path, receipt) if inspect else nullcontext():
        directory = launch(revision)
    assert json.loads(path.read_text()) == receipt == dict(stale, job=directory.name, round_job=directory.name)
    assert len(list(path.with_suffix(".jobs").glob("*/request.json"))) == 1 + history
    assert not path.with_suffix(".release.lock").exists()


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/concurrent-provenance-stays-current")
def test_successor_is_saved_while_both_existing_locks_are_held(revision):
    path, receipt, _, _ = revision
    directory = launch(revision)
    receipt.pop("job")
    jobs.save(path, receipt)
    release = path.with_suffix(".release.lock")
    release.touch()

    def launcher(argv, **kwargs):
        for target in (directory / "control.lock", release):
            with target.open() as probe, pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert json.loads(path.read_text())["round_job"] == Path(argv[-2]).name
        return object()

    with jobs.revision_attempt(path, receipt) as state:
        assert state == "retry"
        launch(revision, launcher=launcher)


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/concurrent-provenance-stays-current")
@pytest.mark.parametrize("writer", ["release", "collection", "snapshot", "successor"])
def test_stale_reader_cannot_persist_over_current_receipt(revision, monkeypatch, writer):
    path, receipt, _, _ = revision
    directory = launch(revision)
    if writer == "release":
        stopped(directory)
    stale = dict(receipt)
    if writer == "release":
        assert jobs.release_job(path.parents[2], directory.name) == "released"
    elif writer == "collection":
        with pytest.raises(jobs.specloop_client.SpecloopError):
            jobs.collect(path, receipt)
    else:
        receipt.pop("job")
        jobs.save(path, receipt)
    if writer in ("snapshot", "successor"):
        path.with_suffix(".release.lock").touch()
        stale = dict(receipt)
        flock = fcntl.flock

        def interleave(fd, flags):
            if os.fstat(fd if isinstance(fd, int) else fd.fileno()).st_ino == path.with_suffix(".release.lock").stat().st_ino:
                replacement = dict(receipt, round_id="newer-round") if writer == "snapshot" else dict(
                    receipt, job="f" * 32, round_job="f" * 32)
                jobs.save(path, replacement)
            return flock(fd, flags)

        monkeypatch.setattr(fcntl, "flock", interleave)
    evidence = snapshot(directory)
    with pytest.raises(jobs.ReceiptChanged):
        with jobs.revision_attempt(path, stale):
            jobs.save(path, stale)
            pytest.fail("stale writer entered successor persistence")
    assert snapshot(directory) == evidence
    assert json.loads(path.read_text()) != stale
    if writer == "collection":
        assert not path.with_suffix(".release.lock").exists()


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/concurrent-provenance-stays-current")
@pytest.mark.parametrize("state", ["live", "live-error", "stopping", "stopped", "success", "malformed"])
def test_only_idle_failures_authorize_retry(revision, state):
    path, receipt, _, _ = revision
    directory = launch(revision)
    receipt.pop("job")
    jobs.save(path, receipt)
    if state in ("stopping", "stopped"):
        stopped(directory)
        if state == "stopping":
            stop = json.loads((directory / "stop.json").read_text())
            jobs.save(directory / "stop.json", dict(stop, state="stopping"))
    elif state in ("success", "live-error", "malformed"):
        jobs.save(directory / "result.json", {"result": {}} if state == "success" else
                  {"error": "failure"} if state == "live-error" else {"unexpected": True})
    before = snapshot(path.parent)
    with (directory / "lock").open() as lock:
        if state.startswith("live"):
            fcntl.flock(lock, fcntl.LOCK_EX)
        if state == "malformed":
            with pytest.raises(ValueError):
                with jobs.revision_attempt(path, receipt):
                    pytest.fail("malformed attempt accepted")
        else:
            with jobs.revision_attempt(path, receipt) as disposition:
                assert disposition == ("released" if state == "stopped" else "pending")
    assert snapshot(path.parent) == before


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/compaction-preserves-round-identity")
@pytest.mark.scenario("background-job-control/durable-command-timing/legacy-and-malformed-timing")
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_compaction_retains_identity_and_inputs(revision, legacy, resume):
    path, receipt, _, feedback = revision
    directory = launch(revision)
    request = json.loads((directory / "request.json").read_text())
    if legacy:
        for key in ("round_id", "started_at", "deadline_at", "timeout_seconds"):
            request.pop(key)
        request["args"] = request["args"][:-2]
        receipt.pop("revision_key_version")
        receipt.pop("round_job")
        receipt["round_id"] = str(receipt["event"])
    idea = directory.parent / "idea.txt"
    idea.write_text("redact this")
    expected = dict(request, **({k: None for k in ("started_at", "timeout_seconds", "deadline_at")} if legacy else {}))
    jobs.save(directory / "request.json", dict(request, args=request["args"] + ["--idea-file", str(idea)]))
    os.utime(directory / "request.json", ns=(123456789, 987654321))
    receipt.update(result=dict(version=1, outcome="revised", commit="abc"), delivered=True)
    jobs.save(path, receipt)
    jobs.finalize(directory, {"result": receipt["result"]})
    before = snapshot(path.parent)
    assert jobs.compact_delivered(path, receipt, prepare_only=resume) == []
    if resume:
        assert idea.exists() and (directory / "request.json").read_bytes() == before[directory / "request.json"][0]
        assert jobs.resume_cleanup(path, directory.name) == []
    assert json.loads((directory / "request.json").read_text()) == expected
    assert (directory / "request.json").stat().st_mtime_ns == 987654321
    assert not idea.exists() and not (directory / "log").exists()
    for target in (path, feedback, *(directory / n for n in ("result.json", "control.lock", "lock"))):
        assert snapshot(path.parent)[target] == before[target]
    assert not path.with_suffix(".release.lock").exists()
    assert jobs.list_jobs(path.parents[2])[1] == []


@pytest.mark.scenario("background-job-control/revision-attempt-provenance/mixed-legacy-and-keyed-evidence-is-refused")
def test_consistent_event_based_legacy_release(revision):
    path, receipt, _, feedback = revision
    directory = launch(revision)
    request = json.loads((directory / "request.json").read_text())
    jobs.save(directory / "request.json", {"args": request["args"][:-2]})
    for key in ("revision_key_version", "round_job"):
        receipt.pop(key)
    receipt["round_id"] = str(receipt["event"])
    jobs.save(path, receipt)
    stopped(directory)
    before, input_bytes = snapshot(directory), feedback.read_bytes()
    with jobs.revision_attempt(path, receipt) as state:
        assert state == "pending"
    assert jobs.release_job(path.parents[2], directory.name) == "released"
    receipt.pop("job")
    assert json.loads(path.read_text()) == receipt
    with jobs.revision_attempt(path, receipt) as state:
        assert state == "legacy"
    assert snapshot(directory) == before and feedback.read_bytes() == input_bytes

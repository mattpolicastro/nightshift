"""Delivered compaction preserves history and refuses unproven ownership."""
import fcntl
import json
import os
from pathlib import Path

import pytest

from nightshift import specloop_job as jobs


def delivered(root, *, stage="proposals", command="propose", outcome="needs_input", serial=1):
    path = root / stage / "o__r" / ("8.json" if stage == "proposals" else "28.json")
    directory = path.with_suffix(".jobs") / f"{serial:032x}"
    directory.mkdir(parents=True)
    idea = directory.parent / f"idea-{serial}.txt"
    idea.write_text("private drafting input")
    args = [command, "r-issue-8-feature"] if command == "revise" else [command, "manifest", "--change", "r-issue-8-feature"]
    args += ["--head", "abc", "--idea-file", str(idea)]
    jobs.save(directory / "request.json", dict(args=args))
    os.utime(directory / "request.json", ns=(1792540800000000000, 1792540800000000000))
    result = dict(version=1, outcome=outcome)
    if outcome in ("drafted", "revised"):
        result.update(commit=f"commit-{serial}", branch="spec/r-issue-8-feature")
    receipt = dict(job=directory.name, command=command, change="r-issue-8-feature", head="abc", result=result, delivered=True)
    jobs.save(path, receipt)
    jobs.save(directory / "result.json", dict(result=result))
    jobs.save(directory / "process.json", dict(version=1, job_id=directory.name, pid=1234, pgid=1234,
                                               started_at="2026-10-21T00:00:00Z", start_identity="original"))
    jobs.save(directory / "collected.json", dict(collected_at="2026-10-21T00:00:00Z"))
    for name in ("lock", "control.lock", "log", "unknown"):
        (directory / name).write_text(name)
    return path, receipt, directory, idea


def snapshot(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


@pytest.mark.scenario("background-job-control/delivered-attempt-compaction/delivered-artifacts-are-removed")
@pytest.mark.parametrize("legacy", [False, True])
def test_finished_job_directory_removed(tmp_path, monkeypatch, legacy):
    # Acceptance name retained: heavy artifacts disappear; the directory survives.
    path, receipt, directory, idea = delivered(tmp_path)
    request = json.loads((directory / "request.json").read_text())
    feedback = directory.parent / "build-feedback.json"
    jobs.save(feedback, dict(feedback="keep"))
    request["args"] += ["--idea=legacy secret", "--idea", "inline secret", "--idea=--literal", "--idea=", f"--idea-file={idea}",
                        "--feedback", str(feedback), "--branch=spec/r-issue-8-feature"]
    timing = dict(started_at=None, timeout_seconds=None, deadline_at=None) if legacy else dict(
        started_at=100, timeout_seconds=3600, deadline_at=3700)
    if not legacy:
        request.update(stage="proposals", **timing)
    jobs.save(directory / "request.json", request)
    os.utime(directory / "request.json", ns=(123456789, 987654321))
    before = snapshot(tmp_path)
    replace = Path.replace
    def checked_replace(source, target):
        if target == directory / "request.json":
            assert source.stat().st_mtime_ns == 987654321
            assert source.stat().st_atime_ns == json.loads((directory / "cleanup.json").read_text())["atime_ns"]
        return replace(source, target)
    monkeypatch.setattr(Path, "replace", checked_replace)
    assert jobs.compact_delivered(path, receipt) == []
    expected = request["args"][:6] + ["--feedback", str(feedback), "--branch=spec/r-issue-8-feature"]
    assert json.loads((directory / "request.json").read_text()) == dict(stage="proposals", args=expected, **timing)
    assert not idea.exists() and not (directory / "log").exists() and directory.is_dir()
    assert (directory / "request.json").stat().st_mtime_ns == 987654321
    for retained in (path, feedback, *(directory / n for n in ("result.json", "process.json", "collected.json", "control.lock", "lock", "unknown"))):
        assert snapshot(tmp_path)[retained] == before[retained]
    journal = json.loads((directory / "cleanup.json").read_text())
    assert journal["complete"] is True and "secret" not in json.dumps(journal)
    assert not path.with_suffix(".release.lock").exists()
    rows, errors = jobs.list_jobs(tmp_path)
    assert not errors and len(rows) == 1 and rows[0].state == "finished"
    after = snapshot(tmp_path)
    assert jobs.compact_delivered(path, receipt) == [] and snapshot(tmp_path) == after


@pytest.mark.scenario("background-job-control/delivered-attempt-compaction/ineligible-work-keeps-evidence")
@pytest.mark.parametrize("state", ["undelivered", "failed", "stopping", "stopped", "released", "live", "overdue",
                                   "paused", "authorization", "notification-retry"])
def test_ineligible_compaction_preserves_evidence(tmp_path, state):
    path, receipt, directory, _ = delivered(tmp_path)
    if state in ("undelivered", "failed", "overdue", "paused", "authorization", "notification-retry"):
        receipt.pop("delivered")
    if state == "failed":
        jobs.save(directory / "result.json", dict(error="wrapper failed"))
    if state == "overdue":
        request = json.loads((directory / "request.json").read_text())
        jobs.save(directory / "request.json", dict(**request, stage="proposals", started_at=1,
                                                    timeout_seconds=3600, deadline_at=3601))
    if state in ("stopping", "stopped", "released"):
        jobs.save(directory / "stop.json", dict(state="stopping" if state == "stopping" else "stopped"))
    if state == "released":
        receipt.pop("job")
    jobs.save(path, receipt)
    with (directory / "lock").open() as lock:
        if state in ("live", "overdue"):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = snapshot(tmp_path)
        assert jobs.compact_delivered(path, receipt)
        assert snapshot(tmp_path) == before


@pytest.mark.scenario("background-job-control/delivered-attempt-compaction/cleanup-cannot-cross-attempt-ownership")
@pytest.mark.parametrize("unsafe", ["outside", "shared", "directory", "symlink", "feedback", "result"])
def test_unsafe_idea_is_retained(tmp_path, unsafe):
    path, receipt, directory, idea = delivered(tmp_path)
    if unsafe == "outside":
        idea = tmp_path / "outside"
        idea.write_text("keep")
    elif unsafe == "directory":
        idea.unlink()
        idea.mkdir()
    elif unsafe == "symlink":
        idea.unlink()
        idea.symlink_to(directory / "unknown")
    elif unsafe == "result":
        idea = directory / "result.json"
    request = json.loads((directory / "request.json").read_text())
    request["args"][-1] = str(idea)
    if unsafe == "shared":
        _, other_receipt, other, _ = delivered(tmp_path, serial=2)
        jobs.save(other / "request.json", request)
        jobs.save(path, receipt)
    if unsafe == "feedback":
        request["args"] += ["--feedback", str(idea)]
    jobs.save(directory / "request.json", request)
    assert jobs.compact_delivered(path, receipt)
    assert idea.exists() and (directory / "result.json").exists()
    assert "--idea-file" not in json.loads((directory / "request.json").read_text())["args"]
    if unsafe == "shared":
        jobs.save(path, other_receipt)
        assert jobs.compact_delivered(path, other_receipt)
        assert idea.exists()  # Redaction cannot transfer ownership to a sibling.


@pytest.mark.scenario("background-job-control/delivered-attempt-compaction/cleanup-cannot-cross-attempt-ownership")
@pytest.mark.parametrize("filename", ["feedback.json", "feedback=round.json"])
@pytest.mark.parametrize("equals_option", [False, True])
@pytest.mark.parametrize("sibling", [False, True])
def test_shared_feedback_is_retained(tmp_path, filename, equals_option, sibling):
    path, receipt, directory, idea = delivered(tmp_path)
    shared = idea.with_name(filename)
    idea.rename(shared)
    request = json.loads((directory / "request.json").read_text())
    request["args"][-1] = str(shared)
    jobs.save(directory / "request.json", request)
    other = delivered(tmp_path, serial=2)[2] if sibling else directory
    jobs.save(path, receipt)
    request = json.loads((other / "request.json").read_text())
    request["args"] += [f"--feedback={shared}"] if equals_option else ["--feedback", str(shared)]
    jobs.save(other / "request.json", request)
    before = snapshot(other) if sibling else None
    original = snapshot(tmp_path)[shared]
    diagnostics = jobs.compact_delivered(path, receipt)
    assert any("shared by another argument or attempt" in message for message in diagnostics)
    assert snapshot(tmp_path)[shared] == original
    assert not (directory / "log").exists()
    assert "--idea-file" not in json.loads((directory / "request.json").read_text())["args"]
    if sibling:
        assert snapshot(other) == before
    else:
        assert json.loads((other / "request.json").read_text())["args"] == request["args"][:6] + request["args"][8:]


@pytest.mark.scenario("background-job-control/delivered-attempt-compaction/cleanup-cannot-cross-attempt-ownership")
@pytest.mark.parametrize("damage", ["replaced", "identity", "context", "malformed", "symlink", "lock-symlink", "result", "journal"])
def test_unproven_attempt_is_untouched(tmp_path, damage):
    path, receipt, directory, _ = delivered(tmp_path)
    if damage == "replaced":
        jobs.save(path, {**receipt, "job": "f" * 32})
    elif damage in ("identity", "context"):
        receipt["change" if damage == "identity" else "repo"] = "different-identity"
        jobs.save(path, receipt)
    elif damage == "malformed":
        (directory / "request.json").write_text("{")
    elif damage in ("symlink", "lock-symlink"):
        record = directory / ("request.json" if damage == "symlink" else "control.lock")
        record.rename(directory / "original")
        record.symlink_to(directory / "original")
    elif damage == "journal":
        jobs.save(directory / "cleanup.json", dict(version=1))
    else:
        jobs.save(directory / "result.json", dict(result=dict(version=1, outcome="unknown")))
    before = snapshot(tmp_path)
    assert jobs.compact_delivered(path, receipt)
    assert snapshot(tmp_path) == before


@pytest.mark.scenario("background-job-control/delivered-attempt-compaction/cleanup-cannot-cross-attempt-ownership")
def test_cleanup_lock_order_and_receipt_reread(tmp_path, monkeypatch):
    path, receipt, directory, _ = delivered(tmp_path)
    release = path.with_suffix(".release.lock")
    release.touch()
    order, flock = [], fcntl.flock
    expected = [directory / "control.lock", release, directory / "lock"]
    def coordinated(fd, flags):
        assert os.fstat(fd.fileno()).st_ino == expected[len(order)].stat().st_ino
        order.append(flags)
        if len(order) == 2:
            jobs.save(path, {**receipt, "job": "f" * 32})
        return flock(fd, flags)
    monkeypatch.setattr(fcntl, "flock", coordinated)
    before = snapshot(directory)
    assert jobs.compact_delivered(path, receipt)
    assert order == [fcntl.LOCK_EX, fcntl.LOCK_EX, fcntl.LOCK_EX | fcntl.LOCK_NB]
    assert snapshot(directory) == before and json.loads(path.read_text())["job"] == "f" * 32

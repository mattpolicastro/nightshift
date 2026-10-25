"""Delivered compaction preserves history and refuses unproven ownership."""
import fcntl
import json
import os
from pathlib import Path

import pytest

from nightshift import specloop_job as jobs
from test_specloop_background import background, delivery_job


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


@pytest.fixture(params=[("proposals", "propose", "needs_input"), ("admissions", "admit", "refused"),
                       ("revisions", "propose", "drafted"), ("revisions", "revise", "revised")])
def historical(tmp_path, request):
    stage, command, outcome = request.param
    path, receipt, directory, idea = delivered(tmp_path, stage=stage, command=command, outcome=outcome)
    if command == "revise":
        feedback = directory.parent / "build-feedback.json"
        feedback.write_text('["exact feedback"]')
        receipt.update(round_id="round-1", round_job=directory.name, revision_key_version=1,
                       feedback=["exact feedback"])
        record = json.loads((directory / "request.json").read_text())
        record["args"] += ["--round-id", "round-1", "--feedback", str(feedback), "--feedback-format", "json"]
        jobs.save(directory / "request.json", dict(record, round_id="round-1"))
        jobs.save(path, receipt)
    return path, receipt, directory, idea


@pytest.mark.scenario("background-job-control/historical-compaction-pass/compact-delivered-historical-attempts-only")
@pytest.mark.scenario("background-job-control/delivered-attempt-compaction/delivered-artifacts-are-removed")
def test_compact_history_preserves_artifacts(tmp_path, historical, monkeypatch):
    path, receipt, directory, idea = historical
    before = snapshot(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("historical cleanup must not execute or deliver work")
    for name in ("start", "collect", "resume_cleanup", "process_start_identity"):
        monkeypatch.setattr(jobs, name, forbidden)
    report = jobs.compact_history(tmp_path)
    assert (report.compacted, report.skipped, report.error, report.diagnostics) == (1, 0, False, ())
    assert report.outcomes[0].job.directory == directory and report.outcomes[0].reason
    assert not idea.exists() and not (directory / "log").exists()
    after = snapshot(tmp_path)
    for retained in before.keys() - {idea, directory / "log", directory / "request.json"}:
        assert after[retained] == before[retained]
    assert (directory / "request.json").stat().st_mtime_ns == before[directory / "request.json"][1]
    original = json.loads(before[directory / "request.json"][0])
    minimal = json.loads((directory / "request.json").read_text())
    assert minimal["args"] == jobs._redact_idea(original["args"])[0]
    assert minimal.get("round_id") == original.get("round_id")
    assert not path.with_suffix(".release.lock").exists()
    rows, errors = jobs.list_jobs(tmp_path)
    assert not errors and [row.id for row in rows] == [directory.name]
    repeat = jobs.compact_history(tmp_path)
    assert (repeat.compacted, repeat.skipped, repeat.error) == (0, 1, False)
    assert repeat.outcomes[0].reason == "already compacted" and snapshot(tmp_path) == after


@pytest.mark.scenario("background-job-control/historical-compaction-pass/unmatched-historical-identity-is-skipped")
@pytest.mark.scenario("background-job-control/delivered-attempt-compaction/ineligible-work-keeps-evidence")
@pytest.mark.parametrize("journal", ["absent", "prepared", "complete"])
@pytest.mark.parametrize("damage", ["absent", "no-job", "round-job-only", "replaced", "pending", "stopped",
                                   "released", "failed", "live", "context", "result", "request", "keyed"])
def test_history_skips_unproven_identity(tmp_path, historical, journal, damage):
    path, receipt, directory, _ = historical
    if journal != "absent":
        assert jobs.compact_delivered(path, receipt, prepare_only=journal == "prepared") == []
    if damage == "absent":
        path.unlink()
    elif damage in ("no-job", "round-job-only", "released"):
        receipt.pop("job")
        if damage == "round-job-only":
            receipt["round_job"] = directory.name
    elif damage == "replaced":
        receipt["job"] = "f" * 32
    elif damage == "pending":
        receipt["delivered"] = False
    elif damage == "failed":
        jobs.save(directory / "result.json", dict(error="failed wrapper"))
    elif damage == "context":
        receipt["repo"] = "another/repository"
    elif damage == "result":
        receipt["result"] = {**receipt["result"], "unexpected": True}
    elif damage == "request":
        receipt["head"] = "different-head"
    elif damage == "keyed":
        receipt["round_job"] = "f" * 32
    if damage in ("stopped", "released"):
        jobs.save(directory / "stop.json", dict(state="stopped"))
    if damage != "absent":
        jobs.save(path, receipt)
    with (directory / "lock").open() as lock:
        if damage == "live":
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = snapshot(tmp_path)
        report = jobs.compact_history(tmp_path)
        assert (report.compacted, report.skipped, report.error) == (0, 1, False)
        assert report.outcomes[0].reason and snapshot(tmp_path) == before


@pytest.mark.scenario("background-job-control/historical-compaction-pass/receipt-replacement-during-cleanup-is-refused")
@pytest.mark.scenario("background-job-control/delivered-attempt-compaction/cleanup-cannot-cross-attempt-ownership")
@pytest.mark.parametrize("release_exists", [False, True])
@pytest.mark.parametrize("removed", [False, True])
def test_history_rereads_receipt_under_locks(tmp_path, monkeypatch, release_exists, removed):
    path, receipt, directory, _ = delivered(tmp_path)
    release = path.with_suffix(".release.lock")
    if release_exists:
        release.touch()
    flock, order = fcntl.flock, []
    def racing(lock, flags):
        result = flock(lock, flags)
        if lock.name == str(directory / "control.lock") or order:
            order.append(Path(lock.name))
        if lock.name == str(directory / "control.lock"):
            if removed:
                path.unlink()
            else:
                jobs.save(path, {**receipt, "job": "f" * 32})
        return result
    monkeypatch.setattr(fcntl, "flock", racing)
    before = snapshot(directory)
    report = jobs.compact_history(tmp_path)
    assert (report.compacted, report.skipped, report.error) == (0, 1, False)
    assert ("receipt disappeared" if removed else "receipt changed") in report.outcomes[0].reason
    assert order == [directory / "control.lock", *([release] if release_exists else []), directory / "lock"]
    assert snapshot(directory) == before
    assert not path.exists() if removed else json.loads(path.read_text())["job"] == "f" * 32
    assert release.exists() == release_exists


@pytest.mark.scenario("background-job-control/historical-compaction-pass/interrupted-and-repeated-passes-are-safe")
@pytest.mark.scenario("background-job-control/recoverable-cleanup/interruption-resumes-safely")
@pytest.mark.parametrize("point", ["absent", "journal", "request", "unlink", "completion"])
@pytest.mark.parametrize("replaced", [False, True])
def test_history_resumes_only_current_receipt(tmp_path, monkeypatch, point, replaced):
    from test_specloop_cleanup_recovery import interrupt
    path, receipt, directory, idea = delivered(tmp_path)
    before = snapshot(tmp_path)
    with monkeypatch.context() as fault:
        interrupt(fault, directory, point)
        report = jobs.compact_history(tmp_path)
    assert (report.compacted, report.skipped, report.error) == (0, 1, True)
    assert any("cleanup interrupted" in message for message in report.diagnostics)
    if replaced:
        jobs.save(path, {**receipt, "job": "f" * 32})
    interrupted = snapshot(tmp_path)
    retry = jobs.compact_history(tmp_path)
    assert (retry.compacted, retry.skipped, retry.error) == (int(not replaced), int(replaced), False)
    if replaced:
        assert snapshot(tmp_path) == interrupted
    else:
        assert not idea.exists() and not (directory / "log").exists()
        for retained in (path, directory / "result.json"):
            assert snapshot(tmp_path)[retained] == before[retained]
        assert (directory / "request.json").stat().st_mtime_ns == before[directory / "request.json"][1]
    after = snapshot(tmp_path)
    repeat = jobs.compact_history(tmp_path)
    assert (repeat.compacted, repeat.skipped, repeat.error) == (0, 1, False)
    assert snapshot(tmp_path) == after


@pytest.mark.scenario("background-job-control/historical-compaction-pass/report-partial-progress-and-safe-skips")
@pytest.mark.parametrize("damage", ["receipt", "request", "result", "process", "cleanup", "collected",
                                   "receipt-link", "request-link", "log-link", "unreadable", "filesystem"])
def test_history_continues_after_damaged_evidence(tmp_path, monkeypatch, damage):
    path, _, directory, _ = delivered(tmp_path)
    healthy = delivered(tmp_path, stage="revisions", serial=2)[2]
    target = path if damage in ("receipt", "receipt-link", "unreadable") else directory / (damage + ".json")
    if damage.endswith("-link"):
        target = path if damage == "receipt-link" else directory / ("log" if damage == "log-link" else "request.json")
        original = target.with_suffix(".original")
        target.rename(original)
        target.symlink_to(original)
        read = Path.read_text
        def guarded(p, *args, **kwargs):
            assert p != target, "must reject evidence links before reading"
            return read(p, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", guarded)
    elif damage == "unreadable":
        opening = os.open
        def denied(p, *args, **kwargs):
            if p == path:
                raise PermissionError("unreadable receipt")
            return opening(p, *args, **kwargs)
        monkeypatch.setattr(os, "open", denied)
    elif damage == "filesystem":
        from test_specloop_cleanup_recovery import interrupt
        interrupt(monkeypatch, directory, "unlink")
    else:
        target.write_text("{")
    before = snapshot(directory)
    report = jobs.compact_history(tmp_path)
    assert (report.compacted, report.skipped, report.error) == (1, 1, True)
    assert [outcome.job.directory for outcome in report.outcomes] == [directory, healthy]
    assert report.diagnostics and not (healthy / "log").exists()
    if damage != "filesystem":
        assert snapshot(directory) == before


@pytest.mark.scenario("background-job-control/historical-compaction-pass/report-partial-progress-and-safe-skips")
@pytest.mark.parametrize("damage", ["malformed", "non-object", "missing-references", "invalid-references",
                                   "symlink", "unreadable"])
def test_history_reports_corrupt_sibling_ownership_evidence(tmp_path, monkeypatch, damage):
    path, receipt, directory, idea = delivered(tmp_path)
    _, _, sibling, sibling_idea = delivered(tmp_path, serial=2)
    jobs.save(path, receipt)  # The sibling has no matching current receipt.
    healthy = delivered(tmp_path, stage="revisions", serial=3)[2]
    journal = sibling / "cleanup.json"
    if damage == "malformed":
        journal.write_text("{")
    elif damage == "non-object":
        journal.write_text("[]")
    else:
        jobs.save(journal, {} if damage == "missing-references" else {
            "idea_references": [None] if damage == "invalid-references" else []})
    if damage == "symlink":
        original = sibling / "original-journal"
        journal.rename(original)
        journal.symlink_to(original)
    read = Path.read_text
    def guarded(p, *args, **kwargs):
        if p == journal:
            assert damage != "symlink", "must not follow sibling evidence links"
            if damage == "unreadable":
                raise PermissionError(13, "unreadable sibling journal", str(p))
        return read(p, *args, **kwargs)
    before = snapshot(tmp_path)
    monkeypatch.setattr(Path, "read_text", guarded)
    report = jobs.compact_history(tmp_path)
    assert (report.compacted, report.skipped, report.error) == (2, 1, True)
    assert [outcome.job.directory for outcome in report.outcomes] == [directory, sibling, healthy]
    assert [outcome.status for outcome in report.outcomes] == ["compacted", "skipped", "compacted"]
    assert any("retained idea input" in message and str(journal) in message
               for message in report.diagnostics)
    after = snapshot(tmp_path)
    for retained in (path, idea, sibling_idea, *(p for p in before if p.is_relative_to(sibling))):
        assert after[retained] == before[retained]
    assert not (directory / "log").exists() and not (healthy / "log").exists()
    assert after[directory / "request.json"][1] == before[directory / "request.json"][1]
    assert after[directory / "result.json"] == before[directory / "result.json"]


@pytest.mark.scenario("background-job-control/historical-compaction-pass/report-partial-progress-and-safe-skips")
@pytest.mark.parametrize("unsafe", ["outside", "shared", "directory", "symlink"])
def test_history_warns_about_retained_targets(tmp_path, unsafe):
    path, receipt, directory, idea = delivered(tmp_path)
    if unsafe == "outside":
        idea = tmp_path / "outside"
        idea.write_text("keep")
    elif unsafe in ("directory", "symlink"):
        idea.unlink()
        if unsafe == "directory":
            idea = directory / "idea-directory"
            idea.mkdir()
            (idea / "nested").write_text("keep")
        else:
            idea.symlink_to(directory / "unknown")
    record = json.loads((directory / "request.json").read_text())
    record["args"][-1] = str(idea)
    jobs.save(directory / "request.json", record)
    if unsafe == "shared":
        other = delivered(tmp_path, serial=2)[2]
        jobs.save(other / "request.json", record)
        jobs.save(path, receipt)
    before = snapshot(tmp_path)
    report = jobs.compact_history(tmp_path)
    assert (report.compacted, report.skipped, report.error) == (1, int(unsafe == "shared"), False)
    assert any("retained idea input" in message for message in report.diagnostics)
    assert idea.exists() and not (directory / "log").exists()
    for retained in ([idea / "nested"] if unsafe == "directory" else [idea]):
        assert snapshot(tmp_path)[retained] == before[retained]
    # Reporting retained inputs must not change daemon replacement eligibility.
    assert jobs.compact_delivered(path, receipt, prepare_only=True) == []
    after = snapshot(tmp_path)
    repeat = jobs.compact_history(tmp_path)
    assert repeat.compacted == 0 and not repeat.error
    assert any("retained idea input" in message for message in repeat.diagnostics)
    assert snapshot(tmp_path) == after


@pytest.mark.scenario("background-job-control/recoverable-cleanup/unarmed-cleanup-waits-for-authorized-eligibility")
@pytest.mark.scenario("background-job-control/recoverable-cleanup/authorization-denial-preserves-interrupted-cleanup")
@pytest.mark.scenario("background-job-control/recoverable-cleanup/replacement-retains-cleanup-authority")
@pytest.mark.parametrize("gate", ["unarmed", "denied"])
@pytest.mark.parametrize("point", ["absent", "request"])
@pytest.mark.parametrize("replaced", [False, True])
def test_explicit_pass_does_not_change_daemon_authority(delivery_job, monkeypatch, gate, point, replaced):
    from nightshift import daemon
    from test_specloop_cleanup_recovery import interrupt
    from test_specloop_delivery_completion import finish, read
    from test_specloop_label_authorization_recovery import restarted_intake
    job = delivery_job("draft")
    finish(job, human=True)
    with monkeypatch.context() as fault:
        interrupt(fault, job.directory, point)
        job.poll()
    if replaced:
        assert jobs.compact_delivered(job.path, read(job.path), prepare_only=True) == []
        newer = job.directory.with_name("f" * 32)
        newer.mkdir()
        for name in ("request.json", "result.json", "control.lock", "lock"):
            (newer / name).write_bytes((job.directory / name).read_bytes())
        request = read(newer / "request.json")
        request["args"] = jobs._redact_idea(request["args"])[0]
        jobs.save(newer / "request.json", request)
        jobs.save(job.path, {**read(job.path), "job": newer.name, "delivered": False})
    job.gh.labels.clear()
    if gate == "denied":
        job.gh.label_event("spec:draft", "labeled", actor="outsider")
    before = snapshot(job.path.parent)
    for _ in range(2):
        monkeypatch.setattr(daemon, "specloop_intake", restarted_intake())
        job.poll()
        assert snapshot(job.path.parent) == before and len(job.children) == 1
    report = jobs.compact_history(daemon.STATE_DIR)
    assert (report.compacted, report.skipped, report.error) == (int(not replaced), 2 * int(replaced), False)
    if replaced:
        assert snapshot(job.path.parent) == before
        # Journal recovery remains available only on a trusted eligible visit.
        job.gh.labels.clear()
        job.gh.label_event("spec:draft", "labeled")
        job.poll()
    assert read(job.directory / "cleanup.json")["complete"]

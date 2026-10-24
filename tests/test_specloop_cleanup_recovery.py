"""Recovery requires today's item gates even after a durably delivered result."""
import json
from pathlib import Path

import pytest

from nightshift import daemon, queue, specloop_intake as intake, specloop_job as jobs
from test_specloop_background import background, delivery_job
from test_specloop_compaction import delivered, snapshot
from test_specloop_delivery_completion import finish, read
from test_specloop_label_authorization_recovery import restarted_intake

STAGES = ["draft", "admit", "spec_revision", "build_revision"]


def interrupt(monkeypatch, directory, point):
    save, unlink = jobs._cleanup_save, Path.unlink

    def saving(path, data, **kwargs):
        if (point == "absent" and path.name == "cleanup.json"
                or point == "completion" and path.name == "cleanup.json" and data["complete"]):
            raise OSError("cleanup interrupted")
        save(path, data, **kwargs)
        if (point == "journal" and path.name == "cleanup.json"
                or point == "request" and path.name == "request.json"):
            raise OSError("cleanup interrupted")

    def unlinking(path, *args, **kwargs):
        if point == "unlink" and path == directory / "log":
            raise OSError("cleanup interrupted")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(jobs, "_cleanup_save", saving)
    monkeypatch.setattr(Path, "unlink", unlinking)


@pytest.mark.scenario("background-job-control/recoverable-cleanup/interruption-resumes-safely")
@pytest.mark.scenario("background-job-control/recoverable-cleanup/authorization-denial-preserves-interrupted-cleanup")
@pytest.mark.scenario("background-job-control/recoverable-cleanup/unarmed-cleanup-waits-for-authorized-eligibility")
@pytest.mark.scenario("background-job-control/recoverable-cleanup/replacement-retains-cleanup-authority")
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("point", ["absent", "journal", "request", "unlink", "completion"])
@pytest.mark.parametrize("replaced", [False, True])
def test_cleanup_waits_for_current_authority(delivery_job, monkeypatch, caplog, stage, point, replaced):
    job = delivery_job(stage)
    finish(job, human=True)
    request = job.directory / "request.json"
    original_mtime = request.stat().st_mtime_ns
    result = job.result_path.read_bytes()
    with monkeypatch.context() as fault:
        interrupt(fault, job.directory, point)
        job.poll()
    assert read(job.path)["delivered"]
    assert "cleanup interrupted" in caplog.text
    assert not job.health.failures and not job.health.item_failures
    if replaced:
        # Replacement is legal only after preparation succeeds, even when the
        # initial interruption occurred before a journal could be published.
        assert jobs.compact_delivered(job.path, read(job.path), prepare_only=True) == []
        saved = read(job.path)
        newer = job.directory.with_name("f" * 32)
        newer.mkdir()
        for name in ("request.json", "result.json", "lock", "control.lock"):
            (newer / name).write_bytes((job.directory / name).read_bytes())
        new_input = newer / "idea.txt"
        new_input.write_text("new round input")
        new_request = read(newer / "request.json")
        new_request["args"] = jobs._redact_idea(new_request["args"])[0] + ["--idea-file", str(new_input)]
        jobs.save(newer / "request.json", new_request)
        successor = {**saved, "job": newer.name, "delivered": False}
        if "revision_key_version" in saved:
            successor["round_job"] = newer.name
        jobs.save(job.path, successor)
    trigger = {"draft": "spec:draft", "admit": "spec:approved"}.get(stage, "agent:revise")
    runner = job.runner
    for gate in ("unarmed", "held", "closed", "denied", "lookup", "paused"):
        job.gh.labels.clear()
        job.gh.label_event(trigger, "labeled", actor="outsider" if gate == "denied" else "maintainer")
        if stage == "draft" and gate == "denied":
            job.gh.label_event("agent:ready", "labeled")  # Both present triggers must be trusted.
        if gate == "unarmed":
            job.gh.label_event(trigger, "unlabeled")
        if gate == "held":
            job.gh.labels.add("needs-human")
        if gate == "paused":
            daemon.PAUSE_FILE.touch()

        def github(args):
            if gate == "lookup" and args == ["api", "repos/owner/sample"]:
                raise queue.GhError("membership unavailable", transient=True)
            response = runner(args)
            if gate == "closed" and args[:2] == ["issue" if stage == "draft" else "pr", "view"]:
                return json.dumps({**json.loads(response), "state": "CLOSED"})
            return response

        job.runner = github
        before = snapshot(job.path.parent)
        for _ in range(2):
            jobs._children.clear()
            monkeypatch.setattr(daemon, "specloop_intake", restarted_intake())
            job.poll()
            assert snapshot(job.path.parent) == before
            assert len(job.children) == 1
        daemon.PAUSE_FILE.unlink(missing_ok=True)
    job.runner = runner
    job.health = daemon.RepoHealth()
    job.gh.labels.clear()
    job.gh.label_event(trigger, "labeled")
    module = restarted_intake()
    recover = module._recover_item_cleanup
    recovered = []

    def checked(path):
        newer_before = snapshot(newer) if replaced else None
        receipt_before = path.read_bytes()
        assert recover(path)
        assert path.read_bytes() == receipt_before
        if replaced:
            assert snapshot(newer) == newer_before
        recovered.append(path)
        return True

    monkeypatch.setattr(module, "_recover_item_cleanup", checked)
    monkeypatch.setattr(daemon, "specloop_intake", module)
    comments = list(job.gh.comments)
    job.poll()
    assert recovered
    assert read(job.directory / "cleanup.json")["complete"]
    assert request.stat().st_mtime_ns == original_mtime and job.result_path.read_bytes() == result
    assert not (job.directory / "log").exists()
    assert not job.health.failures and not job.health.item_failures
    assert job.gh.comments == comments  # Cleanup never replays the old delivery.
    assert len(job.children) == 2  # Only the normally rearmed new round starts.


@pytest.mark.scenario("background-job-control/recoverable-cleanup/replacement-retains-cleanup-authority")
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("point", ["absent", "unlink"])
def test_replacement_prepares_journal_before_new_work(delivery_job, monkeypatch, stage, point):
    job = delivery_job(stage)
    finish(job, human=True)
    with monkeypatch.context() as fault:
        interrupt(fault, job.directory, point)
        job.poll()
        old_receipt = job.path.read_bytes()
        job.gh.labels.clear()
        job.gh.label_event({"draft": "spec:draft", "admit": "spec:approved"}.get(stage, "agent:revise"), "labeled")
        before = snapshot(job.path.parent)
        job.poll()
        if point == "absent":
            assert snapshot(job.path.parent) == before and len(job.children) == 1
        else:
            assert job.path.read_bytes() != old_receipt and len(job.children) == 2
            assert not read(job.directory / "cleanup.json")["complete"]
    assert not job.health.failures and not job.health.item_failures


@pytest.mark.scenario("background-job-control/recoverable-cleanup/replacement-retains-cleanup-authority")
@pytest.mark.parametrize("damage", ["request", "result", "stop", "symlink", "live", "missing"])
def test_resume_revalidates_old_attempt(tmp_path, damage):
    import fcntl
    path, receipt, directory, _ = delivered(tmp_path)
    assert jobs.compact_delivered(path, receipt, prepare_only=True) == []
    jobs.save(path, {"newer": "receipt"})
    if damage in ("request", "result"):
        jobs.save(directory / (damage + ".json"), {})
    elif damage == "stop":
        jobs.save(directory / "stop.json", {"state": "stopped"})
    elif damage == "symlink":
        (directory / "log").unlink()
        (directory / "log").symlink_to(directory / "result.json")
    elif damage == "missing":
        (directory / "cleanup.json").unlink()
    with (directory / "lock").open() as lock:
        if damage == "live":
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = snapshot(tmp_path)
        assert jobs.resume_cleanup(path, directory.name)
        assert snapshot(tmp_path) == before


@pytest.mark.scenario("background-job-control/recoverable-cleanup/unarmed-cleanup-waits-for-authorized-eligibility")
@pytest.mark.scenario("background-job-control/recoverable-cleanup/authorization-denial-preserves-interrupted-cleanup")
@pytest.mark.parametrize("point", ["absent", "request"])
@pytest.mark.parametrize("gate", ["blocked", "denied", "lookup"])
def test_held_cleanup_does_not_hold_healthy_neighbor(delivery_job, monkeypatch, point, gate):
    from test_engine_routing import DraftGh

    job = delivery_job("draft")
    finish(job, human=True)
    with monkeypatch.context() as fault:
        interrupt(fault, job.directory, point)
        job.poll()
    job.gh.labels.clear()
    job.gh.label_event("spec:draft", "labeled", actor="outsider" if gate == "denied" else "maintainer")
    neighbor = DraftGh(("spec:draft",))
    runner = job.runner

    def github(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps(json.loads(runner(args)) + [
                {**item, "number": 9} for item in json.loads(neighbor(args))])
        if (args[:3] in (["issue", "view", "9"], ["issue", "edit", "9"], ["issue", "comment", "9"])
                or args[0] == "api" and "/issues/9" in args[1]):
            return neighbor([arg.replace("/issues/9", "/issues/7") for arg in args])
        if args[:3] == ["issue", "view", "6"]:
            return json.dumps({"state": "OPEN"})
        if gate == "lookup" and args[0] == "api" and "/issues/7/events" in args[1]:
            raise queue.GhError("events unavailable", transient=True)
        response = runner(args)
        if gate == "blocked" and args[:3] == ["issue", "view", "7"]:
            return json.dumps({**json.loads(response), "body": "blocked-by: #6"})
        return response

    job.runner = github
    before = snapshot(job.directory.parent)
    receipt = snapshot(job.path.parent)[job.path]
    comments = list(job.gh.comments)
    for _ in range(2):
        jobs._children.clear()
        job.poll()
        assert snapshot(job.directory.parent) == before
        assert snapshot(job.path.parent)[job.path] == receipt
        assert len(job.children) == 2
        neighbor_path = job.path.with_name("9.json")
        assert read(neighbor_path)["job"] != read(job.path)["job"]
    if gate in ("blocked", "lookup"):
        assert job.gh.comments == comments  # Blocker skips remain silent.


@pytest.mark.scenario("background-job-control/recoverable-cleanup/unarmed-cleanup-waits-for-authorized-eligibility")
@pytest.mark.parametrize("job_id", [None, "legacy-job"])
def test_discovery_preserves_absent_legacy_and_orphan_state(tmp_path, job_id):
    path = tmp_path / "proposals/o__r/8.json"
    assert intake._recover_item_cleanup(path) and not list(tmp_path.iterdir())
    path, receipt, directory, _ = delivered(tmp_path)
    jobs.save(path, {key: value for key, value in receipt.items() if key != "job"})
    if job_id is not None:
        jobs.save(path, {**receipt, "job": job_id})
    before = snapshot(tmp_path)
    assert intake._recover_item_cleanup(path)
    assert snapshot(tmp_path) == before  # Neither legacy delivery nor an orphan proves ownership.


@pytest.mark.scenario("background-job-control/recoverable-cleanup/unarmed-cleanup-waits-for-authorized-eligibility")
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("point", ["absent", "request"])
def test_final_trigger_removal_leaves_interrupted_cleanup_unarmed(delivery_job, monkeypatch, stage, point):
    job = delivery_job(stage)
    finish(job)
    with monkeypatch.context() as fault:
        interrupt(fault, job.directory, point)
        job.poll()
    assert read(job.path)["delivered"]
    assert not job.gh.labels.intersection({"spec:draft", "spec:approved", "agent:revise"})
    before, comments = snapshot(job.path.parent), list(job.gh.comments)
    for _ in range(2):
        jobs._children.clear()
        monkeypatch.setattr(daemon, "specloop_intake", restarted_intake())
        job.poll()
        assert snapshot(job.path.parent) == before
        assert job.gh.comments == comments and len(job.children) == 1

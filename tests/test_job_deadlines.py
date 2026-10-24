"""Deadline observation through the daemon, retaining real live job locks."""

import json
from functools import partial

import pytest

from nightshift import daemon, specloop_intake, specloop_job
from nightshift.config import Config
from test_specloop_background import background, delivery_job, _finish_delivery_job
from test_specloop_isolation import Pipeline


@pytest.mark.scenario("issue-intake/overdue-job-escalation/hung-job-escalates-after-deadline")
@pytest.mark.scenario("issue-intake/overdue-job-escalation/reporting-retries-retain-the-attempt")
@pytest.mark.parametrize("stage", ["draft", "admit", "spec_revision", "build_revision"])
@pytest.mark.parametrize("failure", [None, "comment", "edit"])
def test_hung_job_escalates_after_deadline(delivery_job, monkeypatch, stage, failure):
    job = delivery_job(stage)
    request_path = job.directory / "request.json"
    request = json.loads(request_path.read_text())
    evidence = (job.path.read_bytes(), request_path.read_bytes())
    comments_before = len(job.gh.comments)
    monkeypatch.setattr(specloop_job.time, "time", lambda: request["deadline_at"] - 1)
    job.poll()
    assert not job.health.item_failures and len(job.gh.comments) == comments_before
    github = job.runner
    failed = []

    def report(args):
        if len(args) > 1 and args[1] == failure and not failed:
            failed.append(args)
            if failure == "comment":
                github(args)  # Stored remotely before the response is lost.
            raise RuntimeError("reporting unavailable")
        return github(args)

    job.runner = report
    monkeypatch.setattr(specloop_job.time, "time", lambda: request["deadline_at"])
    job.poll()
    assert list(job.health.item_failures.values()) == [1]  # Immediate, below threshold.
    assert ("needs-human" in job.gh.labels) == (failure is None)
    if failure:
        if failure == "comment":
            assert not any("--add-label" in c and "needs-human" in c for c in job.calls)
        specloop_job._children.clear()
        job.health = daemon.RepoHealth()
        job.poll()
    assert "needs-human" in job.gh.labels
    assert len(job.gh.comments) == comments_before + 1
    body = job.gh.comments[-1]["body"]
    for detail in (job.directory.name, request["args"][0], str(request["timeout_seconds"]),
                   "elapsed", "deadline budget", str(job.directory / "log"),
                   "nightshift jobs stop", "nightshift jobs release", "Inspect"):
        assert detail in body
    assert (job.path.read_bytes(), request_path.read_bytes()) == evidence
    assert len(job.children) == 1 and job.children[0].poll() is None
    assert not job.result_path.exists()
    # Resolve the hold after late completion: collect this attempt, never launch another.
    _finish_delivery_job(job)
    job.gh.labels.remove("needs-human")
    job.poll()
    assert json.loads(job.path.read_text())["delivered"]
    assert len(job.children) == 1


@pytest.mark.scenario("issue-intake/overdue-job-escalation/control-and-completion-take-precedence")
@pytest.mark.parametrize("stage", ["draft", "admit", "spec_revision", "build_revision"])
@pytest.mark.parametrize("control", ["paused", "unauthorized", "stopping", "stopped", "finished", "crashed"])
def test_expired_job_control_and_completion(delivery_job, monkeypatch, stage, control):
    job = delivery_job(stage)
    request = json.loads((job.directory / "request.json").read_text())
    monkeypatch.setattr(specloop_job.time, "time", lambda: request["deadline_at"] + 10)
    if control == "paused":
        daemon.PAUSE_FILE.touch()
    elif control == "unauthorized":
        for event in job.gh.events:
            event["actor"]["login"] = "outsider"
    elif control in ("stopping", "stopped"):
        process = json.loads((job.directory / "process.json").read_text())
        specloop_job.request_stop(job.directory, verify_identity=lambda: process)
    if control in ("stopped", "finished", "crashed"):
        _finish_delivery_job(job)
        if control == "stopped":
            specloop_job.finish_stop(job.directory)
        elif control == "crashed":
            job.result_path.unlink()
    evidence = {p: p.read_bytes() for p in job.directory.iterdir() if p.is_file()}
    receipt = job.path.read_bytes()
    specloop_job._children.clear()
    job.poll()
    assert not any("is overdue" in c["body"] for c in job.gh.comments)
    if control == "finished":
        assert json.loads(job.path.read_text())["delivered"]
    elif control == "crashed":
        assert "job" not in json.loads(job.path.read_text())
        assert list(job.health.item_failures.values()) == [1]
        assert "needs-human" not in job.gh.labels
    else:
        assert job.path.read_bytes() == receipt
        assert {p: p.read_bytes() for p in job.directory.iterdir() if p.is_file()} == evidence
        assert not job.health.item_failures
        if control in ("paused", "unauthorized"):
            daemon.PAUSE_FILE.unlink(missing_ok=True)
            for event in job.gh.events:
                event["actor"]["login"] = "maintainer"
            job.poll()
            assert "needs-human" in job.gh.labels
    assert len(job.children) == 1


@pytest.mark.scenario("issue-intake/overdue-job-escalation/hung-job-escalates-after-deadline")
def test_overdue_does_not_starve_other_items(background, monkeypatch):
    root, repo, children = background
    pipeline = Pipeline(repo)
    pipeline.issue(1)
    monkeypatch.setattr(specloop_intake, "run_git", pipeline.command)
    poll = partial(daemon.poll_specloop, Config(repos=[repo]), daemon.RepoHealth(), runner=pipeline.gh)
    poll()
    request_path, = daemon.STATE_DIR.glob("proposals/*/1.jobs/*/request.json")
    request = json.loads(request_path.read_text())
    monkeypatch.setattr(specloop_job.time, "time", lambda: request["deadline_at"])
    pipeline.issue(2)
    poll()
    assert {"name": "needs-human"} in pipeline.issues[1]["labels"]
    assert len(children) == 2
    (root / "release").touch()
    for child in children:
        child.wait(timeout=5)
    poll()
    assert {"name": "needs-human"} in pipeline.issues[2]["labels"]  # Delivered needs_input.
    assert json.loads((daemon.STATE_DIR / "proposals/owner__sample/2.json").read_text())["delivered"]


@pytest.mark.parametrize("timing", ["missing_request", "absent", "null"])
def test_legacy_live_job_has_no_inferred_deadline(delivery_job, monkeypatch, timing):
    job = delivery_job("draft")
    path = job.directory / "request.json"
    request = {"args": json.loads(path.read_text())["args"]}
    if timing == "null":
        request.update(stage="proposals", started_at=None, timeout_seconds=None, deadline_at=None)
    specloop_job.save(path, request)
    if timing == "missing_request":
        path.unlink()
    evidence = {p: p.read_bytes() for p in (path, job.path) if p.exists()}
    monkeypatch.setattr(specloop_job.time, "time", lambda: 10**12)
    job.poll()
    assert not job.health.item_failures and "needs-human" not in job.gh.labels
    assert {p: p.read_bytes() for p in (path, job.path) if p.exists()} == evidence
    assert len(job.children) == 1

"""A stalled origin must leave the daemon able to service worker repositories."""

import json
import subprocess
import sys
import time
from functools import partial

import pytest

from manifest_fixtures import manifest_text

from nightshift import daemon, queue, specloop_intake
from nightshift.config import Config, Repo
from test_queue import FakeGh
from test_specloop_isolation import Pipeline


@pytest.mark.scenario("issue-intake/engine-per-repository/worker-repository-unchanged")
@pytest.mark.scenario("issue-intake/git-process-group-timeout/production-runner-preserves-daemon-progress")
@pytest.mark.parametrize("operation", ["push", "ls-remote", "fetch", "rev-parse", "publish"])
def test_origin_timeout_allows_worker_claim_heartbeat_harvest_and_retry(
    tmp_path, monkeypatch, caplog, operation,
):
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(daemon, "PAUSE_FILE", tmp_path / "pause")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    repo = Repo("owner/spec", engine="specloop", manifest=manifest)
    pipeline = Pipeline(repo)
    stage = "draft" if operation in ("push", "ls-remote") else "admit"
    if operation == "publish":
        pipeline.ready(1, daemon.STATE_DIR)
        stage = "publish"
    elif stage == "draft":
        pipeline.issue(1)
        if operation == "ls-remote":
            pipeline.approved(1)
            pipeline.prs[1]["headRefName"] = "spec/owner__spec-issue-1-gate"
            pipeline.prs[1]["state"] = "CLOSED"
    else:
        pipeline.approved(1)
    item = pipeline.issues[1] if stage == "draft" else pipeline.prs[1]
    original_labels = list(item["labels"])
    assert 0 < specloop_intake.POLL_COMMAND_TIMEOUT <= 30
    monkeypatch.setattr(specloop_intake, "POLL_COMMAND_TIMEOUT", .05)
    real_run, real_git = subprocess.run, specloop_intake.run_git
    stalled = True
    timeouts = []

    def command(args, **kwargs):
        if stalled and operation in args:
            # Exercise the production group runner's timeout, without contacting git
            # or GitHub. The finite sleep also bounds a timeout regression.
            start = time.monotonic()
            try:
                run = real_git if args[0] == "git" else real_run
                return run([sys.executable, "-c", "import time; time.sleep(2)"], **kwargs)
            except subprocess.TimeoutExpired:
                timeouts.append(time.monotonic() - start)
                raise
        return pipeline.command(args, **kwargs)

    monkeypatch.setattr(specloop_intake, "run_git", command)
    poll = daemon.poll_specloop
    health_seen = []

    def intake(cfg, health):
        health_seen.append(health)
        poll(cfg, health, runner=pipeline.gh, specloop_runner=command)

    monkeypatch.setattr(daemon, "poll_specloop", intake)
    monkeypatch.setattr(daemon, "startup", lambda *a: None)
    monkeypatch.setattr(daemon, "_note_unclean_exit", lambda *a: None)
    monkeypatch.setattr(daemon.notify, "send", lambda *a: None)
    worker_gh = FakeGh({"agent:ready": [7]})
    monkeypatch.setattr(queue, "claim", partial(queue.claim, runner=worker_gh))
    claim = daemon.claim_next
    claims, beats = [], []
    real_beat, real_harvest = daemon.beat, daemon._harvest

    def beat(*args):
        real_beat(*args)
        beats.append(json.loads(daemon.HEARTBEAT_FILE.read_text()))

    def claim_worker(*args, **kwargs):
        nonlocal stalled
        if not claims:
            assert len(beats) == 2  # Startup and the first poll.
            assert len(timeouts) == 1 and timeouts[0] < 1
            assert health_seen[-1].item_failures == {(repo.name, stage, 1): 1}
            assert health_seen[-1].failures == {repo.name: 1}
            assert item["labels"] == original_labels  # Still armed for retry.
            assert not item["comments"] or stage == "publish"  # Admission comment only.
            stalled = False
        result = claim(*args, **kwargs)
        if result:
            claims.append(result)
        return result

    def worker(cfg, claimed, tally):
        assert claimed.repo.engine == "worker"
        tally.shipped += 1

    class Stop(Exception):
        pass

    def harvest(in_flight, tally):
        result = real_harvest(in_flight, tally)
        if tally.shipped:
            raise Stop
        return result

    monkeypatch.setattr(daemon, "beat", beat)
    monkeypatch.setattr(daemon, "claim_next", claim_worker)
    monkeypatch.setattr(daemon, "run_claimed", worker)
    monkeypatch.setattr(daemon, "_harvest", harvest)
    cfg = Config(repos=[repo, Repo("owner/worker", verify="true")], poll_seconds=.01)
    with pytest.raises(Stop):
        daemon.loop(cfg, {"owner/worker": tmp_path})
    assert len(claims) == 1 and claims[0].issue.number == 7
    assert queue.claim_path("owner/worker", 7).exists()
    assert len(beats) >= 3
    assert any("agent:working" in edit and "agent:ready" in edit for edit in worker_gh.edits())
    assert "timed out" in caplog.text and "retry on the next poll" in caplog.text

    # Recovery reuses the durable draft/publication receipt and delivers once.
    for _ in range(2):
        poll(cfg, health_seen[-1], runner=pipeline.gh, specloop_runner=command)
    assert not health_seen[-1].failures and not health_seen[-1].item_failures
    directory = {"draft": "proposals", "admit": "admissions", "publish": "publications"}[stage]
    receipt = json.loads((daemon.STATE_DIR / directory / "owner__spec/1.json").read_text())
    assert receipt["delivered"]
    assert len(item["comments"]) == (2 if stage == "publish" else 1)
    if stage == "draft":
        assert sum(call[:2] == ["specloop", "propose"] for call in pipeline.calls) == 1


@pytest.mark.scenario("issue-intake/engine-per-repository/worker-repository-unchanged")
def test_publication_timeout_after_remote_success_reconciles(tmp_path):
    """Losing the response must not publish again or lose the human checks."""
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    pipeline = Pipeline(Repo("owner/spec", engine="specloop", manifest=manifest))
    pipeline.ready(1, tmp_path)
    publications = []

    def command(args, **kwargs):
        result = pipeline.command(args, **kwargs)
        if args[:2] == ["specloop", "publish"]:
            publications.append(args)
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return result

    publish = partial(specloop_intake.publish_pending, pipeline.repo, queue.Labels(), tmp_path,
                      runner=pipeline.gh, specloop_runner=command)
    with pytest.raises(RuntimeError, match="timed out.*retry"):
        publish()
    assert publish()[0]["outcome"] == "published"
    assert publish() == []
    assert len(publications) == 1
    assert "Audition the gate" in pipeline.prs[1]["comments"][-1]["body"]

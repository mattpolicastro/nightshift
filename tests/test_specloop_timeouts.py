"""A stalled origin must leave independent specloop work and daemon upkeep live."""

import json
import os
import signal
import subprocess
import sys
import time
from functools import partial

import pytest

from manifest_fixtures import manifest_text

from nightshift import daemon, queue, specloop_intake, specloop_job
from nightshift.config import Config, Repo
from test_specloop_isolation import Pipeline


@pytest.mark.scenario("worker-retirement/sole-execution-engine/polling-remains-responsive")
@pytest.mark.scenario("issue-intake/git-process-group-timeout/git-timeout-kills-process-group")
@pytest.mark.scenario("issue-intake/git-process-group-timeout/production-runner-preserves-daemon-progress")
@pytest.mark.parametrize("operation", ["push", "ls-remote", "fetch", "rev-parse", "publish"])
def test_origin_timeout_allows_specloop_heartbeat_reap_and_retry(
    tmp_path, monkeypatch, caplog, operation,
):
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(daemon, "PAUSE_FILE", tmp_path / "pause")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    repo = Repo("owner/spec", engine="specloop", manifest=manifest)
    pipeline = Pipeline(repo)
    peer_manifest = tmp_path / "peer.toml"
    peer_manifest.write_text(manifest_text(tmp_path / "peer"))
    peer = Pipeline(Repo("owner/peer", engine="specloop", manifest=peer_manifest))
    peer.issue(7)
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
    original_comments = list(item["comments"])
    directory = {"draft": "proposals", "admit": "admissions", "publish": "publications"}[stage]
    receipt_path = daemon.STATE_DIR / directory / "owner__spec/1.json"
    peer_path = daemon.STATE_DIR / "proposals/owner__peer/7.json"
    assert 0 < specloop_intake.POLL_COMMAND_TIMEOUT <= 30
    monkeypatch.setattr(specloop_intake, "POLL_COMMAND_TIMEOUT", .05)
    real_run, real_git, real_popen = subprocess.run, specloop_intake.run_git, subprocess.Popen
    stalled = True
    timeouts, timed_children = [], []

    def launch(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        timed_children.append((child, kwargs))
        return child

    monkeypatch.setattr(subprocess, "Popen", launch)

    def command(args, **kwargs):
        if args[:2] == ["specloop", "status"]:
            return subprocess.CompletedProcess(args, 0, json.dumps({
                "version": 1, "runs": [*pipeline.runs.values(), *peer.runs.values()],
            }), "")
        if args[0] == "git":
            target = peer if args[2] == str(tmp_path / "peer") else pipeline
        else:
            change = args[2] if args[1] == "publish" else args[args.index("--change") + 1]
            target = peer if change.startswith("owner__peer-") else pipeline
        if stalled and target is pipeline and operation in args:
            # Keep the real timeout boundary and production git group runner.
            # Only the external command is substituted; no origin is contacted.
            assert kwargs["timeout"] == .05
            start = time.monotonic()
            try:
                run = real_git if args[0] == "git" else real_run
                return run([sys.executable, "-c", "import time; time.sleep(2)"], **kwargs)
            except subprocess.TimeoutExpired as exc:
                assert exc.timeout == .05
                timeouts.append(time.monotonic() - start)
                raise
        return target.command(args, **kwargs)

    def gh(args):
        name = ("/".join(args[1].split("/")[1:3]) if args[0] == "api"
                else args[args.index("--repo") + 1])
        assert name in (repo.name, peer.repo.name)
        return (pipeline if name == repo.name else peer).gh(args)

    monkeypatch.setattr(specloop_intake, "run_git", command)
    monkeypatch.setattr(daemon, "startup", lambda *a: None)
    monkeypatch.setattr(daemon, "_note_unclean_exit", lambda *a: None)
    monkeypatch.setattr(daemon.notify, "send", lambda *a: None)
    real_poll, real_beat, real_reap = daemon.poll_specloop, daemon.beat, specloop_job.reap
    health_seen, beats, reaps = [], [], []
    retained = {}
    release = tmp_path / "release"
    # A completed detached child must be reaped by service polling, not by the
    # test waiting on it. Release it only after observing the failed intake.
    child = real_popen([sys.executable, "-c", """
import pathlib, sys, time
release = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 10
while not release.exists() and time.monotonic() < deadline:
    time.sleep(.01)
sys.exit(0 if release.exists() else 1)
""", str(release)])
    child_key = str(tmp_path / "completed-job")
    monkeypatch.setattr(specloop_job, "_children", {child_key: child})
    deadline = time.monotonic() + 5

    def beat(*args):
        real_beat(*args)
        beats.append(json.loads(daemon.HEARTBEAT_FILE.read_text()))

    def reap():
        before = dict(specloop_job._children)
        real_reap()
        removed = set(before) - set(specloop_job._children)
        assert all(before[key].returncode is not None for key in removed)
        reaps.append((len(beats), removed))

    class Stop(Exception):
        pass

    def intake(cfg, health):
        nonlocal stalled
        assert time.monotonic() < deadline, "daemon did not progress after intake timeout"
        real_poll(cfg, health, runner=gh, specloop_runner=command)
        health_seen.append(health)
        if len(health_seen) == 1:
            assert len(beats) == 2  # Startup and the first poll.
            assert len(timeouts) == 1 and .05 <= timeouts[0] < 1
            assert health.item_failures == {(repo.name, stage, 1): 1}
            assert health.failures == {repo.name: 1}
            assert item["labels"] == original_labels  # Still armed for retry.
            assert item["comments"] == original_comments
            if stage in ("draft", "publish"):
                retained.update(json.loads(receipt_path.read_text()))
                assert not retained.get("delivered")
                if stage == "draft":
                    assert retained["result"]["outcome"] == "drafted"
                else:
                    assert retained["checks"] and "result" not in retained
            else:
                assert not receipt_path.exists()  # Fetch failed before admission.
            # The later repository delivers in the very same failed poll.
            assert json.loads(peer_path.read_text())["delivered"]
            assert not peer.issues[7]["labels"]
            assert len(peer.issues[7]["comments"]) == len(peer.prs) == 1
            assert child_key in specloop_job._children and child.poll() is None
            release.touch()
            stalled = False
        elif (len(health_seen) >= 3 and child_key not in specloop_job._children):
            assert not health.failures and not health.item_failures
            raise Stop

    monkeypatch.setattr(daemon, "beat", beat)
    monkeypatch.setattr(specloop_job, "reap", reap)
    monkeypatch.setattr(daemon, "poll_specloop", intake)
    cfg = Config(repos=[repo, peer.repo], poll_seconds=.01)
    try:
        with pytest.raises(Stop):
            daemon.loop(cfg, {})
        assert child.returncode == 0
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        release.touch()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
    assert len(beats) >= 4
    assert all(beat["pid"] == os.getpid() for beat in beats)
    assert len({beat["started_at"] for beat in beats}) == 1
    assert all(a["last_poll"] < b["last_poll"] for a, b in zip(beats, beats[1:]))
    assert {number for number, _ in reaps} >= set(range(2, len(beats) + 1))
    assert any(number >= 3 and child_key in removed for number, removed in reaps)
    assert sum(child_key in removed for _, removed in reaps) == 1
    assert not daemon.HEARTBEAT_FILE.exists()
    assert not list(queue.CLAIM_DIR.rglob("*.json"))
    timed_child, options = timed_children[0]
    assert len(timed_children) == len(timeouts) == 1
    assert timed_child.returncode == -signal.SIGKILL
    if operation != "publish":
        assert options["start_new_session"] is True
    with pytest.raises(ChildProcessError):
        os.waitpid(timed_child.pid, os.WNOHANG)
    assert "timed out" in caplog.text and "retry on the next poll" in caplog.text

    # Recovery reuses durable results/identity; later polls deliver neither item twice.
    for _ in range(2):
        real_poll(cfg, health_seen[-1], runner=gh, specloop_runner=command)
    assert not health_seen[-1].failures and not health_seen[-1].item_failures
    receipt = json.loads(receipt_path.read_text())
    assert receipt["delivered"]
    assert all(receipt[key] == value for key, value in retained.items())
    assert len(item["comments"]) == (2 if stage == "publish" else 1)
    assert not item["labels"]
    assert len(peer.issues[7]["comments"]) == len(peer.prs) == 1
    peer_receipt = json.loads(peer_path.read_text())
    assert peer_receipt["delivered"] and peer_receipt["result"]["outcome"] == "drafted"
    assert "Spec draft ready" in peer.issues[7]["comments"][0]["body"]
    assert sum(call[:2] == ["specloop", "propose"] for call in peer.calls) == 1
    assert sum(call[:2] == ["pr", "create"] for call in peer.calls) == 1
    if stage == "draft":
        assert sum(call[:2] == ["specloop", "propose"] for call in pipeline.calls) == 1
        assert sum(call[:2] == ["pr", "create"] for call in pipeline.calls) == 1
    else:
        assert sum(call[:2] == ["specloop", "admit"] for call in pipeline.calls) == 1
        if stage == "publish":
            assert sum(call[:2] == ["specloop", "publish"] for call in pipeline.calls) == 1


@pytest.mark.scenario("issue-intake/git-process-group-timeout/production-runner-preserves-daemon-progress")
def test_publication_timeout_after_remote_success_reconciles(tmp_path):
    """Losing the response must not publish again or lose the human checks."""
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    pipeline = Pipeline(Repo("owner/spec", engine="specloop", manifest=manifest))
    pipeline.ready(1, tmp_path)
    item = pipeline.prs[1]
    labels, comments = list(item["labels"]), list(item["comments"])
    admission_path = tmp_path / "admissions/owner__spec/1.json"
    admission = admission_path.read_bytes()
    receipt_path = tmp_path / "publications/owner__spec/1.json"
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
    pending = json.loads(receipt_path.read_text())
    assert not pending.get("delivered") and "result" not in pending
    assert pending["checks"] and pending["round_id"]
    assert item["labels"] == labels and item["comments"] == comments
    assert pipeline.runs[pending["change"]]["pause"]["reason"] == "human_checks"
    assert admission_path.read_bytes() == admission

    assert publish()[0]["outcome"] == "published"
    delivered = json.loads(receipt_path.read_text())
    assert delivered["delivered"]
    assert delivered["result"] == {"outcome": "published", "pr": item["url"]}
    assert all(delivered[key] == value for key, value in pending.items())
    assert pending["checks"] in item["comments"][-1]["body"]
    assert item["labels"] == labels
    assert len(item["comments"]) == len(comments) + 1
    delivered_comments = list(item["comments"])
    saved_delivery = receipt_path.read_bytes()
    assert publish() == []
    assert receipt_path.read_bytes() == saved_delivery
    assert admission_path.read_bytes() == admission
    assert item["labels"] == labels and item["comments"] == delivered_comments
    assert len(publications) == 1
    assert "Audition the gate" in pipeline.prs[1]["comments"][-1]["body"]

"""Real background processes; fake GitHub/git and a blocking specloop CLI."""

import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightshift import daemon, queue, specloop_job
from nightshift.config import Config, Repo
from specloop import serve, state
from specloop.state import Run
from test_engine_routing import DraftGh
from test_queue import FakeGh
from test_specloop_approval import ApprovalGh, HEAD
from test_specloop_command_json import BRANCH, CHANGE, git, template
from test_specloop_isolation import Pipeline, CHECKS
from test_specloop_spec_revision import RevisionGh


# Keep tests bounded even if a regression waits synchronously for the CLI.
CLI = '''
import json, os, sys, time
from pathlib import Path
root = Path(os.environ["TEST_INTAKE_ROOT"])
if sys.argv[1] == "status":
    print(json.dumps({"version": 1, "runs": []}))
    sys.exit(0)
with (root / "started").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
deadline = time.monotonic() + 15
while not (root / "release").exists():
    if time.monotonic() > deadline:
        (root / "timed-out").touch()
        break
    time.sleep(.01)
result = json.loads((root / "cli-result.json").read_text())
(root / "finished").touch()
print(json.dumps(result))
sys.exit(int(result.get("outcome") in ("refused", "needs_input", "rejected")))
'''


def eventually(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "background process did not reach its checkpoint"
        time.sleep(.01)


@pytest.fixture
def background(tmp_path, monkeypatch):
    executable = tmp_path / "specloop"
    executable.write_text(f"#!{sys.executable}\n" + CLI)
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    # Verification runs from a writable build directory beside the source.
    # The child needs the package under test, not the caller's working directory
    # (or an older installed copy). Exercise that layout even in local runs.
    source = str(Path(specloop_job.__file__).resolve().parent.parent)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        [source, *filter(None, os.environ.get("PYTHONPATH", "").split(os.pathsep))]
    ))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TEST_INTAKE_ROOT", str(tmp_path))
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(f'[run]\nrepo = {json.dumps(str(tmp_path / "source"))}\n')
    repo = Repo("owner/sample", engine="specloop", manifest=manifest)
    children = []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", launch)
    (tmp_path / "cli-result.json").write_text(json.dumps({
        "version": 1, "outcome": "needs_input", "questions": ["Which voltage?"],
    }))
    yield tmp_path, repo, children
    (tmp_path / "release").touch()
    for child in children:
        child.wait(timeout=5)
    specloop_job.reap()


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.parametrize("checkpoint", ["cleanup", "feasibility", "state", "refusal_cleanup"])
def test_same_branch_background_admissions_share_run_lock(template, background, checkpoint):
    """Two PR jobs use the real CLI and contend before admission commits state."""
    from nightshift import specloop_intake

    path, source = template
    root, _, children = background
    repo = Repo("owner/sample", engine="specloop", manifest=path)
    remote = root / "remote.git"
    git(source, "clone", "-q", "--bare", str(source), str(remote))
    git(source, "remote", "add", "origin", str(remote))
    pipeline = Pipeline(repo)
    pipeline.approved(1)
    pipeline.prs[1].update(headRefName=BRANCH, headRefOid=git(source, "rev-parse", BRANCH))
    run = Run.for_change(CHANGE)
    run.dir.mkdir(parents=True)
    (run.dir / "stale-clone").mkdir()
    (run.dir / "stale-clone" / "partial").touch()
    (run.dir / "lock").touch()
    lock_inode = (run.dir / "lock").stat().st_ino

    # Only the external feasibility/validator boundaries are faked. The CLI,
    # clone, cleanup, flock, state write and intake job lifecycle are real.
    (root / "checkpoint").write_text(checkpoint)
    (root / "specloop").write_text(f"#!{sys.executable}\n" + '''
import os, sys, time
from pathlib import Path
from specloop import cli, state
root = Path(os.environ["TEST_INTAKE_ROOT"])
state.ROOT = root / "home"
checkpoint = (root / "checkpoint").read_text()
args = sys.argv[1:]
owner = "--pr" in args and args[args.index("--pr") + 1].endswith("/1")
def pause(stage):
    if not owner or checkpoint != stage or (root / "release").exists():
        return
    run_dir = state.runs_dir() / args[args.index("--change") + 1]
    artifact = run_dir / "live-artifact"
    artifact.write_text("live admission evidence")
    (root / "started").touch()
    deadline = time.monotonic() + 15
    while not (root / "release").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("test admission was not released")
        time.sleep(.01)
    assert artifact.read_text() == "live admission evidence"
    artifact.unlink()
clear = cli._clear_admission
clears = 0
def cleanup(run):
    global clears
    clear(run)
    clears += 1
    pause("cleanup" if clears == 1 else "refusal_cleanup")
cli._clear_admission = cleanup
cli.problems_with = lambda *args: []
def feasibility(*args):
    pause("feasibility")
    if owner and checkpoint == "refusal_cleanup" and not (root / "release").exists():
        raise ValueError("test refusal")
    return {"contradictions": [], "risks": []}
cli.feasibility.check = feasibility
save = state.Run.save
def save_state(run):
    pause("state")
    save(run)
state.Run.save = save_state
sys.exit(cli.main(args))
''')

    def poll():
        return specloop_intake.admit_pending(
            repo, queue.Labels(), daemon.STATE_DIR, runner=pipeline.gh,
        )

    poll()
    eventually(lambda: (root / "started").exists())
    assert not (run.dir / "state.json").exists()
    assert not (run.dir / "stale-clone").exists()
    pipeline.approved(2)
    pipeline.prs[2].update(headRefName=BRANCH, headRefOid=pipeline.prs[1]["headRefOid"])
    poll()  # The second PR has a different job lock but the same change lock.
    second_receipt = daemon.STATE_DIR / "admissions/owner__sample/2.json"
    second_job = json.loads(second_receipt.read_text())["job"]
    result_path = second_receipt.parent / "2.jobs" / second_job / "result.json"
    eventually(result_path.exists)
    poll()
    first_receipt = daemon.STATE_DIR / "admissions/owner__sample/1.json"
    first_job = json.loads(first_receipt.read_text())["job"]
    assert not (first_receipt.parent / "1.jobs" / first_job / "result.json").exists()
    assert (run.dir / "live-artifact").read_text() == "live admission evidence"
    assert (run.dir / "lock").stat().st_ino == lock_inode
    with run.lock() as mine:
        assert not mine
    refused = json.loads(second_receipt.read_text())["result"]
    assert refused["outcome"] == "refused"
    assert "another process" in refused["reasons"][0]
    assert {"name": "needs-human"} in pipeline.prs[2]["labels"]
    assert "build has started" not in pipeline.prs[2]["comments"][0]["body"]

    (root / "release").touch()
    for child in children:
        child.wait(timeout=5)
    poll()
    if checkpoint == "refusal_cleanup":
        assert list(run.dir.iterdir()) == [run.dir / "lock"]
        pipeline.prs[1]["labels"] = [{"name": "spec:approved"}]
        poll()
        children[-1].wait(timeout=5)
        poll()
    poll()
    assert Run.for_change(CHANGE).state.pr == pipeline.prs[1]["url"]
    assert git(run.dir / "repo.git", "rev-parse", BRANCH) == pipeline.prs[1]["headRefOid"]
    assert sum("build has started" in c["body"] for c in pipeline.prs[1]["comments"]) == 1
    assert not pipeline.prs[1]["labels"]
    assert (run.dir / "lock").stat().st_ino == lock_inode
    with run.lock() as mine:
        assert mine


@pytest.mark.scenario("issue-intake/engine-per-repository/worker-repository-unchanged")
def test_long_propose_allows_worker_claim_heartbeat_and_harvest(background, monkeypatch):
    root, repo, _ = background
    gh = DraftGh(("spec:draft",))
    worker_gh = FakeGh({"agent:ready": [7]})
    cfg = Config(repos=[repo, Repo("owner/worker", verify="true")], poll_seconds=.01)
    monkeypatch.setattr(queue, "_gh", gh)
    monkeypatch.setattr(daemon, "startup", lambda *a: None)
    monkeypatch.setattr(daemon, "_note_unclean_exit", lambda *a: None)
    monkeypatch.setattr(daemon.notify, "send", lambda *a: None)
    monkeypatch.setattr(queue, "claim", partial(queue.claim, runner=worker_gh))
    claim = daemon.claim_next
    claims, beats, harvested = [], [], []
    real_beat, real_harvest = daemon.beat, daemon._harvest

    def beat(*args):
        real_beat(*args)
        beats.append(json.loads(daemon.HEARTBEAT_FILE.read_text()))

    def claim_worker(*args, **kwargs):
        eventually(lambda: (root / "started").exists())
        # This is still the first poll: the spec repo precedes the worker repo.
        assert len(beats) == 2  # startup and this poll
        assert not (root / "finished").exists()
        result = claim(*args, **kwargs)
        claims.append(result)
        return result

    def run_worker(cfg, claimed, tally):
        assert claimed.repo.engine == "worker"
        tally.shipped += 1

    class Stop(Exception):
        pass

    def harvest(in_flight, tally):
        result = real_harvest(in_flight, tally)
        if tally.shipped and not harvested:
            assert not (root / "finished").exists()
            harvested.append(tally.shipped)
            raise Stop
        return result

    monkeypatch.setattr(daemon, "claim_next", claim_worker)
    monkeypatch.setattr(daemon, "run_claimed", run_worker)
    monkeypatch.setattr(daemon, "beat", beat)
    monkeypatch.setattr(daemon, "_harvest", harvest)
    with pytest.raises(Stop):
        daemon.loop(cfg, {"owner/worker": root})
    assert len(claims) == 1 and claims[0].issue.number == 7
    assert queue.claim_path("owner/worker", 7).exists()
    assert harvested and len(beats) >= 3
    assert any("agent:working" in edit and "agent:ready" in edit for edit in worker_gh.edits())
    receipt = json.loads((daemon.STATE_DIR / "proposals/owner__sample/7.json").read_text())
    assert "job" in receipt and "result" not in receipt
    assert len((root / "started").read_text().splitlines()) == 1
    assert gh.labels == {"spec:draft"}


@pytest.mark.parametrize("command,outcome", [
    ("propose", "needs_input"), ("propose", "drafted"),
    ("admit", "refused"), ("admit", "admitted"),
])
def test_jobs_survive_polls_and_restart_then_deliver_once(background, monkeypatch, command, outcome):
    root, repo, children = background
    change = "sample-issue-7-add-a-gate"
    result = {"version": 1, "outcome": outcome, "branch": f"spec/{change}",
              "commit": HEAD, "head": HEAD, "questions": ["Which voltage?"],
              "reasons": ["Contradictory voltage."], "risks": []}
    (root / "cli-result.json").write_text(json.dumps(result))
    gh = DraftGh(("spec:draft",)) if command == "propose" else ApprovalGh()
    original_run = subprocess.run

    def run(args, **kwargs):
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, HEAD, "")
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    cfg, health = Config(repos=[repo]), daemon.RepoHealth()
    poll = partial(daemon.poll_specloop, cfg, health, runner=gh)
    poll()
    eventually(lambda: (root / "started").exists())
    path = daemon.STATE_DIR / ("proposals/owner__sample/7.json" if command == "propose"
                               else "admissions/owner__sample/8.json")
    saved = path.read_text()
    assert "result" not in json.loads(saved)
    for _ in range(3):
        poll()
    # Forget process handles as a daemon restart would; the file lock and
    # receipt alone must prevent duplicates and recover the completed result.
    specloop_job._children.clear()
    poll()
    assert path.read_text() == saved
    assert len((root / "started").read_text().splitlines()) == 1
    assert not health.failures
    assert not gh.prs and not any("--remove-label" in call for call in gh.calls)

    daemon.PAUSE_FILE.touch()
    (root / "release").touch()
    for child in children:
        child.wait(timeout=5)
    before = list(gh.calls)
    poll()
    assert gh.calls == before and path.read_text() == saved
    daemon.PAUSE_FILE.unlink()
    poll()
    poll()
    receipt = json.loads(path.read_text())
    assert receipt["delivered"]
    assert receipt["result"]["outcome"] == outcome
    assert not health.failures
    assert len((root / "started").read_text().splitlines()) == 1
    if command == "propose":
        assert gh.labels == ({"needs-human"} if outcome == "needs_input" else set())
        assert len(gh.comments) == 2
        assert len(gh.prs) == int(outcome == "drafted")
    else:
        assert gh.labels == ({"needs-human", "spec:review"} if outcome == "refused" else set())
        assert len(gh.comments) == 1
        assert ("Admission refused" if outcome == "refused" else "build has started") in gh.comments[0]["body"]


@pytest.mark.scenario("background-job-control/durable-cancellation/stopped-job-stays-stopped")
@pytest.mark.scenario("background-job-control/durable-cancellation/stop-wins-completion-race")
@pytest.mark.parametrize("stage", ["proposals", "admissions", "revisions"])
@pytest.mark.parametrize("late_result", ["success", "error", "missing"])
def test_cancelled_jobs_remain_pending_after_restart_and_resume(
    background, monkeypatch, stage, late_result,
):
    root, repo, children = background
    gh = (DraftGh(("spec:draft",)) if stage == "proposals" else
          ApprovalGh() if stage == "admissions" else RevisionGh())
    result = {"version": 1, "outcome": "admitted" if stage == "admissions" else "drafted",
              "branch": BRANCH, "commit": HEAD, "head": HEAD}
    if late_result == "error":
        result = {"version": 1, "error": "late CLI failure"}
    (root / "cli-result.json").write_text(json.dumps(result))
    original_run = subprocess.run

    def run(args, **kwargs):
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, HEAD, "")
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    notifications = []
    monkeypatch.setattr(daemon.notify, "send", lambda *args: notifications.append(args))
    cfg, health = Config(repos=[repo]), daemon.RepoHealth()

    def poll():
        daemon.poll_specloop(cfg, health, runner=gh)

    poll()
    eventually(lambda: (root / "started").exists())
    number = 7 if stage == "proposals" else 8
    path = daemon.STATE_DIR / stage / "owner__sample" / f"{number}.json"
    saved = path.read_bytes()
    receipt = json.loads(saved)
    directory = path.parent / f"{number}.jobs" / receipt["job"]
    # Drafting payloads share .jobs with attempt directories (see #12).
    idea = directory.parent / f"idea-{'f' * 32}.txt"
    idea.write_text("Drafting input fixture\n", encoding="utf-8")
    process = json.loads((directory / "process.json").read_text())
    # The subprocess is held behind the fixture's release-file barrier.
    # Let it finish to exercise a late result from the real wrapper after
    # accepted intent. Process-group signalling is covered in the job tests.
    assert specloop_job.request_stop(directory, verify_identity=lambda: process) == "stopping"
    intent = (directory / "stop.json").read_bytes()
    labels, comments, prs = set(gh.labels), list(gh.comments), list(gh.prs)

    def assert_held(expected_state):
        assert path.read_bytes() == saved
        assert gh.labels == labels and gh.comments == comments and gh.prs == prs
        assert not health.failures and not health.item_failures and not health.announced
        assert not notifications
        assert len((root / "started").read_text().splitlines()) == 1
        assert len([p for p in directory.parent.iterdir() if p.is_dir()]) == 1
        assert idea.read_text(encoding="utf-8") == "Drafting input fixture\n"
        assert not (directory / "collected.json").exists()
        rows, errors = specloop_job.list_jobs(daemon.STATE_DIR)
        assert not errors and len(rows) == 1 and rows[0].state == expected_state

    for _ in range(4):
        poll()
        assert_held("stopping")
    specloop_job._children.clear()
    health = daemon.RepoHealth()
    poll()
    assert_held("stopping")
    daemon.PAUSE_FILE.touch()
    (root / "release").touch()
    for child in children:
        child.wait(timeout=5)
    assert (directory / "result.json").exists()
    assert (directory / "stop.json").read_bytes() == intent
    if late_result == "missing":
        (directory / "result.json").unlink()
    poll()
    assert_held("stopping")
    # Confirmation is recorded only after the real wrapper and CLI exited.
    specloop_job.finish_stop(directory)
    stopped = (directory / "stop.json").read_bytes()
    assert json.loads(stopped)["stopped_at"]
    evidence = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    # Restart while paused, resume with the original arming labels, then
    # restart once more. None of these transitions releases a confirmed stop.
    for paused in (True, False, False):
        if not paused:
            daemon.PAUSE_FILE.unlink(missing_ok=True)
        specloop_job._children.clear()
        health = daemon.RepoHealth()
        for _ in range(5):
            poll()
            assert_held("stopped")
            assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == evidence

    # Reapplying a trigger label is not manual receipt recovery and must not
    # release the local hold documented for operators.
    gh.labels.clear()
    poll()
    gh.labels.update(labels)
    specloop_job._children.clear()
    health = daemon.RepoHealth()
    poll()
    assert_held("stopped")
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == evidence


@pytest.mark.parametrize("failure", ["invalid_result", "dead_child", "missing_executable"])
def test_failed_background_job_reports_health_before_retry(background, monkeypatch, failure):
    root, repo, children = background
    gh = DraftGh(("spec:draft",))
    health = daemon.RepoHealth()
    poll = partial(daemon.poll_specloop, Config(repos=[repo]), health, runner=gh)
    if failure == "invalid_result":
        (root / "cli-result.json").write_text('{"version":1,"outcome":"wrong"}')
        (root / "release").touch()
    elif failure == "missing_executable":
        (root / "specloop").unlink()
        # Ensure there is no real specloop elsewhere on PATH.
        monkeypatch.setenv("PATH", str(root))
    if failure == "dead_child":
        launch = subprocess.Popen

        def die(args, **kwargs):
            # Exit without running the wrapper, deterministically leaving a
            # dead job rather than racing the CLI's inherited lock.
            return launch([sys.executable, "-c", "pass"], **kwargs)

        monkeypatch.setattr(subprocess, "Popen", die)
    poll()
    children[0].wait(timeout=5)
    poll()
    assert health.failures == {repo.name: 1}
    assert gh.labels == {"spec:draft"}
    assert not gh.prs and len(gh.comments) == 1
    receipt = json.loads((daemon.STATE_DIR / "proposals/owner__sample/7.json").read_text())
    assert "job" not in receipt and "result" not in receipt


@pytest.mark.scenario("paused-job-delivery/pause-gates-launches-and-delivery/pause-before-launch")
@pytest.mark.parametrize("command", ["propose", "admit", "revise"])
def test_pause_immediately_before_launch_stops_new_job(background, monkeypatch, command):
    root, repo, _ = background
    gh = (DraftGh(("spec:draft",)) if command == "propose" else
          ApprovalGh() if command == "admit" else RevisionGh())
    labels = set(gh.labels)
    original_run = subprocess.run

    def trusted_author(*args):
        # Pause after intake reads, so the launch guard itself must stop Popen.
        if command == "propose":
            daemon.PAUSE_FILE.touch()
        return True

    monkeypatch.setattr(daemon.specloop_intake, "_issue_author_trusted", trusted_author)

    def run(args, **kwargs):
        if args[0] == "git":
            if "rev-parse" in args:
                daemon.PAUSE_FILE.touch()
            return subprocess.CompletedProcess(args, 0, HEAD, "")
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh)
    assert daemon.PAUSE_FILE.exists()
    assert not (root / "started").exists()
    assert not specloop_job._children
    assert not health.failures and not health.item_failures and not health.announced
    stage = {"propose": "proposals", "admit": "admissions", "revise": "revisions"}[command]
    path = daemon.STATE_DIR / stage / "owner__sample" / ("7.json" if command == "propose" else "8.json")
    assert "job" not in json.loads(path.read_text())
    assert gh.labels == labels
    assert not any("--remove-label" in call or "--add-label" in call for call in gh.calls)


@pytest.mark.scenario("paused-job-delivery/pause-gates-launches-and-delivery/pause-before-launch")
@pytest.mark.parametrize("stage", ["draft", "admit", "revise"])
def test_pause_at_poll_entry_stops_new_job(background, monkeypatch, stage):
    root, repo, children = background
    gh = (DraftGh(("spec:draft",)) if stage == "draft" else
          ApprovalGh() if stage == "admit" else RevisionGh())
    labels = set(gh.labels)

    def unexpected(*args, **kwargs):
        pytest.fail("paused poll dispatched an external operation")

    monkeypatch.setattr(subprocess, "run", unexpected)
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    daemon.PAUSE_FILE.touch()
    health = daemon.RepoHealth()
    daemon.poll_specloop(Config(repos=[repo]), health, runner=gh)
    assert not gh.calls and gh.labels == labels
    assert not children and not (root / "started").exists()
    assert not list(daemon.STATE_DIR.rglob("*.json"))
    assert not health.failures and not health.item_failures and not health.announced


@pytest.fixture
def delivery_job(background, monkeypatch):
    """A real blocked job with recorded, entirely local delivery boundaries."""
    root, repo, children = background
    original_run = subprocess.run

    def unexpected_stop(*args, **kwargs):
        pytest.fail("pause implicitly terminated a background job")

    monkeypatch.setattr(os, "kill", unexpected_stop)
    monkeypatch.setattr(os, "killpg", unexpected_stop)

    def start(stage):
        gh = (DraftGh(("spec:draft",)) if stage == "draft" else
              ApprovalGh() if stage == "admit" else RevisionGh())
        change = "sample-issue-7-add-a-gate"
        result = {"version": 1, "outcome": "admitted" if stage == "admit" else "drafted",
                  "branch": f"spec/{change}", "commit": HEAD, "head": HEAD}
        runs = []
        if stage == "build_revision":
            result["outcome"] = "revised"
            runs = [{"change": change, "pr": gh.pr_url, "status": "running"}]
        (root / "cli-result.json").write_text(json.dumps(result))
        job = SimpleNamespace(root=root, gh=gh, children=children, result=result,
                              health=daemon.RepoHealth(), calls=[], runs=runs,
                              after_call=lambda args: None)

        def github(args):
            job.calls.append(["gh", *args])
            response = gh(args)
            job.after_call(job.calls[-1])
            return response

        def run(args, **kwargs):
            if args[0] == "gh":
                return subprocess.CompletedProcess(args, 0, github(args[1:]), "")
            if args[0] == "git" or args[:2] == ["specloop", "status"]:
                job.calls.append(args)
                output = HEAD if args[0] == "git" else json.dumps({"version": 1, "runs": job.runs})
                job.after_call(args)
                return subprocess.CompletedProcess(args, 0, output, "")
            return original_run(args, **kwargs)

        monkeypatch.setattr(subprocess, "run", run)
        job.runner = github
        job.poll = lambda: daemon.poll_specloop(Config(repos=[repo]), job.health, runner=job.runner)
        job.poll()
        eventually(lambda: (root / "started").exists())
        directory = {"draft": "proposals", "admit": "admissions"}.get(stage, "revisions")
        number = 7 if stage == "draft" else 8
        job.path = daemon.STATE_DIR / directory / "owner__sample" / f"{number}.json"
        receipt = json.loads(job.path.read_text())
        job.directory = job.path.parent / f"{number}.jobs" / receipt["job"]
        job.result_path = job.directory / "result.json"
        assert len(children) == 1 and children[0].poll() is None
        assert not job.result_path.exists()
        return job

    return start


def _finish_delivery_job(job):
    (job.root / "release").touch()
    assert job.children[0].wait(timeout=5) == 0
    assert (job.root / "finished").exists()
    assert not (job.root / "timed-out").exists()
    assert json.loads(job.result_path.read_text())["result"] == job.result


def test_jobs_ignores_drafting_idea_file(delivery_job, capsys):
    from nightshift import cli

    job = delivery_job("draft")
    _finish_delivery_job(job)
    # Use the input written by intake and passed to the real background job.
    args = json.loads((job.directory / "request.json").read_text())["args"]
    idea = Path(args[args.index("--idea-file") + 1])
    assert idea.parent == job.directory.parent
    assert idea.match("idea-*.txt") and idea.read_text()
    before = {p: p.read_bytes() for p in daemon.STATE_DIR.rglob("*") if p.is_file()}

    assert cli.main(["jobs"]) == 0
    output = capsys.readouterr()
    rows = [line for line in output.out.splitlines() if not line.startswith(" ")]
    assert rows == [
        f"{job.directory.name}  proposals  owner/sample#7  finished "
        "(execution complete; delivery may be pending)"
    ]
    assert output.err == ""
    assert {p: p.read_bytes() for p in daemon.STATE_DIR.rglob("*") if p.is_file()} == before


def _assert_paused_delivery_held(job):
    assert daemon.PAUSE_FILE.exists()
    saved = job.path.read_bytes()
    result = job.result_path.read_bytes()
    effects = deepcopy((job.gh.labels, job.gh.comments, job.gh.prs))
    calls = list(job.calls)
    for _ in range(2):
        job.poll()
        assert job.path.read_bytes() == saved
        assert job.result_path.read_bytes() == result
        assert not json.loads(saved).get("delivered")
        assert (job.gh.labels, job.gh.comments, job.gh.prs) == effects
        assert job.calls == calls  # Includes git, CLI publication and GitHub.
        assert not job.health.failures and not job.health.item_failures and not job.health.announced
        assert not (job.directory / "stop.json").exists()
        assert len(job.children) == 1
        assert len((job.root / "started").read_text().splitlines()) == 1


def _finish_while_paused(job):
    saved = job.path.read_bytes()
    calls = list(job.calls)
    daemon.PAUSE_FILE.touch()
    job.poll()  # Pausing a still-running job must leave it at its barrier.
    assert job.children[0].poll() is None
    assert not job.result_path.exists()
    _finish_delivery_job(job)
    assert job.path.read_bytes() == saved and job.calls == calls
    _assert_paused_delivery_held(job)


@pytest.mark.scenario("paused-job-delivery/resume-pending-delivery/resume-once-after-restart")
@pytest.mark.parametrize("stage", ["draft", "admit", "spec_revision", "build_revision"])
def test_finished_job_delivers_once_after_paused_restart(delivery_job, stage):
    job = delivery_job(stage)
    # Reproduce a non-job entry without depending on intake to create one.
    idea = job.directory.parent / f"idea-{'f' * 32}.txt"
    idea.write_text("Drafting input fixture\n", encoding="utf-8")
    comments_before = len(job.gh.comments)
    _finish_while_paused(job)
    result = job.result_path.read_bytes()
    assert not (job.directory / "collected.json").exists()

    # The result must be recovered from disk after completion, with neither
    # process handles nor the previous daemon's health state available.
    specloop_job._children.clear()
    job.health = daemon.RepoHealth()
    _assert_paused_delivery_held(job)
    daemon.PAUSE_FILE.unlink()
    job.poll()
    receipt = job.path.read_bytes()
    assert json.loads(receipt)["delivered"] is True
    assert json.loads(receipt)["result"] == job.result
    assert (job.directory / "collected.json").exists()
    assert len(job.gh.comments) == comments_before + 1
    assert len(job.gh.prs) == int(stage == "draft")
    assert job.gh.labels == ({"spec:review"} if "revision" in stage else set())
    completion = {"draft": "Spec draft ready", "admit": "build has started",
                  "spec_revision": "Revised the spec", "build_revision": "build has resumed"}
    assert completion[stage] in job.gh.comments[-1]["body"]
    assert sum(call[:3] == ["gh", "pr", "create"] for call in job.calls) == int(stage == "draft")
    kind = "issue" if stage == "draft" else "pr"
    assert sum(call[:3] == ["gh", kind, "comment"] for call in job.calls) == 1
    assert sum(call[0] == "git" and call[3] == "push" for call in job.calls) == int(
        stage in ("draft", "spec_revision")
    )

    effects = deepcopy((job.gh.labels, job.gh.comments, job.gh.prs))
    delivered_at = len(job.calls)
    for _ in range(3):
        job.health = daemon.RepoHealth()
        specloop_job._children.clear()
        job.poll()
        assert job.path.read_bytes() == receipt
        assert job.result_path.read_bytes() == result
        assert (job.gh.labels, job.gh.comments, job.gh.prs) == effects
        assert not job.health.failures and not job.health.item_failures and not job.health.announced
        assert len(job.children) == 1
        assert len([p for p in job.directory.parent.iterdir() if p.is_dir()]) == 1
        assert idea.read_text(encoding="utf-8") == "Drafting input fixture\n"
        assert len((job.root / "started").read_text().splitlines()) == 1
    # Repeated polls may read status, but must dispatch no further delivery.
    for call in job.calls[delivered_at:]:
        assert not (call[0] == "git" and call[3] == "push")
        assert not (call[:2] in (["gh", "pr"], ["gh", "issue"])
                    and call[2] in ("create", "comment", "edit"))
        assert not (call[:2] == ["gh", "api"] and "PATCH" in call)
        assert call[:2] != ["specloop", "publish"]


@pytest.mark.scenario("paused-job-delivery/independent-supervisor-boundary/admission-already-committed")
@pytest.mark.parametrize("cancelled", [False, True], ids=["paused", "cancelled"])
@pytest.mark.parametrize("already_driving", [False, True], ids=["supervisor-starts", "supervisor-continues"])
def test_committed_admission_survives_delivery_hold(delivery_job, monkeypatch, cancelled, already_driving):
    job = delivery_job("admit")
    monkeypatch.setattr(state, "ROOT", job.root / "specloop-home")
    change = json.loads(job.path.read_text())["change"]
    # Model the admission commit before the blocked CLI returns its result.
    # The independent supervisor uses real local state; only driver launch is
    # fake, so this cannot start a build or contact an external service.
    run = Run.for_change(change)
    run.state.pr = job.gh.pr_url
    run.state.task("1.1")
    run.save()
    run.manifest_path.write_bytes((job.root / "manifest.toml").read_bytes())
    run.event("admitted", head=HEAD)
    with run.lock() as mine:
        assert mine
    committed = {p.name: p.read_bytes() for p in run.dir.iterdir()}
    job.runs.append({"change": change, "pr": job.gh.pr_url, "status": "running"})
    drivers, spawned, notifications = serve.Drivers(), [], []
    monkeypatch.setattr(daemon.notify, "send", lambda *args: notifications.append(args))

    def spawn(change):
        spawned.append(change)
        return SimpleNamespace(poll=lambda: None)

    def supervisor_poll():
        return serve.pass_once(notifications.append, spawn=spawn, drivers=drivers)

    if already_driving:
        assert supervisor_poll() == [change]
    pending = job.path.read_bytes()
    effects = deepcopy((job.gh.labels, job.gh.comments, job.gh.prs))
    daemon.PAUSE_FILE.touch()
    if cancelled:
        process = json.loads((job.directory / "process.json").read_text())
        assert specloop_job.request_stop(job.directory, verify_identity=lambda: process) == "stopping"
    _finish_delivery_job(job)
    if cancelled:
        # Confirm only after the real wrapper exits; retain its late success.
        specloop_job.finish_stop(job.directory)
    result = job.result_path.read_bytes()
    job_evidence = {p.name: p.read_bytes() for p in job.directory.iterdir() if p.is_file()}

    for resumed in (False, cancelled):
        if resumed:
            daemon.PAUSE_FILE.unlink()
        specloop_job._children.clear()
        job.health = daemon.RepoHealth()
        for _ in range(3):
            job.poll()
            expected = [] if spawned else [change]
            assert supervisor_poll() == expected
            assert spawned == [change] and drivers.alive() == [change]
            assert {p.name: p.read_bytes() for p in run.dir.iterdir()} == committed
            assert Run.for_change(change).state.status == "running"
            assert job.path.read_bytes() == pending
            assert job.result_path.read_bytes() == result
            assert {p.name: p.read_bytes() for p in job.directory.iterdir() if p.is_file()} == job_evidence
            assert not (job.directory / "collected.json").exists()
            assert (job.gh.labels, job.gh.comments, job.gh.prs) == effects
            assert not job.health.failures and not job.health.item_failures and not job.health.announced
            assert not notifications
            assert len(job.children) == 1
            rows, errors = specloop_job.list_jobs(daemon.STATE_DIR)
            assert not errors and len(rows) == 1
            assert rows[0].state == ("stopped" if cancelled else "finished")


@pytest.mark.scenario("paused-job-delivery/pause-gates-launches-and-delivery/admission-finishes-while-paused")
def test_paused_daemon_does_not_deliver_finished_admission(delivery_job):
    job = delivery_job("admit")
    _finish_while_paused(job)
    # The operator can discover completed execution after a daemon restart
    # without collecting its result or releasing paused delivery.
    specloop_job._children.clear()
    job.health = daemon.RepoHealth()
    evidence = {p.name: p.read_bytes() for p in job.directory.iterdir() if p.is_file()}
    rows, errors = specloop_job.list_jobs(daemon.STATE_DIR)
    assert not errors and len(rows) == 1
    assert rows[0].id == job.directory.name and rows[0].state == "finished"
    assert rows[0].log_path.is_file()
    _assert_paused_delivery_held(job)
    assert not (job.directory / "collected.json").exists()
    assert {p.name: p.read_bytes() for p in job.directory.iterdir() if p.is_file()} == evidence


@pytest.mark.scenario("paused-job-delivery/pause-gates-launches-and-delivery/draft-or-revision-finishes-while-paused")
@pytest.mark.parametrize("stage", ["draft", "spec_revision", "build_revision"])
def test_paused_daemon_does_not_deliver_finished_draft_or_revision(delivery_job, stage):
    _finish_while_paused(delivery_job(stage))


@pytest.mark.scenario("paused-job-delivery/pause-gates-launches-and-delivery/pause-during-delivery")
@pytest.mark.parametrize("stage,operation", [
    ("draft", "list"), ("draft", "push"), ("draft", "create"), ("draft", "view"),
    ("draft", "edit"), ("draft", "comments"), ("draft", "comment"),
    ("admit", "status"), ("admit", "comments"), ("admit", "comment"),
    ("spec_revision", "events"), ("spec_revision", "push"),
    ("spec_revision", "comments"), ("spec_revision", "comment"),
    ("build_revision", "comments"), ("build_revision", "comment"),
])
def test_pause_between_delivery_calls_holds_remaining_operations(delivery_job, stage, operation):
    job = delivery_job(stage)
    _finish_delivery_job(job)
    paused_after = []

    def pause_after(args):
        matched = (args[0] == "git" and args[3] == operation
                   or args[:2] == ["specloop", operation]
                   or args[:3] == ["gh", "pr", operation]
                   or args[:3] == ["gh", "issue", "comment"] and operation == "comment"
                   or args[:2] == ["gh", "api"] and args[2].endswith("/" + operation))
        if matched:
            paused_after.append(args)
            daemon.PAUSE_FILE.touch()

    job.after_call = pause_after
    job.poll()
    assert len(paused_after) == 1
    assert job.calls[-1] == paused_after[0]  # Even the next read must wait.
    if stage == "spec_revision" and operation == "events":
        # Authorization now precedes collection of the finished revision job.
        assert "result" not in json.loads(job.path.read_text())
        assert not (job.directory / "collected.json").exists()
    else:
        assert json.loads(job.path.read_text())["result"] == job.result
    _assert_paused_delivery_held(job)


@pytest.mark.scenario("paused-job-delivery/pause-gates-launches-and-delivery/pause-during-delivery")
@pytest.mark.parametrize("stage", ["draft", "admit", "spec_revision", "build_revision"])
@pytest.mark.parametrize("cached_retry", [False, True])
def test_pause_during_successful_final_label_update_holds_delivery(delivery_job, stage, cached_retry):
    job = delivery_job(stage)
    _finish_delivery_job(job)
    kind = "issue" if stage == "draft" else "pr"
    label = {"draft": "spec:draft", "admit": "spec:approved"}.get(stage, "agent:revise")

    if cached_retry:
        def pause_after_comment(args):
            if args[:3] == ["gh", kind, "comment"]:
                daemon.PAUSE_FILE.touch()

        job.after_call = pause_after_comment
        job.poll()
        _assert_paused_delivery_held(job)
        assert label in job.gh.labels
        daemon.PAUSE_FILE.unlink()
        specloop_job._children.clear()

    completed = []

    def pause_after_final_update(args):
        if args[:3] == ["gh", kind, "edit"] and "--remove-label" in args:
            # The dispatched write succeeds before pause is observed by the daemon.
            assert label not in job.gh.labels
            completed.append(args)
            daemon.PAUSE_FILE.touch()

    job.after_call = pause_after_final_update
    job.poll()
    assert len(completed) == 1 and job.calls[-1] == completed[0]
    assert json.loads(job.path.read_text())["result"] == job.result
    _assert_paused_delivery_held(job)


@pytest.mark.scenario("paused-job-delivery/pause-gates-launches-and-delivery/pause-during-delivery")
def test_pause_during_cached_delivery_retry(delivery_job):
    job = delivery_job("admit")
    _finish_delivery_job(job)

    def pause_after_comment(args):
        if args[:3] == ["gh", "pr", "comment"]:
            daemon.PAUSE_FILE.touch()

    job.after_call = pause_after_comment
    job.poll()
    _assert_paused_delivery_held(job)
    assert len(job.gh.comments) == 1
    labels = set(job.gh.labels)
    daemon.PAUSE_FILE.unlink()
    specloop_job._children.clear()  # Retry from the receipt, as after restart.

    def pause_after_patch(args):
        if args[:2] == ["gh", "api"] and "PATCH" in args:
            daemon.PAUSE_FILE.touch()

    job.after_call = pause_after_patch
    job.poll()
    assert "PATCH" in job.calls[-1]
    assert len(job.gh.comments) == 1 and job.gh.labels == labels
    _assert_paused_delivery_held(job)


@pytest.mark.scenario("paused-job-delivery/pause-gates-launches-and-delivery/pause-during-delivery")
@pytest.mark.parametrize("operation", ["comment", "edit"])
@pytest.mark.parametrize("write_landed", [False, True])
def test_pause_during_github_delivery_retry(delivery_job, monkeypatch, operation, write_landed):
    job = delivery_job("admit")
    _finish_delivery_job(job)
    run = subprocess.run
    attempts, waits = [], []

    def lose_response(args, **kwargs):
        if args[:3] == ["gh", "pr", operation]:
            attempts.append(args)
            if write_landed:
                run(args, **kwargs)
            else:
                job.calls.append(args)
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502: Bad Gateway")
        return run(args, **kwargs)

    def pause_during_backoff(seconds):
        waits.append(seconds)
        daemon.PAUSE_FILE.touch()

    monkeypatch.setattr(subprocess, "run", lose_response)
    job.runner = partial(queue._gh, sleep=pause_during_backoff)
    job.poll()
    assert len(attempts) == len(waits) == 1
    assert job.calls[-1] == attempts[0]  # No reconciliation read or retry dispatch.
    assert len(job.gh.comments) == (int(write_landed) if operation == "comment" else 1)
    assert (not job.gh.labels) == (operation == "edit" and write_landed)
    assert json.loads(job.path.read_text())["result"] == job.result
    _assert_paused_delivery_held(job)


def test_spawn_failure_can_retry_on_next_poll(background, monkeypatch):
    root, repo, children = background
    gh, health = DraftGh(("spec:draft",)), daemon.RepoHealth()
    poll = partial(daemon.poll_specloop, Config(repos=[repo]), health, runner=gh)
    launch = subprocess.Popen

    def unavailable(*args, **kwargs):
        raise OSError("out of process slots")

    monkeypatch.setattr(subprocess, "Popen", unavailable)
    poll()
    assert health.failures == {repo.name: 1}
    assert not (root / "started").exists()
    monkeypatch.setattr(subprocess, "Popen", launch)
    poll()
    eventually(lambda: (root / "started").exists())
    # A successful launch is not yet a successful retry.
    assert health.failures == {repo.name: 1}
    assert health.item_failures == {(repo.name, "draft", 7): 1}
    assert len((root / "started").read_text().splitlines()) == 1
    (root / "release").touch()
    children[-1].wait(timeout=5)
    poll()
    assert not health.failures and not health.item_failures


@pytest.mark.scenario("issue-intake/approval-admits/approved-spec-admitted")
@pytest.mark.scenario("issue-intake/publication/ready-run-published")
@pytest.mark.parametrize("command", ["propose", "admit"])
@pytest.mark.parametrize("failure", ["error", "invalid_result"])
def test_background_retries_retain_failures_until_reported_or_recovered(
    background, monkeypatch, command, failure,
):
    root, repo, children = background
    pipeline = Pipeline(repo)
    pipeline.ready(30, daemon.STATE_DIR)
    if command == "propose":
        pipeline.issue(1)
        item, stage = pipeline.issues[1], "draft"
    else:
        pipeline.approved(1)
        item, stage = pipeline.prs[1], "admit"
    # Only item 1 waits/fails. Other jobs finish normally through the same
    # production launcher, file lock, result collection and delivery paths.
    executable = root / "specloop"
    executable.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys, time
from pathlib import Path
root = Path(os.environ["TEST_INTAKE_ROOT"])
args = sys.argv[1:]
change = args[args.index("--change") + 1]
result = {"version": 1, "outcome": "drafted" if args[0] == "propose" else "admitted",
          "branch": "spec/" + change, "commit": "a" * 40, "head": "a" * 40}
if "-issue-1-" in change:
    deadline = time.monotonic() + 15
    while not (root / "release").exists():
        if time.monotonic() > deadline:
            sys.exit(1)
        time.sleep(.01)
    override = json.loads((root / "cli-result.json").read_text())
    if override:
        result = override
print(json.dumps(result))
sys.exit(int("error" in result))
''')
    bad_result = ({"version": 1, "error": "persistent job failure"} if failure == "error"
                  else {"version": 1, "outcome": "wrong"})
    (root / "cli-result.json").write_text(json.dumps(bad_result))
    monkeypatch.setattr(subprocess, "run", pipeline.command)
    notifications = []
    monkeypatch.setattr(daemon.notify, "send", lambda *args: notifications.append(args))
    health = daemon.RepoHealth()
    poll = partial(daemon.poll_specloop, Config(repos=[repo]), health, runner=pipeline.gh)
    key = (repo.name, stage, 1)

    for attempt in range(1, 4):
        pipeline.issue(10 + attempt)
        change = pipeline.approved(40 + attempt)
        first_child = len(children)
        poll()  # Launch the retry and unrelated jobs.
        for _ in range(2):
            poll()  # The failing job's inherited lock is still held.
            assert health.failures.get(repo.name, 0) == attempt - 1
            assert health.item_failures.get(key, 0) == attempt - 1
            assert not notifications
        assert len(children) == first_child + 3
        (root / "release").touch()
        for child in children[first_child:]:
            child.wait(timeout=5)
        poll()  # Collect the failure and both successful sibling jobs.
        assert health.item_failures[key] == attempt
        assert health.failures == {repo.name: attempt}
        assert not pipeline.issues[10 + attempt]["labels"]
        pr = pipeline.prs[40 + attempt]
        receipt = json.loads((daemon.STATE_DIR / f"admissions/owner__sample/{40 + attempt}.json").read_text())
        assert receipt["result"]["outcome"] == "admitted"
        requests = [json.loads(path.read_text())["args"] for path in
                    (daemon.STATE_DIR / f"admissions/owner__sample/{40 + attempt}.jobs").glob("*/request.json")]
        assert len(requests) == 1 and requests[0][requests[0].index("--pr") + 1] == pr["url"]
        assert "build has started" in pr["comments"][0]["body"]
        assert CHECKS in pipeline.prs[30]["comments"][-1]["body"]
        assert pipeline.prs[30]["url"] in pipeline.prs[30]["comments"][-1]["body"]
        assert ({"name": "needs-human"} in item["labels"]) == (attempt == 3)
        # Supply the supervisor's next status snapshot for publication on the
        # next poll, while item 1 is still awaiting a retry or human input.
        pipeline.runs[change] = dict(change=change, pr=pr["url"], head=HEAD, status="paused",
                                     pause={"reason": "ready_to_publish", "detail": CHECKS})
        (root / "release").unlink()

    assert len(notifications) == 1 and repo.name in notifications[0][1]
    assert "Human input is needed" in item["comments"][-1]["body"]
    launches = len(children)
    poll()  # The reported hold prevents another launch; publication continues.
    assert len(children) == launches
    assert not health.item_failures and not health.failures
    for number in (41, 42, 43):
        assert CHECKS in pipeline.prs[number]["comments"][-1]["body"]

    # Human resolution permits another asynchronous attempt. Recovery is
    # recorded only after its result and notification are delivered.
    item["labels"].remove({"name": "needs-human"})
    (root / "cli-result.json").write_text("{}")
    poll()
    (root / "release").touch()
    children[-1].wait(timeout=5)
    poll()
    assert not health.item_failures and not health.failures and not health.announced
    assert ("Spec draft ready" if command == "propose" else "build has started") in item["comments"][-1]["body"]

import json
import os
import sys
import time

import pytest

from nightshift.cli import main

from specloop import cli, serve, state
from specloop.state import Pause, Run, TaskState


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    return tmp_path


def make(change, status="running", pause=None, tasks=None):
    run = Run.for_change(change)
    run.state.status, run.state.pause = status, pause
    for key, ts in (tasks or {}).items():
        run.state.tasks[key] = ts
    run.save()
    return run


def test_lock_is_exclusive(home):
    run = make("c")
    with run.lock() as first:
        with Run.for_change("c").lock() as second:
            assert first and not second
    with run.lock() as again:
        assert again


def test_usage_limit_is_lifted_only_once_due(home):
    notes = []
    run = make("c", "paused", Pause("usage_limit", "t", "", resume_at=time.time() + 3600))
    assert not serve.lift_due(run, time.time(), notes.append)
    assert serve.lift_due(run, time.time() + 7200, notes.append)
    assert Run.for_change("c").state.status == "running" and "usage_limit" in notes[0].text()


def test_infra_is_lifted_at_most_twice_per_task(home):
    from specloop.state import TaskState

    ts = TaskState(infra=4)
    run = make("c", "paused", Pause("infra", "t", "", resume_at=1), {"t": ts})
    for expected in (True, True, False):
        run = Run.for_change("c")
        if run.state.status == "running":
            run.state.status, run.state.pause = "paused", Pause("infra", "t", "", resume_at=1)
            run.save()
        assert serve.lift_due(run, time.time(), lambda t: None) is expected
    assert Run.for_change("c").state.tasks["t"].infra == 0


@pytest.mark.parametrize("reason", ["stuck", "blocked", "phase_gate", "needs_human", "ready_to_publish"])
def test_human_pauses_are_never_lifted(home, reason):
    run = make("c", "paused", Pause(reason, "t", "", resume_at=1))
    assert not serve.lift_due(run, time.time(), lambda t: None)


def test_pass_drives_running_changes_and_skips_locked_ones(home):
    make("a")
    make("b")
    make("p", "paused", Pause("stuck"))
    spawned = []
    with Run.for_change("b").lock():
        driven = serve.pass_once(lambda t: None, spawn=spawned.append)
    assert driven == ["a"] and spawned == ["a"]


@pytest.mark.parametrize("reason,verb", [
    ("infra", "abort"), ("usage_limit", "abort"), ("infra", "resume"),
])
def test_pass_preserves_action_after_loading_due_pause(home, monkeypatch, capfd, reason, verb):
    # This fixture models supervisor state only, without an admitted Git repo.
    monkeypatch.setattr(cli, "_ctx", lambda run: run)
    monkeypatch.setattr(cli, "_sync", lambda ctx: None)
    run = make("c", "paused", Pause(reason, "t", "", resume_at=1),
               {"t": TaskState(infra=4, attempts=3)})
    original_runs = serve.runs
    saved = {}

    def act_after_snapshot():
        snapshot = original_runs()
        # The action completes after discovery loaded the supervisor's snapshot.
        assert main(["act", "c", verb, "--note", "Keep this guidance", "--json"]) == 0
        assert json.loads(capfd.readouterr().out)["outcome"] == "done"
        for name in ("state.json", "events.jsonl"):
            saved[name] = (run.dir / name).read_bytes()
        return snapshot

    monkeypatch.setattr(serve, "runs", act_after_snapshot)
    spawned, notes = [], []
    expected = ["c"] if verb == "resume" else []
    assert serve.pass_once(notes.append, spawn=spawned.append) == expected
    assert spawned == expected
    assert notes == []
    for name, contents in saved.items():
        assert (run.dir / name).read_bytes() == contents
    run.reload()
    assert run.state.status == ("running" if verb == "resume" else "aborted")
    if verb == "resume":
        assert run.state.tasks["t"].note == "Keep this guidance"


@pytest.mark.parametrize("reason", ["infra", "usage_limit"])
def test_pass_does_not_lift_a_locked_due_pause(home, reason):
    run = make("c", "paused", Pause(reason, "t", "", resume_at=1), {"t": TaskState(infra=4)})
    before = (run.dir / "state.json").read_bytes()
    spawned, notes = [], []
    with run.lock() as mine:
        assert mine
        assert serve.pass_once(notes.append, spawn=spawned.append) == []
    assert spawned == notes == []
    assert (run.dir / "state.json").read_bytes() == before
    assert not (run.dir / "events.jsonl").exists()


@pytest.mark.parametrize("reason", ["infra", "usage_limit"])
def test_pass_reloads_and_lifts_due_pause_under_lock(home, monkeypatch, reason):
    run = make("c", "paused", Pause(reason, "t", "", resume_at=1), {"t": TaskState(infra=4)})
    original_reload, original_save = Run.reload, Run.save
    operations, spawned, notes = [], [], []

    def under_lock(operation, name):
        def checked(current):
            with run.lock() as mine:
                assert not mine, f"supervisor must hold the lock during {name}"
            operations.append(name)
            return operation(current)
        return checked

    monkeypatch.setattr(Run, "reload", under_lock(original_reload, "reload"))
    monkeypatch.setattr(Run, "save", under_lock(original_save, "save"))

    def spawn(change):
        with run.lock() as mine:
            assert mine, "the driver needs to acquire the lock itself"
        spawned.append(change)

    assert serve.pass_once(notes.append, spawn=spawn) == ["c"]
    assert operations == ["reload", "save"]
    assert spawned == ["c"] and len(notes) == 1
    assert Run.for_change("c").state.status == "running"


def test_liveness_reports_dead_and_not_running(home):
    assert serve.liveness() == "not running"
    serve.heartbeat_path().write_text(json.dumps({"pid": 999999, "started": 0, "last_poll": time.time()}))
    assert serve.liveness().startswith("DEAD")
    serve.beat(time.time())
    assert serve.liveness().startswith("running")


def test_serve_once_cleans_up_its_heartbeat(home, monkeypatch):
    monkeypatch.setattr(serve, "pass_once", lambda notify, **kw: [])
    assert serve.serve(1, lambda t: None, once=True) == 0
    assert not serve.heartbeat_path().exists()


class FakeProc:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


@pytest.mark.scenario("nightshift-cli/canonical-internal-callers/calls-retain-request-identity")
def test_default_driver_uses_current_interpreter(home, monkeypatch):
    change = "owner__sample-issue-7-add-a-gate"
    make(change)
    proc, calls = FakeProc(), []

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        with Run.for_change(change).lock() as mine:
            assert mine, "the child must be able to acquire its run lock"
        return proc

    monkeypatch.setattr(serve.subprocess, "Popen", popen)
    drivers = serve.Drivers()
    assert serve.pass_once(lambda t: None, drivers=drivers) == [change]
    assert calls == [([sys.executable, "-m", "nightshift.cli", "run", change], {})]
    assert drivers.alive() == [change]
    assert serve.pass_once(lambda t: None, drivers=drivers) == []
    assert len(calls) == 1
    proc.code = 0
    drivers.reap()
    assert drivers.alive() == []


def test_drivers_are_polled_not_waited_on(home):
    make("a")
    make("b")
    procs, drivers = {}, serve.Drivers()
    spawn = lambda change: procs.setdefault(change, FakeProc())
    assert serve.pass_once(lambda t: None, spawn=spawn, drivers=drivers, limit=1) == ["a"]
    assert serve.pass_once(lambda t: None, spawn=spawn, drivers=drivers, limit=1) == []
    procs["a"].code = 0
    assert serve.pass_once(lambda t: None, spawn=spawn, drivers=drivers, limit=1) == ["b"]
    assert drivers.alive() == ["b"]


def test_parallel_limit_caps_live_drivers(home):
    for c in "abc":
        make(c)
    drivers = serve.Drivers()
    started = serve.pass_once(lambda t: None, spawn=lambda c: FakeProc(), drivers=drivers, limit=2)
    assert started == ["a", "b"] and drivers.alive() == ["a", "b"]


def test_due_pauses_are_lifted_while_a_driver_runs(home):
    make("a")
    make("p", "paused", Pause("usage_limit", "t", "", resume_at=1))
    drivers = serve.Drivers()
    serve.pass_once(lambda t: None, spawn=lambda c: FakeProc(), drivers=drivers, limit=1)
    assert drivers.alive() == ["a"] and Run.for_change("p").state.status == "running"


def test_a_stale_heartbeat_from_a_live_pid_is_wedged(home):
    serve.heartbeat_path().write_text(json.dumps({"pid": os.getpid(), "started": 0, "last_poll": time.time() - 1000, "poll": 60}))
    assert serve.liveness().startswith("WEDGED")
    serve.beat(time.time(), ["a"])
    assert serve.liveness().endswith("driving a)")

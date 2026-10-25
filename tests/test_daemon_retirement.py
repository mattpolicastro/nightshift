"""The front door owns polling and liveness, never an execution pool."""

import signal
import subprocess
import sys
from pathlib import Path

import pytest

from nightshift import daemon, queue, specloop_job
from nightshift.config import Config


@pytest.fixture
def lifecycle(monkeypatch):
    events, handlers = [], {}
    real_beat = daemon.beat

    def beat(started):
        events.append("beat")
        real_beat(started)

    def startup(*args):
        events.append("startup")

    def forbidden(*args, **kwargs):
        pytest.fail("polling must not claim, wait for, or stop detached work")

    class DetachedChild:
        def poll(self):
            events.append("reap")
            return None

        wait = terminate = kill = forbidden

    child = DetachedChild()
    monkeypatch.setattr(specloop_job, "_children", {"detached": child})
    monkeypatch.setattr(daemon, "beat", beat)
    monkeypatch.setattr(daemon, "startup", startup)
    monkeypatch.setattr(daemon, "_note_unclean_exit", lambda *a: events.append("check-crash"))
    monkeypatch.setattr(daemon, "poll_specloop", lambda *a: events.append("poll"))
    monkeypatch.setattr(queue, "claim", forbidden)
    monkeypatch.setattr(daemon.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))
    return events, handlers, child


@pytest.mark.scenario("worker-retirement/sole-execution-engine/pause-and-one-shot-retain-control-boundaries")
@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("existing_heartbeat", [False, True])
def test_once_does_one_pass_without_waiting(lifecycle, monkeypatch, paused, existing_heartbeat):
    events, _, child = lifecycle
    if existing_heartbeat:
        daemon.HEARTBEAT_FILE.write_text("service heartbeat")
    if paused:
        daemon.PAUSE_FILE.touch()
    monkeypatch.setattr(daemon.time, "sleep", lambda _: pytest.fail("one-shot slept"))

    assert daemon.loop(Config(repos=[]), {}, once=True) is None

    assert events == ["startup", "reap"] + ([] if paused else ["poll"])
    assert specloop_job._children == {"detached": child}
    assert daemon.HEARTBEAT_FILE.exists() == existing_heartbeat
    if existing_heartbeat:
        assert daemon.HEARTBEAT_FILE.read_text() == "service heartbeat"


@pytest.mark.scenario("worker-retirement/sole-execution-engine/pause-and-one-shot-retain-control-boundaries")
@pytest.mark.scenario("worker-retirement/sole-execution-engine/polling-remains-responsive")
@pytest.mark.parametrize("paused", [False, True])
def test_service_keeps_beating_and_reaping_until_sigterm(lifecycle, monkeypatch, paused):
    events, handlers, child = lifecycle
    if paused:
        daemon.PAUSE_FILE.touch()
    sleeps = []

    def sleep(seconds):
        assert daemon.heartbeat().alive
        sleeps.append(seconds)
        events.append("sleep")
        if len(sleeps) == 2:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        daemon.PAUSE_FILE.unlink(missing_ok=True)

    monkeypatch.setattr(daemon.time, "sleep", sleep)
    with pytest.raises(SystemExit) as stop:
        daemon.loop(Config(repos=[], poll_seconds=7), {})

    assert stop.value.code == 0 and sleeps == [7, 7]
    assert events == (["check-crash", "beat", "startup", "beat", "reap"]
                      + ([] if paused else ["poll"])
                      + ["sleep", "beat", "reap", "poll", "sleep"])
    assert not daemon.HEARTBEAT_FILE.exists()
    assert specloop_job._children == {"detached": child}


@pytest.mark.scenario("worker-retirement/sole-execution-engine/pause-and-one-shot-retain-control-boundaries")
@pytest.mark.parametrize("once", [False, True])
@pytest.mark.parametrize("failure", ["exception", "exit", "sigterm"])
def test_startup_failure_remains_visible_but_sigterm_is_orderly(lifecycle, monkeypatch, once, failure):
    events, handlers, child = lifecycle
    if once:
        daemon.HEARTBEAT_FILE.write_text("service heartbeat")

    def startup(*args):
        assert daemon.HEARTBEAT_FILE.exists()
        if failure == "sigterm":
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        if failure == "exit":
            raise SystemExit(1)
        raise RuntimeError("startup failed")

    monkeypatch.setattr(daemon, "startup", startup)
    with pytest.raises(RuntimeError if failure == "exception" else SystemExit):
        daemon.loop(Config(repos=[]), {}, once=once)

    assert events == ([] if once else ["check-crash", "beat"])
    assert daemon.HEARTBEAT_FILE.exists() == (once or failure != "sigterm")
    if once:
        assert daemon.HEARTBEAT_FILE.read_text() == "service heartbeat"
    assert specloop_job._children == {"detached": child}


@pytest.mark.scenario("worker-retirement/sole-execution-engine/worker-execution-is-absent")
def test_daemon_has_no_worker_executor_or_dispatch_entry_points():
    # worker.py remains until preflight and mirror migrate in the next task.
    retired = """Claimed Tally _WindowStart _FALLBACK_WINDOW_SECONDS
        endpoints_ready claim_next run_claimed _run_claimed _recover _merge
        _running _harvest _wrap_up _record_quota _branch_to_delete _escalation_text
        task worker recovery trace vcs preflight ThreadPoolExecutor Future
        futures_wait FIRST_COMPLETED""".split()
    assert not (set(retired) & vars(daemon).keys())


@pytest.mark.scenario("worker-retirement/sole-execution-engine/worker-execution-is-absent")
def test_retired_modules_are_absent_and_surviving_imports_load():
    # A fresh interpreter catches dependencies hidden by pytest's import cache.
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib
import importlib.util
from pathlib import Path
import nightshift

package = Path(nightshift.__file__).parent
for name in ("task", "prompts", "recovery"):
    assert not (package / f"{name}.py").exists(), name
    assert importlib.util.find_spec(f"nightshift.{name}") is None, name

for name in ("nightshift.cli", "specloop.cli"):
    assert callable(importlib.import_module(name).main), name
for name in ("nightshift.preflight", "nightshift.trace", "nightshift.mirror",
             "nightshift.queue", "nightshift.vcs", "specloop.prompts"):
    importlib.import_module(name)
"""],
        cwd=Path(daemon.__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""Is the daemon alive? The question `status` could not answer.

BACKLOG §2. The log's last line was a startup message from 2026-09-03 and the
queue had been empty since, which is exactly what a crashed daemon produces
too. `status` read GitHub and reported the QUEUE, and said "running" from a
pause file — a statement about a file, not a process — so settling it took `ps`
over SSH, the expensive option from a phone.

The pid is what separates the two states that want different reactions: a live
process that has stopped polling is WEDGED, a heartbeat whose process is gone
means it CRASHED. A missing file is neither: a clean stop removes it.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from nightshift import cli, daemon, notify
from nightshift.config import Config, Repo


@pytest.fixture
def hb(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat.json"
    monkeypatch.setattr(daemon, "HEARTBEAT_FILE", path)
    return path


def write(path, *, pid=None, age=0.0, uptime=600.0):
    now = time.time()
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid() if pid is None else pid,
                "started_at": now - uptime,
                "last_poll": now - age,
            }
        )
    )


def cfg(**kw) -> Config:
    kw.setdefault("repos", [Repo(name="matt/sandbox", verify="true")])
    return Config(**kw)


# --- the file itself --------------------------------------------------------


def test_a_beat_records_this_process_and_the_moment(hb):
    started = time.time() - 300
    daemon.beat(started, hb)
    beat = daemon.heartbeat(hb)

    assert beat.pid == os.getpid()
    assert beat.alive is True
    assert beat.age < 5
    assert 295 < beat.uptime < 400


def test_a_beat_never_fails_a_poll(tmp_path, monkeypatch):
    """Liveness reporting is not the job. A poll must not die reporting on itself."""
    unwritable = tmp_path / "nope" / "heartbeat.json"
    monkeypatch.setattr(
        daemon.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("full"))
    )
    daemon.beat(time.time(), unwritable)  # must not raise


def test_an_unreadable_heartbeat_is_no_heartbeat(hb):
    hb.write_text("{ this is not json")
    assert daemon.heartbeat(hb) is None


def test_a_heartbeat_missing_a_field_is_no_heartbeat(hb):
    hb.write_text(json.dumps({"pid": 1}))
    assert daemon.heartbeat(hb) is None


def test_staleness_tolerates_one_slow_pass(hb):
    write(hb, age=100)
    beat = daemon.heartbeat(hb)
    # Two poll intervals, so a single slow pass is not news.
    assert beat.stale(poll_seconds=120) is False
    assert beat.stale(poll_seconds=30) is True


def test_a_dead_pid_is_not_alive(hb):
    # A pid that cannot exist: the kernel caps at 99999 on macOS and 4194304 on
    # Linux, and either way nothing owns this one.
    write(hb, pid=2**31 - 1)
    assert daemon.heartbeat(hb).alive is False


def test_the_first_beat_lands_before_startup_runs(hb, monkeypatch):
    """Found by running it: the heartbeat used to be written only once the loop
    was turning, which is AFTER `startup()` — and `startup()` is where 105 of
    the log's 107 tracebacks happened. The crash this exists to expose would
    have left nothing behind."""
    seen: list[bool] = []

    def startup(cfg_, repo_dirs, health=None):
        seen.append(hb.exists())
        raise SystemExit(0)  # stop the loop where the old ordering failed

    monkeypatch.setattr(daemon, "startup", startup)
    monkeypatch.setattr(daemon, "paused", lambda: False)
    monkeypatch.setattr(daemon.notify, "send", lambda *a: None)

    with pytest.raises(SystemExit):
        daemon.loop(cfg(), {})

    assert seen == [True], "no heartbeat existed while startup() was running"


def test_a_one_shot_run_does_not_touch_the_daemon_s_heartbeat(hb, monkeypatch):
    """`--once` is a human at a terminal, not the service, and they share a file.

    Found by running `--once` beside the live daemon an hour after shipping the
    heartbeat: a clean one-shot DELETED the daemon's proof of life, so `status`
    reported "not running" for a process that was fine, until the next poll
    rewrote it. A crashed one is worse — it leaves a dead pid, which reads as
    DEAD and makes the next restart announce an unclean exit that never was.
    """
    write(hb, age=1)  # the live daemon's beat
    before = hb.read_text()

    monkeypatch.setattr(daemon, "startup", lambda *a, **k: [])
    monkeypatch.setattr(daemon, "paused", lambda: False)
    monkeypatch.setattr(daemon, "claim_next", lambda *a, **k: None)
    monkeypatch.setattr(daemon.notify, "send", lambda *a: None)

    daemon.loop(cfg(), {}, once=True)

    assert hb.exists(), "a one-shot run must not delete the daemon's heartbeat"
    assert hb.read_text() == before, "nor overwrite it with its own pid"


# --- what `status` says about each state ------------------------------------


def test_a_fresh_heartbeat_reads_as_running(hb, monkeypatch):
    monkeypatch.setattr(daemon, "paused", lambda: False)
    write(hb, age=3, uptime=3700)
    line = cli._liveness(cfg())

    assert line.startswith("running")
    assert f"pid {os.getpid()}" in line
    assert "up 1h 01m" in line


def test_a_live_process_that_stopped_polling_reads_as_wedged(hb, monkeypatch):
    """The state that looks healthiest and is not: the process is up, and it
    has stopped doing the thing it exists to do."""
    monkeypatch.setattr(daemon, "paused", lambda: False)
    write(hb, age=900)
    line = cli._liveness(cfg(poll_seconds=120))

    assert line.startswith("WEDGED")
    assert "15m" in line


def test_a_heartbeat_whose_process_is_gone_reads_as_a_crash(hb, monkeypatch):
    monkeypatch.setattr(daemon, "paused", lambda: False)
    write(hb, pid=2**31 - 1, age=200)
    line = cli._liveness(cfg())

    assert line.startswith("DEAD")
    assert "crashed rather than stopped" in line


def test_no_heartbeat_reads_as_stopped_not_crashed(hb, monkeypatch):
    monkeypatch.setattr(daemon, "paused", lambda: False)
    assert cli._liveness(cfg()).startswith("not running")


def test_paused_is_reported_beside_liveness_not_instead_of_it(hb, monkeypatch):
    """A paused daemon is still alive and still beating. Reporting only the
    pause file is how "paused" came to mean "some file exists"."""
    monkeypatch.setattr(daemon, "paused", lambda: True)
    write(hb, age=2)
    line = cli._liveness(cfg())

    assert "running (paused)" in line
    assert f"pid {os.getpid()}" in line


# --- the restart loop, made visible -----------------------------------------


def test_a_heartbeat_left_by_a_dead_process_announces_the_crash(hb, monkeypatch):
    """`notify` is otherwise reachable only from task outcomes, never from a
    crash on the way up — which is why launchd could restart the daemon every
    two minutes for three quarters of an hour on 08-15 and say nothing."""
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda hook, text: sent.append(text))
    write(hb, pid=2**31 - 1, age=130)

    found = daemon._note_unclean_exit("hook", hb)

    assert found is not None
    assert len(sent) == 1
    assert "restarted after an unclean exit" in sent[0]
    assert "restart loop" in sent[0]


def test_a_clean_start_says_nothing(hb, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda hook, text: sent.append(text))

    assert daemon._note_unclean_exit("hook", hb) is None
    assert sent == []


def test_a_heartbeat_from_a_process_still_running_is_not_a_crash(hb, monkeypatch):
    """Two daemons at once is a different problem, and not this message."""
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda hook, text: sent.append(text))
    write(hb, age=5)

    assert daemon._note_unclean_exit("hook", hb) is None
    assert sent == []

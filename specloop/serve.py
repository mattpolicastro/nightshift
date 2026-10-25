"""`specloop serve`: drive every running change, unattended.

Each change runs as its own `specloop run` subprocess, so a crash is
contained and every driver picks up the current code. At most
`SPECLOOP_SERVE_PARALLEL` (default 2) are driven at once; a change a human is
driving (its lock is held) is left alone. Timed pauses are lifted when due:
usage limits always (the window has reset), infra pauses at most twice per task
(a local inference box that was off may be back). Every other pause waits for a
human.

Same liveness contract as the nightshift daemon: a heartbeat written every
poll and removed on a clean exit, so `specloop status` can tell running from
wedged from dead. Drivers are children the loop polls, not calls it waits on,
so the heartbeat stays fresh while a task takes an hour.

(2026-10-20: the first version waited on each driver in turn. Its heartbeat
went stale for the whole run, so busy and wedged looked the same, and a second
running change was not started until the first paused.)
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from specloop import messages, state
from specloop.state import Run

AUTO_RESUME = {"usage_limit": None, "infra": 2}  # reason -> max lifts per task (None: unlimited)


def heartbeat_path() -> Path:
    return state.ROOT / "serve.json"


def parallel() -> int:
    try:
        return max(1, int(os.environ.get("SPECLOOP_SERVE_PARALLEL", "2")))
    except ValueError:
        return 2


def beat(started: float, driving: list[str] = (), poll: float = 60) -> None:
    p = heartbeat_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "started": started, "last_poll": time.time(),
                               "poll": poll, "driving": list(driving)}))
    os.replace(tmp, p)


def liveness() -> str:
    p = heartbeat_path()
    if not p.exists():
        return "not running"
    hb = json.loads(p.read_text())
    try:
        os.kill(hb["pid"], 0)
    except ProcessLookupError:
        return f"DEAD (pid {hb['pid']} gone; last poll {time.time() - hb['last_poll']:.0f}s ago)"
    age = time.time() - hb["last_poll"]
    if "poll" in hb and age > max(5 * hb["poll"], 300):  # a pre-2026-10-20 supervisor never wrote "poll"
        return f"WEDGED (pid {hb['pid']} alive, but no poll for {age:.0f}s)"
    if "driving" not in hb:
        return f"running (pid {hb['pid']}, last poll {age:.0f}s ago)"
    return f"running (pid {hb['pid']}, last poll {age:.0f}s ago, driving {', '.join(hb['driving']) or 'nothing'})"


def lift_due(run: Run, now: float, notify) -> bool:
    st = run.state
    p = st.pause
    if st.status != "paused" or not p or not p.resume_at or now < p.resume_at or p.reason not in AUTO_RESUME:
        return False
    cap = AUTO_RESUME[p.reason]
    ts = st.tasks.get(p.task)
    if cap is not None and ts is not None:
        if ts.auto_resumes >= cap:
            return False
        ts.auto_resumes += 1
        ts.infra = 0
    st.status, st.pause = "running", None
    run.save()
    run.event("auto_resumed", reason=p.reason, task=p.task)
    notify(messages.fyi(st.change, f"lifted the {p.reason} pause automatically; continuing."))
    return True


def runs() -> list[Run]:
    found = [Run(d) for d in state.runs_dir().glob("*") if (d / "state.json").exists()]
    return sorted(found, key=lambda r: r.state.created)


class Drivers:
    """The `specloop run` children this supervisor started, by change."""

    def __init__(self):
        self.procs: dict = {}
        self.last: dict[str, float] = {}  # change -> when it was last started, for fairness

    def reap(self) -> list[str]:
        done = [c for c, p in self.procs.items() if p.poll() is not None]
        for c in done:
            del self.procs[c]
        return done

    def alive(self) -> list[str]:
        return sorted(self.procs)


def pass_once(notify, spawn=None, drivers: Drivers | None = None, limit: int = 1) -> list[str]:
    """One sweep: lift due pauses everywhere, start a driver for each running
    change nobody is driving, up to `limit` alive at once. Returns the changes
    it started. A `spawn` that returns None is treated as having finished."""
    spawn = spawn or (lambda change: subprocess.Popen([sys.executable, "-m", "nightshift.cli", "run", change]))
    drivers = drivers if drivers is not None else Drivers()
    drivers.reap()
    started = []
    for run in sorted(runs(), key=lambda r: drivers.last.get(r.state.change, 0)):
        with run.lock() as mine:
            if not mine:
                continue  # a human is driving it
            # An action may have changed the pause since runs() loaded it.
            run.reload()
            lift_due(run, time.time(), notify)
            if run.state.status != "running" or run.state.change in drivers.procs or len(drivers.procs) >= limit:
                continue
        proc = spawn(run.state.change)
        drivers.last[run.state.change] = time.time()
        if proc is not None:
            drivers.procs[run.state.change] = proc
        started.append(run.state.change)
    return started


def serve(poll: float, notify, once: bool = False) -> int:
    started = time.time()
    previous = heartbeat_path()
    if previous.exists():
        hb = json.loads(previous.read_text())
        try:
            os.kill(hb["pid"], 0)
            print(f"specloop serve already running as pid {hb['pid']}", file=sys.stderr)
            return 1
        except ProcessLookupError:
            notify(messages.Message(messages.ACTION, "supervisor", "Check the log: the specloop supervisor crashed",
                                    f"pid {hb['pid']} exited without cleaning up; it has restarted.",
                                    "tail -50 ~/.logs/specloop.log"))

    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    drivers, limit = Drivers(), parallel()
    try:
        while not stopping:
            pass_once(notify, drivers=drivers, limit=limit)
            beat(started, drivers.alive(), poll)
            if once:
                for proc in drivers.procs.values():
                    proc.wait()
                break
            for _ in range(int(poll)):
                if stopping:
                    break
                time.sleep(1)
    finally:
        heartbeat_path().unlink(missing_ok=True)
    return 0

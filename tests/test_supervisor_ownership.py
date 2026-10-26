import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from nightshift import daemon
from specloop import serve, state, state_locks


EXCLUSION = "state-migration/supervisor-exclusion/live-supervisor-blocks-migration"
LIFETIME = "state-migration/supervisor-exclusion/supervisor-lock-survives-startup-and-exit"


def heartbeat(path, pid):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "last_poll": 0}))


def refused():
    # The migration preflight interface: nothing after acquisition may run.
    with pytest.raises(state_locks.SupervisorBusy, match="Stop the supervisor"):
        with state_locks.serve_lock():
            pytest.fail("exclusive state operation reached with a live supervisor")
    assert serve.serve(1, lambda _: None, once=True) == 1


@pytest.mark.scenario(EXCLUSION)
@pytest.mark.parametrize("evidence", ["live", "denied", "malformed", "missing-pid", "bad-pid", "unreadable", "encoding"])
def test_uncertain_legacy_blocks_even_with_dead_canonical(monkeypatch, evidence):
    canonical, legacy = state.heartbeat_paths()
    heartbeat(canonical, 999999)
    heartbeat(legacy, os.getpid())
    original_kill = os.kill

    def probe(pid, sig):
        if pid == 999999:
            raise ProcessLookupError()
        if evidence == "denied":
            raise PermissionError()
        return original_kill(pid, sig)

    monkeypatch.setattr(os, "kill", probe)
    if evidence == "malformed":
        legacy.write_text("{")
    elif evidence == "missing-pid":
        legacy.write_text("{}")
    elif evidence == "bad-pid":
        heartbeat(legacy, -1)
    elif evidence == "encoding":
        legacy.write_bytes(b"\xff")
    elif evidence == "unreadable":
        read = Path.read_text

        def unreadable(path, *args, **kwargs):
            if path == legacy:
                raise PermissionError()
            return read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
    before = [p.read_bytes() for p in (canonical, legacy)]
    refused()
    assert [p.read_bytes() for p in (canonical, legacy)] == before


@pytest.mark.scenario(LIFETIME)
def test_owned_lock_reuse_and_heartbeat_lookup(monkeypatch):
    canonical, legacy = state.heartbeat_paths()
    heartbeat(legacy, 999999)
    monkeypatch.setattr(os, "kill", lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    assert state.heartbeat_path(existing=True) == legacy
    with state_locks.serve_lock() as owned:
        assert owned.dead_pids == (999999,)
        assert legacy.exists()  # Acquisition does not erase crash evidence.
        serve.beat(time.time())
        assert state.heartbeat_path(existing=True) == canonical
        with state_locks.serve_lock(owned) as borrowed:
            assert borrowed is owned
        with pytest.raises(state_locks.SupervisorBusy):
            with state_locks.serve_lock():
                pytest.fail("borrowing must not release ownership")
    with pytest.raises(ValueError, match="ownership"):
        with state_locks.serve_lock(owned):
            pytest.fail("expired ownership accepted")


@pytest.mark.scenario(LIFETIME)
def test_legacy_crash_recovery_then_clean_exit_and_pid_reuse(monkeypatch):
    canonical, legacy = state.heartbeat_paths()
    heartbeat(legacy, 999999)
    original = legacy.read_bytes()
    reused = False
    probe = os.kill

    def kill(pid, sig):
        if pid != 999999:
            return probe(pid, sig)
        if not reused:
            raise ProcessLookupError()

    monkeypatch.setattr(os, "kill", kill)
    publish = serve.beat

    def beat(*args):
        assert legacy.read_bytes() == original
        assert not canonical.exists()
        publish(*args)
        assert canonical.exists()

    monkeypatch.setattr(serve, "beat", beat)
    notes = []
    assert serve.serve(1, notes.append, once=True) == 0
    assert len(notes) == 1 and "999999" in notes[0].text()
    assert not canonical.exists() and not legacy.exists()
    assert serve.liveness() == "not running"
    reused = True
    monkeypatch.setattr(serve, "beat", publish)
    assert serve.serve(1, notes.append, once=True) == 0
    assert len(notes) == 1  # No repeated crash alert or stale PID refusal.
    assert serve.liveness() == "not running"


@pytest.mark.scenario(EXCLUSION)
@pytest.mark.parametrize("publication", ["failed", "legacy-changed"])
def test_startup_preserves_unsuperseded_legacy_evidence(monkeypatch, publication):
    _, legacy = state.heartbeat_paths()
    heartbeat(legacy, 999999)
    original = legacy.read_bytes()
    probe = os.kill

    def kill(pid, sig):
        if pid == 999999:
            raise ProcessLookupError()
        return probe(pid, sig)

    monkeypatch.setattr(os, "kill", kill)
    publish = serve.beat

    def beat(*args):
        if publication == "failed":
            raise OSError("heartbeat publication failed")
        heartbeat(legacy, os.getpid())
        publish(*args)

    monkeypatch.setattr(serve, "beat", beat)
    if publication == "failed":
        with pytest.raises(OSError, match="publication failed"):
            serve.serve(1, lambda _: None, once=True)
        assert legacy.read_bytes() == original
    else:
        assert serve.serve(1, lambda _: None, once=True) == 0
        assert json.loads(legacy.read_text())["pid"] == os.getpid()
        refused()


CHILD = """
import importlib, sys
from specloop import cli, serve
cli.load_env = lambda _: {}
cli.notifier = lambda _: lambda message: None
class Driver:
    def wait(self):
        print('waiting', flush=True)
        sys.stdin.readline()
def sweep(notify, **kwargs):
    print('startup', flush=True)
    sys.stdin.readline()
    kwargs['drivers'].procs['fixture'] = Driver()
serve.pass_once = sweep
beat = serve.beat
def report(*args):
    beat(*args)
    print('heartbeat', flush=True)
serve.beat = report
sys.exit(importlib.import_module(sys.argv[1]).main(['serve', '--poll', '60'] + sys.argv[2:]))
"""


def line(proc, expected):
    assert select.select([proc.stdout], [], [], 10)[0], f"child stalled before {expected}"
    assert proc.stdout.readline().decode().strip() == expected


@pytest.mark.scenario(EXCLUSION)
@pytest.mark.scenario(LIFETIME)
@pytest.mark.parametrize("module", ["nightshift.cli", "specloop.cli"])
@pytest.mark.parametrize("exit_kind", ["once", "term", "crash"])
def test_subprocess_lifetime(module, exit_kind):
    daemon.beat(time.time())
    daemon_bytes = daemon.HEARTBEAT_FILE.read_bytes()
    # Startup relocates entries before the first scheduling pass.
    entry = state.legacy_root() / "homes" / "fixture"
    entry.mkdir(parents=True)
    (entry / "sentinel").write_bytes(b"preserved")
    once = exit_kind != "term"
    proc = subprocess.Popen([sys.executable, "-u", "-c", CHILD, module] + (["--once"] if once else []),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    try:
        line(proc, "startup")
        lock = state_locks.serve_lock_path()
        inode = lock.stat().st_ino
        assert not state.heartbeat_path().exists()
        refused()  # Excludes rivals even before the first heartbeat.
        proc.stdin.write(b"continue\n")
        proc.stdin.flush()
        line(proc, "heartbeat")
        if once:
            line(proc, "waiting")
        refused()  # --once retains ownership while waiting for drivers.
        assert lock.stat().st_ino == inode
        if exit_kind == "once":
            proc.stdin.write(b"finish\n")
            proc.stdin.flush()
        elif exit_kind == "term":
            proc.terminate()
        else:
            proc.kill()
        assert proc.wait(timeout=10) == (-9 if exit_kind == "crash" else 0)
        assert state.heartbeat_path().exists() == (exit_kind == "crash")
        with state_locks.serve_lock() as owned:
            assert owned.dead_pids == ((proc.pid,) if exit_kind == "crash" else ())
            assert lock.stat().st_ino == inode
        # A real subsequent startup reports/replaces crash evidence and cleans up.
        notes = []
        assert serve.serve(1, notes.append, once=True) == 0
        assert len(notes) == (1 if exit_kind == "crash" else 0)
        assert not state.heartbeat_path().exists()
        assert lock.stat().st_ino == inode
        assert daemon.HEARTBEAT_FILE.read_bytes() == daemon_bytes
        assert daemon.heartbeat().alive
        assert (entry / "sentinel").read_bytes() == b"preserved"
        assert entry.is_symlink()
        assert entry.readlink() == state.state_root() / "homes" / "fixture"
        assert (state.legacy_root() / ".migrated-to-nightshift-v1.json").exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)

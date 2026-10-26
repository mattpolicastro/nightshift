"""Stable supervisor exclusion, shared by serve and future migration startup.

Lock order is serve, migration coordination, then run locks. Never unlink or
relocate serve.lock. Pass the yielded ownership token to nested startup work;
reacquiring flock through another descriptor would contend with ourselves.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
from typing import IO, Iterator

from specloop import state


class SupervisorBusy(RuntimeError):
    """A live or uncertain supervisor prevents exclusive state operations."""


def serve_lock_path() -> Path:
    return state.legacy_root() / "serve.lock"


def _refusal(evidence: object) -> SupervisorBusy:
    return SupervisorBusy(f"Stop the supervisor before continuing; supervisor ownership is busy or uncertain: {evidence}")


@dataclass(frozen=True)
class DeadHeartbeat:
    path: Path
    pid: int
    contents: str


def _dead_supervisors() -> tuple[DeadHeartbeat, ...]:
    dead = []
    for path in state.heartbeat_paths():
        try:
            raw = path.read_text()
        except FileNotFoundError:
            if path.is_symlink():
                raise _refusal(path)
            continue
        except (OSError, UnicodeError) as exc:
            raise _refusal(path) from exc
        try:
            hb = json.loads(raw)
            pid, last_poll = hb["pid"], hb["last_poll"]
            if (type(pid) is not int or pid <= 0 or
                    type(last_poll) not in (int, float) or not math.isfinite(last_poll)):
                raise ValueError("invalid heartbeat")
            os.kill(pid, 0)
        except ProcessLookupError:
            dead.append(DeadHeartbeat(path, pid, raw))
            continue
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise _refusal(path) from exc
        raise _refusal(f"{path} (pid {pid})")
    return tuple(dead)


@dataclass(frozen=True)
class ServeLock:
    """Borrowable only inside its acquiring context and in the owning process."""

    path: Path
    _file: IO[bytes]
    _pid: int
    dead_heartbeats: tuple[DeadHeartbeat, ...]

    @property
    def dead_pids(self) -> tuple[int, ...]:
        return tuple(heartbeat.pid for heartbeat in self.dead_heartbeats)


@contextmanager
def serve_lock(owned: ServeLock | None = None) -> Iterator[ServeLock]:
    """Acquire nonblocking exclusion, or borrow verified lifetime ownership.

    Fresh acquisition checks both old supervisor heartbeats before yielding.
    Borrowing neither rechecks our own heartbeat nor releases the outer lock.
    Dead heartbeat evidence is retained for normal startup's crash reporting.
    """
    path = serve_lock_path()
    if owned is not None:
        if owned.path != path or owned._pid != os.getpid() or owned._file.closed:
            raise ValueError("serve lock ownership is no longer valid for this root/process")
        yield owned
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise _refusal(path) from exc
        # Closing releases ownership on every exit, including heartbeat refusal.
        yield ServeLock(path, handle, os.getpid(), _dead_supervisors())

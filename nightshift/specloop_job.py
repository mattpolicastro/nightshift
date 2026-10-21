"""Durable background CLI jobs for intake, without importing specloop internals.

The child owns an inherited file lock until its result is on disk. A new
daemon can therefore distinguish a running job from a dead one without PID
reuse races. Only the poller writes the intake receipt; children write their
own atomic result files. Logs use files so a verbose CLI cannot fill a pipe.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import uuid
from functools import partial
from pathlib import Path

from . import specloop_client


_children: dict[str, subprocess.Popen] = {}


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data) + "\n")
    temp.replace(path)


def reap() -> None:
    """Reap even children whose issue/PR is no longer armed."""
    for key, child in list(_children.items()):
        if child.poll() is not None:
            del _children[key]


def start(path: Path, receipt: dict, args: list[str], *, launcher=None) -> None:
    reap()
    job = uuid.uuid4().hex
    directory = path.parent / f"{path.stem}.jobs" / job
    directory.mkdir(parents=True)
    save(directory / "request.json", {"args": args})
    with (directory / "lock").open("w") as lock, (directory / "log").open("w") as log:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt["job"] = job
        # Persist the identity before launch. The inherited lock covers the
        # gap between Popen and the child starting, including daemon restarts.
        save(path, receipt)
        try:
            child = (launcher or subprocess.Popen)(
                [sys.executable, "-m", "nightshift.specloop_job", str(directory.resolve()), str(lock.fileno())],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                pass_fds=(lock.fileno(),), start_new_session=True,
            )
        except Exception:
            # This includes a pause detected immediately before Popen. No
            # child exists, so the next poll need not report a crashed job.
            receipt.pop("job")
            save(path, receipt)
            raise
        _children[str(directory)] = child


def collect(path: Path, receipt: dict) -> dict | None:
    """Return a completed result, or None immediately while the job is live.

    A dead/failed job is reported through RepoHealth before a later poll may
    retry (after the usual GitHub and specloop state checks).
    """
    reap()
    directory = path.parent / f"{path.stem}.jobs" / receipt["job"]
    with (directory / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        result_path = directory / "result.json"
        outcome = (json.loads(result_path.read_text()) if result_path.exists()
                   else {"error": f"specloop job exited without a result; see {directory / 'log'}"})
    if "error" in outcome:
        receipt.pop("job")
        save(path, receipt)
        raise specloop_client.SpecloopError(outcome["error"])
    return outcome["result"]


def main() -> None:
    directory, fd = Path(sys.argv[1]), int(sys.argv[2])
    try:
        args = json.loads((directory / "request.json").read_text())["args"]
        # If this wrapper dies, the CLI keeps the lock until it too exits.
        result = specloop_client.call(args, runner=partial(subprocess.run, pass_fds=(fd,)))
        outcome = {"result": result}
    except Exception as exc:
        outcome = {"error": str(exc)}
    save(directory / "result.json", outcome)
    os.close(fd)


if __name__ == "__main__":
    main()

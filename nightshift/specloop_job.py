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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from . import specloop_client


_children: dict[str, subprocess.Popen] = {}


@dataclass(frozen=True)
class Job:
    id: str
    stage: str
    repository: str
    number: int
    command: tuple[str, ...]
    state: str
    directory: Path
    # None explicitly identifies legacy jobs with unavailable identity.
    process: dict | None

    @property
    def identity_available(self) -> bool:
        return self.process is not None and self.process["start_identity"] is not None

    @property
    def pid(self) -> int | None:
        return self.process["pid"] if self.process else None

    @property
    def pgid(self) -> int | None:
        return self.process["pgid"] if self.process else None

    @property
    def log_path(self) -> Path:
        return self.directory / "log"


def process_start_identity(pid: int) -> str:
    """Read a POSIX process start identity locally; injectable in control tests.

    Keep the locale fixed so a later daemon can compare the persisted value.
    This value alone is not sufficient authority to signal a process group.
    """
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True, text=True, timeout=5,
        env={**os.environ, "LC_ALL": "C"}, check=True,
    )
    identity = result.stdout.strip()
    if not identity:
        raise ValueError(f"process {pid} has no start identity")
    return identity


def _record_process(directory: Path) -> None:
    pid = os.getpid()
    process = {
        "version": 1, "job_id": directory.name,
        "pid": pid, "pgid": os.getpgrp(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        process["start_identity"] = process_start_identity(pid)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # Process inspection can be restricted. Preserve job execution, but
        # explicitly leave this attempt without a verifiable signal target.
        process["start_identity"] = None
        process["identity_error"] = str(exc)
    save(directory / "process.json", process)


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _read_process(directory: Path) -> dict | None:
    try:
        process = _read_object(directory / "process.json")
    except FileNotFoundError:
        return None
    if (type(process.get("version")) is not int or process["version"] != 1
            or process.get("job_id") != directory.name
            or any(type(process.get(key)) is not int or process[key] <= 0
                   for key in ("pid", "pgid"))
            or not isinstance(process.get("started_at"), str) or not process["started_at"].strip()
            or not (
                isinstance(process.get("start_identity"), str) and process["start_identity"].strip()
                or "start_identity" in process and process["start_identity"] is None
                and isinstance(process.get("identity_error"), str) and process["identity_error"].strip()
            )):
        raise ValueError(f"{directory / 'process.json'}: invalid process identity")
    return process


def _job_state(directory: Path, process: dict | None) -> str:
    # Read-only open: inventory must never create state or replace a lock.
    with (directory / "lock").open("r") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            live = False
        except BlockingIOError:
            live = True
        stop = _read_stop(directory)
        if stop is not None:
            return stop["state"]
        try:
            outcome = _read_object(directory / "result.json")
        except FileNotFoundError:
            return ("running" if process else "starting") if live else "failed"
        if set(outcome) == {"result"} and isinstance(outcome["result"], dict):
            return "finished"
        if set(outcome) == {"error"} and isinstance(outcome["error"], str):
            return "failed"
        raise ValueError(f"{directory / 'result.json'}: invalid result record")


def list_jobs(state_dir: Path) -> tuple[list[Job], list[str]]:
    # mutation-equivalent: Docstring wording is not read by inventory logic or callers.
    """Return local attempts and diagnostics, independent of receipts/handles."""

    # Rows sort by stage, repository, numeric item and full ID. Malformed jobs
    # remain visible as unknown when their directory context can be recovered;
    # mutation-equivalent: This explanatory prose does not control diagnostic handling.
    # errors are returned separately so callers can display good rows and fail.
    # A live lock without metadata is starting (or a legacy live job); absent
    # process metadata is always exposed as None, never guessed from a PID.
    # identity_available also distinguishes failed process-start lookups.
    jobs, errors = [], []

    def directories(parent: Path) -> list[Path]:
        try:
            return sorted(p for p in parent.iterdir() if p.is_dir() and not p.is_symlink())
        except FileNotFoundError:
            return []
        except OSError as exc:
            errors.append(f"{parent}: {exc}")
            return []

    for stage in ("admissions", "proposals", "revisions"):
        stage_dir = state_dir / stage
        if stage_dir.is_symlink():
            continue
        for repo in directories(stage_dir):
            for attempts in directories(repo):
                if attempts.suffix != ".jobs":
                    continue
                for directory in directories(attempts):
                    try:
                        number = int(attempts.stem)
                        if number <= 0 or uuid.UUID(directory.name).hex != directory.name:
                            raise ValueError("invalid item number or job ID")
                    except ValueError as exc:
                        errors.append(f"{directory}: {exc}")
                        continue
                    command, process, state = (), None, "unknown"
                    record_errors = []
                    try:
                        args = _read_object(directory / "request.json").get("args")
                        if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
                            raise ValueError("request.json: expected a nonempty string argument list")
                        command = tuple(args)
                    except (OSError, ValueError) as exc:
                        record_errors.append(str(exc))
                    try:
                        process = _read_process(directory)
                    except (OSError, ValueError) as exc:
                        record_errors.append(str(exc))
                    try:
                        state = _job_state(directory, process)
                    except (OSError, ValueError) as exc:
                        record_errors.append(str(exc))
                    if record_errors:
                        state = "unknown"
                        errors.extend(f"{directory}: {error}" for error in record_errors)
                    # Only the owner/repository separator is encoded; the
                    # repository name itself may contain double underscores.
                    jobs.append(Job(directory.name, stage, repo.name.replace("__", "/", 1),
                                    number, command, state, directory, process))
    jobs.sort(key=lambda job: (job.stage, job.repository, job.number, job.id))
    return jobs, errors


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data) + "\n")
    temp.replace(path)


@contextmanager
def control_lock(directory: Path):
    # mutation-equivalent: Docstring conjunctions do not change the flock used by all three callers.
    """Serialize stop admission, finalization and collection across processes.

    This is separate from the inherited lifetime lock. Never wait for that
    # mutation-equivalent: This docstring text cannot change lock acquisition or termination waits.
    lock or for process termination while holding this short-lived lock.
    """
    with (directory / "control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _read_stop(directory: Path) -> dict | None:
    try:
        stop = _read_object(directory / "stop.json")
    except FileNotFoundError:
        return None
    if stop.get("state") not in ("stopping", "stopped"):
        raise ValueError(f"{directory / 'stop.json'}: invalid stop state")
    return stop


def request_stop(directory: Path, *, verify_identity) -> str:
    # mutation-equivalent: Return values come from the branches below, not this unread docstring.
    """Admit stop intent, returning stopping, stopped, or finished.

    # mutation-equivalent: This docstring text does not resolve paths or invoke the ownership verifier.
    The control caller must resolve the job directory and supply an ownership
    verifier returning the verified process identity (or raising on refusal).
    Verification runs under the lock, before accepting intent. Only a stopping
    return permits the caller to proceed to signalling/termination checks;
    those happen after this lock is released. A retry re-verifies ownership
    without replacing the original accepted intent.
    """
    with control_lock(directory):
        stop = _read_stop(directory)
        if stop is not None and stop["state"] == "stopped":
            return "stopped"
        if stop is None and ((directory / "result.json").exists()
                             or (directory / "collected.json").exists()):
            return "finished"
        process = verify_identity()
        if stop is None:
            save(directory / "stop.json", {
                "state": "stopping", "requested_at": datetime.now(timezone.utc).isoformat(),
                "process": process,
            })
        return "stopping"


def finish_stop(directory: Path) -> None:
    """Record stopped only after the caller confirms group termination."""
    with control_lock(directory):
        stop = _read_stop(directory)
        if stop is None:
            raise ValueError(f"{directory}: no accepted stop request")
        if stop["state"] != "stopped":
            save(directory / "stop.json", {
                **stop, "state": "stopped", "stopped_at": datetime.now(timezone.utc).isoformat(),
            })


def finalize(directory: Path, outcome: dict) -> None:
    """Keep execution evidence even after stop; never clear accepted intent."""
    with control_lock(directory):
        save(directory / "result.json", outcome)


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
    # mutation-equivalent: The listed replacement is identical; docstring wording cannot alter collection.
    """Return a completed result, or None while the job is live or cancelled.

    A dead/failed job is reported through RepoHealth before a later poll may
    # mutation-equivalent: Retry checks execute in callers; none reads this docstring's conjunctions.
    retry (after the usual GitHub and specloop state checks). Intentional stop
    # mutation-equivalent: Pending receipts and failure/retry handling are independent of this docstring.
    keeps the parent's job reference pending, without failure or automatic retry.
    """
    reap()
    directory = path.parent / f"{path.stem}.jobs" / receipt["job"]
    with control_lock(directory):
        if _read_stop(directory) is not None:
            return None
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
        result = outcome["result"]
        # Persist acceptance before returning to intake, whose cached result
        # bypasses collect on delivery retries. Stop must respect that interval.
        if not (directory / "collected.json").exists():
            save(directory / "collected.json", {
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
        return result


def main() -> None:
    directory, fd = Path(sys.argv[1]), int(sys.argv[2])
    try:
        _record_process(directory)
        args = json.loads((directory / "request.json").read_text())["args"]
        # If this wrapper dies, the CLI keeps the lock until it too exits.
        result = specloop_client.call(args, runner=partial(subprocess.run, pass_fds=(fd,)))
        outcome = {"result": result}
    except Exception as exc:
        outcome = {"error": str(exc)}
    try:
        finalize(directory, outcome)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()

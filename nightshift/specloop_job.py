"""Durable background CLI jobs for intake, without importing specloop internals.

The child owns an inherited file lock until its result is on disk. A new
daemon can therefore distinguish a running job from a dead one without PID
reuse races. The poller and explicit release write intake receipts; children
write their own atomic result files. Logs use files so a verbose CLI cannot fill a pipe.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from . import specloop_client


_children: dict[str, subprocess.Popen] = {}
COMMAND_TIMEOUT_SECONDS = {"propose": 3600, "admit": 300, "revise": 1800}


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
    started_at: float | None = None
    timeout_seconds: float | None = None
    deadline_at: float | None = None

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


def _read_timing(request: dict, stage: str) -> tuple[float | None, float | None, float | None]:
    """Validate persisted timing without inferring or migrating legacy values."""
    if "stage" in request and request["stage"] != stage:
        raise ValueError("request.json: stage does not match inventory stage")
    keys = ("started_at", "timeout_seconds", "deadline_at")
    if not any(key in request for key in keys):
        return None, None, None
    if not all(key in request for key in keys):
        raise ValueError("request.json: partial timing")
    if all(request[key] is None for key in keys):
        return None, None, None
    values = tuple(request[key] for key in keys)
    try:
        valid = all(type(value) in (int, float) and math.isfinite(value) for value in values)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("request.json: timing must contain finite numbers")
    started_at, timeout_seconds, deadline_at = values
    if request.get("stage") != stage:
        raise ValueError("request.json: timed request requires its inventory stage")
    if timeout_seconds <= 0 or deadline_at != started_at + timeout_seconds:
        raise ValueError("request.json: inconsistent timing or nonpositive timeout")
    return started_at, timeout_seconds, deadline_at


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


def _argument(args: list[str], flag: str) -> str | None:
    values = []
    for index, arg in enumerate(args):
        if arg == flag:
            if index + 1 == len(args):
                raise ValueError(f"request.json: missing {flag} value")
            values.append(args[index + 1])
        elif arg.startswith(flag + "="):
            values.append(arg.split("=", 1)[1])
    if len(values) > 1:
        raise ValueError(f"request.json: repeated {flag}")
    return values[0] if values else None


def _request_round_id(request: dict) -> str | None:
    args = request.get("args")
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        raise ValueError("request.json: invalid argument list")
    key = request.get("round_id")
    if key is not None and (not isinstance(key, str)
                           or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key)):
        raise ValueError("request.json: invalid round identity")
    if _argument(args, "--round-id") != key or key is not None and args[0] != "revise":
        raise ValueError("request.json: inconsistent round identity and argv")
    return key


def validate_revision_request(request: dict, receipt: dict, *, job_id: str | None = None) -> None:
    """Never infer keyed provenance from a historical event-derived round_id."""
    key = _request_round_id(request)
    declared = "revision_key_version" in receipt or "round_job" in receipt
    if key is None and not declared:
        return
    if (key is None or type(receipt.get("revision_key_version")) is not int
            or receipt["revision_key_version"] != 1 or receipt.get("round_id") != key
            or receipt.get("command") != "revise"
            or job_id is not None and (receipt.get("round_job") != job_id
                                      or receipt.get("job", job_id) != job_id)):
        raise ValueError("inconsistent revision attempt provenance")
    args = request["args"]
    feedback = receipt.get("feedback")
    source = _argument(args, "--feedback")
    if (len(args) < 2 or args[1] != receipt.get("change")
            or _argument(args, "--feedback-format") != "json"
            or not isinstance(feedback, list) or not feedback
            or not all(isinstance(block, str) for block in feedback)
            or source is None or not Path(source).is_absolute()
            or json.loads(Path(source).read_text()) != feedback):
        raise ValueError("inconsistent revision change or feedback provenance")


class ReceiptChanged(RuntimeError):
    """A stale poll must defer to the current receipt writer."""


_revision_writers = threading.local()


@contextmanager
def _revision_writer(path: Path):
    """Cover snapshots with no attempt lock yet, without creating lock files.

    The existing repository directory is stable across atomic receipt saves.
    Reentrant in this thread so start can also protect standalone dispatch;
    competing polls defer. Attempt control then release locks still follow.
    """
    parent = path.parent.resolve()
    held = getattr(_revision_writers, "held", ())
    if parent in held:
        yield
        return
    descriptor = os.open(parent, os.O_RDONLY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReceiptChanged("revision writer active; retry on a later poll") from exc
        _revision_writers.held = (*held, parent)
        try:
            yield
        finally:
            _revision_writers.held = held
    finally:
        os.close(descriptor)


def _check_unattempted(path: Path, receipt: dict) -> None:
    """Missing receipt pointers cannot erase retained keyed attempt evidence."""
    root = path.with_suffix(".jobs")
    if not root.exists():
        return
    for directory in root.iterdir():
        # Only UUID directories can contain attempts dispatched by this writer.
        if not directory.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", directory.name):
            continue
        request = _read_object(directory / "request.json")
        key = _request_round_id(request)
        if key is not None and (key == receipt.get("round_id")
                                or "revision_key_version" not in receipt):
            raise ValueError("inconsistent revision attempt provenance: missing round_job")


@contextmanager
def revision_attempt(path: Path, receipt: dict):
    """Inspect provenance and persist a successor within the same context.

    Callers retain their eligibility/authorization/pause gates. Keep this
    context open through snapshot saving and start(), so release, collection
    and other retry polls cannot replace the evidence between read and write.
    Only retry/unattempted permit reusing a key; released/completed permit selection.
    This read never creates lock files or repairs evidence. ReceiptChanged defers
    stale writers; invalid or missing provenance raises instead of redispatching.
    """
    job_id = receipt.get("round_job", receipt.get("job"))
    with _revision_writer(path), ExitStack() as locks:
        directory = None
        if job_id is not None:
            if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
                raise ValueError("invalid revision attempt reference")
            directory = path.with_suffix(".jobs") / job_id
            control = locks.enter_context((directory / "control.lock").open("r"))
            fcntl.flock(control, fcntl.LOCK_EX)
        elif "round_job" in receipt or "job" in receipt:
            raise ValueError("invalid revision attempt reference")
        try:
            release = path.with_suffix(".release.lock").open("r")
        except FileNotFoundError:
            pass
        else:
            locks.enter_context(release)
            fcntl.flock(release, fcntl.LOCK_EX)
        if _read_object(path) != receipt:
            raise ReceiptChanged("revision receipt changed; retry on a later poll")
        if directory is None:
            _check_unattempted(path, receipt)
            if "revision_key_version" in receipt:
                key = receipt.get("round_id")
                if (type(receipt["revision_key_version"]) is not int
                        or receipt["revision_key_version"] != 1
                        or not isinstance(key, str)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key)
                        or receipt.get("command") != "revise"):
                    raise ValueError("invalid revision snapshot provenance")
                yield "unattempted"
            else:
                yield "legacy"
            return
        request = _read_object(directory / "request.json")
        validate_revision_request(request, receipt, job_id=job_id)
        _read_timing(request, path.parent.parent.name)
        lifetime = locks.enter_context((directory / "lock").open("r"))
        try:
            fcntl.flock(lifetime, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield "pending"
            return
        process, stop = _read_process(directory), _read_stop(directory)
        # _job_state takes the lifetime lock itself; drop our probe first.
        fcntl.flock(lifetime, fcntl.LOCK_UN)
        state = _job_state(directory, process)
        if "result" in receipt and stop is None and state in ("finished", "failed"):
            yield "completed"
        elif "job" in receipt or "result" in receipt or receipt.get("delivered"):
            yield "pending"
        elif state == "stopped":
            if (process is None or process["start_identity"] is None or stop is None
                    or stop.get("process") != process
                    or any(not isinstance(stop.get(key), str)
                           or datetime.fromisoformat(stop[key]).tzinfo is None
                           for key in ("requested_at", "stopped_at"))):
                raise ValueError("confirmed stop with matching process identity required")
            yield "released"
        elif state == "failed":
            yield "retry"
        else:
            yield "pending"


def list_jobs(state_dir: Path) -> tuple[list[Job], list[str]]:
    """Return local attempts and diagnostics, independent of receipts/handles."""

    # Rows sort by stage, repository, numeric item and full ID. Malformed jobs
    # remain visible as unknown when their directory context can be recovered;
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
                    started_at = timeout_seconds = deadline_at = None
                    record_errors = []
                    try:
                        request = _read_object(directory / "request.json")
                        args = request.get("args")
                        if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
                            raise ValueError("request.json: expected a nonempty string argument list")
                        command = tuple(args)
                        _request_round_id(request)
                        started_at, timeout_seconds, deadline_at = _read_timing(request, stage)
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
                                    number, command, state, directory, process,
                                    started_at, timeout_seconds, deadline_at))
    jobs.sort(key=lambda job: (job.stage, job.repository, job.number, job.id))
    return jobs, errors


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data) + "\n")
    temp.replace(path)


def _cleanup_file(path: Path) -> None:
    """Reject links before opening evidence or an exact deletion target."""
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"symlinked cleanup evidence: {path}")
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"not a regular cleanup file: {path}")


def _redact_idea(args: object) -> tuple[list[str], list[str]]:
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        raise ValueError("invalid request arguments")
    retained, files = [], []
    arguments = iter(args)
    for arg in arguments:
        flag, equals, value = arg.partition("=")
        if flag not in ("--idea", "--idea-file"):
            retained.append(arg)
            continue
        if not equals:
            try:
                value = next(arguments)
            except StopIteration:
                raise ValueError("missing idea argument") from None
        if flag == "--idea-file":
            files.append(value)
    return retained, files


def _owned_idea(directory: Path, value: str) -> Path:
    _cleanup_file(Path(value).absolute())  # Check links before normalizing '..'.
    target = Path(os.path.abspath(value))
    root = directory.parent
    _cleanup_file(target)
    if (not target.is_relative_to(root)
            or target.name in {"request.json", "result.json", "process.json", "stop.json",
                               "collected.json", "cleanup.json", "lock", "control.lock", "log"}
            or any(p != directory and p.is_dir() and target.is_relative_to(p)
                   for p in root.iterdir())):
        raise ValueError("idea file is outside this attempt's ownership")
    # A malformed sibling cannot prove exclusive ownership. Compacted siblings
    # retain their deletion references in the journal after argv is redacted.
    for other in root.iterdir():
        if not other.is_dir():
            continue
        _cleanup_file(other / "request.json")
        args = _read_object(other / "request.json").get("args")
        retained, references = _redact_idea(args)
        if other != directory:
            journal = other / "cleanup.json"
            _cleanup_file(journal)
            if journal.exists():
                references += _read_object(journal)["idea_references"]
        else:
            references = []
        # Preserve feedback (and any other file argument), even if also named
        # as an idea input. Never infer ownership merely from a file extension.
        # Only option tokens use equals syntax; standalone paths may contain '='.
        references += [arg.split("=", 1)[-1] if arg.startswith("--") else arg
                       for arg in retained]
        for ref in references:
            candidate = Path(ref).absolute()
            if any(p.is_symlink() for p in (candidate, *candidate.parents)):
                raise ValueError("cannot prove ownership through a symlinked reference")
            if Path(os.path.abspath(ref)) == target:
                raise ValueError("idea file is shared by another argument or attempt")
    return target


def _cleanup_save(path: Path, data: dict, *, timestamps=None) -> None:
    temp = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x") as stream:
            stream.write(json.dumps(data) + "\n")
            stream.flush()
            if timestamps is not None:
                os.utime(temp, ns=timestamps)  # Before publication, even on interruption.
            os.fsync(stream.fileno())
        temp.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temp.unlink(missing_ok=True)


def resume_cleanup(path: Path, job_id: str) -> list[str]:
    """Resume journaled delivery after the caller checks current item authority."""
    return _compact_delivered(path, {"job": job_id}, resume=True)


def compact_delivered(path: Path, receipt: dict, *, prepare_only=False) -> list[str]:
    """Journal validated delivery; optionally defer artifact changes until resume."""
    return _compact_delivered(path, receipt, prepare_only=prepare_only)


def _compact_delivered(path: Path, receipt: dict, *, resume=False, prepare_only=False) -> list[str]:
    """Compact one durably delivered attempt; return local diagnostics.

    The caller must have passed this visit's eligibility, authorization and
    pause gates. Neither a delivered receipt nor a journal grants that authority.
    No receipt, result, control record or directory is removed or rewritten.
    """
    diagnostics: list[str] = []
    try:
        job_id = receipt.get("job")
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("missing exact job identity")
        path = path.absolute()
        stage = path.parent.parent.name
        if (stage not in ("proposals", "admissions", "revisions") or path.suffix != ".json"
                or not path.stem.isdecimal() or int(path.stem) <= 0):
            raise ValueError("invalid attempt context")
        directory = path.parent / f"{path.stem}.jobs" / job_id
        release = path.with_suffix(".release.lock")
        evidence = [path, release, *(directory / name for name in (
            "request.json", "result.json", "process.json", "stop.json", "collected.json",
            "cleanup.json", "lock", "control.lock", "log"))]
        for record in evidence:
            _cleanup_file(record)
        with ExitStack() as locks:
            for record in (directory / "control.lock", release, directory / "lock"):
                if record == release and not record.exists():
                    continue  # Never manufacture a release lock for cleanup.
                lock = locks.enter_context(record.open("r"))
                fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if record.name == "lock" else 0))
            for record in evidence:
                _cleanup_file(record)
            if not resume and (_read_object(path) != receipt or receipt.get("delivered") is not True):
                raise ValueError("receipt changed or delivery is incomplete")
            context = {"repo": path.parent.name.replace("__", "/", 1),
                       "issue" if stage == "proposals" else "pr": int(path.stem)}
            if any(key in receipt and (type(receipt[key]) is not type(value) or receipt[key] != value)
                   for key, value in context.items()):
                raise ValueError("conflicting receipt item context")
            if _read_stop(directory) is not None:
                raise ValueError("stopped attempts retain their evidence")
            _read_process(directory)
            if (directory / "collected.json").exists():
                collected = _read_object(directory / "collected.json")
                if not isinstance(collected.get("collected_at"), str) or not collected["collected_at"]:
                    raise ValueError("invalid collection record")
            request_path = directory / "request.json"
            original = request_path.stat()  # Capture before the first request read.
            request = _read_object(request_path)
            _request_round_id(request)
            if not resume:
                validate_revision_request(request, receipt, job_id=job_id)
            timing = _read_timing(request, stage)
            args, idea_files = _redact_idea(request.get("args"))
            outcomes = {"propose": ("drafted", "needs_input", "rejected"),
                        "admit": ("admitted", "refused"), "revise": ("revised", "refused")}
            command = args[0]
            result = _read_object(directory / "result.json")
            payload = result.get("result")
            if (set(result) != {"result"} or not isinstance(payload, dict)
                    or type(payload.get("version")) is not int or payload["version"] != 1
                    or "error" in payload or payload.get("outcome") not in outcomes.get(command, ())
                    or not resume and payload != receipt.get("result")
                    or command != receipt.get("command", command)
                    or stage == "proposals" and command != "propose"
                    or stage == "admissions" and command != "admit"
                    or stage == "revisions" and command not in ("propose", "revise")
                    or payload.get("outcome") in ("drafted", "revised") and not payload.get("commit")
                    or not resume and payload.get("outcome") == "drafted"
                    and payload.get("branch") != f"spec/{receipt.get('change')}"):
                raise ValueError("invalid or conflicting terminal result")
            for flag in ("change", "head", "branch"):
                values = [a.split("=", 1)[1] for a in args if a.startswith(f"--{flag}=")]
                values += [args[i + 1] for i, a in enumerate(args[:-1]) if a == f"--{flag}"]
                if flag == "change" and command == "revise":
                    values.append(args[1])
                if flag in receipt and any(v != receipt[flag] for v in values):
                    raise ValueError(f"conflicting request {flag}")
            minimal = dict(stage=stage, args=args, **dict(zip(
                ("started_at", "timeout_seconds", "deadline_at"), timing)))
            if "round_id" in request:
                minimal["round_id"] = request["round_id"]
            identity = dict(version=1, job_id=job_id, stage=stage,
                            repository=path.parent.name, number=int(path.stem), request=minimal,
                            result_sha256=hashlib.sha256((directory / "result.json").read_bytes()).hexdigest())
            journal_path = directory / "cleanup.json"
            if journal_path.exists():
                journal = _read_object(journal_path)
                if (any(type(journal.get(k)) is not type(v) or journal[k] != v for k, v in identity.items())
                        or any(type(journal.get(k)) is not int for k in ("atime_ns", "mtime_ns"))
                        or type(journal.get("complete")) is not bool
                        or any(not isinstance(journal.get(key), list)
                               or not all(isinstance(v, str) for v in journal[key])
                               for key in ("idea_files", "idea_references"))):
                    raise ValueError("invalid cleanup journal identity")
            else:
                if resume:
                    raise ValueError("missing cleanup journal")
                owned = []
                for value in idea_files:
                    try:
                        owned.append(str(_owned_idea(directory, value)))
                    except (OSError, ValueError, KeyError) as exc:
                        diagnostics.append(f"retained idea input: {exc}")
                journal = dict(**identity, atime_ns=original.st_atime_ns,
                               mtime_ns=original.st_mtime_ns, idea_files=owned, complete=False,
                               idea_references=[os.path.abspath(v) for v in idea_files])
                _cleanup_save(journal_path, journal)
            if prepare_only or journal["complete"]:
                return diagnostics
            _cleanup_save(request_path, minimal, timestamps=(journal["atime_ns"], journal["mtime_ns"]))
            for value in journal["idea_files"]:
                _owned_idea(directory, value).unlink(missing_ok=True)
            (directory / "log").unlink(missing_ok=True)
            _cleanup_save(journal_path, {**journal, "complete": True})
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        diagnostics.append(f"cannot compact {path}: {exc}")
    return diagnostics


@contextmanager
def control_lock(directory: Path):
    """Serialize stop admission, finalization and collection across processes.

    This is separate from the inherited lifetime lock. Never wait for that
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
    """Admit stop intent, returning stopping, stopped, or finished.

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


class StopJobError(RuntimeError):
    """An unknown, unsafe or uncontrollable job (CLI callers should exit 1)."""


class ReleaseJobError(RuntimeError):
    """A job cannot safely be detached (CLI callers should exit 1)."""


def release_job(state_dir: Path, job_id: str) -> str:
    """Detach only a confirmed stopped attempt, retaining all its evidence."""
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ReleaseJobError("expected an exact full job ID, not a path or prefix")
    rows, errors = list_jobs(state_dir)
    matches = [job for job in rows if job.id == job_id]
    if len(matches) != 1:
        raise ReleaseJobError(f"{'unknown' if not matches else 'ambiguous'} job ID {job_id}"
                              + (": " + "; ".join(errors) if errors else ""))
    job = matches[0]
    directory = job.directory
    path = directory.parent.with_suffix(".json")

    def matching_receipt() -> dict | None:
        if path.is_symlink():
            raise ValueError("symlinked receipt")
        receipt = _read_object(path)
        if "job" not in receipt:
            return None
        reference = receipt["job"]
        if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{32}", reference):
            raise ValueError("invalid receipt job reference")
        if reference != job_id:
            return None
        if "result" in receipt or receipt.get("delivered", False) is not False:
            raise ValueError("receipt already contains completion evidence")
        return receipt

    try:
        if job.state != "stopped":
            raise ValueError(f"expected a confirmed stopped job, found {job.state}")
        # These locks must already exist. Refusal and no-op never create files.
        with (directory / "control.lock").open("r") as control:
            fcntl.flock(control, fcntl.LOCK_EX)
            with (directory / "lock").open("r") as lifetime:
                fcntl.flock(lifetime, fcntl.LOCK_EX | fcntl.LOCK_NB)
                process, stop = _read_process(directory), _read_stop(directory)
                if (process is None or process["start_identity"] is None or stop is None
                        or stop["state"] != "stopped" or stop.get("process") != process):
                    raise ValueError("confirmed stop with matching process identity required")
                for key in ("requested_at", "stopped_at"):
                    stamp = stop.get(key)
                    if (not isinstance(stamp, str)
                            or datetime.fromisoformat(stamp).tzinfo is None):
                        raise ValueError(f"invalid stop timestamp: {key}")
                args = _read_object(directory / "request.json").get("args")
                if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
                    raise ValueError("invalid request argument list")
                if (directory / "collected.json").exists():
                    raise ValueError("job result was already collected")
                try:
                    outcome = _read_object(directory / "result.json")
                except FileNotFoundError:
                    pass
                else:
                    if not (set(outcome) == {"result"} and isinstance(outcome["result"], dict)
                            or set(outcome) == {"error"} and isinstance(outcome["error"], str)):
                        raise ValueError("invalid result record")
                if matching_receipt() is None:
                    return "already released"
                # Lock order is job control, then receipt release. Reread after
                # acquiring the receipt lock so a stale copy cannot be saved.
                with path.with_suffix(".release.lock").open("a") as release:
                    fcntl.flock(release, fcntl.LOCK_EX)
                    receipt = matching_receipt()
                    if receipt is None:
                        return "already released"
                    del receipt["job"]
                    save(path, receipt)
                return "released"
    except (OSError, ValueError) as exc:
        raise ReleaseJobError(f"cannot release job {job_id}: {exc}; see {job.log_path}") from exc


def _verify_process(directory: Path) -> dict:
    process = _read_process(directory)
    if process is None or process["start_identity"] is None:
        raise ValueError("process identity unavailable; legacy or starting job cannot be stopped")
    pid, pgid = process["pid"], process["pgid"]
    if pgid in (0, 1, os.getpgrp()) or pid != pgid:
        raise ValueError("unsafe process group: expected an independent wrapper leader")
    stop = _read_stop(directory)
    if stop is not None and stop.get("process") != process:
        raise ValueError("process identity differs from accepted stop intent")
    if (os.getpgid(pid) != pgid or os.getsid(pid) != pgid
            or process_start_identity(pid) != process["start_identity"]):
        raise ValueError("wrapper process identity or session no longer matches")
    command = subprocess.run(
        ["/bin/ps", "-ww", "-p", str(pid), "-o", "args="],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout.strip()
    # ps renders argv with spaces (including spaces inside path arguments).
    # Anchor the entire wrapper suffix, including its exact absolute job path.
    if not re.fullmatch(
        r".+ -m nightshift\.specloop_job " + re.escape(str(directory.resolve())) + r" [0-9]+",
        command,
    ):
        raise ValueError("leader command does not identify this job's wrapper")
    return process


def _executing_group(pgid: int, *, timeout: float = 5) -> set[int]:
    """Probe every member; zombies waiting for a parent are no longer work."""
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,pgid=,stat="],
        capture_output=True, text=True, timeout=timeout, check=True,
    )
    if not result.stdout.strip():
        raise ValueError("process inspection returned no process table")
    members = set()
    for line in result.stdout.splitlines():
        pid, group, state = line.split()
        if int(group) == pgid and not state.startswith("Z"):
            members.add(int(pid))
    return members


def _wait_group(process: dict, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        reap()
        # A restarted caller may still be the parent but have lost its Popen.
        try:
            os.waitpid(process["pid"], os.WNOHANG)
        except ChildProcessError:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if not _executing_group(process["pgid"], timeout=remaining):
            return time.monotonic() <= deadline
        time.sleep(min(.05, max(0, deadline - time.monotonic())))


def _group_identities(process: dict, members: set[int]) -> dict[int, str]:
    """Remember live witnesses while the wrapper still proves ownership.

    SIGTERM may kill the wrapper before an ignoring descendant. A surviving
    member with the same start identity and group/session proves continuity
    for escalation within this call; a later stop still requires the leader.
    """
    identities = {}
    for pid in members:
        try:
            identity = process_start_identity(pid)
            if os.getpgid(pid) == process["pgid"] and os.getsid(pid) == process["pgid"]:
                identities[pid] = identity
        except ProcessLookupError:
            continue
        except subprocess.CalledProcessError as exc:
            # ps returns 1 when a member exits during inspection.
            if exc.returncode != 1:
                raise
    return identities


def _verify_escalation(directory: Path, process: dict, members: set[int],
                       identities: dict[int, str]) -> None:
    if process["pid"] in members:
        if _verify_process(directory) != process:
            raise ValueError("process identity changed before escalation")
        return
    for pid in members & identities.keys():
        try:
            if (process_start_identity(pid) == identities[pid]
                    and os.getpgid(pid) == process["pgid"]
                    and os.getsid(pid) == process["pgid"]):
                return
        except ProcessLookupError:
            continue
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 1:
                raise
    raise ValueError("cannot verify surviving group ownership before SIGKILL")


def stop_job(state_dir: Path, job_id: str) -> str:
    """Stop an exactly identified owned group; return stopped or finished.

    Raise StopJobError on refusal or operational failure. Accepted intent is
    retained on failure, suppressing collection until an operator retries.
    """
    # Reject paths and UUID prefixes before any filesystem access.
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise StopJobError("expected an exact full job ID, not a path or prefix")
    rows, errors = list_jobs(state_dir)
    matches = [job for job in rows if job.id == job_id]
    if len(matches) != 1:
        detail = "; ".join(errors)
        raise StopJobError(f"{'unknown' if not matches else 'ambiguous'} job ID {job_id}"
                           + (f": {detail}" if detail else ""))
    job = matches[0]
    try:
        if job.state == "unknown":
            raise ValueError("unreadable job records: " + "; ".join(
                error for error in errors if str(job.directory) in error))
        process = None

        def verify():
            nonlocal process
            process = _verify_process(job.directory)
            return process

        state = request_stop(job.directory, verify_identity=verify)
        if state != "stopping":
            return state
        # request_stop has released control.lock before any signal or wait.
        identities = {}
        for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 1)):
            members = _executing_group(process["pgid"])
            if not members:
                finish_stop(job.directory)
                return "stopped"
            if sig == signal.SIGTERM:
                identities = _group_identities(process, members)
                if _verify_process(job.directory) != process:
                    raise ValueError("process identity changed before signalling")
            else:
                _verify_escalation(job.directory, process, members, identities)
            try:
                os.killpg(process["pgid"], sig)
            except ProcessLookupError:
                # The group may have exited between verification and signal.
                pass
            try:
                terminated = _wait_group(process, grace)
            except subprocess.TimeoutExpired:
                # A probe uses the remaining phase budget. Near the TERM
                # deadline even a normal ps can run out of time: proceed to
                # fresh ownership verification and escalation. After KILL,
                # a timed-out probe leaves termination unconfirmed.
                if sig != signal.SIGTERM:
                    raise
                terminated = False
            if terminated:
                finish_stop(job.directory)
                return "stopped"
        raise ValueError("process group still executing after SIGKILL or termination unconfirmed "
                         "within 1 second; retry stop after inspection")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise StopJobError(f"cannot stop job {job_id}: {exc}; see {job.log_path}") from exc


def finalize(directory: Path, outcome: dict) -> None:
    """Keep execution evidence even after stop; never clear accepted intent."""
    with control_lock(directory):
        save(directory / "result.json", outcome)


def reap() -> None:
    """Reap even children whose issue/PR is no longer armed."""
    for key, child in list(_children.items()):
        if child.poll() is not None:
            del _children[key]


def start(path: Path, receipt: dict, args: list[str], *, launcher=None, clock=None) -> None:
    coordinated = path.parent.resolve() in getattr(_revision_writers, "held", ())
    if _argument(args, "--round-id") is not None and not coordinated:
        with revision_attempt(path, receipt) as disposition:
            if disposition not in ("unattempted", "retry"):
                raise ValueError("revision attempt does not permit dispatch")
            _start(path, receipt, args, launcher=launcher, clock=clock)
    else:
        _start(path, receipt, args, launcher=launcher, clock=clock)


def _start(path: Path, receipt: dict, args: list[str], *, launcher=None, clock=None) -> None:
    reap()
    started_at = (clock or time.time)()
    timeout_seconds = COMMAND_TIMEOUT_SECONDS[args[0]]
    request = {
        "stage": path.parent.parent.name, "args": args,
        "started_at": started_at, "timeout_seconds": timeout_seconds,
        "deadline_at": started_at + timeout_seconds,
        "round_id": _argument(args, "--round-id"),
    }
    _read_timing(request, path.parent.parent.name)
    validate_revision_request(request, receipt)
    if request["round_id"] is not None:
        if _read_object(path) != receipt:
            raise ReceiptChanged("revision snapshot changed before launch")
        if "job" in receipt:
            raise ValueError("revision attempt is still attached")
    job = uuid.uuid4().hex
    directory = path.parent / f"{path.stem}.jobs" / job
    directory.mkdir(parents=True)
    (directory / "control.lock").touch()
    save(directory / "request.json", request)
    with (directory / "lock").open("w") as lock, (directory / "log").open("w") as log:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt["job"] = job
        if request["round_id"] is not None:
            receipt["round_job"] = job
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


class JobOverdue(RuntimeError):
    """A live attempt crossed its reporting deadline; execution stays attached."""

    def __init__(self, directory: Path, command: str, elapsed: float, budget: float):
        self.job_id, self.command = directory.name, command
        self.elapsed, self.budget = elapsed, budget
        self.log_path = directory / "log"
        super().__init__(
            f"Specloop job {self.job_id} ({command}) is overdue: elapsed {elapsed:g}s, "
            f"deadline budget {budget:g}s. Log: {self.log_path}. "
            f"Inspect with `nightshift jobs` and read the log; "
            f"stop with `nightshift jobs stop {self.job_id}`, then inspect retained evidence "
            f"before `nightshift jobs release {self.job_id}`. "
            "The job has not been terminated or replaced."
        )


def collect(path: Path, receipt: dict) -> dict | None:
    """Return a completed result, or None while the job is live or cancelled.

    A dead/failed job is reported through RepoHealth before a later poll may
    retry (after the usual GitHub and specloop state checks). Intentional stop
    keeps the parent's job reference pending, without failure or automatic retry.
    """
    reap()
    directory = path.parent / f"{path.stem}.jobs" / receipt["job"]
    with control_lock(directory):
        return _collect_locked(path, receipt)


def _collect_locked(path: Path, receipt: dict) -> dict | None:
    """Collect with the caller already holding the attempt control lock."""
    directory = path.parent / f"{path.stem}.jobs" / receipt["job"]
    if _read_stop(directory) is not None:
        return None
    with (directory / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                request = _read_object(directory / "request.json")
            except FileNotFoundError:
                return None  # Older retained work may lack a request and deadline.
            started, budget, deadline = _read_timing(request, path.parent.parent.name)
            now = time.time()
            if started is not None and budget is not None and deadline is not None and now >= deadline:
                raise JobOverdue(directory, request["args"][0], max(0, now - started), budget)
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

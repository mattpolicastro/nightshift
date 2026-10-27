"""Private, confirmed Factory landing and detached execution evidence.

Deliberately absent from both canonical CLIs and background job control.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import ctypes
from dataclasses import asdict, dataclass
import errno
import fcntl
import json
import locale
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, cast
import uuid

from specloop.state import SAFE_CHANGE, state_root

from . import land
from .host_paths import host_path, tool_path


MAX_LIMIT = 20
DETAIL_LIMIT = 8192
RECORD_LIMIT = 256 * 1024
OUTPUT_LIMIT = 1024 * 1024
START_ALLOWANCE = 60
OVERDUE_SECONDS = 2400
_SHA = re.compile(r"[a-fA-F0-9]{40}")
_PR = re.compile(r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)")
_ATTEMPT = re.compile(r"[a-f0-9]{32}")
_PHASES = {"starting", "validating", "recording checks", "finishing", "checking CI", "merging"}
_OUTCOMES = {"succeeded", "failed", "refused", "stopped"}


class Refusal(ValueError):
    """No execution is permitted; the reason can be retained and displayed."""


def _timestamp(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _change(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 255 or not SAFE_CHANGE.fullmatch(value):
        raise ValueError("invalid change identifier")
    return value


@dataclass(frozen=True)
class Offer:
    change: str
    pr: str
    pause_id: str
    head: str
    since: int | float

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> Offer:
        change = _change(value.get("change"))
        pr, pause_id, head, since = (value.get(key) for key in ("pr", "pause_id", "head", "since"))
        match = _PR.fullmatch(pr) if isinstance(pr, str) and len(pr) <= 512 else None
        if match is None or match[2] in (".", ".."):
            raise ValueError("invalid canonical GitHub PR URL")
        if (not isinstance(pause_id, str) or not pause_id.startswith("human_checks:")
                or len(pause_id) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in pause_id)):
            raise ValueError("invalid human_checks pause_id")
        if not isinstance(head, str) or not _SHA.fullmatch(head):
            raise ValueError("invalid full head SHA")
        if not _timestamp(since):
            raise ValueError("invalid finite pause since")
        return cls(change, cast(str, pr), pause_id, head.lower(), cast(int | float, since))


def offer_from_status(document: Any, change: str) -> Offer:
    """Project only a complete version-1 human-check pause; never change status."""
    _change(change)
    if (not isinstance(document, dict) or type(document.get("version")) is not int
            or document["version"] != 1 or "error" in document
            or not isinstance(document.get("runs"), list)):
        raise Refusal("landing offer unavailable: unreadable version-1 status")
    rows = [row for row in document["runs"] if isinstance(row, dict) and row.get("change") == change]
    if len(rows) != 1:
        raise Refusal("landing offer unavailable: expected one existing run")
    row = rows[0]
    pause, action = row.get("pause"), row.get("action")
    if ("error" in row or row.get("status") != "paused" or not isinstance(pause, dict)
            or pause.get("reason") != "human_checks"):
        raise Refusal("landing offer unavailable: run is not a readable human_checks pause")
    try:
        return Offer.parse({**row, "since": pause.get("since"),
                            "pause_id": action.get("pause_id") if isinstance(action, dict) else None})
    except ValueError as exc:
        raise Refusal(f"landing offer unavailable: {exc}") from exc


def _read_json_command(argv: list[str]) -> Any:
    result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=20)
    if result.returncode:
        raise Refusal("fresh identity read failed")
    return json.loads(result.stdout)


def read_run_status(change: str) -> Any:
    return _read_json_command([sys.executable, "-m", "nightshift.cli", "status", change, "--json"])


def read_remote_head(pr: str) -> str:
    data = _read_json_command(["gh", "pr", "view", pr, "--json", "url,headRefOid"])
    if (not isinstance(data, dict) or data.get("url") != pr
            or not isinstance(data.get("headRefOid"), str) or not _SHA.fullmatch(data["headRefOid"])):
        raise Refusal("fresh PR identity is unreadable or contradictory")
    return data["headRefOid"].lower()


def validate_offer(
    offer: Offer, *, read_status: Callable[[str], Any] = read_run_status,
    remote_head: Callable[[str], str] = read_remote_head, pause_file: Path | None = None,
) -> None:
    """Shared fresh admission/startup check, with no cached permission or writes."""
    pause_file = pause_file if pause_file is not None else Path.home() / ".nightshift" / "pause"
    try:
        # lstat also treats a dangling pause symlink as a pause, and fails closed
        # on permission errors rather than treating them as absence.
        try:
            pause_file.lstat()
        except FileNotFoundError:
            pass
        else:
            raise Refusal("daemon is paused; new landing execution is refused")
        current = offer_from_status(read_status(offer.change), offer.change)
        if current != offer:
            raise Refusal("stale landing confirmation: published pause identity changed")
        head = remote_head(offer.pr)
        if not isinstance(head, str) or not _SHA.fullmatch(head) or head.lower() != offer.head:
            raise Refusal("stale landing confirmation: remote PR head differs from published head")
    except Refusal:
        raise
    except (OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError) as exc:
        raise Refusal(f"fresh landing identity could not be read: {exc}") from exc


class ConfirmedRunner:
    """Bind land's pre-finish calls to one confirmation, without owning its gates.

    Inspect the responses land actually consumes as well as independent fresh
    identity reads before writes. After finish, land alone validates successor
    heads and conditions the merge. No attestation state survives this runner.
    """

    def __init__(self, offer: Offer, *, runner: land.Runner | None = None,
                 read_status: Callable[[str], Any] = read_run_status,
                 remote_head: Callable[[str], str] = read_remote_head,
                 pause_file: Path | None = None):
        self.offer = Offer.parse(asdict(offer))
        self.runner = subprocess.run if runner is None else runner
        self.read_status, self.remote_head, self.pause_file = read_status, remote_head, pause_file
        match = _PR.fullmatch(self.offer.pr)
        assert match is not None
        self.repo, self.number = f"{match[1]}/{match[2]}".lower(), match[3]
        self.pr_read = self.status_read = self.finished = False

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if self.finished:
            return self.runner(argv, **kwargs)
        repo, number = self.repo, self.number
        view = ["gh", "pr", "view", number, "--repo", repo, "--json", land.PR_FIELDS]
        status = [sys.executable, "-m", "nightshift.cli", "status", "--json"]
        finish = [sys.executable, "-m", "nightshift.cli", "finish", self.offer.change,
                  "--expect-head", self.offer.head]
        reads = [view, status, ["gh", "api", f"repos/{repo}/git/ref/heads/main"],
                 ["gh", "api", f"repos/{repo}/issues/{number}/comments", "--paginate", "--slurp"],
                 ["gh", "api", "user"]]
        is_finish = (len(argv) == len(finish) and argv[:-1] == finish[:-1]
                     and argv[-1].lower() == self.offer.head)
        mutation = is_finish
        if (len(argv) == 7 and argv[:3] == ["gh", "api", "--method"]
                and argv[5] == "-f" and argv[6].startswith("body=")):
            endpoint = argv[4]
            mutation = (
                argv[3] == "POST" and endpoint == f"repos/{repo}/issues/{number}/comments"
            ) or (argv[3] == "PATCH" and (
                endpoint == f"repos/{repo}/pulls/{number}"
                or re.fullmatch(rf"repos/{re.escape(repo)}/issues/comments/[1-9][0-9]*", endpoint)
                is not None
            ))
        try:
            if mutation:
                if not self.pr_read or not self.status_read:
                    raise Refusal("confirmed landing requires actual initial PR and run responses")
                validate_offer(self.offer, read_status=self.read_status,
                               remote_head=self.remote_head, pause_file=self.pause_file)
            elif argv not in reads:
                raise Refusal("unknown pre-finish landing command shape; refusing execution")
            result = self.runner(argv, **kwargs)
            if result.returncode == 0 and argv in (view, status):
                data = json.loads(result.stdout)
                if argv == status:
                    if offer_from_status(data, self.offer.change) != self.offer:
                        raise Refusal("stale landing confirmation: actual run response changed")
                    self.status_read = True
                else:
                    if (not isinstance(data, dict) or not isinstance(data.get("url"), str)
                            or data["url"].lower() != self.offer.pr.lower()
                            or type(data.get("number")) is not int or data["number"] != int(number)
                            or not isinstance(data.get("headRefOid"), str)
                            or data["headRefOid"].lower() != self.offer.head):
                        raise Refusal("stale landing confirmation: actual PR response changed")
                    self.pr_read = True
            if is_finish:
                self.finished = True
            return result
        except (Refusal, ValueError, TypeError, RecursionError) as exc:
            raise land.Refusal(str(exc)) from exc


def invoke_confirmed(offer: Offer, *, runner: land.Runner | None = None,
                     read_status: Callable[[str], Any] = read_run_status,
                     remote_head: Callable[[str], str] = read_remote_head,
                     pause_file: Path | None = None, config_path: Path | None = None) -> int:
    """Invocation-child boundary; lifecycle/records belong to the worker."""
    confirmed = ConfirmedRunner(offer, runner=runner, read_status=read_status,
                                remote_head=remote_head, pause_file=pause_file)
    argv = [offer.pr, "--checks-done"]
    if config_path is not None:
        argv.extend(["--config", str(config_path)])
    return land.main(argv, runner=confirmed)


def _clip(value: str, limit: int = DETAIL_LIMIT) -> str:
    marker = "… [truncated]"
    return value if len(value) <= limit else value[:limit - len(marker)] + marker


def write_record(path: Path, record: dict[str, Any]) -> None:
    """Publish small evidence atomically and durably before dependent execution."""
    data = json.dumps(record, ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(data) > RECORD_LIMIT:
        raise ValueError("landing record exceeds metadata limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_record(path: Path, *, optional: bool = False) -> dict[str, Any] | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(RECORD_LIMIT + 1)
    except FileNotFoundError:
        if optional and not path.is_symlink():
            return None
        raise ValueError(f"missing {path.name}") from None
    if len(raw) > RECORD_LIMIT:
        raise ValueError(f"{path.name} exceeds metadata limit [truncated]")
    value = json.loads(raw)
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError(f"invalid {path.name} version-1 record")
    return value


def attempt_directory(root: Path, offer: Offer, attempt_id: str) -> Path:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise ValueError("invalid attempt ID")
    match = _PR.fullmatch(offer.pr)
    assert match is not None
    return root / "landings" / f"{match[1]}__{match[2]}".lower() / match[3] / "attempts" / attempt_id


def record_progress(directory: Path, phase: str, *, reason: str = "", now: float | None = None) -> None:
    if phase not in _PHASES:
        raise ValueError("invalid landing phase")
    write_record(directory / "progress.json", {
        "version": 1, "phase": phase, "reason": _clip(reason),
        "updated_at": time.time() if now is None else now,
    })


def record_result(directory: Path, outcome: str, reason: str, *, phase: str = "validating",
                  exit_code: int | None = None, now: float | None = None) -> None:
    if outcome not in _OUTCOMES or phase not in _PHASES:
        raise ValueError("invalid landing result")
    write_record(directory / "result.json", {
        "version": 1, "outcome": outcome, "phase": phase, "reason": _clip(reason),
        "exit_code": exit_code, "finished_at": time.time() if now is None else now,
    })


def start_attempt(offer: Offer, *, launch: Callable[[str], Any], root: Path | None = None,
                  checks_done: bool = True, now: Callable[[], float] = time.time,
                  read_status: Callable[[str], Any] = read_run_status,
                  remote_head: Callable[[str], str] = read_remote_head,
                  pause_file: Path | None = None) -> dict[str, Any]:
    """Persist each deliberate confirmation before launch; never replay/retry.

    The launch seam receives only the generated attempt ID. Trusted transport
    configuration, not request fields, will select the detached worker command.
    """
    offer = Offer.parse(asdict(offer))
    if checks_done is not True:
        raise ValueError("explicit checks-done confirmation is required")
    attempt_id = uuid.uuid4().hex
    directory = attempt_directory(root if root is not None else state_root(), offer, attempt_id)
    write_record(directory / "request.json", {
        "version": 1, "attempt_id": attempt_id, "offer": asdict(offer),
        "checks_done": True, "created_at": now(),
    })
    try:
        validate_offer(offer, read_status=read_status, remote_head=remote_head, pause_file=pause_file)
    except Refusal as exc:
        record_result(directory, "refused", str(exc), now=now())
        return {"version": 1, "attempt_id": attempt_id, "accepted": False, "reason": _clip(str(exc))}
    record_progress(directory, "starting", now=now())
    try:
        launch(attempt_id)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = f"worker launch failed: {exc}"
        record_result(directory, "failed", reason, phase="starting", now=now())
        return {"version": 1, "attempt_id": attempt_id, "accepted": False, "reason": _clip(reason)}
    return {"version": 1, "attempt_id": attempt_id, "accepted": True}


def load_attempt(attempt_id: str, root: Path) -> tuple[Path, Offer]:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise ValueError("invalid attempt ID")
    matches = list((root / "landings").glob(f"*/*/attempts/{attempt_id}"))
    if len(matches) != 1:
        raise Refusal("expected one retained landing request")
    directory = matches[0]
    request = _read_record(directory / "request.json")
    assert request is not None
    offer = Offer.parse(request.get("offer", {}))
    if (request.get("attempt_id") != attempt_id or request.get("checks_done") is not True
            or not _timestamp(request.get("created_at"))
            or directory != attempt_directory(root, offer, attempt_id)):
        raise Refusal("invalid retained landing request")
    return directory, offer


def launch_worker(attempt_id: str, *, root: Path | None = None) -> None:
    root = root if root is not None else state_root()
    directory, _ = load_attempt(attempt_id, root)
    bin_dir = host_path("NIGHTSHIFT_BIN_DIR", "/opt/homebrew/bin")
    argv = [host_path("NIGHTSHIFT_UV", f"{bin_dir}/uv"), "run", "--no-sync", "python",
            "-m", "nightshift.factory_land", "worker", attempt_id]
    env = {**os.environ, "PATH": host_path("NIGHTSHIFT_NODE_BIN", "~/n/bin") + ":" + tool_path(),
           "NIGHTSHIFT_STATE_ROOT": str(root), "PYTHONUNBUFFERED": "1"}
    with (directory / "output.log").open("ab", buffering=0) as output:
        subprocess.Popen(argv, cwd=host_path("NIGHTSHIFT_CHECKOUT", "~/Projects/nightshift"),
                         env=env, stdin=subprocess.DEVNULL, stdout=output,
                         stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)


def read_start_identity(pid: int) -> str:
    """Read the kernel birth time without creating a pre-record subprocess."""
    if sys.platform == "darwin":
        class BSDInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint32) for name in (
                "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid",
                "rgid", "svuid", "svgid", "reserved")
            ] + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)] + [
                (name, ctypes.c_uint32) for name in (
                    "nfiles", "pgid", "jobc", "tdev", "tpgid", "nice")
            ] + [("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64)]
        info = BSDInfo()
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        result = libproc.proc_pidinfo(pid, 3, ctypes.c_uint64(0), ctypes.byref(info), ctypes.sizeof(info))
        if result != ctypes.sizeof(info):
            error = ctypes.get_errno()
            raise OSError(error or errno.EIO, "worker birth identity could not be read")
        return f"darwin:{info.start_seconds}:{info.start_microseconds}"
    if sys.platform.startswith("linux"):
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return f"linux:{int(fields[19])}"
    raise Refusal("worker birth identity is unsupported on this platform")


def process_identity() -> dict[str, Any]:
    pid = os.getpid()
    identity = read_start_identity(pid)
    pgid, sid = os.getpgrp(), os.getsid(0)
    if pgid <= 1 or sid != pgid:
        raise Refusal("worker must run in its own detached session")
    return {"version": 1, "pid": pid, "pgid": pgid, "sid": sid,
            "start_identity": identity, "started_at": time.time()}


def _safe_group(process: dict[str, Any]) -> int:
    pid, pgid, sid = (process.get(key) for key in ("pid", "pgid", "sid"))
    if (any(type(value) is not int or not 1 < value <= 2**31 - 1 for value in (pid, pgid, sid))
            or sid != pgid or pgid in (os.getpgrp(), os.getpgid(os.getppid()))
            or not isinstance(process.get("start_identity"), str)
            or not process["start_identity"].strip()
            or not _timestamp(process.get("started_at"))):
        raise Refusal("unsafe or corrupt prior process identity")
    return cast(int, pgid)


def exclude_prior_groups(directory: Path) -> None:
    """Called only under the PR flock. Only kernel ESRCH proves drainage."""
    for prior in directory.parent.iterdir():
        if prior == directory:
            continue
        try:
            if not prior.is_dir() or not _ATTEMPT.fullmatch(prior.name):
                raise Refusal("corrupt prior attempt directory")
            process = _read_record(prior / "process.json", optional=True)
            if process is None:
                # Publication is the permission boundary for child creation.
                if (prior / "drained.json").exists():
                    raise Refusal("drainage observation has no process identity")
                continue
            pgid = _safe_group(process)
            drained = _read_record(prior / "drained.json", optional=True)
            if drained is not None:
                if (drained.get("process") != process or drained.get("drained") is not True
                        or not _timestamp(drained.get("observed_at"))):
                    raise Refusal("corrupt prior drainage observation")
                continue
            try:
                os.killpg(pgid, 0)
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise Refusal(f"prior group {pgid} absence cannot be proved: {exc}") from exc
            else:
                raise Refusal(f"prior group {pgid} still exists; wait for drainage or inspect descendants")
            write_record(prior / "drained.json", {
                "version": 1, "drained": True, "process": process, "observed_at": time.time(),
            })
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise Refusal(f"prior attempt {prior.name}: {exc}") from exc


class _WorkerStopped(BaseException):
    pass


def run_worker(attempt_id: str, *, root: Path | None = None,
               read_status: Callable[[str], Any] = read_run_status,
               remote_head: Callable[[str], str] = read_remote_head,
               pause_file: Path | None = None) -> int:
    root = root if root is not None else state_root()
    directory, offer = load_attempt(attempt_id, root)

    def stopped(signum: int, frame: Any) -> None:
        raise _WorkerStopped()

    previous = signal.signal(signal.SIGTERM, stopped)
    try:
        # Never remove the stable lock or hand its descriptor to descendants.
        with (directory.parent.parent / "lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                record_result(directory, "refused", "another landing worker holds the PR lock")
                return 1
            try:
                if ((directory / "process.json").exists() or (directory / "result.json").exists()):
                    return 1  # A request is never replayed, even after failure.
                exclude_prior_groups(directory)
                # The durable record precedes identity-read and invocation children.
                write_record(directory / "process.json", process_identity())
                record_progress(directory, "validating")
                validate_offer(offer, read_status=read_status, remote_head=remote_head, pause_file=pause_file)
                with (directory / "output.log").open("ab", buffering=0) as output:
                    child = subprocess.Popen(
                        [sys.executable, "-m", "nightshift.factory_land", "invoke", attempt_id],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        env={**os.environ, "NIGHTSHIFT_STATE_ROOT": str(root), "PYTHONUNBUFFERED": "1"},
                        close_fds=True)
                    code = child.wait()
                # Capture a bounded tail, while leaving the complete log intact.
                with (directory / "output.log").open("rb") as tail:
                    tail.seek(0, os.SEEK_END)
                    tail.seek(max(0, tail.tell() - DETAIL_LIMIT))
                    detail = tail.read(DETAIL_LIMIT).decode("utf-8", errors="replace").strip()
                progress = _read_record(directory / "progress.json")
                assert progress is not None
                record_result(directory, "succeeded" if code == 0 else "failed",
                              "merge confirmed" if code == 0 else detail or f"invocation exited {code}",
                              phase=progress["phase"], exit_code=code)
                return 0 if code == 0 else 1
            except Refusal as exc:
                record_result(directory, "refused", str(exc))
                return 1
            except _WorkerStopped:
                record_result(directory, "stopped", "worker received SIGTERM; inspect any surviving descendants")
                return 1
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                record_result(directory, "failed", f"worker failed: {exc}")
                return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


def _run_logged(argv: list[str], output: Path, *, capture_output: bool, text: bool,
                check: bool, timeout: float) -> subprocess.CompletedProcess[str]:
    """Tee raw pipes to retained output while preserving land's text responses.

    There is no extra process/session or inherited lock. On TERM, drain bytes
    already written by the command before exiting, including a command which
    flushed a diagnostic immediately before signalling its invocation parent.
    Surviving descendants remain covered by the recorded group exclusion.
    """
    if not capture_output or not text or check:
        raise ValueError("unsupported landing runner capture options")
    interrupted = False

    def stopped(signum: int, frame: Any) -> None:
        nonlocal interrupted
        interrupted = True

    previous = signal.signal(signal.SIGTERM, stopped)
    child = None
    buffers = [bytearray(), bytearray()]
    try:
        with output.open("ab", buffering=0) as log, selectors.DefaultSelector() as selector:
            child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     close_fds=True)
            assert child.stdout is not None and child.stderr is not None
            for index, pipe in enumerate((child.stdout, child.stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, index)
            deadline = time.monotonic() + timeout

            def drain(events: Any) -> None:
                for key, _ in events:
                    data = os.read(key.fd, 65536)
                    if data:
                        log.write(data)
                        buffers[key.data].extend(data)
                    else:
                        selector.unregister(key.fileobj)

            while selector.get_map() or child.poll() is None:
                if interrupted or time.monotonic() >= deadline:
                    if not interrupted:
                        child.kill()
                        child.wait()
                    # Do not wait for resistant descendants holding the pipes.
                    while events := selector.select(0):
                        drain(events)
                    os.fsync(log.fileno())
                    if interrupted:
                        raise SystemExit(128 + signal.SIGTERM)
                    raise subprocess.TimeoutExpired(argv, timeout, output=bytes(buffers[0]),
                                                    stderr=bytes(buffers[1]))
                drain(selector.select(max(0, min(.1, deadline - time.monotonic()))))
            os.fsync(log.fileno())
            # Match subprocess.run(text=True), including universal newlines;
            # the log retains the original bytes, without decoding or clipping.
            if interrupted:
                raise SystemExit(128 + signal.SIGTERM)
            encoding = "utf-8" if sys.flags.utf8_mode else locale.getencoding()
            stdout, stderr = (bytes(data).decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
                              for data in buffers)
            return subprocess.CompletedProcess(argv, child.wait(), stdout, stderr)
    finally:
        if child is not None:
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
        signal.signal(signal.SIGTERM, previous)


def run_invocation(attempt_id: str, *, root: Path | None = None) -> int:
    directory, offer = load_attempt(attempt_id, root if root is not None else state_root())
    process = _read_record(directory / "process.json")
    if (process is None or process.get("pgid") != os.getpgrp()
            or process.get("sid") != os.getsid(0) or process.get("pid") != os.getppid()):
        raise Refusal("invocation is outside its recorded worker group")

    def logged(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        phase = ("finishing" if "finish" in argv else "merging" if "merge" in argv
                 else "checking CI" if "checks" in argv or "update-branch" in argv
                 else "recording checks" if "--method" in argv else None)
        if phase is not None:
            record_progress(directory, phase)
        return _run_logged(argv, directory / "output.log", **kwargs)

    return invoke_confirmed(offer, runner=logged)


def worker_alive(process: dict[str, Any]) -> bool:
    """Display-only identity check; this is never authority to signal or retry."""
    pid, identity = process.get("pid"), process.get("start_identity")
    if (type(pid) is not int or not 1 < pid <= 2**31 - 1
            or not isinstance(identity, str) or not identity.strip()):
        raise ValueError("invalid worker identity")
    try:
        current = read_start_identity(pid)
    except (ProcessLookupError, FileNotFoundError):
        return False
    return current == identity


_READ_ERRORS = (OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError)


def _summary(directory: Path, request: dict[str, Any], process: dict[str, Any] | None,
             live: bool | None, diagnostics: list[str], now: float) -> dict[str, Any]:
    summary = {**request["offer"], "attempt_id": directory.name, "created_at": request["created_at"],
               "phase": "starting", "outcome": None, "reason": "", "exit_code": None,
               "updated_at": request["created_at"], "diagnostics": list(diagnostics)}
    terminal = False
    for filename in ("progress.json", "result.json"):
        try:
            record = _read_record(directory / filename, optional=True)
            if record is None:
                continue
            stamp = "finished_at" if filename == "result.json" else "updated_at"
            if (record.get("phase") not in _PHASES or not _timestamp(record.get(stamp))
                    or not isinstance(record.get("reason", ""), str)):
                raise ValueError(f"invalid {filename} progress fields")
            if filename == "result.json":
                if (record.get("outcome") not in _OUTCOMES
                        or (record.get("exit_code") is not None and type(record["exit_code"]) is not int)):
                    raise ValueError("invalid result.json outcome")
                terminal = True
                summary.update(outcome=record["outcome"], exit_code=record.get("exit_code"))
            summary.update(phase=record["phase"], reason=_clip(record.get("reason", "")),
                           updated_at=record[stamp])
        except _READ_ERRORS as exc:
            summary["diagnostics"].append(_clip(f"damaged {filename}: {exc}", 512))
    if not terminal:
        if process is not None and live is False:
            summary.update(outcome="failed")
            summary["diagnostics"].append("interrupted: worker identity is gone without a terminal result; inspect retained evidence")
        elif process is None and now - request["created_at"] > START_ALLOWANCE:
            summary.update(outcome="failed")
            summary["diagnostics"].append("interrupted: startup not established within 60 seconds")
        elif live is None and process is not None:
            summary["diagnostics"].append("worker identity is unverifiable; no completion can be inferred")
    summary["overdue"] = not terminal and now - request["created_at"] > OVERDUE_SECONDS
    return summary


def _bounded(document: dict[str, Any]) -> dict[str, Any]:
    """Account for JSON escaping as well as Unicode length, retaining identity."""
    document["truncated"] = "[truncated]" in json.dumps(document)
    limit = DETAIL_LIMIT
    while len(json.dumps(document, ensure_ascii=True).encode("utf-8")) >= OUTPUT_LIMIT:
        limit //= 2
        document["truncated"] = True
        for entry in [*document["attempts"], document.get("current")]:
            if entry is not None:
                entry["reason"] = _clip(entry["reason"], limit)
        # Other fields and diagnostic counts are independently bounded below.
    return document


def landing_status(change: str | None = None, *, limit: int = MAX_LIMIT, root: Path | None = None,
                   now: float | None = None, read_status: Callable[[str], Any] = read_run_status,
                   is_alive: Callable[[dict[str, Any]], bool] = worker_alive) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be between 1 and 20")
    if change is not None:
        _change(change)
    root = root if root is not None else state_root()
    now = time.time() if now is None else now
    document: dict[str, Any] = {"version": 1, "attempts": [], "total": 0, "diagnostics": []}

    def diagnose(reason: str) -> None:
        if len(document["diagnostics"]) < MAX_LIMIT:
            document["diagnostics"].append(_clip(reason, 512))
        else:
            document["diagnostics"][-1] = "additional damaged-record diagnostics omitted [truncated]"

    # Enumeration is intentionally not bounded by the display page. Only small
    # request/process metadata discovers candidates; bounded summaries validate
    # terminal results only for the display page and possible current attempts.
    # Complete logs never participate in selection or worker discovery.
    selected: list[tuple[float, str, Path, dict[str, Any], dict[str, Any] | None, bool | None, list[str]]] = []
    current = None
    current_summary = None
    for directory in (root / "landings").glob("*/*/attempts/*"):
        if not directory.is_dir():
            continue
        if change is None:
            document["total"] += 1
        try:
            if not _ATTEMPT.fullmatch(directory.name):
                raise ValueError("invalid attempt directory ID")
            request = _read_record(directory / "request.json")
            assert request is not None
            offer = Offer.parse(request["offer"])
            if (request.get("attempt_id") != directory.name or request.get("checks_done") is not True
                    or not _timestamp(request.get("created_at"))
                    or directory != attempt_directory(root, offer, directory.name)):
                raise ValueError("invalid request identity or timestamp")
            if change is not None and offer.change != change:
                continue
            if change is not None:
                document["total"] += 1
            request = {**request, "offer": asdict(offer)}
        except (*_READ_ERRORS, KeyError, AttributeError) as exc:
            diagnose(f"damaged attempt {_clip(directory.name, 64)}: {exc}")
            continue
        process, live, diagnostics = None, None, []
        try:
            process = _read_record(directory / "process.json", optional=True)
            if process is not None:
                live = is_alive(process)
        except _READ_ERRORS as exc:
            diagnostics.append(_clip(f"damaged or unreadable process identity: {exc}", 512))
        item = (request["created_at"], directory.name, directory, request, process, live, diagnostics)
        selected.append(item)
        selected.sort(key=lambda row: (row[0], row[1]), reverse=True)
        del selected[limit:]
        if (change is not None
                and (live is True or (process is None and now - request["created_at"] <= START_ALLOWANCE))):
            # A live worker outranks starting contenders, even if it is older.
            if current is None or (live is True, item[0], item[1]) > (current[5] is True, current[0], current[1]):
                candidate = _summary(directory, request, process, live, diagnostics, now)
                # Only a validated terminal result suppresses current progress.
                # Damaged results retain the live phase and its diagnostic.
                if candidate["outcome"] is None:
                    current = item
                    current_summary = candidate
    document["attempts"] = [current_summary if row is current else
                            _summary(row[2], row[3], row[4], row[5], row[6], now) for row in selected]
    if change is not None:
        document.update(offer=None, current=None)
        document["current"] = current_summary
        try:
            document["offer"] = asdict(offer_from_status(read_status(change), change))
        except _READ_ERRORS as exc:
            diagnose(str(exc))
    return _bounded(document)


def _since_arg(value: str) -> int | float:
    try:
        parsed = json.loads(value)
    except (ValueError, RecursionError) as exc:
        raise argparse.ArgumentTypeError("since must be finite epoch seconds") from exc
    if not _timestamp(parsed):
        raise argparse.ArgumentTypeError("since must be finite epoch seconds")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = result.add_subparsers(dest="mode", required=True)
    start = modes.add_parser("start", allow_abbrev=False)
    start.add_argument("change", type=_change)
    for field in ("pr", "pause-id", "head"):
        start.add_argument(f"--{field}", required=True)
    start.add_argument("--since", required=True, type=_since_arg)
    start.add_argument("--checks-done", action="store_true", required=True)
    status = modes.add_parser("status", allow_abbrev=False)
    status.add_argument("change", nargs="?", type=_change)
    status.add_argument("--limit", type=int, choices=range(1, MAX_LIMIT + 1), default=MAX_LIMIT)
    for mode in (start, status):
        mode.add_argument("--json", action="store_true", required=True)
    for name in ("worker", "invoke"):
        mode = modes.add_parser(name, allow_abbrev=False)
        mode.add_argument("attempt_id")
    return result


def main(argv: list[str] | None = None, *, launch: Callable[[str], Any] | None = None,
         **dependencies: Any) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.mode in ("worker", "invoke"):
        try:
            return (run_worker if args.mode == "worker" else run_invocation)(args.attempt_id, **dependencies)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"factory landing: {exc}", file=sys.stderr)
            return 1
    if args.mode == "status":
        response = landing_status(args.change, limit=args.limit, **dependencies)
    else:
        try:
            offer = Offer.parse(vars(args))
        except ValueError as exc:
            arguments.error(str(exc))
        if launch is None:
            launch = lambda attempt_id: launch_worker(attempt_id, root=dependencies.get("root"))
        response = start_attempt(offer, launch=launch, **dependencies)
    print(json.dumps(response, ensure_ascii=True, allow_nan=False))
    return 1 if response.get("accepted") is False else 0


if __name__ == "__main__":
    sys.exit(main())

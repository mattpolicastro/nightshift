"""Private Factory landing admission and retained display records.

This module is deliberately not registered with either canonical CLI. Execution
is an injected boundary: reads never launch, repair, acknowledge or prune work.
The worker lifecycle and confirmed land runner build on these records separately.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, cast
import uuid

from specloop.state import SAFE_CHANGE, state_root

from . import land


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
        if optional:
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


def worker_alive(process: dict[str, Any]) -> bool:
    """Display-only identity check; this is never authority to signal or retry."""
    pid, identity = process.get("pid"), process.get("start_identity")
    if type(pid) is not int or pid <= 1 or not isinstance(identity, str) or not identity.strip():
        raise ValueError("invalid worker identity")
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart="],
                            capture_output=True, text=True, timeout=5, check=False,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode == 1 and not result.stdout.strip():
        return False
    if result.returncode:
        raise ValueError("worker identity could not be inspected")
    return result.stdout.strip() == identity


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
    return result


def main(argv: list[str] | None = None, *, launch: Callable[[str], Any] | None = None,
         **dependencies: Any) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.mode == "status":
        response = landing_status(args.change, limit=args.limit, **dependencies)
    else:
        try:
            offer = Offer.parse(vars(args))
        except ValueError as exc:
            arguments.error(str(exc))
        if launch is None:
            # Fail closed until the detached lifecycle is supplied. Never claim
            # execution merely because this transport parsed a confirmation.
            def unavailable(attempt_id: str) -> None:
                raise ValueError("detached worker launcher is unavailable")
            launch = unavailable
        response = start_attempt(offer, launch=launch, **dependencies)
    print(json.dumps(response, ensure_ascii=True, allow_nan=False))
    return 1 if response.get("accepted") is False else 0


if __name__ == "__main__":
    sys.exit(main())

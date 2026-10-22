"""Read-only discovery, identity joins and intake accounting for touchpoints."""

from __future__ import annotations

import argparse
import json
import math
import re
import stat
from datetime import date, datetime, time, timezone
from pathlib import Path


def _safe_change(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", value) or value in (".", ".."):
        raise ValueError("unsafe change name")
    return value


def _repository(directory: str, explicit: object = None) -> str:
    if explicit is None:
        if len(re.findall(r"(?=__)", directory)) != 1:
            raise ValueError("ambiguous repository directory without explicit repo")
        explicit = directory.replace("__", "/")
    if not isinstance(explicit, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", explicit):
        raise ValueError("invalid repository identity")
    if explicit.replace("/", "__") != directory:
        raise ValueError("repository conflicts with directory identity")
    return explicit


class _Reader:
    def __init__(self):
        self.errors = []

    def error(self, path, exc, line=None):
        self.errors.append(dict(path=str(path), line=line, message=f"{type(exc).__name__}: {exc}"))

    def children(self, path, *, missing_ok=False):
        children = []
        try:
            # Keep entries already discovered if enumeration fails partway.
            for child in path.iterdir():
                children.append(child)
        except FileNotFoundError as exc:
            if not missing_ok:
                self.error(path, exc)
        except OSError as exc:
            self.error(path, exc)
        return sorted(children)

    def read(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def is_directory(self, path):
        try:
            # Path.is_dir() suppresses stat errors on newer Python versions.
            return stat.S_ISDIR(path.stat().st_mode)
        except OSError as exc:
            self.error(path, exc)
            return False

    def record(self, path, kind, directory, number, job=None):
        try:
            data = self.read(path)
            repo = _repository(directory, data.get("repo"))
            command = data.get("command", "admit" if kind == "admissions" else "propose")
            head = data.get("head")
            change = data.get("change")
            result, wrapper_error = data.get("result"), None
            result_unreadable = False
            if job is not None:
                result = None  # Only result.json can complete a retained request.
                args = data.get("args")
                if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
                    raise ValueError("request args must be a nonempty string array")
                command = args[0]

                def option(flag):
                    if args.count(flag) != 1 or args.index(flag) + 1 >= len(args):
                        raise ValueError(f"request needs one {flag} value")
                    return args[args.index(flag) + 1]

                change = args[1] if command == "revise" and len(args) > 1 else option("--change")
                head = option("--head") if "--head" in args else None
                result_path = path.with_name("result.json")
                try:
                    wrapper = self.read(result_path)
                    result, wrapper_error = wrapper.get("result"), wrapper.get("error")
                    if result is None and wrapper_error is None:
                        raise ValueError("job wrapper needs result or error")
                except FileNotFoundError:
                    pass  # Retain pending requests for identity and draft-date provenance.
                except (OSError, ValueError, RecursionError) as exc:
                    result_unreadable = True
                    self.error(result_path, exc)
            if not isinstance(command, str):
                raise ValueError("command must be a string")
            if command not in ({"propose", "revise"} if kind == "revisions" else
                               {"admit" if kind == "admissions" else "propose"}):
                raise ValueError("command conflicts with intake source")
            change = _safe_change(change)
            issue = data.get("issue", number if kind == "proposals" else None)
            if issue is not None and (type(issue) is not int or issue <= 0):
                raise ValueError("issue must be a positive integer")
            if kind == "proposals" and issue != number:
                raise ValueError("issue conflicts with proposal filename")
            # A valid request establishes a draft attempt even if its result is
            # malformed. Validate retained results during counting instead.
            if job is None and result is not None and not isinstance(result, dict):
                raise ValueError("result must be an object")
            return dict(path=path, kind=kind, repo=repo, number=number, issue=issue,
                        change=change, command=command, head=head, result=result,
                        job=job if job is not None else data.get("job"),
                        retained=job is not None, wrapper_error=wrapper_error,
                        result_unreadable=result_unreadable,
                        mtime=path.stat().st_mtime)
        except (OSError, ValueError, RecursionError) as exc:
            self.error(path, exc)
            return None

    def records(self, root):
        for kind in ("proposals", "admissions", "revisions"):
            for directory in self.children(root / kind, missing_ok=True):
                if not self.is_directory(directory):
                    continue
                for path in self.children(directory):
                    match = re.fullmatch(r"([1-9][0-9]*)\.(json|jobs)", path.name)
                    if not match:
                        continue
                    number, suffix = int(match[1]), match[2]
                    if suffix == "json":
                        record = self.record(path, kind, directory.name, number)
                        if record is not None:
                            yield record
                    else:
                        for job in self.children(path):
                            if self.is_directory(job):
                                record = self.record(job / "request.json", kind, directory.name, number, job.name)
                                if record is not None:
                                    yield record


def collect_history(nightshift_root: Path, runs_root: Path) -> dict:
    """Return identity rows, normalized records and uniquely attributable log paths.

    Records retain their source number (issue for proposals, PR otherwise), job
    ID, command, head, result and mtime for later accounting. No deduplication or
    event replay happens here; events_path is None for ambiguous run identities.
    """
    reader, rows = _Reader(), {}
    records = list(reader.records(nightshift_root))
    for record in records:
        try:
            repo, change = record["repo"], record["change"]
            matches = [key for key in rows if key[0] == repo and key[2] == change]
            if record["kind"] == "proposals":
                issue = record["issue"]
            elif len(matches) == 1:
                issue = matches[0][1]
            elif matches:
                raise ValueError("ambiguous proposal identity for change")
            else:
                pattern = re.escape(repo.split("/")[1]) + r"-issue-([1-9][0-9]*)-([a-z0-9]+(?:-[a-z0-9]+)*)"
                match = re.fullmatch(pattern, change)
                if not match or len(match[2]) > 40:
                    raise ValueError("missing proposal and no front-door change identity")
                issue = int(match[1])
            if record["issue"] is not None and record["issue"] != issue:
                raise ValueError("issue conflicts with joined change identity")
            key = repo, issue, change
            if key not in rows:
                warnings = ([] if record["kind"] == "proposals" else
                            ["Missing proposal; inferred issue from change name; draft date unknown."])
                rows[key] = dict(repo=repo, issue=issue, change=change, drafted_at=None,
                                 drafted_at_source=None, warnings=warnings, records=[], events_path=None)
            rows[key]["records"].append(record)
        except ValueError as exc:
            reader.error(record["path"], exc)
    by_change = {}
    for row in rows.values():
        by_change.setdefault(row["change"], []).append(row)
    for change, matches in by_change.items():
        path = runs_root / change / "events.jsonl"
        if len(matches) > 1:
            reader.error(path, ValueError("ambiguous run name belongs to multiple rows; events not read"))
        else:
            matches[0]["events_path"] = path
    return dict(changes=[rows[key] for key in sorted(rows)],
                errors=sorted(reader.errors, key=lambda error: (error["path"], error["message"])))


_SCALAR_COUNTS = ("drafter_questions", "spec_revisions", "build_revisions", "human_pauses",
                  "manual_resumes", "human_accepts", "admission_refusals")


def _intake_counts(row, reader):
    counts = dict.fromkeys(_SCALAR_COUNTS, 0)
    counts.update(pauses_by_reason={}, resumes_by_reason={})
    completed = []
    for record in row["records"]:
        path = record["path"]
        try:
            for field in ("job", "head"):
                value = record[field]
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise ValueError(f"{field} must be a nonempty string")
            if record["result_unreadable"]:
                continue  # Already diagnosed; an unreadable result is not pending.
            if record["retained"]:
                path = path.with_name("result.json")
            if record["wrapper_error"] is not None:
                raise ValueError("intake job wrapper reported an error; no completed round")
            result = record["result"]
            if result is None:
                # A receipt awaiting delivery may already have a terminal job.
                linked = not record["retained"] and record["job"] is not None and any(
                    other["retained"] and all(record[field] == other[field] for field in
                    ("kind", "repo", "number", "change", "command", "job"))
                    for other in row["records"])
                if not linked:
                    row["warnings"].append(f"Pending intake request: {record['path']}")
                continue
            if not isinstance(result, dict):
                raise ValueError("result must be an object")
            outcomes = {"propose": ("drafted", "needs_input", "rejected"),
                        "admit": ("admitted", "refused"), "revise": ("revised", "refused")}
            outcome = result.get("outcome")
            if outcome not in outcomes[record["command"]] and not (
                    record["kind"] == "revisions" and outcome == "empty"):
                raise ValueError("unrecognized intake outcome for command")
            commit = result.get("commit")
            if (commit is not None or outcome == "revised") and (
                    not isinstance(commit, str) or not commit.strip()):
                raise ValueError("result commit must be a nonempty string")
            completed.append(record)
        except ValueError as exc:
            reader.error(path, exc)

    jobs = [record for record in completed if record["retained"]]
    rounds = list(jobs)
    for receipt in (record for record in completed if not record["retained"]):
        matches = []
        for job in jobs:
            if any(receipt[field] != job[field] for field in
                   ("kind", "repo", "number", "change", "command")):
                continue
            if receipt["job"] is not None:
                compatible = receipt["job"] == job["job"]
            else:
                compatible = all(receipt["result"].get(field) == job["result"].get(field)
                                 for field in ("outcome", "commit"))
                if receipt["kind"] == "admissions":
                    compatible = compatible and receipt["head"] == job["head"]
            if compatible:
                matches.append(job)
        if len(matches) > 1:
            row["warnings"].append(f"Ambiguous receipt match; retained jobs counted: {receipt['path']}")
        elif not matches:
            rounds.append(receipt)
            row["warnings"].append(
                f"Unmatched legacy snapshot; earlier rounds may have been overwritten: {receipt['path']}")

    build_commits = set()
    for record in rounds:
        outcome = record["result"]["outcome"]
        if record["command"] == "propose" and outcome == "needs_input":
            counts["drafter_questions"] += 1
        if record["kind"] == "revisions" and outcome != "empty":
            if record["command"] == "propose":
                counts["spec_revisions"] += 1
            elif outcome == "revised":
                build_commits.add(record["result"]["commit"])
            else:
                counts["build_revisions"] += 1
        if record["kind"] == "admissions" and outcome == "refused":
            counts["admission_refusals"] += 1
    counts["build_revisions"] += len(build_commits)
    counts["total"] = sum(counts[field] for field in _SCALAR_COUNTS)
    row.update(counts=counts, build_commits=build_commits)


def collect_intake(nightshift_root: Path, runs_root: Path) -> dict:
    """Aggregate observed intake rounds, preserving history for later event replay.

    Each row retains successful build_commits so event accounting can add only
    unseen commits. The normalized records remain available for date provenance.
    """
    history = collect_history(nightshift_root, runs_root)
    reader = _Reader()
    for row in history["changes"]:
        _intake_counts(row, reader)
    history["errors"] = sorted(history["errors"] + reader.errors,
                               key=lambda error: (error["path"], error["message"]))
    return history


def _event_counts(row, reader):
    path, counts = row["events_path"], row["counts"]
    if path is None:
        return
    pause, pause_line = None, None
    # Only observed automatic infra recoveries consume this per-task allowance.
    # Manual resumes reset attempt counters in the writer, not auto_resumes.
    auto_resumes, recovery_certain = {}, True

    def increment(field, reason):
        counts[field][reason] = counts[field].get(reason, 0) + 1

    def resolve(resolution=None):
        nonlocal pause
        if pause is None:
            return
        reason, task = pause["reason"], pause.get("task", "")
        scheduled = bool(pause.get("resume_at", 0))
        human, uncertainty = False, None
        if reason == "ready_to_publish" or resolution == "auto_resumed":
            pass
        elif resolution in ("resumed", "human_accept") or not scheduled:
            human = True
        elif reason == "infra" and not task:
            pass  # The supervisor applies no recovery cap without a task.
        elif not recovery_certain:
            uncertainty = "broken event history; recovery allowance is uncertain"
        elif reason == "infra":
            # mutation-equivalent: For absent keys, 0 and 1 are both < 2; existing counts are unchanged.
            human = auto_resumes.get(task, 0) >= 2
        elif reason != "usage_limit":
            uncertainty = "unknown scheduling policy"
        if uncertainty:
            row["warnings"].append(
                f"Unclassified scheduled pause ({reason}): {path}:{pause_line}; {uncertainty}")
        if human:
            counts["human_pauses"] += 1
            increment("pauses_by_reason", reason)
        pause = None

    def validate(event):
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError("event must be an object with a string event kind")
        for field in ("at", "resume_at"):
            # mutation-equivalent: Defaults 0 and 1 both pass numeric validation; neither is stored in event.
            value = event.get(field, 0) if field == "resume_at" else event.get(field)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"event {field} must be a finite number")
        kind = event["event"]
        if kind == "pause" and (not isinstance(event.get("reason"), str) or not event["reason"]):
            raise ValueError("pause reason must be a nonempty string")
        for field in ("reason", "task"):
            if field in event and not isinstance(event[field], str):
                raise ValueError(f"event {field} must be a string")
        if kind == "revised" and (not isinstance(event.get("commit"), str) or not event["commit"].strip()):
            raise ValueError("revised event commit must be a nonempty string")

    try:
        # Decode each line separately so a damaged line cannot hide later events.
        with path.open("rb") as stream:
            for line, raw in enumerate(stream, 1):
                try:
                    event = json.loads(raw.decode("utf-8"))
                    validate(event)
                except (ValueError, OverflowError, RecursionError) as exc:
                    # Invalidate before resolving: even an exhausted allowance
                    # cannot be inferred through a gap in the recorded history.
                    recovery_certain = False
                    resolve()
                    reader.error(path, exc, line)
                    continue
                kind = event["event"]
                if kind == "pause":
                    resolve()
                    pause = event
                    pause_line = line
                elif kind in ("resumed", "auto_resumed", "human_accept", "revised", "finished", "aborted"):
                    if kind == "resumed":
                        counts["manual_resumes"] += 1
                        increment("resumes_by_reason", pause["reason"] if pause else "unknown")
                        if pause is None:
                            row["warnings"].append(f"Unpaired manual resume; reason unknown: {path}:{line}")
                    elif kind == "human_accept":
                        counts["human_accepts"] += 1
                    elif kind == "revised" and event["commit"] not in row["build_commits"]:
                        row["build_commits"].add(event["commit"])
                        counts["build_revisions"] += 1
                    if kind == "auto_resumed":
                        reason = event.get("reason", pause["reason"] if pause else None)
                        task = event.get("task", pause.get("task", "") if pause else "")
                        if reason == "infra" and task:
                            auto_resumes[task] = auto_resumes.get(task, 0) + 1
                    resolve(kind)
    except FileNotFoundError as exc:
        if any(record["kind"] == "admissions" and isinstance(record["result"], dict) and
               record["result"].get("outcome") == "admitted" for record in row["records"]):
            reader.error(path, exc)
    except OSError as exc:
        recovery_certain = False
        reader.error(path, exc)
    resolve()
    for field in ("pauses_by_reason", "resumes_by_reason"):
        counts[field] = dict(sorted(counts[field].items()))
    counts["total"] = sum(counts[field] for field in _SCALAR_COUNTS)


def collect_events(nightshift_root: Path, runs_root: Path) -> dict:
    """Add append-order event accounting to intake history, preserving provenance."""
    history = collect_intake(nightshift_root, runs_root)
    reader = _Reader()
    for row in history["changes"]:
        _event_counts(row, reader)
    history["errors"] = sorted(history["errors"] + reader.errors,
                               key=lambda error: (error["path"], error["line"] or 0, error["message"]))
    return history


def collect_report(nightshift_root: Path, runs_root: Path, *, since: date | None = None) -> dict:
    """Select whole changes by estimated initial draft time and return a JSON document.

    # mutation-equivalent: This docstring is not read by collection or CLI output; wording cannot affect report behavior.
    Events are never cut off at a lifecycle marker or filtered by event date.
    Keep errors from all discovered history, even when its row is excluded.
    """
    cutoff = datetime.combine(since, time.min, tzinfo=timezone.utc) if since is not None else None
    history = collect_events(nightshift_root, runs_root)
    reader, changes = _Reader(), []
    reason_fields = ("pauses_by_reason", "resumes_by_reason")
    totals = dict(changes=0, **dict.fromkeys((*_SCALAR_COUNTS, "total"), 0))
    totals.update({field: {} for field in reason_fields})
    for row in history["changes"]:
        proposals = [record for record in row["records"] if record["kind"] == "proposals"]
        requests = [record for record in proposals if record["retained"]]
        candidates = requests or proposals
        drafted_at, source = None, None
        warnings = list(row["warnings"])
        if candidates:
            earliest = min(candidates, key=lambda record: record["mtime"])
            try:
                drafted_at = datetime.fromtimestamp(earliest["mtime"], timezone.utc)
                source = "request_mtime" if requests else "receipt_mtime"
                warnings.append(f"Draft date is approximate; using earliest proposal {source}: {earliest['path']}")
            except (OSError, OverflowError, ValueError) as exc:
                reader.error(earliest["path"], exc)
        if drafted_at is None:
            warnings.append("Draft date unknown; no usable proposal timestamp is available.")
            if cutoff is not None:
                reader.error(row["records"][0]["path"], ValueError(
                    f"Cannot apply --since {since.isoformat()} to {row['change']}: draft date unknown; change excluded"))
                continue
        elif cutoff is not None and drafted_at < cutoff:
            continue
        counts = dict(row["counts"])
        counts["total"] = sum(counts[field] for field in _SCALAR_COUNTS)
        for field in reason_fields:
            counts[field] = dict(sorted(counts[field].items()))
            for reason, count in counts[field].items():
                totals[field][reason] = totals[field].get(reason, 0) + count
        for field in (*_SCALAR_COUNTS, "total"):
            totals[field] += counts[field]
        changes.append(dict(repo=row["repo"], issue=row["issue"], change=row["change"],
                            drafted_at=drafted_at.isoformat() if drafted_at is not None else None,
                            drafted_at_source=source, counts=counts, warnings=sorted(warnings)))
    totals["changes"] = len(changes)
    for field in reason_fields:
        totals[field] = dict(sorted(totals[field].items()))
    return dict(
        version=1, since=since.isoformat() if since is not None else None,
        coverage="observed_local_history", merge_boundary="unknown",
        limitations=[
            "Draft dates use approximate filesystem timestamps; counts may be lower bounds when "
            "retained local history was overwritten or removed; actual merge time is unavailable.",
            "Total is the sum of category observations, not unique interventions: a question during "
            "spec revision counts in both categories, and a pause and its resume count separately.",
        ],
        changes=sorted(changes, key=lambda row: (row["repo"], row["issue"], row["change"])),
        totals=totals,
        errors=sorted(history["errors"] + reader.errors,
                      key=lambda error: (error["path"], error["line"] or 0, error["message"])),
    )


def render_table(report: dict) -> str:
    """Render the same counts and diagnostics as the versioned JSON document."""
    headers = ("Repository", "Issue", "Change", "Questions", "Spec revisions",
               "Build revisions", "Pauses", "Resumes", "Accepts", "Refusals", "Total")
    fields = (*_SCALAR_COUNTS, "total")
    rows = [[row["repo"], str(row["issue"]), row["change"],
             *(str(row["counts"][field]) for field in fields)] for row in report["changes"]]
    rows.append(["TOTAL", "-", f"{report['totals']['changes']} changes",
                 *(str(report["totals"][field]) for field in fields)])
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(len(headers))]

    def format_row(row):
        return "  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()

    lines = [format_row(headers), format_row(["-" * width for width in widths])]
    lines.extend(format_row(row) for row in rows)
    lines.extend(["", "Pause/resume reasons:"])

    def reasons(label, counts):
        pauses = json.dumps(counts["pauses_by_reason"], sort_keys=True)
        resumes = json.dumps(counts["resumes_by_reason"], sort_keys=True)
        return f"  {label}: pauses={pauses}; resumes={resumes}"

    for row in report["changes"]:
        lines.append(reasons(f"{row['repo']} #{row['issue']} {row['change']}", row["counts"]))
    lines.append(reasons("TOTAL", report["totals"]))
    lines.extend(["", f"Coverage: {report['coverage']}; merge boundary: {report['merge_boundary']}; "
                  f"since: {report['since'] or 'all'}", "Limitations:"])
    lines.extend(f"  - {limitation}" for limitation in report["limitations"])
    lines.append("Warnings:")
    warnings = [f"  - {row['repo']} #{row['issue']} {row['change']}: {warning}"
                for row in report["changes"] for warning in row["warnings"]]
    lines.extend(warnings or ["  None"])
    lines.append("Errors:")
    for error in report["errors"]:
        location = error["path"] + (f":{error['line']}" if error["line"] is not None else "")
        lines.append(f"  - {location}: {error['message']}")
    if not report["errors"]:
        lines.append("  None")
    return "\n".join(lines)


def _since_date(value: str) -> date:
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("expected YYYY-MM-DD")
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a valid calendar date in YYYY-MM-DD format") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report observed local human touchpoints.", allow_abbrev=False)
    parser.add_argument("--json", action="store_true", help="emit one versioned JSON document")
    parser.add_argument("--since", type=_since_date, metavar="YYYY-MM-DD",
                        help="include changes drafted on or after this date (UTC, approximate)")
    args = parser.parse_args(argv)
    home = Path.home()
    report = collect_report(home / ".nightshift", home / ".specloop" / "runs", since=args.since)
    print(json.dumps(report, indent=2) if args.json else render_table(report))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

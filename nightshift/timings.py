"""Read-only timing and recorded-outcome summaries from local run events."""

import json
import math
from pathlib import Path
from statistics import median


# Phase, start kinds, end kind, and whether the pairing requires a task key.
PAIRS = (
    ("implement", {"implement"}, "implemented", True),
    ("verify", {"sealed"}, "verified", True),
    ("review", {"review"}, "reviewed", True),
    ("evidence_gate", {"landed"}, "evidence", False),
    ("slot_wait", {"admitted", "resumed"}, "implement", False),
    ("paused_wait", {"pause"}, "resumed", False),
)


def _read_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            continue
        at = event.get("at")
        if not isinstance(at, (int, float)) or isinstance(at, bool):
            continue
        try:
            if not math.isfinite(at):
                continue
        except OverflowError:
            continue
        events.append(event)
    return events


def _hours(start, end):
    return (end - start) / 3600 if start is not None and end is not None and end >= start else None


def _change_summary(name: str, events: list[dict]) -> dict:
    def first(kind, after=-1):
        return next(((i, e["at"]) for i, e in enumerate(events)
                     if i > after and e["event"] == kind), (-1, None))

    admission_index, admitted = first("admitted")
    publication_index, published = first("published", admission_index)
    finished = first("finished", publication_index)[1] if published is not None else None
    landed = {e.get("task") for e in events if e["event"] == "landed"
              and isinstance(e.get("task"), str) and e["task"]}
    return dict(change=name, tasks_landed=len(landed),
                failed_attempts=sum(e["event"] == "attempt_failed" for e in events),
                build_hours=_hours(admitted, published),
                publication_wait_hours=_hours(published, finished))


def collect_report(runs_root: Path) -> dict:
    """Pool readable runs in name order, retaining one diagnostic per failed read."""
    samples: dict[str, list[float]] = {phase: [] for phase, *_ in PAIRS}
    changes, errors = [], []
    profiles: dict[str, dict] = {}
    directories = sorted(runs_root.iterdir()) if runs_root.exists() else []
    for directory in directories:
        if not directory.is_dir():
            continue
        path = directory / "events.jsonl"
        try:
            events = _read_events(path)
        except (OSError, UnicodeError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        changes.append(_change_summary(directory.name, events))
        pending: dict[tuple[str, str], float] = {}
        for event in events:
            kind, at, task = event["event"], event["at"], event.get("task")
            for phase, starts, end, by_task in PAIRS:
                if by_task:
                    if not isinstance(task, str) or not task:
                        continue
                    key = (phase, task)
                else:
                    key = (phase, "")
                if kind in starts:
                    pending[key] = at
                elif kind == end and key in pending:
                    duration = at - pending.pop(key)
                    if duration >= 0:
                        samples[phase].append(duration)
            if kind in {"implemented", "reviewed"}:
                profile = event.get("profile")
                profile = profile if isinstance(profile, str) and profile else "unknown"
                row = profiles.setdefault(profile, dict(profile=profile, implementation_attempts=0,
                                                       review_attempts=0, review_failures=0))
                row["implementation_attempts" if kind == "implemented" else "review_attempts"] += 1
                output = event.get("output")
                if (kind == "reviewed" and event.get("disposition") == "ok"
                        and isinstance(output, dict) and output.get("verdict") == "FAIL"):
                    row["review_failures"] += 1
    for row in profiles.values():
        row["review_failure_rate"] = (row["review_failures"] / row["review_attempts"]
                                      if row["review_attempts"] else None)
    return dict(phases={phase: dict(count=len(values), total_seconds=sum(values),
                                   median_seconds=median(values) if values else None)
                        for phase, values in samples.items()},
                changes=changes, profiles=[profiles[name] for name in sorted(profiles)], errors=errors)

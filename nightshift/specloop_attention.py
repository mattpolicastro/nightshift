"""Human pauses observed through specloop's CLI, with a durable last snapshot."""

from __future__ import annotations

import json
import subprocess
from functools import partial

from . import outcomes, specloop_client


def _needs_attention(pause: dict) -> bool:
    if "auto_resumes_remaining" not in pause:
        return pause["reason"] != "usage_limit"
    return pause["auto_resumes_remaining"] == 0


def _entries(document: dict) -> list[dict]:
    """Validate before replacing the snapshot; malformed status is not absence."""
    runs = document.get("runs")
    if not isinstance(runs, list):
        raise ValueError("specloop status has no runs list")
    entries = []
    for run in runs:
        if (not isinstance(run, dict)
                or not isinstance(run.get("change"), str)
                or not isinstance(run.get("status"), str)
                or "pr" not in run
                or (run["pr"] is not None and not isinstance(run["pr"], str))
                or "pause" not in run):
            raise ValueError("specloop status has an invalid run")
        pause = run["pause"]
        if pause is not None and (
                not isinstance(pause, dict) or not isinstance(pause.get("reason"), str)):
            raise ValueError("specloop status has an invalid pause")
        if pause is not None:
            allowance = pause.get("auto_resumes_remaining")
            if allowance is not None and (type(allowance) is not int or allowance < 0):
                raise ValueError("specloop status has an invalid auto_resumes_remaining")
        if run["status"] == "paused" and pause is not None and _needs_attention(pause):
            entries.append({"change": run["change"], "reason": pause["reason"], "pr": run["pr"]})
    return entries


def snapshot(*, offline: bool = False, runner=None) -> dict:
    """Read all runs once, preserving the last good read on transport/schema errors.

    The runner has subprocess.run's signature. Bound the default subprocess so
    a stuck status command cannot indefinitely hold up attention.
    """
    if not offline:
        try:
            document = specloop_client.call(
                ["status"], runner=runner or partial(subprocess.run, timeout=outcomes.REQUEST_SECONDS)
            )
            entries = _entries(document)
        except (specloop_client.SpecloopError, subprocess.TimeoutExpired, ValueError) as exc:
            with outcomes.database() as db:
                db.execute("""INSERT INTO specloop_snapshot VALUES(1, '[]', NULL, ?)
                    ON CONFLICT(id) DO UPDATE SET error=excluded.error""", (str(exc),))
        else:
            with outcomes.database() as db:
                db.execute("INSERT OR REPLACE INTO specloop_snapshot VALUES(1, ?, ?, NULL)",
                           (json.dumps(entries), outcomes.now()))
    with outcomes.database() as db:
        row = db.execute("SELECT * FROM specloop_snapshot WHERE id=1").fetchone()
    checked_at, error = (row["checked_at"], row["error"]) if row else (None, None)
    entries = json.loads(row["data"]) if row else []
    for entry in entries:
        entry.update(checked_at=checked_at, error=error, stale=error is not None)
    return {"entries": entries, "checked_at": checked_at, "error": error}


def render(snapshot: dict) -> str:
    entries = snapshot["entries"]
    lines = [f"{len(entries)} specloop run(s) need you"]
    for entry in entries:
        prefix = "STALE " if entry["stale"] else ""
        lines.append(f"  {prefix}{entry['change']} — {entry['reason']}")
        if entry["pr"]:
            lines.append(f"    {entry['pr']}")
    if snapshot["error"]:
        lines.append(f"  STALE specloop — last checked {snapshot['checked_at'] or 'never'}: "
                     + " ".join(snapshot["error"].split()))
    return "\n".join(lines)

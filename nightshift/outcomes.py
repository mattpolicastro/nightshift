"""Durable task history and a GitHub-reconciled action list.

Events are immutable; observations are separate and tied to the event observed.
A slow refresh cannot overwrite a worker's newer outcome. No display writes to
GitHub, and no provider session is needed to represent a task.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path.home() / ".nightshift" / "outcomes.sqlite3"
log = logging.getLogger("nightshift")

# Leave room inside Paseo's 60-second subprocess timeout for startup and JSON.
REFRESH_SECONDS = 40
REQUEST_SECONDS = 8
REFRESH_LIMIT = 32
REFRESH_WORKERS = 4
HISTORY_SLOTS = 8
HISTORY_INTERVAL = 3600
HISTORY_RETRY_SECONDS = 60


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, repo TEXT NOT NULL, issue INTEGER NOT NULL,
                at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS task_events ON events(repo, issue, id);
            CREATE TABLE IF NOT EXISTS refresh_attempts (
                event_id INTEGER PRIMARY KEY, attempted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                event_id INTEGER PRIMARY KEY, checked_at TEXT, state TEXT,
                resolution TEXT, error TEXT
            );
        """)
        with db:
            yield db
    finally:
        db.close()


def record(repo: str, issue: int, **changes) -> None:
    """Append a snapshot; reporting failure must never change task execution."""
    try:
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT data FROM events WHERE repo=? AND issue=? ORDER BY id DESC LIMIT 1",
                (repo, issue),
            ).fetchone()
            data = json.loads(previous["data"]) if previous else {}
            data.update(changes)
            db.execute("INSERT INTO events(repo,issue,at,data) VALUES(?,?,?,?)",
                       (repo, issue, now(), json.dumps(data)))
    except (OSError, sqlite3.Error, ValueError) as exc:
        log.warning("could not record outcome for %s#%s: %s", repo, issue, exc)


def tasks() -> list[dict]:
    with database() as db:
        rows = db.execute("""
            SELECT e.*, o.checked_at, o.state AS observed_state, o.resolution, o.error
            FROM events e LEFT JOIN observations o ON o.event_id=e.id
            WHERE e.id IN (SELECT MAX(id) FROM events GROUP BY repo,issue)
            ORDER BY e.repo,e.issue
        """).fetchall()
    result = []
    for row in rows:
        item = json.loads(row["data"])
        item.update({k: row[k] for k in ("id", "repo", "issue", "at", "checked_at", "resolution", "error")})
        item["state"] = row["observed_state"] or item.get("state", "running")
        item["issue_url"] = f"https://github.com/{row['repo']}/issues/{row['issue']}"
        result.append(item)
    return result


def _observe(item, labels, runner):
    state, resolution = item["state"], item.get("resolution") or ""
    issue = json.loads(runner([
        "issue", "view", str(item["issue"]), "--repo", item["repo"],
        "--json", "state,labels",
    ]))
    if issue["state"] not in {"OPEN", "CLOSED"}:
        raise ValueError("unexpected issue state")
    present = {x["name"] for x in issue["labels"]}
    pr_url = item.get("pr_url")
    if pr_url:
        pr = json.loads(runner([
            "pr", "view", pr_url, "--repo", item["repo"], "--json", "state",
        ]))
        if pr["state"] == "MERGED":
            state, resolution = "resolved", "PR merged"
        elif pr["state"] == "OPEN":
            state, resolution = "awaiting_merge", ""
        elif pr["state"] == "CLOSED":
            if issue["state"] == "CLOSED":
                state, resolution = "resolved", "PR and issue closed"
            else:
                state, resolution = "needs_decision", "PR closed without merging; decide whether to revise or close the issue"
        else:
            raise ValueError("unexpected PR state")
    elif issue["state"] == "CLOSED":
        state, resolution = "resolved", "issue closed"
    elif item.get("escalated") and labels.needs_human not in present:
        state, resolution = "resolved", "needs-human removed (resolved or re-armed)"
    else:
        state, resolution = "needs_decision", ""
    return state, resolution


def _read_github(args, deadline):
    """Attention has its own deadline; queue retries must not hold the UI open."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Refresh time budget exhausted")
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False,
            timeout=min(REQUEST_SECONDS, remaining),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("GitHub check timed out") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GitHub check failed")
    return result.stdout


def refresh(labels, *, runner=None) -> list[dict]:
    """Bound work per invocation, rotating attempts so failures cannot starve peers.

    Outstanding tasks get priority; historical checks have reserved capacity and
    an hourly cooldown. The default runner bounds each subprocess by the shared
    deadline. Injected runners must return promptly (used by offline tests).
    """
    deadline = time.monotonic() + REFRESH_SECONDS
    runner = runner or (lambda args: _read_github(args, deadline))
    items = [item for item in tasks() if item["state"] != "running"]
    with database() as db:
        attempts = dict(db.execute("SELECT event_id, attempted_at FROM refresh_attempts"))
    stamp = datetime.fromisoformat(now())

    def history_due(item):
        interval = HISTORY_RETRY_SECONDS if item.get("error") else HISTORY_INTERVAL
        cutoff = (stamp - timedelta(seconds=interval)).isoformat()
        return attempts.get(item["id"], "") <= cutoff
    active = sorted((i for i in items if i["state"] != "resolved"),
                    key=lambda i: (attempts.get(i["id"], ""), i["id"]))
    history = sorted((i for i in items if i["state"] == "resolved" and history_due(i)),
                     key=lambda i: (attempts.get(i["id"], ""), i["id"]))
    # Borrow unused capacity in either direction, but never starve history.
    active_count = min(len(active), REFRESH_LIMIT - min(HISTORY_SLOTS, len(history)))
    history_count = min(len(history), REFRESH_LIMIT - active_count)
    selected = []
    active_batch, history_batch = active[:active_count], history[:history_count]
    # Interleave the reserved history work too: putting it last would starve it
    # whenever outstanding checks used the entire time budget.
    while active_batch or history_batch:
        selected.extend(active_batch[:3])
        del active_batch[:3]
        selected.extend(history_batch[:1])
        del history_batch[:1]
    completed = set()

    def save_error(db, item, message):
        # Preserve the last successful state and its timestamp, including resolved.
        db.execute("""INSERT INTO observations(event_id,error) VALUES(?,?)
            ON CONFLICT(event_id) DO UPDATE SET error=excluded.error""",
                   (item["id"], message))

    # Only keep one wave in flight. A slow/outage wave cannot cause the whole
    # history to be queued behind it and outlive the shared deadline.
    with ThreadPoolExecutor(max_workers=REFRESH_WORKERS) as pool:
        pending = {}
        remaining = iter(selected)
        exhausted = False
        while pending or not exhausted:
            while len(pending) < REFRESH_WORKERS and not exhausted:
                if time.monotonic() >= deadline:
                    exhausted = True
                    break
                item = next(remaining, None)
                if item is None:
                    exhausted = True
                    break
                with database() as db:
                    db.execute("INSERT OR REPLACE INTO refresh_attempts VALUES(?,?)",
                               (item["id"], now()))
                pending[pool.submit(_observe, item, labels, runner)] = item
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            with database() as db:
                for future in done:
                    item = pending.pop(future)
                    completed.add(item["id"])
                    try:
                        state, resolution = future.result()
                    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
                        save_error(db, item, str(exc))
                    else:
                        db.execute("INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)",
                                   (item["id"], now(), state, resolution, None))
    with database() as db:
        for item in active + history:
            if item["id"] not in completed:
                save_error(db, item, "Refresh deferred by work/time limit; showing last known state")
    return tasks()


def attention(items: list[dict]) -> list[dict]:
    return [i for i in items if i["state"] in {"awaiting_merge", "needs_decision"}]


def render(items: list[dict]) -> str:
    selected = attention(items)
    lines = [f"{len(selected)} task(s) need you"]
    for item in selected:
        state = item["state"].replace("_", " ")
        lines.append(f"  {item['repo']}#{item['issue']} — {state}")
        summary = item.get("resolution") or item.get("summary") or item.get("reason") or item.get("title")
        if summary:
            lines.append("    " + " ".join(summary.split())[:300])
        lines.append("    " + (item.get("pr_url") or item["issue_url"]))
        if item.get("harness_error"):
            lines.append("    Harness error: " + " ".join(item["harness_error"].split())[:300])
    for item in items:
        if item.get("error"):
            lines.append(f"  STALE {item['repo']}#{item['issue']} — last checked "
                         f"{item.get('checked_at') or 'never'}: {' '.join(item['error'].split())}")
    if not items:
        lines.append("  No recorded tasks yet; history starts when this version runs.")
    return "\n".join(lines)

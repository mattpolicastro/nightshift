"""Durable task history and a GitHub-reconciled action list.

Events are immutable; observations are separate and tied to the event observed.
A slow refresh cannot overwrite a worker's newer outcome. No display writes to
GitHub, and no provider session is needed to represent a task.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

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
            CREATE TABLE IF NOT EXISTS specloop_snapshot (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL, checked_at TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS front_door_snapshot (
                repo TEXT NOT NULL, forge TEXT NOT NULL, label TEXT NOT NULL,
                entries TEXT NOT NULL, checked_at TEXT, error TEXT, error_code TEXT,
                PRIMARY KEY (repo, forge, label)
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


class _BudgetExhausted(RuntimeError):
    pass


class _TimedOut(RuntimeError):
    pass


def _read_github(args, deadline):
    """Attention has its own deadline; queue retries must not hold the UI open."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _BudgetExhausted("Refresh time budget exhausted")
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False,
            timeout=min(REQUEST_SECONDS, remaining),
        )
    except subprocess.TimeoutExpired as exc:
        raise _TimedOut("GitHub check timed out") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GitHub check failed")
    return result.stdout


# Fixed display text: raw stderr can carry credentials and is never shown.
SAFE_ERRORS = {
    "read_failed": "GitHub read failed",
    "timeout": "GitHub read timed out",
    "invalid_response": "GitHub returned an invalid response",
    "budget_exhausted": "Refresh time budget exhausted",
    "unsupported_forge": "Attention does not support this repository's forge",
    "not_checked": "Not checked yet",
}
SUMMARY_LIMIT = 400
# A larger comment thread is refused as oversized rather than parsed.
COMMENT_BYTES_LIMIT = 4 * 1024 * 1024
SUMMARY_UNAVAILABLE = "Summary unavailable"
_HEADLINE_PREFIX = "**nightshift** — "
_HEADLINES = {
    "The spec draft needs human input.": "draft",
    "Admission refused. Human input is needed.": "admit",
    "Revision needs human input.": "revise",
    "Publication refused. Human input is needed.": "publish",
}
_STAGE_HEADLINE = re.compile(r"Specloop ([A-Za-z0-9_-]+) failed\. Human input is needed\.")


class _FrontDoorError(Exception):
    def __init__(self, code: str):
        super().__init__(SAFE_ERRORS[code])
        self.code = code


def _gh_pages(runner, path: str, limit: int | None = None) -> list[dict]:
    """Every row of every page of a GitHub list endpoint; partial is failure."""
    try:
        raw = runner(["api", path, "--paginate", "--slurp"])
    except _BudgetExhausted as exc:
        raise _FrontDoorError("budget_exhausted") from exc
    except _TimedOut as exc:
        raise _FrontDoorError("timeout") from exc
    except (RuntimeError, OSError) as exc:
        raise _FrontDoorError("read_failed") from exc
    if limit is not None and isinstance(raw, str) and len(raw.encode()) > limit:
        raise _FrontDoorError("invalid_response")
    try:
        pages = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise _FrontDoorError("invalid_response") from exc
    if not isinstance(pages, list) or not all(
            isinstance(page, list) and all(isinstance(row, dict) for row in page)
            for page in pages):
        raise _FrontDoorError("invalid_response")
    return [row for page in pages for row in page]


def _positive(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _listing_entry(repo: str, row: dict) -> dict:
    number, title, url = row.get("number"), row.get("title"), row.get("html_url")
    kind = "pr" if row.get("pull_request") is not None else "issue"
    if not _positive(number) or not isinstance(title, str) or row.get("state") != "open":
        raise _FrontDoorError("invalid_response")
    expected = f"/{repo}/{'pull' if kind == 'pr' else 'issues'}/{number}"
    if not isinstance(url, str):
        raise _FrontDoorError("invalid_response")
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "github.com"
            or "?" in url or "#" in url or parts.path.lower() != expected.lower()):
        raise _FrontDoorError("invalid_response")
    return {"repo": repo, "number": number, "kind": kind, "title": title, "url": url}


def _list_repository(cfg, repo, runner) -> dict:
    """One enrollment's complete open labelled listing, or its safe failure."""
    label = cfg.labels.needs_human
    status: dict = {"repo": repo.name, "label": label, "entries": None,
                    "error": None, "error_code": None}
    try:
        try:
            forge = cfg.forge_for(repo)
        except ValueError as exc:
            raise _FrontDoorError("unsupported_forge") from exc
        # Never fall back to a same-named repository on github.com.
        if forge.kind != "github" or forge.url.rstrip("/") != "https://github.com":
            raise _FrontDoorError("unsupported_forge")
        rows = _gh_pages(runner, f"repos/{repo.name}/issues?state=open"
                                 f"&labels={quote(label, safe='')}&per_page=100")
        entries = [_listing_entry(repo.name, row) for row in rows]
        if len({e["number"] for e in entries}) != len(entries):
            raise _FrontDoorError("invalid_response")
    except _FrontDoorError as exc:
        status.update(error=str(exc), error_code=exc.code)
    else:
        status["entries"] = sorted(entries, key=lambda e: (e["number"], e["kind"]))
    return status


def _collect_listings(cfg, runner) -> list[dict]:
    """Listings only: summaries are enriched separately, once membership is known."""
    return [_list_repository(cfg, repo, runner)
            for repo in sorted(cfg.repos, key=lambda r: r.name)]


def _escalation_headline(body) -> str | None:
    """The first physical line of an exact delivery comment, else None.

    Physical lines only: no trimming, and no reading of fences, HTML or
    indentation. Anything not exactly headline ... marker refuses evidence.
    """
    if not isinstance(body, str):
        return None
    lines = [line[:-1] if line.endswith("\r") else line for line in body.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    if len(lines) < 2 or not lines[0].startswith(_HEADLINE_PREFIX):
        return None
    headline = lines[0][len(_HEADLINE_PREFIX):]
    stage = _STAGE_HEADLINE.fullmatch(headline)
    action = _HEADLINES.get(headline) or (f"{stage[1]}-error" if stage else None)
    if action is None or not re.fullmatch(
            rf"<!-- nightshift:{re.escape(action)}:[^\s<>]+ -->", lines[-1]):
        return None
    return lines[0]


def _comment_recency(comment: dict) -> tuple[datetime, int]:
    stamp = comment.get("updated_at")
    if stamp is None:
        stamp = comment.get("created_at")
    if not isinstance(stamp, str):
        raise _FrontDoorError("invalid_response")
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise _FrontDoorError("invalid_response") from exc
    if parsed.tzinfo is None or not _positive(comment.get("id")):
        raise _FrontDoorError("invalid_response")
    return parsed, comment["id"]


def _read_summary(repo: str, number: int, runner) -> str | None:
    """The latest qualifying escalation's headline across every comment page."""
    comments = _gh_pages(runner, f"repos/{repo}/issues/{number}/comments?per_page=100",
                         COMMENT_BYTES_LIMIT)
    qualifying = [(_comment_recency(c), line) for c in comments
                  if (line := _escalation_headline(c.get("body"))) is not None]
    if not qualifying:
        return None
    line = max(qualifying)[1]
    return line if len(line) <= SUMMARY_LIMIT else line[:SUMMARY_LIMIT - 1] + "…"


def _enrich_summary(entry: dict, runner, clock=None) -> dict:
    """Summary fields for one listed item; a failure affects only that item."""
    try:
        summary = _read_summary(entry["repo"], entry["number"], runner)
    except _FrontDoorError as exc:
        return {"summary": None, "summary_checked_at": None, "summary_stale": True,
                "summary_error": str(exc), "summary_error_code": exc.code}
    return {"summary": summary, "summary_checked_at": (clock or now)(),
            "summary_stale": False, "summary_error": None, "summary_error_code": None}


def _summary_failure(code: str) -> dict:
    return {"summary_stale": True, "summary_error": SAFE_ERRORS[code], "summary_error_code": code}


def _selection(cfg, repo) -> tuple[tuple[str, str, str], bool]:
    """Cache key (repository, forge identity, label) and whether GitHub can answer."""
    try:
        forge = cfg.forge_for(repo)
    except ValueError:
        return (repo.name, json.dumps(["missing", repo.forge]), cfg.labels.needs_human), False
    identity = "github" if repo.forge is None else json.dumps([forge.kind, forge.name, forge.url])
    supported = forge.kind == "github" and forge.url.rstrip("/") == "https://github.com"
    return (repo.name, identity, cfg.labels.needs_human), supported


def _cached(db, key):
    return db.execute("SELECT * FROM front_door_snapshot WHERE repo=? AND forge=? AND label=?",
                      key).fetchone()


def _commit_listing(key, status) -> None:
    """Replace membership only with a complete listing; failure keeps the last good one."""
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        prior = _cached(db, key)
        if status["entries"] is None:
            if prior:
                db.execute("UPDATE front_door_snapshot SET error=?, error_code=? WHERE repo=? "
                           "AND forge=? AND label=?", (status["error"], status["error_code"], *key))
            else:
                db.execute("INSERT INTO front_door_snapshot VALUES(?,?,?,'[]',NULL,?,?)",
                           (*key, status["error"], status["error_code"]))
            return
        old = {(e["number"], e["kind"]): e for e in json.loads(prior["entries"])} if prior else {}
        entries = []
        for entry in status["entries"]:
            previous = old.get((entry["number"], entry["kind"]))
            summary = ({k: previous.get(k) for k in ("summary", "summary_checked_at", "summary_stale",
                                                     "summary_error", "summary_error_code")}
                       if previous else {"summary": None, "summary_checked_at": None,
                                         **_summary_failure("not_checked")})
            entries.append({**entry, **summary})
        db.execute("INSERT OR REPLACE INTO front_door_snapshot VALUES(?,?,?,?,?,NULL,NULL)",
                   (*key, json.dumps(entries), now()))


def _commit_summary(key, entry, fields) -> None:
    """One item's summary result, applied only while the item is still listed."""
    if fields.get("summary_error_code"):
        fields = _summary_failure(fields["summary_error_code"])  # keep the last good summary
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _cached(db, key)
        if row is None:
            return
        entries = json.loads(row["entries"])
        for item in entries:
            if (item["number"], item["kind"]) == (entry["number"], entry["kind"]):
                item.update(fields)
        db.execute("UPDATE front_door_snapshot SET entries=? WHERE repo=? AND forge=? AND label=?",
                   (json.dumps(entries), *key))


def _bounded(jobs, work, done, clock, deadline) -> list:
    """Stable order, at most REFRESH_WORKERS in flight, nothing started after the deadline.

    `done` runs on the calling thread, so database writes stay off the workers.
    Returns the jobs never started.
    """
    queue = list(jobs)
    with ThreadPoolExecutor(max_workers=REFRESH_WORKERS) as pool:
        pending: dict = {}
        while queue or pending:
            while queue and len(pending) < REFRESH_WORKERS and clock() < deadline:
                job = queue.pop(0)
                pending[pool.submit(work, job)] = job
            if not pending:
                break
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                done(pending.pop(future), future.result())
    return queue


def _repository_view(repo: str, label: str, row) -> dict:
    if row is None:
        return {"repo": repo, "label": label, "entries": [], "checked_at": None, "stale": True,
                "error": SAFE_ERRORS["not_checked"], "error_code": "not_checked"}
    entries = sorted(json.loads(row["entries"]), key=lambda e: (e["number"], e["kind"]))
    failed = [e for e in entries if e.get("summary_error_code")]
    error, code = row["error"], row["error_code"]
    if code is None and failed:
        code = failed[0]["summary_error_code"]
        error = f"{SUMMARY_UNAVAILABLE} for {len(failed)} item(s): {failed[0]['summary_error']}"
    return {"repo": repo, "label": label, "entries": entries, "checked_at": row["checked_at"],
            "stale": code is not None, "error": error, "error_code": code}


def front_door_snapshot(cfg, *, offline=False, runner=None, clock=None) -> list[dict]:
    """Current needs-human issues and PRs per enrolled repository, with last-good caching.

    Listings are attempted before any comment read; each complete listing is
    committed before its summaries, and each summary result commits alone. No
    transaction spans network work. `clock` is the monotonic clock for the
    shared deadline; injected runners are additionally gated by it.
    """
    if not cfg.repos:
        return []  # empty enrollment must not create the cache
    clock = clock or time.monotonic
    selections = [(repo, *_selection(cfg, repo)) for repo in sorted(cfg.repos, key=lambda r: r.name)]
    if not offline:
        deadline = clock() + REFRESH_SECONDS
        gh_deadline = time.monotonic() + REFRESH_SECONDS
        base = runner or (lambda args: _read_github(args, gh_deadline))

        def gated(args):
            if clock() >= deadline:
                raise _BudgetExhausted("Refresh time budget exhausted")
            return base(args)

        supported = [(repo, key) for repo, key, ok in selections if ok]
        listed = []

        def listing_done(job, status):
            _commit_listing(job[1], status)
            if status["entries"] is not None:
                listed.extend((job[1], entry) for entry in status["entries"])

        for repo, key in _bounded(supported, lambda job: _list_repository(cfg, job[0], gated),
                                  listing_done, clock, deadline):
            _commit_listing(key, {"entries": None, "error": SAFE_ERRORS["budget_exhausted"],
                                  "error_code": "budget_exhausted"})
        for key, entry in _bounded(listed, lambda job: _enrich_summary(job[1], gated),
                                   lambda job, fields: _commit_summary(job[0], job[1], fields),
                                   clock, deadline):
            _commit_summary(key, entry, _summary_failure("budget_exhausted"))
    result = []
    with database() as db:
        for repo, key, ok in selections:
            if not ok:
                result.append({"repo": repo.name, "label": key[2], "entries": [],
                               "checked_at": None, "stale": True,
                               "error": SAFE_ERRORS["unsupported_forge"],
                               "error_code": "unsupported_forge"})
            else:
                result.append(_repository_view(repo.name, key[2], _cached(db, key)))
    return result


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

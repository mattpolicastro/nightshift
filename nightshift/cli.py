"""nightshift status|pause|resume|run|reconcile|digest"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import outcomes, config, daemon, preflight, queue, trace


def _repo_dirs(cfg: config.Config) -> dict[str, Path]:
    """The clone each repo's worktrees are cut from. See `Repo.clone_dir`."""
    return {r.name: r.clone_dir() for r in cfg.repos}


def _elapsed(started_at: str, now: datetime | None = None) -> str:
    """How long the running task has been running, for `status`.

    Elapsed rather than a wall-clock start time: the question is "is this still
    moving or did something wedge", and a timestamp makes you do the
    subtraction yourself. A claim written by an older version — or by hand —
    may not parse, and an unreadable clock must not cost you the line telling
    you WHICH issue is running.
    """
    try:
        began = datetime.fromisoformat(started_at)
    except (ValueError, TypeError):
        return "?"
    if began.tzinfo is None:
        began = began.replace(tzinfo=timezone.utc)
    mins = int(((now or datetime.now(timezone.utc)) - began).total_seconds() // 60)
    if mins < 0:
        return "?"
    return f"{mins}m" if mins < 60 else f"{mins // 60}h {mins % 60:02d}m"


def _liveness(cfg: config.Config) -> str:
    """One line: is the daemon alive, and when did it last do anything?

    Three states the pid separates, because they want different reactions —
    healthy, WEDGED (a live process that has stopped polling), and not running
    at all. A paused daemon is still alive and still beating, so pause is
    reported alongside liveness rather than instead of it.
    """
    paused = " (paused)" if daemon.paused() else ""
    beat = daemon.heartbeat()
    if beat is None:
        # No file at all: a clean stop removes it, so this is "not running"
        # rather than "crashed" — the crash case leaves one behind.
        return (
            f"not running — no heartbeat{paused}. A clean stop removes it; a "
            "crash leaves one behind, so this is a stopped daemon rather than "
            "a dead one (or one started before it kept a heartbeat)."
        )

    age, up = daemon._human(beat.age), daemon._human(beat.uptime)  # noqa: SLF001
    if not beat.alive:
        return (
            f"DEAD — pid {beat.pid} is gone, last polled {age} ago. "
            "It exited without cleaning up, so it crashed rather than stopped."
        )
    if beat.stale(cfg.poll_seconds):
        return (
            f"WEDGED — pid {beat.pid} is alive but has not polled for {age} "
            f"(expected every {cfg.poll_seconds}s){paused}"
        )
    return f"running{paused} — pid {beat.pid}, up {up}, last poll {age} ago"


def _progress_of(repo: str, number: int) -> str:
    """The newest in-flight transcript for an issue, summarised.

    Newest by mtime because a task on its second attempt has several, and the
    one being written is the one worth reading. Both naming schemes are
    matched: transcripts written before 2026-09-05 carry no repo, and issue
    numbers collide across repos, so an old file may belong to either.
    """
    if not daemon.TRANSCRIPT_DIR.exists():
        return ""
    slug = repo.replace("/", "__")
    candidates = sorted(
        [
            *daemon.TRANSCRIPT_DIR.rglob(f"{slug}#{number}-*.jsonl"),
            *daemon.TRANSCRIPT_DIR.rglob(f"{number}-*.jsonl"),
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return ""
    try:
        return trace.progress(candidates[0].read_text()).line
    except OSError:
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nightshift")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["status", "pause", "resume", "run", "reconcile", "preflight", "digest", "attention"],
    )
    parser.add_argument("--json", action="store_true", help="attention: machine-readable task records")
    parser.add_argument("--offline", action="store_true", help="attention: use cached records without GitHub")
    parser.add_argument("--once", action="store_true", help="one task, then exit")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="preflight: also run a tool-loop probe on each endpoint's models "
             "(slow — a cold model load is minutes, not seconds)",
    )
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    # The daemon gets its credentials from the launchd wrapper, which sources
    # the env file. A human at a terminal has not, so without this every
    # command reports on the wrong environment — `status` says alerts are
    # unconfigured when they are fine, and preflight's negative control probes
    # the interactive login instead of GH_TOKEN, which is precisely the false
    # pass it was written to rule out.
    config.load_env_file()

    # pause/resume must work even with a broken or missing config — they are the
    # kill switch, and a kill switch that needs a valid config is not one.
    if args.command == "pause":
        daemon.PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        daemon.PAUSE_FILE.touch()
        print(f"paused — {daemon.PAUSE_FILE}")
        return 0

    if args.command == "resume":
        daemon.PAUSE_FILE.unlink(missing_ok=True)
        print("resumed")
        return 0

    try:
        cfg = config.load(args.config)
    except FileNotFoundError:
        print("no config.toml — copy config.example.toml and edit it", file=sys.stderr)
        return 2

    if args.command == "attention":
        items = outcomes.tasks() if args.offline else outcomes.refresh(cfg.labels)
        if args.json:
            print(json.dumps({"version": 1, "offline": args.offline,
                              "tasks": outcomes.attention(items),
                              "stale": [i for i in items if i.get("error")]}, indent=2))
        else:
            if args.offline:
                print("Cached outcomes (GitHub not checked)")
            print(outcomes.render(items))
        return 1 if any(i.get("error") for i in items) else 0

    if args.command == "status":
        # The process, THEN the queue. Until 2026-09-05 this line said
        # "running" from a pause file, which is a statement about a file rather
        # than about a daemon: an idle daemon and a dead one printed the same
        # thing, and settling it took `ps` over SSH — the expensive option from
        # a phone, which is where this gets read.
        print(_liveness(cfg))
        # Unconfigured alerting is a silent no-op by design, which makes it
        # invisible exactly when it matters — before an unattended night.
        print(f"  alerts: {'slack' if cfg.slack_webhook else 'NOT CONFIGURED'}")
        # Only when something is NOT on the subscription endpoint. Silence
        # means the default, which is what an unconfigured install is; a phase
        # routed elsewhere is a decision worth seeing every time you look.
        for repo in [None, *cfg.repos]:
            for phase in ("implement", "review", "chores"):
                if not cfg.model_spec(phase, repo):
                    continue
                a = cfg.assign(phase, repo)
                if a.endpoint.is_default:
                    continue
                if repo is not None and cfg.assign(phase).endpoint.name == a.endpoint.name:
                    continue
                scope = f" [{repo.name}]" if repo else ""
                print(f"  {phase}{scope}: {a.endpoint.name}:{a.model} (off-subscription)")
        for repo in cfg.repos:
            # Read this BEFORE asking GitHub anything. A claimed issue carries
            # `agent:working`, so `ready` cannot see it and every count below
            # is blind to the task actually consuming the machine — which for
            # a serial daemon is the first thing you want to know.
            running, unreadable = queue.in_flight(repo.name)
            pending = queue.ready(repo.name, cfg.labels)
            # Split them: a queue full of blocked issues looks identical to an
            # idle daemon otherwise, and the difference is the whole question
            # you are asking when you run `status`.
            blocked: list[tuple[int, str, list[int]]] = []
            claimable = []
            for issue in pending:
                unmet = queue.open_blockers(
                    repo.name, queue.blockers(issue.body)
                )
                (blocked.append((issue.number, issue.title, unmet))
                 if unmet else claimable.append(issue))

            print(
                f"  {repo.name}: {len(running)} working, "
                f"{len(claimable)} ready, {len(blocked)} blocked"
            )
            for c in running:
                title = queue.title_of(repo.name, c.number)
                kind = "revising" if c.revise else c.phase
                print(
                    f"    >> #{c.number} {title}".rstrip()
                    + f"  ({kind}, {_elapsed(c.started_at)} on {c.branch})"
                )
                # What it is doing RIGHT NOW, read from the transcript the
                # worker is streaming. Before this, a forty minute implement
                # pass reported its phase and elapsed time and nothing else.
                moving = _progress_of(repo.name, c.number)
                if moving:
                    print(f"       {moving}")
            for name in unreadable:
                print(
                    f"    !! unreadable claim file {name}"
                    f" — `nightshift reconcile` will clear it"
                )
            for issue in claimable:
                print(f"    #{issue.number} {issue.title}")
            for number, title, unmet in blocked:
                waiting = ", ".join(f"#{n}" for n in unmet)
                print(f"    -- #{number} {title}  (waiting on {waiting})")
            # Skipped silently by claim(), so this is the only place a human
            # finds out they left an issue in a contradictory state.
            for number, clash in queue.contradictions(repo.name, cfg.labels):
                print(
                    f"    !! #{number} is also labelled {', '.join(sorted(clash))}"
                    f" — it will not be claimed"
                )
        return 0

    if args.command == "preflight":
        checks = preflight.run(cfg, dict(os.environ), deep=args.deep)
        for c in checks:
            print(f"  {c.line}")
        failed = [c for c in checks if not c.ok]
        print(f"\n{len(failed)} of {len(checks)} checks failed" if failed else "\nready")
        return 1 if failed else 0

    if args.command == "reconcile":
        repairs = daemon.startup(cfg, _repo_dirs(cfg))
        for r in repairs:
            print(f"  #{r.number} {r.repair.value}: {r.detail}")
        if not repairs:
            print("  clean")
        return 0

    if args.command == "run":
        tally = daemon.loop(cfg, _repo_dirs(cfg), once=args.once)
        print(
            f"shipped={tally.shipped} escalated={tally.escalated} "
            f"turns={tally.turns} cost=${tally.cost:.2f}"
            + (f" unbilled={tally.unbilled}" if tally.unbilled else "")
        )
        for line in tally.lines:
            print(f"  {line}")
        return 0

    if args.command == "digest":
        print("digest is a Phase 2 item — not implemented", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Canonical build commands and explicit daemon controls."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import sys
import time
from pathlib import Path

from specloop import cli as build_cli

from . import outcomes, config, daemon, preflight, queue, specloop_attention, specloop_job


def _repo_dirs(cfg: config.Config) -> dict[str, Path]:
    """The clone each repo's worktrees are cut from. See `Repo.clone_dir`."""
    return {r.name: r.clone_dir() for r in cfg.repos}


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


def _jobs(action: str | None, job_id: str | None, *, show_all: bool = False) -> int:
    if action == "compact":
        report = specloop_job.compact_history(daemon.STATE_DIR)
        for outcome in report.outcomes:
            job = outcome.job
            print(f"{job.id}  {job.stage}  {job.repository}#{job.number}  "
                  f"{outcome.status}: {outcome.reason}")
        for diagnostic in report.diagnostics:
            print(diagnostic, file=sys.stderr)
        print(f"Compacted: {report.compacted}; skipped: {report.skipped}.")
        return 1 if report.error else 0

    if action == "release":
        assert job_id is not None  # Required by argument validation.
        try:
            state = specloop_job.release_job(daemon.STATE_DIR, job_id)
        except specloop_job.ReleaseJobError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"{job_id}: {state}")
        return 0

    if action == "stop":
        try:
            state = specloop_job.stop_job(daemon.STATE_DIR, job_id)
        except specloop_job.StopJobError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"{job_id}: {'already finished' if state == 'finished' else state}")
        return 0

    jobs, errors = specloop_job.list_jobs(daemon.STATE_DIR, include_delivery=True)
    visible = jobs if show_all else [
        job for job in jobs
        if job.diagnostics or job.state in ("starting", "running", "stopping", "stopped")
        or job.state == "finished" and not job.delivered
    ]
    hidden = len(jobs) - len(visible)
    if not jobs and not errors:
        print("No background jobs.")
    elif not visible and hidden and not errors:
        print("No active background jobs.")
    for job in visible:
        identity = "" if job.identity_available else " (process identity unavailable)"
        state = job.state
        if state == "finished":
            state = "delivered" if job.delivered else "finished (delivery pending)"
        print(f"{job.id}  {job.stage}  {job.repository}#{job.number}  {state}")
        print(f"  command: {shlex.join(job.command) if job.command else 'unavailable'}")
        print(f"  PID={job.pid if job.pid is not None else '?'} "
              f"PGID={job.pgid if job.pgid is not None else '?'}{identity}")
        print(f"  log: {job.log_path}")
    if hidden:
        print(f"Hidden historical jobs: {hidden} (use --all to show).")
    for error in errors:
        print(f"unreadable job: {error}", file=sys.stderr)
    return 1 if errors else 0


def _active_jobs() -> None:
    """Render local active attempts without changing job or delivery state."""
    jobs, errors = specloop_job.list_jobs(daemon.STATE_DIR)
    now = time.time()
    for job in jobs:
        if job.state not in ("starting", "running", "stopping"):
            continue
        timing = "elapsed unavailable; deadline unavailable"
        if job.started_at is not None and job.deadline_at is not None:
            elapsed = max(0, int(now - job.started_at))
            deadline = (f"OVERDUE by {int(now - job.deadline_at)}s"
                        if now >= job.deadline_at
                        else f"{int(job.deadline_at - now)}s remaining")
            timing = f"elapsed {elapsed}s; {deadline}"
        command = job.command[0] if job.command else "unavailable"
        print(f"  {job.id}  {job.stage}  {job.repository}#{job.number}  "
              f"{command}  {job.state}  {timing}")
    for error in errors:
        print(f"unreadable job: {error}", file=sys.stderr)


def _front_options(parser, *, json_option=True):
    # Suppressed defaults preserve options supplied before a subcommand.
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    for flag, help_text in (
        ("all", "jobs: include historical attempts"),
        # mutation-equivalent: 'or' -> 'and' only rewords help; --offline parsing and execution are unchanged.
        ("offline", "attention: use cached records without GitHub or specloop"),
        ("once", "one task, then exit"),
        ("deep", "preflight: accepted for compatibility; worker probes are retired"),
    ):
        parser.add_argument(f"--{flag}", action="store_true", default=argparse.SUPPRESS, help=help_text)
    if json_option:
        parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)


def build_parser(*, json_errors=False):
    """Construct the public parser without credentials, state reads or dispatch."""
    class Parser(build_cli.argument_parser_class(json_errors)):
        def parse_args(self, args=None, namespace=None):
            argv = list(sys.argv[1:] if args is None else args)
            parsed = super().parse_args(argv or ["status"], namespace)
            # The frozen service wrapper uses bare run; other build operands
            # remain required, and a named run cannot take daemon options.
            if parsed.cmd == "run" and parsed.change is None:
                parsed.handler, parsed.command = _daemon_command, "run"
            if parsed.handler is build_cli.dispatch:
                if any(hasattr(parsed, name) for name in ("config", "all", "offline", "deep")) or (
                    parsed.cmd != "serve" and hasattr(parsed, "once")
                ):
                    self.error("daemon-only options require 'nightshift daemon'")
            else:
                if getattr(parsed, "all", False) and (
                    parsed.command != "jobs" or parsed.action is not None or parsed.job_id is not None
                ):
                    self.error("--all is only valid for jobs listing")
                if parsed.command == "jobs" and not (
                    parsed.action is None and parsed.job_id is None
                    or parsed.action == "compact" and parsed.job_id is None
                    or parsed.action in ("stop", "release") and parsed.job_id is not None
                ):
                    self.error("expected 'jobs [--all]', 'jobs compact', 'jobs stop <id>' or 'jobs release <id>'")
            return parsed

    parser = Parser(prog="nightshift")
    _front_options(parser, json_option=False)
    parser.set_defaults(handler=build_cli.dispatch)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = build_cli.add_build_parsers(sub, optional_run=True)
    _front_options(run, json_option=False)
    group = sub.add_parser("daemon", help="daemon run/status/pause/resume")
    _front_options(group)
    group.set_defaults(handler=_daemon_command, command="status")
    controls = group.add_subparsers(dest="daemon_command")
    for command in ("run", "status", "pause", "resume"):
        control = controls.add_parser(command)
        _front_options(control)
        control.set_defaults(command=command)
    for command in ("jobs", "attention", "preflight", "reconcile", "digest"):
        front = sub.add_parser(command)
        _front_options(front)
        front.set_defaults(handler=_daemon_command, command=command)
        if command == "jobs":
            front.add_argument("action", nargs="?", help="compact, stop or release")
            front.add_argument("job_id", nargs="?", help="exact full job ID")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    option_end = argv.index("--") if "--" in argv else len(argv)
    parser = build_parser(json_errors="--json" in argv[:option_end])
    try:
        args = parser.parse_args(argv)
    except build_cli.ParseError as exc:
        print(json.dumps({"version": 1, "error": str(exc)}))
        return 2
    return args.handler(args)


def _daemon_command(args) -> int:
    for name, default in (("config", None), ("all", False), ("json", False),
                          ("offline", False), ("once", False), ("deep", False)):
        if not hasattr(args, name):
            setattr(args, name, default)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    # Local job control must also work offline, without configuration or credentials.
    if args.command == "jobs":
        return _jobs(args.action, args.job_id, show_all=args.all)

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
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "attention":
        items = outcomes.tasks() if args.offline else outcomes.refresh(cfg.labels)
        specloop = specloop_attention.snapshot(offline=args.offline)
        if args.json:
            print(json.dumps({"version": 1, "offline": args.offline,
                              "tasks": outcomes.attention(items),
                              "specloop": specloop["entries"],
                              "specloop_error": specloop["error"],
                              "stale": [i for i in items if i.get("error")]}, indent=2))
        else:
            if args.offline:
                print("Cached outcomes (GitHub and specloop not checked)")
            print(outcomes.render(items))
            print(specloop_attention.render(specloop))
        return 1 if specloop["error"] or any(i.get("error") for i in items) else 0

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
        _active_jobs()
        for repo in cfg.repos:
            if cfg.forge_for(repo).kind == "gitea":
                print(f"  {repo.name}: not queried (gitea)")
                continue
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

            print(f"  {repo.name}: {len(claimable)} ready, {len(blocked)} blocked")
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
        daemon.loop(cfg, _repo_dirs(cfg), once=args.once)
        if args.once:
            print("poll complete")
        return 0

    if args.command == "digest":
        print("digest is a Phase 2 item — not implemented", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

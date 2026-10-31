"""Specloop polling, liveness and durable recovery of interrupted worker evidence."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from . import outcomes, notify, queue, specloop_intake, specloop_job
from .config import Config, Repo
from .forge import ForgeError
from .forge.gitea import GiteaForge
from .forge.github import GitHubForge

log = logging.getLogger("nightshift")

# Hold front-door launches and delivery; admitted builds have their own supervisor.
PAUSE_FILE = Path.home() / ".nightshift" / "pause"

STATE_DIR = Path.home() / ".nightshift"
TRANSCRIPT_DIR = STATE_DIR / "transcripts"


# Written every poll, removed on a clean exit. The absence of this file after
# a run means the daemon stopped deliberately; its PRESENCE with a dead pid
# means it did not — which is the only way a restart loop becomes visible
# without reading the log.
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"


def forge_for(repo: Repo, *, cfg: Config, runner=None, guard=None):
    """Construct the configured adapter without requests."""
    definition = cfg.forge_for(repo)
    if definition.kind == "github":
        return GitHubForge(runner or queue._gh)
    token = os.environ.get(definition.token_env)
    if not token:
        raise specloop_intake.ConfigurationError(
            f"{definition.name}: missing bot credential {definition.token_env}")
    return GiteaForge(definition.url, token, trusted_logins=definition.trusted_logins,
                     bot_login=definition.bot_login, wip_prefix=definition.wip_prefix,
                     guard=guard, after_request=guard)


def _check_landing_environment(cfg: Config) -> None:
    for definition in cfg.forges:
        if definition.land_token_env and definition.land_token_env in os.environ:
            raise specloop_intake.ConfigurationError(
                f"{definition.name}: landing credential {definition.land_token_env} "
                "must be absent from the daemon environment")


def _gitea_held(repo: Repo, cfg: Config, health: RepoHealth) -> bool:
    if cfg.forge_for(repo).kind != "gitea":
        return False
    detail = None
    root = STATE_DIR / "engine-switch" / repo.name.replace("/", "__")
    try:
        # iterdir surfaces inspection errors that glob may silently suppress.
        if any(path.name.endswith(".json") for path in root.iterdir()):
            detail = f"Gitea foreign engine-switch journal hold; inspect {root}"
    except FileNotFoundError:
        pass
    except OSError:
        detail = f"Gitea engine-switch journal location cannot be inspected; inspect {root}"
    if detail is None:
        return False
    error = specloop_intake.ConfigurationError(detail)
    log.warning("%s — %s", repo.name, error)
    health.record(repo.name, error, cfg.slack_webhook)
    return True


@dataclass(frozen=True)
class Heartbeat:
    """Proof of life, written by the loop and read by `status`.

    The question `status` could not answer until 2026-09-05 is "is it alive".
    It read GitHub and reported the QUEUE, and said "running" from a pause
    file — which is a statement about a file, not a process. An idle daemon and
    a dead one produced identical output, so the honest check was `ps` over SSH,
    which is the expensive option from a phone.

    Three states worth telling apart, and the pid is what separates the last
    two: fresh means healthy, stale with a live pid means WEDGED, and stale
    with a dead pid means it is not running at all.
    """

    pid: int
    started_at: float
    last_poll: float

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.last_poll)

    @property
    def uptime(self) -> float:
        return max(0.0, time.time() - self.started_at)

    @property
    def alive(self) -> bool:
        """Is that pid still a process? Signal 0 tests existence, kills nothing.

        A pid can be recycled, so this is evidence rather than proof — but a
        recycled pid needs a stale heartbeat to mislead anyone, and that case
        already reads as "not healthy".
        """
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # someone else's process now, but a process
        return True

    def stale(self, poll_seconds: int) -> bool:
        """Late by more than a couple of polls. One slow pass is not news."""
        return self.age > max(60, poll_seconds * 2)


def beat(started_at: float, path: Path | None = None) -> None:
    """Record one pass of the loop. Best effort — never fails a poll."""
    path = path or HEARTBEAT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": started_at,
                    "last_poll": time.time(),
                }
            )
        )
    except OSError as exc:  # noqa: BLE001 — liveness reporting is not the job
        log.warning("could not write heartbeat: %s", exc)


def heartbeat(path: Path | None = None) -> Heartbeat | None:
    """The last recorded pass, or None if there is no readable one."""
    path = path or HEARTBEAT_FILE
    try:
        raw = json.loads(path.read_text())
        return Heartbeat(
            pid=int(raw["pid"]),
            started_at=float(raw["started_at"]),
            last_poll=float(raw["last_poll"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _note_unclean_exit(webhook: str, path: Path | None = None) -> Heartbeat | None:
    """A heartbeat left behind by a dead process means the last run crashed.

    This is the half that makes a restart loop visible. `notify` is otherwise
    reachable only from task outcomes, never from a crash on the way up, so
    launchd could restart the daemon every two minutes for three quarters of an
    hour — as it did on 2026-08-15 — and say nothing at all.
    """
    previous = heartbeat(path)
    if previous is None or previous.alive:
        return None
    log.warning(
        "previous run exited uncleanly — last poll %.0fs before this start, pid %s",
        previous.age, previous.pid,
    )
    notify.send(
        webhook,
        f"nightshift: restarted after an unclean exit — the previous process "
        f"(pid {previous.pid}) last polled {_human(previous.age)} ago. "
        "Repeated messages here mean a restart loop.",
    )
    return previous


def _handle_sigterm() -> None:
    """Turn SIGTERM into an ordinary exception so `finally` blocks run.

    Without it the heartbeat survives its own process, which would report
    every `launchctl unload` as a crash.
    """
    def stop(_signum, _frame):
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, stop)
    except ValueError:
        pass  # not the main thread — a test, or an embedded caller


def _human(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return f"{int(seconds)}s"
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def paused() -> bool:
    return PAUSE_FILE.exists()


@dataclass
class RepoHealth:
    """How long each repo has been unreachable, and when to say so.

    The half of the fix that is easy to leave out. Skipping a repo keeps the
    daemon alive, but a repo skipped forever in silence is the failure that
    LOOKS fine — which is worse than the crash it replaced, because a crash at
    least leaves a traceback. So: a permanent error is somebody's problem right
    now and is announced on the first pass; a transient one is announced only
    once it has outlived a few polls, because GitHub blipping for twenty
    seconds is not news.
    """

    #: Failed passes per repo since recovery; pending retries retain the count.
    failures: dict[str, int] = field(default_factory=dict)
    #: Repos already announced, so a long outage is one message, not one a poll.
    announced: set[str] = field(default_factory=set)
    #: Transient failures tolerated before the outage is worth a human's time.
    threshold: int = 3
    #: Repeated item errors may need intervention even when other work succeeds.
    item_failures: dict[tuple[str, str, int], int] = field(default_factory=dict)

    def record(self, repo: str, exc: Exception, webhook: str = "") -> None:
        count = self.failures.get(repo, 0) + 1
        self.failures[repo] = count
        transient = getattr(exc, "transient", True)
        if repo in self.announced or (transient and count < self.threshold):
            return
        self.announced.add(repo)
        if isinstance(exc, specloop_intake.ConfigurationError):
            notify.send(webhook, f"nightshift: configuration unhealthy for {repo} — {exc}")
            return
        notify.send(
            webhook,
            f"nightshift: cannot reach {repo} — {exc}\n"
            + ("The token may no longer read it; this will not clear on its own."
               if not transient
               else f"Unreachable for {count} consecutive polls. Other repos keep running."),
        )

    def recovered(self, repo: str) -> None:
        """A pass succeeded. Re-arm, so the NEXT outage is news again."""
        if self.failures.pop(repo, 0) and repo in self.announced:
            self.announced.discard(repo)
            log.info("%s is healthy again", repo)


def _reconcile_engine_switch(repo: Repo, labels: queue.Labels, *, runner=None) -> None:
    """Retire worker claims without executing or discarding surviving work.

    Journals outlive labels and claims: a lost write response must never turn
    the final pointer to interrupted work into queue.reconcile's litter.
    """
    def check():
        if paused():
            raise specloop_intake.Paused()

    read_errors: list[Exception] = []

    def gh(args):
        try:
            check()
            with queue.gh_guard(check):
                result = (runner or queue._gh)(args)
            check()
            if tuple(args[:2]) == ("issue", "view"):
                data = json.loads(result)
                if not isinstance(data.get("labels"), list):
                    raise ValueError("missing recovery labels")
            return result
        except Exception as exc:
            # labels_of deliberately hides RuntimeError for its worker callers.
            read_errors.append(exc)
            raise

    forge = GitHubForge(gh)

    def read_labels(number):
        current = forge.issue_labels(repo.name, number)
        if read_errors:
            raise read_errors[-1]
        return current

    check()
    claims, unreadable = queue.in_flight(repo.name)
    if unreadable:
        raise ValueError(f"unreadable worker claims: {', '.join(unreadable)}")
    by_number = {claim.number: claim for claim in claims}
    root = STATE_DIR / "engine-switch" / repo.name.replace("/", "__")
    journals = {}
    for path in sorted(root.glob("*.json")):
        journal = json.loads(path.read_text())
        if journal["version"] != 1 or journal["action"] not in {"release", "hold"}:
            raise ValueError(f"unknown recovery journal: {path}")
        journals[int(path.stem)] = journal
    working = {issue.number for issue in forge.list_issues(repo.name, labels.working)}
    for number in sorted(working | set(by_number) | set(journals)):
        journal = journals.get(number)
        claim = by_number.get(number)
        if journal and journal.get("done") and claim is None and number not in working:
            continue
        current = read_labels(number)
        path = root / f"{number}.json"
        if journal and journal.get("done"):
            if claim is None and labels.working not in current:
                continue  # A stale search result does not start another recovery.
            if (labels.working not in current
                    or (claim is not None and asdict(claim) == journal["claim"])):
                raise ValueError(f"completed recovery #{number} conflicts with its claim; inspect {path}")
            # Completion applies to one attempt, not every future worker claim
            # on this issue. Retain its evidence before saving a fresh intent.
            specloop_job.save(root / "completed" / f"{number}-{journal['id']}.json", journal)
            journal = None
        if (journal is not None
                and (asdict(claim) if claim else None) != journal["claim"]
                and (claim is not None or labels.working in current)):
            # An unfinished intent belongs to its original attempt too. A
            # worker may have reclaimed the issue while outcome saving failed.
            # Only a missing claim after the transition can be our own cleanup
            # interrupted before the final journal save; keep that replayable.
            raise ValueError(f"unfinished recovery #{number} conflicts with its claim; inspect {path}")
        if journal is None:
            if labels.working not in current:
                if number in by_number:
                    raise ValueError(f"worker claim #{number} has conflicting labels; inspect it")
                continue  # Stale search result, already transitioned elsewhere.
            survives = False
            if claim:
                try:
                    Path(claim.worktree).stat()
                    survives = True
                except FileNotFoundError:
                    pass
            detail = (f"phase `{claim.phase}`, branch `{claim.branch}`, location `{claim.worktree}`"
                      if claim else "no worker claim file")
            journal = {"version": 1, "id": uuid4().hex,
                       "action": "hold" if survives else "release",
                       "claim": asdict(claim) if claim else None,
                       "detail": f"interrupted worker recovery after switch to specloop: {detail}",
                       "done": False}
            journal["marker"] = f"<!-- nightshift:draft:engine-switch-{number}:{journal['id']} -->"
            specloop_job.save(path, journal)
        hold = journal["action"] == "hold"
        target = labels.needs_human if hold else labels.ready
        conflicts = {labels.ready, labels.revise, labels.needs_human, labels.done} - {target}

        def validate_labels(current):
            if (current & conflicts or (labels.working not in current and target not in current)
                    or (journal.get("transitioned") and labels.working in current)):
                # A recorded transition cannot need repeating. Working has
                # been reapplied since then; retain the human's decision.
                raise ValueError(f"recovery #{number} has conflicting labels; inspect {path}")

        validate_labels(current)
        if hold and not journal.get("commented"):
            pages = forge.comment_pages(repo.name, number)
            if not any(journal["marker"] in c["body"] for page in pages for c in page):
                validate_labels(read_labels(number))
                forge.create_comment(repo.name, number, "issue",
                    f"{queue.SELF_COMMENT_PREFIX} {journal['detail']}. "
                    f"Work is preserved for human inspection.\n\n{journal['marker']}")
            # Once acknowledged, even a stale comment list must not cause a
            # duplicate. An ambiguous response instead reconciles the marker.
            journal["commented"] = True
            specloop_job.save(path, journal)
        # Comment delivery and local writes can take time. Recheck the object
        # before transitioning so an intervening human label edit wins.
        current = read_labels(number)
        validate_labels(current)
        if labels.working in current:
            forge.relabel_issue(repo.name, number, add=[target], remove=[labels.working])
        journal["transitioned"] = True
        specloop_job.save(path, journal)
        # record() is best effort. Verify its durable result before cleanup and
        # recognize it after a crash between the database and journal writes.
        def recorded():
            return any(item.get("engine_switch_id") == journal["id"] for item in outcomes.tasks())

        if not recorded():
            outcomes.record(repo.name, number, state="needs_decision", reason=journal["detail"],
                            summary="", escalated=hold, engine_switch_id=journal["id"])
        if not recorded():
            raise OSError(f"recovery outcome not persisted for {repo.name}#{number}")
        queue.claim_path(repo.name, number).unlink(missing_ok=True)
        journal["done"] = True
        specloop_job.save(path, journal)


def startup(cfg: Config, repo_dirs: dict[str, Path],
            health: "RepoHealth | None" = None) -> list[queue.Reconciliation]:
    """Recover leftover claims safely, independently of manifest health.

    repo_dirs and the empty legacy repair list remain CLI compatibility inputs
    and output. Durable journals and outcomes record the recovery decisions.
    """
    _check_landing_environment(cfg)
    health = health if health is not None else RepoHealth()
    for repo in cfg.repos:
        if paused():
            break
        if _gitea_held(repo, cfg, health):
            continue
        if cfg.forge_for(repo).kind == "gitea":
            continue
        try:
            _reconcile_engine_switch(repo, cfg.labels)
        except specloop_intake.Paused:
            break
        except Exception as exc:
            log.warning("engine-switch recovery %s failed — %s", repo.name, exc)
            if health is not None:
                health.record(repo.name, exc, cfg.slack_webhook)
    return []


def poll_specloop(cfg: Config, health: RepoHealth, *, runner=None, specloop_runner=None) -> None:
    """Poll specloop actions with repository health and authorization guards.

    Recheck the kill switch at every external operation, including delivery
    retries. A pause created during drafting must also stop its push/labels.
    Already admitted runs are driven by specloop's separate supervisor.
    """
    _check_landing_environment(cfg)

    def check_running():
        if paused():
            raise specloop_intake.Paused()

    def guarded(run):
        def call(*args, **kwargs):
            check_running()
            return run(*args, **kwargs)
        return call

    def gh(args):
        check_running()
        with queue.gh_guard(check_running):
            result = (runner or queue._gh)(args)
        # A dispatched write may finish during pause. Keep its receipt pending
        # even when this was the final label/comment operation in delivery.
        check_running()
        return result

    specloop_job.reap()
    cli = guarded(specloop_runner or subprocess.run)
    intake_cli = guarded(specloop_runner) if specloop_runner is not None else None
    launcher = guarded(subprocess.Popen)
    git = guarded(specloop_intake.run_git)
    for repo in cfg.repos:
        if paused():
            return
        if _gitea_held(repo, cfg, health):
            continue
        selected = {}
        if cfg.forge_for(repo).kind == "gitea":
            try:
                selected["forge"] = forge_for(repo, cfg=cfg, guard=check_running)
            except specloop_intake.Paused:
                return
            except Exception as exc:
                log.warning("forge construction %s failed — %s", repo.name, exc)
                health.record(repo.name, exc, cfg.slack_webhook)
                continue
        errors: list[Exception] = []
        readiness = specloop_intake.manifest_readiness(repo)
        if isinstance(readiness, specloop_intake.ConfigurationError):
            errors.append(readiness)
        try:
            if cfg.forge_for(repo).kind == "github":
                _reconcile_engine_switch(repo, cfg.labels, runner=gh)
        except specloop_intake.Paused:
            return
        except Exception as exc:
            log.warning("engine-switch recovery %s failed — %s", repo.name, exc)
            errors.append(exc)
            readiness = specloop_intake.ConfigurationError(
                f"{repo.name}: worker recovery incomplete; inspect retained claims/journals: {exc}")
        failed_items = set()
        pending_items = set()

        def item_pending(number):
            pending_items.add((repo.name, stage, number))

        def item_failed(number, exc):
            errors.append(exc)
            if isinstance(exc, specloop_intake.ConfigurationError):
                return  # A repository hold never consumes an item's retries.
            key = (repo.name, stage, number)
            failed_items.add(key)
            health.item_failures[key] = health.item_failures.get(key, 0) + 1
            log.warning("specloop %s %s #%s failed — %s", stage, repo.name, number, exc)
            # Give ambiguous failures time to reconcile with durable state.
            # Unavailable forge reads remain repository outages, not holds on
            # items whose eligibility we could not establish.
            transient_forge = isinstance(exc, ForgeError) and exc.transient
            if (isinstance(exc, specloop_intake.DeliveryError)
                    or (health.item_failures[key] >= health.threshold and not transient_forge)):
                # Keep the receipt and arming label: after the human resolves
                # the branch and removes needs-human, delivery can resume.
                reporting_errors = specloop_intake.report_failure(
                    repo, cfg.labels, number, stage, kind, exc, runner=gh, **selected,
                )
                for error in reporting_errors:
                    log.warning("specloop %s %s #%s escalation failed — %s",
                                stage, repo.name, number, error)
                errors.extend(reporting_errors)

        stages = (
            ("draft", "issue", specloop_intake.propose_pending,
             dict(specloop_runner=intake_cli, git_runner=git, launcher=launcher,
                  on_pending=item_pending)),
            ("admit", "pr", specloop_intake.admit_pending,
             dict(specloop_runner=intake_cli, git_runner=git, launcher=launcher, status_runner=cli,
                  on_pending=item_pending)),
            ("revise", "pr", specloop_intake.revise_pending,
             dict(specloop_runner=intake_cli, git_runner=git, launcher=launcher, status_runner=cli,
                  on_pending=item_pending)),
            ("publish", "pr", specloop_intake.publish_pending, dict(specloop_runner=cli)),
        )
        for stage, kind, action, kwargs in stages:
            if stage != "publish":
                kwargs["readiness"] = readiness
            try:
                action(repo, cfg.labels, STATE_DIR, runner=gh, on_error=item_failed, **selected, **kwargs)
            except specloop_intake.Paused:
                return
            except Exception as exc:  # noqa: BLE001 — other stages must still run
                log.warning("specloop %s %s failed — %s", stage, repo.name, exc)
                errors.append(exc)
            else:
                for key in list(health.item_failures):
                    # Completion or an item no longer armed (including a
                    # needs-human hold) settles the failure. Launching or
                    # awaiting a retry does not prove recovery.
                    if (key[:2] == (repo.name, stage)
                            and key not in failed_items and key not in pending_items):
                        del health.item_failures[key]
        # Count failed polls, not failed items/stages. A later success must not
        # erase an earlier failure or prevent a persistent outage notification.
        if errors:
            error = next((exc for exc in errors if not getattr(exc, "transient", True)), errors[0])
            health.record(repo.name, error, cfg.slack_webhook)
        elif not any(key[0] == repo.name for key in health.item_failures):
            health.recovered(repo.name)


def loop(cfg: Config, repo_dirs: dict[str, Path], *, once: bool = False) -> None:
    """Beat, reap, obey pause and poll; idle services keep polling.

    One-shot callers make one pass without waiting for detached jobs. Neither
    pause nor service exit stops builds owned by the independent supervisor.
    """
    _check_landing_environment(cfg)
    health = RepoHealth()
    service = not once
    # A terminal's one-shot pass shares state with the service but never owns
    # its heartbeat, including crash detection and cleanup.
    if service:
        _note_unclean_exit(cfg.slack_webhook)
    started_at = time.time()
    if service:
        beat(started_at)
    _handle_sigterm()
    try:
        startup(cfg, repo_dirs, health)
    except SystemExit as exc:
        # SIGTERM during startup is orderly too. Other startup failures leave
        # the first beat behind so the next service start can report the crash.
        if service and exc.code in (None, 0):
            HEARTBEAT_FILE.unlink(missing_ok=True)
        raise

    try:
        while True:
            if service:
                beat(started_at)
            specloop_job.reap()
            if paused():
                log.info("paused — %s exists", PAUSE_FILE)
            else:
                poll_specloop(cfg, health)
            if once:
                return
            time.sleep(cfg.poll_seconds)
    finally:
        if service:
            HEARTBEAT_FILE.unlink(missing_ok=True)

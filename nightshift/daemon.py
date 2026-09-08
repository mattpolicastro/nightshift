"""Polling loop: reconcile → claim → worktree → implement → review → PR.

Every judgement is delegated: `queue` decides what is claimable and repairs
crash state, `task` decides what a run's outcome means. This module only
sequences them and handles the clock.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from . import outcomes, notify, preflight, queue, recovery, task, trace, vcs
from .config import Config, Repo
from .queue import Repair
from .task import Step
from .trace import RateLimit

log = logging.getLogger("nightshift")

# The instant kill switch. Checked before claiming, so a paused daemon finishes
# its current task and then stops rather than being killed mid-commit.
PAUSE_FILE = Path.home() / ".nightshift" / "pause"

STATE_DIR = Path.home() / ".nightshift"
TRANSCRIPT_DIR = STATE_DIR / "transcripts"


# Written every poll, removed on a clean exit. The absence of this file after
# a run means the daemon stopped deliberately; its PRESENCE with a dead pid
# means it did not — which is the only way a restart loop becomes visible
# without reading the log.
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"


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

    Without it the interpreter dies where it stands: workers are abandoned
    mid-commit and the heartbeat survives its own process, which would report
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
class Tally:
    """What a session of the daemon did. Feeds the digest."""

    shipped: int = 0
    escalated: int = 0
    cost: float = 0.0
    turns: int = 0
    lines: list[str] = field(default_factory=list)
    # Latest subscription-window state any run reported. Read off the runs
    # rather than modelled from consumption — see trace.RateLimit. `type` is a
    # discriminant: five_hour is what has been observed, the weekly cap is the
    # one that actually binds, and the loop must not care which it got.
    quota: RateLimit | None = None
    # Tasks that finished without consuming subscription quota — every phase
    # ran off-subscription, by the runs' own telemetry. Counted separately
    # because `max_tasks_per_night` is a cap per SUBSCRIPTION WINDOW: work that
    # touched no window must not eat a slot in one.
    unbilled: int = 0

    @property
    def handled(self) -> int:
        return self.shipped + self.escalated

    @property
    def billed(self) -> int:
        """Handled tasks that actually consumed subscription quota.

        This, not `handled`, is what the cap counts. `handled` stays the number
        of tasks done, which is what the digest and the drain message report.
        """
        return self.handled - self.unbilled


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

    #: Consecutive failed passes per repo. Cleared the moment one succeeds.
    failures: dict[str, int] = field(default_factory=dict)
    #: Repos already announced, so a long outage is one message, not one a poll.
    announced: set[str] = field(default_factory=set)
    #: Transient failures tolerated before the outage is worth a human's time.
    threshold: int = 3

    def record(self, repo: str, exc: Exception, webhook: str = "") -> None:
        count = self.failures.get(repo, 0) + 1
        self.failures[repo] = count
        transient = getattr(exc, "transient", True)
        if repo in self.announced or (transient and count < self.threshold):
            return
        self.announced.add(repo)
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
            log.info("%s is reachable again", repo)


def startup(cfg: Config, repo_dirs: dict[str, Path],
            health: "RepoHealth | None" = None) -> list[queue.Reconciliation]:
    """Repair crash state before claiming anything new.

    RELEASED entries are already back in the queue. RESUMABLE ones carry a
    worktree with real work in it, so each is handed to `recovery.decide` —
    which reads the phase recorded in the claim file rather than guessing from
    the worktree alone.
    """
    repairs: list[queue.Reconciliation] = []
    for repo in cfg.repos:
        # Per repo, and non-fatal. This call was unguarded until 2026-09-05,
        # so one `gh issue list` failing ended the process BEFORE the loop had
        # claimed anything — 105 of the log's 107 tracebacks, across seven
        # dates. launchd's KeepAlive restarted it every two minutes for
        # three quarters of an hour on 08-15, which is what made a total
        # outage look like a working daemon.
        try:
            found = queue.reconcile(repo.name, cfg.labels)
        except Exception as exc:  # noqa: BLE001 — one repo must not end the run
            log.warning("reconcile %s skipped — %s", repo.name, exc)
            if health is not None:
                health.record(repo.name, exc, cfg.slack_webhook)
            continue
        if health is not None:
            health.recovered(repo.name)

        for r in found:
            if r.repair is Repair.RELEASED:
                outcomes.record(repo.name, r.number, state="needs_decision",
                                reason=f"interrupted: {r.detail}", escalated=False)
            log.info(
                "reconcile %s #%s: %s — %s", repo.name, r.number, r.repair.value, r.detail
            )
        repairs += found

        repo_dir = repo_dirs.get(repo.name)
        for r in found:
            if r.repair is Repair.RESUMABLE and repo_dir is not None:
                _recover(cfg, repo, repo_dir, r.number)
    return repairs


def _recover(cfg: Config, repo: Repo, repo_dir: Path, number: int) -> None:
    """Act on one interrupted task."""
    claims, _ = queue._load_claims(repo.name)  # noqa: SLF001 — same package
    claim = claims.get(number)
    if claim is None:
        return

    worktree = Path(claim.worktree)
    try:
        committed = vcs.has_commits(worktree, repo.base_ref)
    except RuntimeError:
        committed = False

    existing_pr = vcs.pr_for_branch(repo.name, claim.branch)
    plan = recovery.decide(
        claim.phase,
        has_commits=committed,
        branch_pushed=vcs.branch_on_remote(repo_dir, claim.branch),
        pr_url=existing_pr,
    )
    log.info("recover #%s from %s: %s — %s", number, claim.phase, plan.action.value, plan.reason)

    outcomes.record(repo.name, number, state="needs_decision",
                    reason=f"interrupted: {plan.reason}", summary="", escalated=False)
    if plan.action is recovery.Action.RELEASE:
        queue.release(repo.name, number, cfg.labels)
        # Through `_branch_to_delete`, not raw. This was the one release path
        # still handing a branch name straight to `git branch -D`, and RELEASE
        # is reached precisely when a task died early — including when it died
        # because an earlier ESCALATION's branch was still there. So the branch
        # this deleted was the escalated work, every time (sample #5).
        vcs.remove_worktree(
            repo_dir,
            worktree,
            _branch_to_delete(worktree, repo.base_ref, claim.branch),
            allowed_root=cfg.worktree_root,
        )

    elif plan.action is recovery.Action.COMPLETE:
        queue.complete(repo.name, number, existing_pr or "", cfg.labels)
        vcs.remove_worktree(repo_dir, worktree, allowed_root=cfg.worktree_root)

    elif plan.action is recovery.Action.OPEN_PR:
        url = vcs.open_pr(
            worktree, repo.name, claim.branch, repo.base,
            f"nightshift: #{number}",
            f"Closes #{number}.\n\nRecovered after an interrupted run "
            f"({claim.phase} phase). The diff was reviewed before the "
            "interruption; re-read it before merging.",
        )
        queue.complete(repo.name, number, url, cfg.labels)
        vcs.remove_worktree(repo_dir, worktree, allowed_root=cfg.worktree_root)

    else:
        # ESCALATE and RE_REVIEW both end up in front of a human for now.
        # RE_REVIEW is safe to automate (a committed diff is immutable) but
        # re-entering the loop mid-task is a bigger change than the recovery
        # path warrants — it is reported so the state is never silently lost.
        queue.escalate(
            repo.name,
            number,
            "**nightshift** — an interrupted run was found at startup.\n\n"
            f"Phase when interrupted: `{claim.phase}`. "
            f"Recovery decision: `{plan.action.value}` — {plan.reason}.\n\n"
            f"Branch `{claim.branch}` has been left in place for inspection.",
            cfg.labels,
        )
        notify.send(
            cfg.slack_webhook,
            f"nightshift: #{number} was interrupted ({claim.phase}) — {plan.reason}",
        )


@dataclass(frozen=True)
class Claimed:
    """One claimed issue and everything running it needs.

    Exists so claiming and running can happen on different threads. Claiming
    must not.
    """

    repo: Repo
    repo_dir: Path
    issue: queue.Issue
    claim: queue.Claim


def endpoints_ready(cfg: Config, repo: Repo, *, prober=None) -> tuple[bool, str]:
    """Can every endpoint this repo's phases need actually be reached?

    An endpoint is a machine that may be off — the EVO-X2 is always-on but MLPC
    is powered up for a specific job, so unreachability is the normal case
    rather than the exception. Checked BEFORE claiming, because claiming work
    that cannot run means an issue labelled `agent:working` with nothing
    working on it, recoverable only by `reconcile`.

    There is deliberately no fallback to the subscription endpoint. Silent
    fallback converts a free run into a billed one and hides that the local
    tier is down — the same shape as a `base:` typo branching from `main`,
    where the work lands, CI passes, and nobody learns until later.

    Repos on the default endpoint never probe anything, so this costs today's
    configuration exactly nothing.
    """
    for phase in ("implement", "review"):
        assignment = cfg.assign(phase, repo)
        if assignment.endpoint.is_default:
            continue
        endpoint = assignment.endpoint
        if not endpoint.url:
            # Nothing to probe, and nothing that could run it: an `openai`
            # endpoint with no proxy cannot be driven by `claude -p` at all.
            # Said plainly here, because the alternative is a urllib artifact
            # ("unknown url type: '/v1/models'") in the daemon log.
            return False, (
                f"{phase} endpoint {endpoint.name} has no "
                + ("proxy_url — `claude -p` speaks Anthropic only"
                   if endpoint.protocol == "openai" else "base_url")
            )
        probe = prober or preflight._default_prober  # noqa: SLF001 — same package
        token = os.environ.get(endpoint.auth_env, "")
        try:
            probe(endpoint, token)
        except Exception as exc:  # noqa: BLE001 — every failure is "not reachable"
            return False, f"{phase} endpoint {endpoint.name} unreachable: {exc}"
    return True, ""


def claim_next(
    cfg: Config,
    repo_dirs: dict[str, Path],
    running: dict[str, int] | None = None,
    *,
    prober=None,
    health: RepoHealth | None = None,
) -> Claimed | None:
    """Claim the next task, walking repos in config order. None when nothing is ready.

    **Always called on the loop thread, never from a worker.** `queue.claim` is
    check-then-act against the GitHub API (`queue.py`'s module docstring says so
    and says it is undefended); two threads inside it can hand the same issue to
    two workers. Serialising the claim is what makes concurrent EXECUTION safe
    without touching `queue.py` at all — the claim is a fast API call and the
    run is minutes, so nothing is lost by keeping it here.

    `running` is how many tasks each repo already has in flight. A repo at its
    own `concurrency` is SKIPPED rather than ending the walk, so a later repo
    still gets claimed — that skip is the whole fairness mechanism.

    Config order remains priority order for the FIRST slot each repo takes: the
    walk starts from the top every time. What it no longer does is let one repo
    hold every slot. Before per-repo caps, a repo with a deep queue refilled
    from the top on each free slot and everything below it starved (observed
    2026-08-07: swift-app at "0 working, 2 ready" for a day behind sample).
    """
    running = running or {}
    for repo in cfg.repos:
        if running.get(repo.name, 0) >= max(1, repo.concurrency):
            continue
        repo_dir = repo_dirs.get(repo.name)
        if repo_dir is None:
            log.warning("no local checkout configured for %s", repo.name)
            continue
        ready, why = endpoints_ready(cfg, repo, prober=prober)
        if not ready:
            # Leave it armed and say so. The issue stays claimable the moment
            # the box comes back, and nothing was spent finding out.
            log.info("skipping %s — %s", repo.name, why)
            continue
        # A repo GitHub cannot currently answer for is SKIPPED, not fatal —
        # the walk continues to the next one, which is the same fairness
        # mechanism the per-repo concurrency cap uses. Before this, an error
        # here propagated out of the loop and ended the process.
        try:
            claimed = queue.claim(repo.name, cfg.worktree_root, cfg.labels)
        except Exception as exc:  # noqa: BLE001 — one repo must not end the loop
            log.warning("claim %s skipped — %s", repo.name, exc)
            if health is not None:
                health.record(repo.name, exc, cfg.slack_webhook)
            continue
        if health is not None:
            health.recovered(repo.name)
        if claimed is None:
            continue
        issue, claim = claimed
        log.info("claimed %s #%s — %s", repo.name, issue.number, issue.title)
        return Claimed(repo=repo, repo_dir=repo_dir, issue=issue, claim=claim)
    return None


def run_claimed(cfg: Config, claimed: Claimed, tally: Tally) -> None:
    try:
        _run_claimed(cfg, claimed, tally)
    except Exception as exc:
        outcomes.record(claimed.repo.name, claimed.issue.number,
                        harness_error=str(exc))
        raise


def _run_claimed(cfg: Config, claimed: Claimed, tally: Tally) -> None:
    """Run one already-claimed task to its outcome. Runs on a worker thread.

    `tally` is this task's OWN tally, not the loop's — the caller merges it in
    once the thread is done. That is what keeps the counters correct without a
    lock: no two threads ever touch the same `Tally`, and `+=` on a dataclass
    attribute is a load/add/store that would otherwise be free to interleave.
    """
    repo, repo_dir = claimed.repo, claimed.repo_dir
    issue, claim = claimed.issue, claimed.claim

    run_id = uuid4().hex
    run_dir = TRANSCRIPT_DIR / run_id
    outcomes.record(repo.name, issue.number, state="running", title=issue.title,
                    run_id=run_id, pr_url="", escalated=False, summary="",
                    reason="started", harness_error="", attempt=0, attempts=0,
                    attempt_result="", verdict=None, implement_transcript=None,
                    review_transcript=None)
    try:
        report = task.run(
            cfg, repo, repo_dir, issue, claim, transcript_dir=run_dir
        )
    except Exception as exc:  # noqa: BLE001 — a crash must not strand the issue
        outcomes.record(repo.name, issue.number, state="needs_decision",
                        reason=f"harness crashed: {exc}", summary="", escalated=False)
        log.exception("task #%s crashed", issue.number)
        queue.release(repo.name, issue.number, cfg.labels)
        vcs.remove_worktree(
            repo_dir, Path(claim.worktree), claim.branch,
            allowed_root=cfg.worktree_root,
        )
        notify.send(cfg.slack_webhook, f"nightshift: #{issue.number} crashed — {exc}")
        # A crash still consumed a slot and still counts against the cap, so it
        # has to register as handled — at concurrency 1 `return True` said that
        # by making the loop re-poll rather than fall through to "drained".
        tally.escalated += 1
        tally.lines.append(f"crashed #{issue.number} — {exc}")
        return

    last = report.attempts[-1] if report.attempts else None
    summary = trace.skim(last.review.text).summary if last and last.review else ""
    outcomes.record(repo.name, issue.number,
                    state="awaiting_merge" if report.step is Step.SHIP else "needs_decision",
                    reason=report.reason, summary=summary, pr_url=report.pr_url,
                    attempts=len(report.attempts))
    tally.cost += report.cost
    tally.turns += report.turns

    if report.step is Step.SHIP:
        queue.complete(repo.name, issue.number, report.pr_url, cfg.labels)
        tally.shipped += 1
        tally.lines.append(f"shipped #{issue.number} → {report.pr_url}")
        log.info("shipped #%s: %s", issue.number, report.pr_url)
        # Success is worth a message too: a PR sitting unreviewed is the one
        # outcome that stalls the whole queue behind it, and until the digest
        # exists this is the only way to learn about it before morning.
        # The link alone made every ship look identical, so deciding which to
        # open meant opening all of them. Lead with what the reviewer said and
        # whether anything actually wants a human.
        last = report.attempts[-1] if report.attempts else None
        s = trace.skim(last.review.text) if last and last.review else trace.Skim()
        tail = (
            f" — {len(s.needs_human)} for you"
            if s.needs_human
            else " — nothing for you"
        )
        notify.send(
            cfg.slack_webhook,
            f"nightshift: #{issue.number} shipped{tail}\n"
            f"{s.summary or issue.title}\n{report.pr_url}",
        )
    else:
        # Read the findings out of the worktree before tearing it down — they
        # only exist on disk until this point, and the issue comment is where
        # they need to survive.
        findings = _escalation_text(Path(claim.worktree), report)
        queue.escalate(repo.name, issue.number, findings, cfg.labels)
        vcs.remove_worktree(
            repo_dir, Path(claim.worktree),
            _branch_to_delete(Path(claim.worktree), repo.base_ref, claim.branch),
            allowed_root=cfg.worktree_root,
        )
        tally.escalated += 1
        tally.lines.append(f"escalated #{issue.number} — {report.reason}")
        log.info("escalated #%s: %s", issue.number, report.reason)
        notify.send(
            cfg.slack_webhook,
            f"nightshift: #{issue.number} needs a human — {report.reason}",
        )

    if not report.subscription_billed:
        tally.unbilled += 1
        log.info("#%s consumed no subscription quota", issue.number)

    _record_quota(report, tally)


def _merge(dst: Tally, src: Tally) -> None:
    """Fold a finished task's tally into the loop's. Loop thread only."""
    dst.shipped += src.shipped
    dst.escalated += src.escalated
    dst.cost += src.cost
    dst.turns += src.turns
    dst.unbilled += src.unbilled
    dst.lines += src.lines
    # Latest wins, same rule as `_record_quota` — a task that reported nothing
    # about the window must not erase what another task just learned about it.
    if src.quota is not None:
        dst.quota = src.quota


def _running(in_flight: dict[Future, tuple[str, Tally]]) -> dict[str, int]:
    """How many tasks each repo has in flight, for the per-repo cap."""
    counts: dict[str, int] = {}
    for repo_name, _ in in_flight.values():
        counts[repo_name] = counts.get(repo_name, 0) + 1
    return counts


def _harvest(in_flight: dict[Future, tuple[str, Tally]], tally: Tally) -> int:
    """Merge every finished task. Returns how many completed."""
    done = [f for f in in_flight if f.done()]
    for fut in done:
        _, sub = in_flight.pop(fut)
        try:
            fut.result()
        except Exception:  # noqa: BLE001 — a worker thread must not kill the loop
            log.exception("worker thread raised")
        _merge(tally, sub)
    return len(done)


def loop(cfg: Config, repo_dirs: dict[str, Path], *, once: bool = False) -> Tally:
    """Poll forever. Nothing here is an exit except `once`.

    An empty queue means "sleep and poll again", not "stop": a launchd agent
    that exited on drain would stay down, since `KeepAlive` only restarts an
    UNSUCCESSFUL exit, and would then miss every issue filed afterwards. The
    pause file and the cap are likewise holds, not stops.

    The cap counts per SUBSCRIPTION WINDOW, read off `resets_at` in the runs'
    own telemetry rather than a wall clock. `max_tasks_per_night` was a
    per-process lifetime counter, which meant the daemon quietly retired after
    N tasks however many days that took.
    """
    tally = Tally()
    # The wrap-up fires when the queue GOES quiet, not when the loop ends —
    # nothing ends the loop, so a run that ships four tasks out of a queue of
    # four would otherwise never send one. Edge-triggered, so a long idle
    # stretch is one message rather than one every poll.
    announced_drain = False
    announced_cap = False
    # Consecutive per-repo failures, so an outage that outlives a few polls
    # reaches a human instead of being skipped in silence forever.
    health = RepoHealth()
    # Before anything else writes one: a heartbeat left by a process that is
    # gone is the evidence that the last run died rather than stopped.
    # `once` is a human at a terminal, not the service. It must not touch the
    # heartbeat: a one-shot run on the same machine shares the file with the
    # daemon, so a clean one DELETES the live daemon's proof of life — `status`
    # then reports "not running" for a process that is fine — and a crashed one
    # leaves a dead pid behind, which reads as DEAD and makes the daemon's next
    # restart announce an unclean exit that never happened. Found by running
    # `--once` beside the live daemon on 2026-09-05, an hour after shipping it.
    service = not once
    if service:
        _note_unclean_exit(cfg.slack_webhook)
    started_at = time.time()
    # Beat BEFORE `startup()`, not after the loop begins. `startup()` is where
    # 105 of the log's 107 tracebacks happened, and it makes GitHub calls that
    # can take seconds — so a heartbeat written only once the loop is turning
    # would be missing for the entire window in which the daemon is most likely
    # to die, and the crash it exists to expose would leave nothing behind.
    if service:
        beat(started_at)
    # launchd stops a service with SIGTERM, which kills the interpreter without
    # running `finally` — so without this a deliberate `launchctl unload` would
    # leave a heartbeat behind and be reported as a crash on the next start.
    _handle_sigterm()
    # After `health` exists, so a repo unreachable at startup is recorded
    # rather than ending the run before a single task is claimed.
    startup(cfg, repo_dirs, health)
    window = _WindowStart()
    # Two ceilings, deliberately. `cfg.concurrency` is the MACHINE limit — what
    # this box can run without the verify suites contending. The per-repo caps
    # are the FAIRNESS limit — what stops one repo's queue holding every slot.
    # Whichever binds first wins, and the pool is sized for the machine.
    slots = max(1, cfg.concurrency)
    in_flight: dict[Future, tuple[str, Tally]] = {}
    pool = ThreadPoolExecutor(max_workers=slots, thread_name_prefix="nightshift")
    log.info(
        "concurrency: %s slot(s) machine-wide; per repo %s",
        slots,
        ", ".join(f"{r.name}={max(1, r.concurrency)}" for r in cfg.repos) or "none",
    )

    try:
        while True:
            # Every pass, before any work or waiting: a poll that takes a long
            # time is still a poll that happened, and the age of this file is
            # what `status` reads to tell idle from dead.
            if service:
                beat(started_at)
            _harvest(in_flight, tally)

            if paused():
                # In-flight tasks are deliberately left to finish. The pause
                # file has always meant "claim nothing new", and killing a
                # worker mid-commit is the thing it exists to avoid.
                log.info("paused — %s exists", PAUSE_FILE)
                if once and not in_flight:
                    break
                time.sleep(cfg.poll_seconds)
                continue

            if window.rolled_over(tally):
                log.info("subscription window rolled over — cap reset")
                window = _WindowStart(handled=tally.billed)
                announced_cap = False

            # In-flight work counts against the cap. It is not `handled` yet —
            # that is the point: without it, N slots dispatch N tasks each
            # believing there is room, and the cap overshoots by up to N-1.
            # In-flight work counts as billed: nothing is known about a run
            # until it reports, and handing out a free slot on the strength of
            # missing telemetry is the wrong direction to be wrong in.
            spent = tally.billed + len(in_flight) - window.handled
            if spent >= cfg.max_tasks_per_night:
                if in_flight:
                    # The cap is spent but stragglers are still running. Let
                    # them land before announcing: the wrap-up reports `tally`,
                    # and a task still in flight has contributed nothing to it
                    # yet — announcing now would undercount the very work that
                    # reached the cap.
                    futures_wait(
                        list(in_flight), timeout=cfg.poll_seconds,
                        return_when=FIRST_COMPLETED,
                    )
                    continue
                if once:
                    break
                if not announced_cap:
                    log.info(
                        "cap reached (%s per window), holding until %s",
                        cfg.max_tasks_per_night, window.describe(tally),
                    )
                    notify.send(
                        cfg.slack_webhook,
                        _wrap_up(
                            f"hit the cap of {cfg.max_tasks_per_night} for this "
                            f"window — resuming {window.describe(tally)}",
                            tally,
                        ),
                    )
                    announced_cap = True
                time.sleep(cfg.poll_seconds)
                continue

            # Fill every free slot the caps still allow, re-walking the repo
            # list each time. `claim_next` skips a repo already at its own
            # concurrency, so a repo with a deep queue can no longer take every
            # slot — which is what starved swift-app behind sample.
            dispatched = 0
            while len(in_flight) < slots and spent + dispatched < cfg.max_tasks_per_night:
                claimed = claim_next(
                    cfg, repo_dirs, _running(in_flight), health=health
                )
                if claimed is None:
                    break
                sub = Tally()
                fut = pool.submit(run_claimed, cfg, claimed, sub)
                in_flight[fut] = (claimed.repo.name, sub)
                dispatched += 1
                if once:
                    break  # `once` means one task, not one full set of slots

            if once and not in_flight:
                break
            if dispatched:
                announced_drain = False  # re-arm: the next quiet spell is news again
                continue
            if in_flight:
                # Something is running and nothing new can start. Wait on the
                # work rather than the clock: polling GitHub every 120s to be
                # told the queue is still busy costs API calls and delays the
                # next claim by up to a full poll after a slot frees.
                futures_wait(
                    list(in_flight), timeout=cfg.poll_seconds,
                    return_when=FIRST_COMPLETED,
                )
                continue
            # "Drained" means nothing ready AND nothing still running. Without
            # the second half it fires the moment the last task is dispatched,
            # reporting a total that excludes the work still in flight.
            if tally.handled and not announced_drain:
                log.info("queue drained after %s task(s)", tally.handled)
                notify.send(cfg.slack_webhook, _wrap_up("queue drained", tally))
                announced_drain = True
            time.sleep(cfg.poll_seconds)
    finally:
        # Never abandon a running worker: it holds a claim, a worktree and quite
        # possibly an open PR. Draining here is what makes `once` and an
        # interrupted loop leave the same recoverable state a clean run does.
        pool.shutdown(wait=True)
        _harvest(in_flight, tally)
        # Reaching here at all means the exit was orderly, so the heartbeat
        # goes. What it leaves behind is the signal: a file whose pid is dead.
        # Only ours to remove — a `--once` run never wrote one, and deleting
        # the daemon's would be reporting a live process as stopped.
        if service:
            HEARTBEAT_FILE.unlink(missing_ok=True)

    return tally


# A day, for when no run has reported a window yet. Only reachable if runs
# completed without `rate_limit` in their telemetry — trace says it rides every
# successful run, so this is the belt to that braces, not a normal path.
_FALLBACK_WINDOW_SECONDS = 24 * 60 * 60


@dataclass
class _WindowStart:
    """Where the current cap window began: a task count and a wall clock.

    Both are needed. The count is what the cap compares against — `Tally.billed`,
    not `handled`, since work that consumed no quota did not spend a window; the clock is
    the fallback for deciding the window is over when the runs told us nothing
    about their subscription window.
    """

    # Billed tasks at the moment the window opened, not total tasks: the cap
    # counts subscription-billed work, so a night of local runs neither spends
    # the cap nor rolls it over.
    handled: int = 0
    # Resolved through the module at call time, not bound at import, so a test
    # can hand the loop a clock.
    at: float = field(default_factory=lambda: time.time())

    def ends_at(self, tally: Tally) -> float:
        if tally.quota is not None:
            return float(tally.quota.resets_at)
        return self.at + _FALLBACK_WINDOW_SECONDS

    def rolled_over(self, tally: Tally) -> bool:
        return tally.billed > self.handled and time.time() >= self.ends_at(tally)

    def describe(self, tally: Tally) -> str:
        when = time.strftime("%H:%M", time.localtime(self.ends_at(tally)))
        kind = tally.quota.type if tally.quota is not None else "fallback 24h"
        return f"{when} ({kind})"


def _wrap_up(headline: str, tally: Tally) -> str:
    return (
        f"nightshift: {headline} — {tally.shipped} shipped, "
        f"{tally.escalated} escalated, {tally.turns} turns, "
        f"${tally.cost:.2f} quota-equivalent"
    )


def _record_quota(report: task.Report, tally: Tally) -> None:
    """Log the window state each run reported, and keep the latest for the loop.

    The loop's cap window is this telemetry, not a wall clock — so the last
    `resets_at` seen is load-bearing rather than decorative. Latest wins: a
    run's own report supersedes anything an earlier run said about the window
    it was in.
    """
    for attempt in report.attempts:
        for run in (attempt.implement, attempt.review):
            if run and run.rate_limit:
                rl = run.rate_limit
                log.info(
                    "quota: %s %s resets_at=%s overage=%s",
                    rl.type, rl.status, rl.resets_at, rl.using_overage,
                )
                if rl.using_overage:
                    log.warning("quota: RUNNING ON OVERAGE")
                tally.quota = rl


def _branch_to_delete(worktree: Path, base: str, branch: str) -> str | None:
    """`None` when the branch holds work, so teardown keeps it.

    Observed 2026-08-05 on sample #2, the first real-repo task. It escalated
    after two review rounds having committed a 240-line diff, and teardown
    passed the branch straight to `git branch -D` — so the worktree AND the
    branch went, and 211 turns of work survived only as a dangling object
    waiting for `gc`. The escalation comment sends a human to read
    `ensemble.ts:1931`; there was nothing left to read it in.

    `task.py`'s teardown comment already claimed escalated work was kept for
    inspection. It was right about the intent and wrong about who does it — the
    daemon tears down, not `task.run`, and it was not asking.

    The worktree still goes: it holds a `node_modules`, and while it lives it
    locks the branch against checkout in the clone Matt works in. The commits
    are the part worth keeping, and a branch ref costs nothing.

    Failure to answer keeps the branch. Deleting work because a `git log` broke
    is the strictly worse mistake.
    """
    try:
        return None if vcs.has_commits(worktree, base) else branch
    except (RuntimeError, OSError):
        return None


def _escalation_text(worktree: Path, report: task.Report) -> str:
    """The comment posted to the issue. Prefers the agent's own findings."""
    escalation = worktree / task.ESCALATION_FILE
    if escalation.exists():
        body = escalation.read_text()
    elif report.escalation:
        # The harness refused before any agent ran, so there is no file and no
        # reviewer text — but there IS something specific to say.
        body = report.escalation
    elif report.attempts and report.attempts[-1].review:
        body = (
            "The reviewer rejected this after "
            f"{len(report.attempts)} attempt(s):\n\n"
            + report.attempts[-1].review.text
        )
    else:
        body = "_No findings were produced._"

    return (
        f"**nightshift** — escalated, not implemented. Reason: {report.reason}\n\n"
        f"{body}\n\n---\n"
        f"Run: {report.turns} turns, ${report.cost:.2f} list-price equivalent "
        "(subscription quota, not metered billing)."
    )

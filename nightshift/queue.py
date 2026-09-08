"""GitHub Issues queue via the `gh` CLI.

Two pieces of state have to agree and cannot be updated atomically together:

  - the **label** on GitHub says whether an issue is claimed
  - the **claim file** on disk says where that claim's work lives

A crash between the two leaves them disagreeing. `reconcile()` is the only
thing that resolves it, and the daemon must call it at startup before claiming
anything new. Everything else here is deliberately dumb so that reconcile has a
small, enumerable set of states to repair.

Ordering: the claim file is written **before** the label swap. Both crash
windows are recoverable, but this one fails safer — a claim file with no
matching label is inert litter, whereas a label with no claim file is an issue
that looks owned by nobody.

Single-daemon assumption: claiming is check-then-act against the GitHub API and
is therefore racy between two daemons. One daemon on the Mac Studio is the
design (plan: concurrency 1 cloud), so this is not defended against — it is
recorded here so nobody later assumes otherwise.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from . import outcomes

CLAIM_DIR = Path.home() / ".nightshift" / "claims"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Labels:
    ready: str = "agent:ready"
    working: str = "agent:working"
    done: str = "agent:done"
    needs_human: str = "needs-human"
    #: Revise a shipped task IN PLACE, on the branch it already produced.
    #: `ready` always branches from base, which is right for new work and
    #: wrong for "this PR is close, change these two things" — that answer
    #: used to be "close the PR and let it start over", throwing away a diff
    #: that was mostly correct.
    revise: str = "agent:revise"

    @property
    def admission(self) -> tuple[str, str]:
        """The two labels that make an issue claimable."""
        return (self.ready, self.revise)


@dataclass(frozen=True)
class Issue:
    repo: str
    number: int
    title: str
    body: str
    #: Claimed via `revise` rather than `ready` — continue the existing
    #: branch instead of cutting a new one from base.
    revise: bool = False

    @property
    def branch(self) -> str:
        return f"claude/{self.number}"


class Phase(str, Enum):
    """How far a task had got when the daemon last touched its claim file.

    Recorded so that recovery after a crash is a decision rather than a guess:
    a task killed before it committed anything and one killed with a pushed
    branch need opposite handling, and the worktree alone cannot tell them
    apart. `str` mixin so it round-trips through JSON unchanged.
    """

    CLAIMED = "claimed"  # claim file written; worktree may not exist yet
    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"  # a diff is committed; the branch is not pushed
    SHIPPING = "shipping"  # push and PR in progress


@dataclass
class Claim:
    """Where an owned issue's work lives. Written before the label swap."""

    repo: str
    number: int
    branch: str
    worktree: str
    started_at: str
    phase: str = Phase.CLAIMED.value
    #: Written so a crash mid-revise recovers as a revise. Defaulted, so claim
    #: files from before this existed still load.
    revise: bool = False

    @property
    def path(self) -> Path:
        return claim_path(self.repo, self.number)

    def advance(self, phase: Phase) -> None:
        """Record progress. Cheap, and the only thing that makes recovery sane."""
        self.phase = phase.value
        self.write()

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a crash mid-write must not leave a claim file that
        # parses as valid but truncated.
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=1))
        tmp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class Repair(Enum):
    """What reconcile() did to a disagreeing pair."""

    RESUMABLE = "resumable"  # label + claim + worktree present — watchdog picks up
    RELEASED = "released"  # label with no usable local work — back to ready
    LITTER = "litter"  # claim file with no matching label — deleted


@dataclass(frozen=True)
class Reconciliation:
    repair: Repair
    number: int
    detail: str


def claim_path(repo: str, number: int) -> Path:
    return CLAIM_DIR / f"{repo.replace('/', '__')}#{number}.json"


class GhError(RuntimeError):
    """A `gh` call that failed, and whether it is worth trying again.

    The distinction is the whole point. "GitHub is briefly unavailable" and
    "the token can no longer read this repo" arrive through the same non-zero
    exit, and they want opposite handling: the first must not be fatal, the
    second must not be retried into silence. Retrying a 404 until the heat
    death of the universe is a WORSE failure than exiting, because it looks
    fine while nothing happens.
    """

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


# Measured, not guessed: every string here comes from the 107 tracebacks in the
# daemon log across 2026-08-09, 08-15, 08-17, 08-25, 08-26, 08-31 and 09-03.
# The 5xx are the ones that matter — they are GitHub being briefly unavailable,
# which it will go on being, not a flaky house connection.
_TRANSIENT_MARKERS = (
    # GitHub answered, badly. The 5xx are the ones that matter: 31 of the log's
    # 107 tracebacks, and they are GitHub being briefly unavailable rather than
    # a flaky house connection.
    "no server is currently available",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    # Nobody answered at all. A transport failure is never "the token cannot
    # read this repo", so the whole family is safe to retry — and the family is
    # wider than the log's phrasings suggested. Measured 2026-09-05 by pointing
    # `gh` at a dead host: it says `dial tcp …: connect: connection refused`,
    # which an earlier version of this list did not match, so the commonest
    # real-world failure was the one not being retried.
    "tls handshake timeout",
    "error connecting to",
    "unexpected eof",
    "timeout",
    "dial tcp",
    "connection refused",
    "connection reset",
    "no such host",
    "network is unreachable",
    "temporary failure in name resolution",
)

_RETRIES = 3
_BACKOFF_SECONDS = (2, 8)


def _is_transient(stderr: str) -> bool:
    """Only a RECOGNISED outage is retried; everything else fails fast.

    Defaulting the unknown to permanent cannot be worse than today, where every
    error is fatal — and it is the safe direction: an unrecognised error
    surfaces on the first pass instead of being retried into a silence that
    looks like a working daemon.
    """
    low = stderr.lower()
    return any(marker in low for marker in _TRANSIENT_MARKERS)


def _gh(args: list[str], *, sleep=time.sleep) -> str:
    """Run `gh`, retrying a transient outage a few times before giving up.

    Retry buys the twenty-second outage and nothing more; the 2026-08-15
    incident ran three quarters of an hour, and what survives that is the
    caller skipping the repo for this pass rather than the process exiting.
    Both halves are needed and they are not the same fix.
    """
    last = ""
    for attempt in range(_RETRIES):
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False
        )
        if proc.returncode == 0:
            return proc.stdout
        last = proc.stderr.strip()
        if not _is_transient(last):
            raise GhError(f"gh {' '.join(args)} failed: {last}", transient=False)
        if attempt < _RETRIES - 1:
            wait = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
            log.warning(
                "gh %s failed (%s) — retrying in %ss", args[0], last, wait
            )
            sleep(wait)
    raise GhError(
        f"gh {' '.join(args)} failed after {_RETRIES} attempts: {last}",
        transient=True,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _list(repo: str, label: str, *, runner=_gh) -> list[Issue]:
    out = runner(
        [
            "issue", "list",
            "--repo", repo,
            "--label", label,
            "--state", "open",
            "--json", "number,title,body",
            "--limit", "100",
        ]
    )
    return [
        Issue(repo=repo, number=i["number"], title=i["title"], body=i.get("body") or "")
        for i in json.loads(out or "[]")
    ]


def labels_of(repo: str, number: int, *, runner=_gh) -> set[str]:
    """One issue's labels, read through the search index.

    `issue view` reads the object; `issue list --label` reads an index that
    lags label writes by a few seconds. Use this wherever being wrong is
    destructive.
    """
    try:
        out = runner(
            ["issue", "view", str(number), "--repo", repo, "--json", "labels"]
        )
    except RuntimeError:
        return set()
    return {label["name"] for label in json.loads(out or "{}").get("labels", [])}


def title_of(repo: str, number: int, *, runner=_gh) -> str:
    """One issue's title, for display. Empty string if GitHub is unreachable.

    Only `status` uses this, and it must degrade rather than fail: the claim
    file already names the issue, and a number with no title is a worse answer
    than a number with one but a much better answer than a traceback because
    the wifi dropped.
    """
    try:
        out = runner(["issue", "view", str(number), "--repo", repo, "--json", "title"])
    except RuntimeError:
        return ""
    try:
        return json.loads(out or "{}").get("title") or ""
    except json.JSONDecodeError:
        return ""


def comments_of(
    repo: str, number: int, since: str | None = None, *, runner=_gh
) -> list[str]:
    """An issue's comments, oldest first, as `author: body` blocks.

    Not decoration. `escalate()` posts its findings here specifically "so the
    work is not repeated" — but the worker was only ever handed `title` and
    `body`, so on a re-queue it could not see them and repeated the work
    anyway. Observed 2026-08-04 on issue #6: the first run escalated, the
    findings were posted, and the second run rediscovered them from scratch.

    Comments are also how a human answers an escalation and how scope gets
    amended after the issue is written. Anything posted here was invisible.

    `since` filters to comments created after an ISO-8601 instant, for the
    revise guard's "did anyone actually ask for something" question. Unset —
    the default, and what the implement path passes — returns all of them,
    because the whole point there is the accumulated history.
    """
    try:
        out = runner(
            ["issue", "view", str(number), "--repo", repo, "--json", "comments"]
        )
    except RuntimeError:
        return []
    found = _newer_than(json.loads(out or "{}").get("comments") or [], since, "createdAt")
    return [b for b in (_block(c.get("author"), c.get("body")) for c in found) if b]


#: Every comment the daemon writes starts with this (`daemon.py:137`, `:406`).
#: It authenticates AS Matt, so the author field cannot tell its own escalation
#: write-ups apart from a human's instructions — the prefix is the only
#: discriminator there is, and `has_instructions` depends on it.
SELF_COMMENT_PREFIX = "**nightshift** —"


def _newer_than(items: list[dict], since: str | None, key: str) -> list[dict]:
    """Comments created after `since`, an ISO-8601 string. All of them if None.

    String comparison, deliberately: GitHub returns Zulu ISO-8601 and so does
    `git log --date=iso-strict-local` with TZ=UTC, which sort correctly as
    text. Parsing two formats to compare them would add a failure mode to a
    guard whose whole job is to be more reliable than the thing it guards.
    """
    if not since:
        return items
    return [i for i in items if (i.get(key) or "") > since]


def pr_feedback(
    repo: str, branch: str, since: str | None = None, *, runner=_gh
) -> list[str]:
    """Review feedback on the branch's most recent PR, oldest first.

    Three kinds, and they are three different shapes from two endpoints:
    conversation comments and review bodies come off `gh pr view`, while the
    INLINE comments — the ones anchored to a file and line — are only on
    `/pulls/{n}/comments`. The inline ones are the reason this exists: they
    carry `path` and `line`, which is exactly the anchoring a revision needs
    and which prose in an issue has to reconstruct by hand.

    Only the most recent PR for the branch. A revise that opens a new PR leaves
    the old closed one holding the previous round's feedback, and re-feeding
    that would make every revision re-litigate the one before it.
    """
    number = _latest_pr_number(repo, branch, runner=runner)
    if number is None:
        return []

    blocks: list[str] = []
    try:
        out = runner(
            ["pr", "view", str(number), "--repo", repo, "--json", "comments,reviews"]
        )
        found = json.loads(out or "{}")
    except RuntimeError:
        found = {}

    for c in _newer_than(found.get("comments") or [], since, "createdAt"):
        blocks.append(_block(c.get("author"), c.get("body")))

    for r in _newer_than(found.get("reviews") or [], since, "submittedAt"):
        state = (r.get("state") or "").replace("_", " ").lower()
        label = f" ({state})" if state and state != "commented" else ""
        blocks.append(_block(r.get("author"), r.get("body"), suffix=label))

    # `gh pr view` cannot reach these; only the REST endpoint has them.
    try:
        out = runner(["api", f"repos/{repo}/pulls/{number}/comments", "--paginate"])
        inline = json.loads(out or "[]")
    except RuntimeError:
        inline = []

    for c in _newer_than(inline, since, "created_at"):
        where = c.get("path") or ""
        line = c.get("line") or c.get("original_line")
        if where and line:
            where = f"{where}:{line}"
        blocks.append(
            _block(c.get("user"), c.get("body"), suffix=f" on `{where}`" if where else "")
        )

    return [b for b in blocks if b]


def _block(author: dict | None, body: str | None, suffix: str = "") -> str:
    name = (author or {}).get("login") or "unknown"
    text = (body or "").strip()
    return f"**{name}{suffix}:**\n\n{text}" if text else ""


def _latest_pr_number(repo: str, branch: str, *, runner=_gh) -> int | None:
    """The highest-numbered PR for a branch, open or closed.

    Highest number rather than `--limit 1`, because that trusts an ordering
    `gh` does not promise, and picking the WRONG one here means revising
    against a previous round's feedback.
    """
    try:
        out = runner(
            [
                "pr", "list",
                "--repo", repo,
                "--head", branch,
                "--state", "all",
                "--json", "number",
                "--limit", "20",
            ]
        )
    except RuntimeError:
        return None
    found = [p.get("number") for p in json.loads(out or "[]") if p.get("number")]
    return max(found) if found else None


def has_instructions(blocks: list[str]) -> bool:
    """Is any of this a human asking for something?

    A revise with no instruction is the trap this whole path creates: the
    natural gesture is to comment on the PR while closing it, and before
    `pr_feedback` existed those words reached nobody. The worker would then see
    the original issue, no revision request, and no way to tell "nothing was
    asked" apart from "the issue body is the ask" — so it would redo the work
    and look like it had succeeded.

    The daemon's own escalation write-ups do not count. They are newer than the
    branch tip and would otherwise satisfy this check on their own — and since
    it comments AS Matt, the prefix in the BODY is the only thing that
    distinguishes them. `_block` puts the author on the first line, so the body
    is what follows the blank line after it.
    """
    for block in blocks:
        body = block.split("\n\n", 1)[-1].strip()
        if body and not body.startswith(SELF_COMMENT_PREFIX):
            return True
    return False


def _relabel(
    repo: str, number: int, *, add: list[str], remove: list[str], runner=_gh
) -> None:
    args = ["issue", "edit", str(number), "--repo", repo]
    for label in add:
        args += ["--add-label", label]
    for label in remove:
        args += ["--remove-label", label]
    runner(args)


def ready(repo: str, labels: Labels = Labels(), *, runner=_gh) -> list[Issue]:
    """Admission gate: issues Matt has marked claimable, oldest first.

    Two labels open the gate. `ready` is new work and cuts a branch from base;
    `revise` continues the branch a previous run already shipped. An issue
    carrying both is taken as a revise — the narrower, less destructive
    reading, since starting from base would discard the diff either way.
    """
    fresh = _list(repo, labels.ready, runner=runner)
    revising = [
        replace(i, revise=True) for i in _list(repo, labels.revise, runner=runner)
    ]
    revising_numbers = {i.number for i in revising}
    both = revising + [i for i in fresh if i.number not in revising_numbers]
    return sorted(both, key=lambda i: i.number)


BLOCKED_BY = re.compile(r"^[ \t>*-]*blocked-by:(.*)$", re.IGNORECASE | re.MULTILINE)


def blockers(body: str) -> list[int]:
    """Issue numbers this one may not start before, from `blocked-by:` lines.

    An explicit marker rather than prose. Bodies mention other issues all the
    time — "see #12", "ported from #4" — and a parser loose enough to catch
    "depends on the voice-count issue (#5)" is loose enough to block on a
    citation. The marker has to be deliberate to be trustworthy.

        blocked-by: #15
        blocked-by: #9, #12

    Leading quote/bullet characters are tolerated because the line is often
    written inside a callout.
    """
    found: list[int] = []
    for match in BLOCKED_BY.finditer(body or ""):
        for number in re.findall(r"#(\d+)", match.group(1)):
            n = int(number)
            if n not in found:
                found.append(n)
    return found


# The capture is a git-branch character class rather than `\S+`, which is what
# makes `> **base:** \`narrow-tier\`` work: the emphasis and backticks fall
# outside it instead of being captured as part of the name. It also stops at
# the first token, so a trailing `and then merge` is dropped rather than
# rejecting the whole line.
BASE_BRANCH = re.compile(
    r"^[ \t>*_-]*base:[ \t*_]*`?([A-Za-z0-9._/-]+)", re.IGNORECASE | re.MULTILINE
)


def base_branch(body: str) -> str | None:
    """The branch this issue's work is cut from and merged into.

    `None` means the repo's configured default, which stays the normal case.

        base: narrow-tier

    THE POINT IS THREADING, not novelty. Measured 2026-08-09: all four merge
    conflicts in a single day were two parallel issues editing one file —
    demo's `global.css` twice, dashboard's list components, then
    `MatrixView.tsx`. Each resolution ran against a `main` that had moved
    underneath the branch, and each cost a rebase plus a fresh CI run.

    Pointing a cluster of related issues at a shared theme branch changes both
    halves. Conflicts resolve INSIDE the theme, between siblings meant to fit
    together, rather than against a trunk unrelated work is also landing on.
    And the theme merges to `main` ONCE, which is what makes the Actions
    arithmetic survivable: before the trigger cuts, a merged PR cost ~9 billed
    minutes across six jobs.

    Deliberately NOT auto-created. The branch must already exist on the
    remote; a typo would otherwise mint a branch nothing ever merges, which is
    worse than the task refusing to start. `task.py` checks and escalates.

    First `base:` wins and the rest are ignored rather than guessed at — same
    reasoning as `blocked-by:`, that a marker only sometimes obeyed is worse
    than one that is strict.
    """
    match = BASE_BRANCH.search(body or "")
    if not match:
        return None
    return match.group(1).strip().strip("`") or None


def open_blockers(repo: str, numbers: list[int], *, runner=_gh) -> list[int]:
    """Which of those are not yet merged.

    An issue closes when its PR merges, because every PR body carries
    `Closes #N` — so CLOSED is precisely "this dependency's work is on main",
    which is the thing a dependent task actually needs. A merged-but-open
    issue would be a lie either way round, and there is no such state.
    """
    still_open = []
    for number in numbers:
        try:
            out = runner(
                ["issue", "view", str(number), "--repo", repo, "--json", "state"]
            )
        except RuntimeError:
            # Unreadable is not provably satisfied, and starting a task on an
            # unmet dependency costs a whole cycle. Treat it as blocking.
            still_open.append(number)
            continue
        if (json.loads(out or "{}").get("state") or "").upper() != "CLOSED":
            still_open.append(number)
    return still_open


def conflicting_labels(present: set[str], labels: Labels = Labels()) -> set[str]:
    """Labels that contradict admission, meaning the issue must not be claimed.

    An issue carrying both `ready` and `working` is the signature of a human
    editing labels on an in-flight task — observed 2026-08-03, adding `ready`
    back to an issue the daemon was mid-implement on.

    It is harmless while the claim file exists, because `claim()` already skips
    anything already claimed. It becomes dangerous the moment that task
    finishes: `complete()` and `escalate()` both DELETE the claim file, so the
    stale `ready` survives with nothing left to suppress it and the next poll
    re-claims finished work — against a branch whose PR is already open.

    **`done` is not a contradiction for `revise`.** It is the normal state of
    the thing being revised: the task shipped, `complete()` labelled it `done`,
    and the revision is a response to that PR. Treating it as a clash would
    make the label unusable without a second manual step whose only purpose is
    to satisfy this check.

    Pure, so the rule is testable without touching the API.
    """
    if labels.revise in present:
        return present & {labels.working, labels.needs_human}
    if labels.ready not in present:
        return set()
    return present & {labels.working, labels.done, labels.needs_human}


def contradictions(
    repo: str, labels: Labels = Labels(), *, runner=_gh
) -> list[tuple[int, set[str]]]:
    """Ready-listed issues whose other labels say they are not claimable.

    For `status`: label contradictions are operator error, and the operator is
    the only one who can fix them, so they have to be visible rather than
    silently skipped.
    """
    found = []
    for issue in ready(repo, labels, runner=runner):
        clash = conflicting_labels(labels_of(repo, issue.number, runner=runner), labels)
        if clash:
            found.append((issue.number, clash))
    return found


WORKFLOW_DIR = ".github/workflows"

_WORKFLOW_REFUSAL_COMMENT = """\
**nightshift** — not claimed. This issue names `.github/workflows/`, and the
daemon's token deliberately has no `workflow` scope, so a diff touching it
cannot be pushed.

That is the design rather than a gap: an agent physically cannot edit the thing
that judges its own work, which is what makes CI a genuinely held-out verifier.
Granting the scope would quietly delete the property the whole review model
rests on.

**A human makes this change.** Nothing was spent finding out — this refusal
costs one API call, where discovering it at `git push` costs a full implement
pass, a full review and a cap slot.

If the issue only MENTIONS that path without changing it, the check is a plain
substring: reword it and re-arm.
"""


def touches_workflows(text: str) -> bool:
    """Does this issue say it changes a GitHub Actions workflow?

    Deliberately coarse — a substring, not an intent parser. The asymmetry
    justifies it: a false positive costs one human re-arm, while a false
    negative costs a full implement pass, a full review, a cap slot, and then
    fails at `git push` anyway because the token has no `workflow` scope. That
    is not a bug to fix by granting the scope: withholding it is what makes CI
    a held-out verifier the agent physically cannot edit.

    Reads the TITLE as well as the body: `ci: bump the workflow action` with the
    path only in the title would otherwise walk straight past this gate into the
    full-budget failure it exists to prevent.

    **Known limit:** issue COMMENTS are not read, and `task.run` treats them as
    authoritative — so a revise comment adding "…and update
    `.github/workflows/ci.yml`" still fails late. Reading them here would cost
    an API call per candidate on every poll, which is a real price for a case
    nobody has hit yet. Recorded rather than guessed at.
    """
    return WORKFLOW_DIR in text


def claim(
    repo: str,
    worktree_root: Path,
    labels: Labels = Labels(),
    *,
    runner=_gh,
) -> tuple[Issue, Claim] | None:
    """Take ownership of the oldest ready issue. None if the queue is empty.

    Does NOT create the worktree — that is the daemon's job. Keeping them
    separate is what makes the crash window recoverable rather than ambiguous.
    """
    # `gh issue list --label` reads a search index that lags label writes by a
    # few seconds (measured ~5s on 2026-08-02), so a just-claimed issue can
    # still appear ready. An existing claim file is the authoritative local
    # answer and does not depend on the index.
    candidates = [
        i
        for i in ready(repo, labels, runner=runner)
        if not claim_path(repo, i.number).exists()
    ]

    # The index says these are ready; `issue view` says what they actually
    # carry. One extra call for the issue we are about to take — the same
    # read-through reconcile does, for the same reason, on the path where
    # being wrong is expensive rather than merely slow.
    issue = None
    for candidate in candidates:
        present = labels_of(repo, candidate.number, runner=runner)
        # Whichever gate the listing matched on must still be there. Checking
        # only `ready` would let a revise through on a stale index after the
        # label was pulled, and vice versa.
        gate = labels.revise if candidate.revise else labels.ready
        if gate not in present:
            continue  # index lag: its labels have already moved on
        clash = conflicting_labels(present, labels)
        if clash:
            log.warning(
                "#%d is labelled %s as well as %s — not claiming it",
                candidate.number,
                ", ".join(sorted(clash)),
                labels.ready,
            )
            continue
        if touches_workflows(f"{candidate.title}\n{candidate.body}"):
            # Refuse before spending anything. The refusal used to arrive at
            # `git push` — after implement, after verify, after review — so a
            # task that was never going to land spent a full budget first
            # (swift-app #14, twice on 2026-08-08). One API call instead.
            log.info(
                "#%d names %s — escalating rather than claiming it",
                candidate.number,
                WORKFLOW_DIR,
            )
            outcomes.record(repo, candidate.number, title=candidate.title)
            escalate(repo, candidate.number, _WORKFLOW_REFUSAL_COMMENT, labels,
                     runner=runner, unclaimed=True)
            continue
        unmet = open_blockers(repo, blockers(candidate.body), runner=runner)
        if unmet:
            # Starting anyway costs a whole cycle: the worker branches off a
            # base without the dependency, discovers it, and escalates. That
            # happened three times on 2026-08-03/04 before this existed.
            log.info(
                "#%d is blocked by %s — skipping",
                candidate.number,
                ", ".join(f"#{n}" for n in unmet),
            )
            continue
        issue = candidate
        break

    if issue is None:
        return None

    record = Claim(
        repo=repo,
        number=issue.number,
        branch=issue.branch,
        worktree=str(worktree_root / f"{repo.split('/')[-1]}-{issue.number}"),
        started_at=_now(),
        revise=issue.revise,
    )
    record.write()

    try:
        # A revise also sheds `done`: the issue is in flight again, and leaving
        # it would make the next revise look like a contradiction to any reader
        # (human or `contradictions()`) that has not internalised the exception
        # in `conflicting_labels`.
        shed = [labels.revise, labels.done] if issue.revise else [labels.ready]
        _relabel(
            repo, issue.number,
            add=[labels.working], remove=shed,
            runner=runner,
        )
    except RuntimeError:
        # The swap failed, so we do not own this issue. Drop the claim file
        # rather than leaving reconcile to infer it.
        record.clear()
        raise

    outcomes.record(repo, issue.number, state="running", title=issue.title,
                    pr_url="", escalated=False, reason="claimed")
    return issue, record


def release(repo: str, number: int, labels: Labels = Labels(), *, runner=_gh) -> None:
    """Hand an issue back to the queue and forget where its work lived."""
    _relabel(repo, number, add=[labels.ready], remove=[labels.working], runner=runner)
    claim_path(repo, number).unlink(missing_ok=True)


def escalate(
    repo: str,
    number: int,
    findings: str,
    labels: Labels = Labels(),
    *,
    runner=_gh,
    unclaimed: bool = False,
) -> None:
    """The agent hit something a human must decide. Comment, relabel, move on.

    Deliberately not a failure path: escalation is a correct outcome, and the
    run that produced it cost real quota. The findings go on the issue so the
    work is not repeated.

    `unclaimed` is for the escalations that happen BEFORE a claim, where the
    issue still carries its ready label rather than `working`. Removing the
    right one matters: an issue left holding both `needs-human` and `ready` is
    a contradiction, which `claim` refuses and `status` flags on every poll —
    so the refusal would be correct and the issue would still nag forever.
    Found by running the claim-time refusal against a real issue.
    """
    outcomes.record(repo, number, state="needs_decision", reason=findings,
                    summary="", escalated=False, pr_url="")
    runner(["issue", "comment", str(number), "--repo", repo, "--body", findings])
    stale = [labels.ready, labels.revise] if unclaimed else [labels.working]
    _relabel(
        repo, number, add=[labels.needs_human], remove=stale, runner=runner
    )
    outcomes.record(repo, number, escalated=True)
    claim_path(repo, number).unlink(missing_ok=True)


def complete(
    repo: str,
    number: int,
    pr_url: str,
    labels: Labels = Labels(),
    *,
    runner=_gh,
) -> None:
    """A PR is open and awaiting human review. The agent's work is finished."""
    outcomes.record(repo, number, state="awaiting_merge", pr_url=pr_url, escalated=False)
    _relabel(repo, number, add=[labels.done], remove=[labels.working], runner=runner)
    claim_path(repo, number).unlink(missing_ok=True)


def in_flight(repo: str) -> tuple[list[Claim], list[str]]:
    """What a worker is on RIGHT NOW, plus the names of any unreadable claims.

    Read-only, and deliberately NOT `_load_claims`: that repairs as it reads,
    deleting claim files it cannot parse. `status` is a question, and a
    question must not be able to destroy the only record of where a running
    task's work lives. A bad file is reported here and repaired by
    `reconcile`, which is the command that exists to be told yes.

    The only surface that answers "what is happening now" without asking
    GitHub — and the authoritative one either way, since the claim file is
    written BEFORE the label swap.
    """
    claims: list[Claim] = []
    unreadable: list[str] = []
    if not CLAIM_DIR.exists():
        return claims, unreadable

    prefix = f"{repo.replace('/', '__')}#"
    for f in sorted(CLAIM_DIR.glob(f"{prefix}*.json")):
        try:
            claims.append(Claim(**json.loads(f.read_text())))
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            unreadable.append(f.name)
    return sorted(claims, key=lambda c: c.number), unreadable


def _load_claims(repo: str) -> tuple[dict[int, Claim], list[Reconciliation]]:
    """Read this repo's claim files. Unparseable ones are deleted, not guessed at."""
    claims: dict[int, Claim] = {}
    repairs: list[Reconciliation] = []
    if not CLAIM_DIR.exists():
        return claims, repairs

    prefix = f"{repo.replace('/', '__')}#"
    for f in sorted(CLAIM_DIR.glob(f"{prefix}*.json")):
        try:
            record = Claim(**json.loads(f.read_text()))
        except (json.JSONDecodeError, KeyError, TypeError):
            # A truncated or hand-edited claim file says nothing reliable about
            # where work lives, so it cannot be resumed from.
            f.unlink(missing_ok=True)
            repairs.append(
                Reconciliation(Repair.LITTER, 0, f"unparseable claim file {f.name}")
            )
            continue
        claims[record.number] = record
    return claims, repairs


def reconcile(
    repo: str,
    labels: Labels = Labels(),
    *,
    runner=_gh,
    worktree_exists=lambda p: Path(p).exists(),
) -> list[Reconciliation]:
    """Make labels and claim files agree. Call at startup, before claiming.

    Four states, three needing repair:

    | label     | claim file | worktree | action                              |
    |-----------|------------|----------|-------------------------------------|
    | working   | yes        | yes      | RESUMABLE — watchdog picks it up    |
    | working   | yes        | no       | RELEASED — crashed before create    |
    | working   | no         | –        | RELEASED — orphaned                 |
    | absent    | yes        | –        | LITTER — delete the file            |

    Releasing rather than resuming a half-created task is deliberate: a
    worktree that does not exist has produced nothing, so re-running from
    `agent:ready` costs one cycle and cannot double-commit.
    """
    claims, repairs = _load_claims(repo)
    working = {i.number for i in _list(repo, labels.working, runner=runner)}

    # `issue list --label` reads a search index that lags label writes by a few
    # seconds. A daemon restarting straight after a crash therefore sees its own
    # in-flight issue as NOT working, and would delete the claim file as litter —
    # destroying the pointer to the worktree and the phase needed to recover
    # from it. Deleting is irreversible, so every candidate for litter is
    # re-checked against `issue view`, which reads through the index.
    for number in sorted(set(claims) - working):
        if labels.working in labels_of(repo, number, runner=runner):
            working.add(number)

    for number in sorted(working):
        record = claims.get(number)
        if record is None:
            release(repo, number, labels, runner=runner)
            repairs.append(
                Reconciliation(Repair.RELEASED, number, "claimed but no claim file")
            )
        elif not worktree_exists(record.worktree):
            release(repo, number, labels, runner=runner)
            repairs.append(
                Reconciliation(Repair.RELEASED, number, "claim file but no worktree")
            )
        else:
            repairs.append(Reconciliation(Repair.RESUMABLE, number, record.worktree))

    for number in sorted(claims):
        if number not in working:
            claims[number].clear()
            repairs.append(
                Reconciliation(
                    Repair.LITTER, number, "claim file with no working label"
                )
            )

    return repairs

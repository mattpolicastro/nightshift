"""GitHub Issues queue via the `gh` CLI.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path

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
    spec_draft: str = "spec:draft"
    spec_review: str = "spec:review"
    spec_approved: str = "spec:approved"

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

# Scoped to the calling context so specloop's pause cannot interrupt worker
# operations running concurrently in another thread.
_GH_GUARD = ContextVar("gh_guard", default=None)


@contextmanager
def gh_guard(check):
    """Check a caller's kill switch before every subprocess, including retries.

    Reconciliation uses nested _gh calls, which inherit the same guard.
    """
    token = _GH_GUARD.set(check)
    try:
        yield
    finally:
        _GH_GUARD.reset(token)


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

    Marked spec intake comments are reconciled before retrying a create: a
    failed response does not mean GitHub failed to store the comment.
    """
    last = ""
    for attempt in range(_RETRIES):
        if attempt:
            reconciled = _reconcile_intake_comment(args, sleep=sleep)
            if reconciled is not None:
                return reconciled
        guard = _GH_GUARD.get()
        if guard is not None:
            guard()
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


def _reconcile_intake_comment(args: list[str], *, sleep) -> str | None:
    """Recover an ambiguous intake-comment create, or confirm it is absent.

    Only specloop's marked creates opt in; worker retry behavior is unchanged.
    A failed read propagates so it cannot authorize another create. Updating a
    known comment ID is safe to retry even if a PATCH response is also lost.
    """
    if (args[:2] not in (["issue", "comment"], ["pr", "comment"])
            or "--body" not in args or "--repo" not in args):
        return None
    body = args[args.index("--body") + 1]
    marker = re.search(r"<!-- nightshift:(?:(?:draft|admit|publish|revise)(?:-error)?|approval):[^\s<>]+ -->$", body)
    if marker is None:
        return None
    repo = args[args.index("--repo") + 1]
    pages = json.loads(_gh([
        "api", f"repos/{repo}/issues/{args[2]}/comments", "--paginate", "--slurp",
    ], sleep=sleep))
    previous = next((comment for page in pages for comment in page
                     if marker.group() in (comment.get("body") or "")), None)
    if previous is None:
        return None
    return _gh(["api", f"repos/{repo}/issues/comments/{previous['id']}",
                "--method", "PATCH", "-f", f"body={body}"], sleep=sleep)


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


#: Who may steer an agent through GitHub text. On a public repository anyone can
#: comment, and an issue's author can edit it after it is labelled; only these
#: associations are people the owner has given access. Anything else is dropped
#: before it reaches a prompt, and a missing association counts as untrusted.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def trusted(item: dict, key: str = "authorAssociation") -> bool:
    """`authorAssociation` from gh's JSON; REST payloads spell it `author_association`."""
    return (item.get(key) or "").upper() in TRUSTED_ASSOCIATIONS


#: Every comment the daemon writes starts with this (`daemon.py:137`, `:406`).
#: It authenticates AS Matt, so the author field cannot tell its own escalation
#: write-ups apart from a human's instructions — the prefix is the only
#: discriminator there is, and `has_instructions` depends on it.
SELF_COMMENT_PREFIX = "**nightshift** —"


@dataclass(frozen=True)
class FeedbackItem:
    id: str
    block: str


def _feedback_id(source: str, record: dict) -> str:
    value = record.get("id")
    if type(value) is not int or value <= 0:
        raise ValueError(f"invalid {source} feedback ID: {value!r}")
    return f"{source}:{value}"


def _pr_feedback_records(repo: str, number: int, *, runner=_gh) -> list[tuple[str, dict]]:
    """Read complete trusted REST records, including non-actionable bodies.

    Legacy migration can use these records with `_feedback_id`, validating
    `created_at` (conversation/inline) or `submitted_at` (review) itself.
    Ordinary selection does not need timestamps or IDs on excluded bodies.
    """
    records = []
    seen: dict[str, dict] = {}
    for source, endpoint in (
        ("conversation", f"issues/{number}/comments"),
        ("review", f"pulls/{number}/reviews"),
        ("inline", f"pulls/{number}/comments"),
    ):
        pages = json.loads(runner(["api", f"repos/{repo}/{endpoint}", "--paginate", "--slurp"]))
        if not isinstance(pages, list):
            raise ValueError(f"invalid {source} feedback pages")
        for page in pages:
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise ValueError(f"invalid {source} feedback page")
            for record in page:
                if not trusted(record, "author_association"):
                    continue
                value = record.get("id")
                if type(value) is int and value > 0:
                    identity = _feedback_id(source, record)
                    if identity in seen:
                        if seen[identity] != record:
                            raise ValueError(f"conflicting feedback records for {identity}")
                        continue
                    seen[identity] = record
                records.append((source, record))
    return records


def pr_feedback_items(repo: str, number: int, *, runner=_gh) -> list[FeedbackItem]:
    """Strict, paginated feedback with stable source-qualified REST IDs.

    Read failures propagate; callers must save only a complete returned list.
    """
    items = []
    for source, record in _pr_feedback_records(repo, number, runner=runner):
        suffix = ""
        if source == "review":
            state = (record.get("state") or "").replace("_", " ").lower()
            suffix = f" ({state})" if state and state != "commented" else ""
        elif source == "inline":
            where = record.get("path") or ""
            line = record.get("line") or record.get("original_line")
            if where and line:
                where = f"{where}:{line}"
            suffix = f" on `{where}`" if where else ""
        block = _block(record.get("user"), record.get("body"), suffix=suffix)
        if block and has_instructions([block]):
            items.append(FeedbackItem(_feedback_id(source, record), block))
    return items


def _block(author: dict | None, body: str | None, suffix: str = "") -> str:
    name = (author or {}).get("login") or "unknown"
    text = (body or "").strip()
    return f"**{name}{suffix}:**\n\n{text}" if text else ""


def _latest_pr_number(repo: str, branch: str, *, runner=_gh, strict: bool = False) -> int | None:
    """The highest-numbered PR for a branch, open or closed.

    Highest number rather than `--limit 1`, because that trusts an ordering
    `gh` does not promise, and picking the WRONG one here means revising
    against a previous round's feedback. Specloop uses strict=True so a failed
    read cannot authorize creating a duplicate PR; worker callers keep their
    historical best-effort behavior.
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
        if strict:
            raise
        return None
    found = [p.get("number") for p in json.loads(out if strict else out or "[]") if p.get("number")]
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
    sample-ui's `global.css` twice, sample-midi's list components, then
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

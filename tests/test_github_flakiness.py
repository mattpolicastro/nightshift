"""A transient GitHub error must not be a daemon exit.

BACKLOG §1, and the largest known defect in the system: `startup()` called
`queue.reconcile` per repo with no guard, and `_gh` raised on any non-zero
`gh` exit, so ONE failed API call ended the process before the loop had
claimed anything. The log holds 107 tracebacks across seven dates — 08-09,
08-15, 08-17, 08-25, 08-26, 08-31, 09-03 — of which 105 are `gh issue list`.
launchd's KeepAlive restarted it every couple of minutes for three quarters of
an hour on 08-15, which is what made a total outage look like a live daemon.

The fix has two halves and these tests keep them apart: retry inside `_gh`
buys the twenty-second outage, and skipping the repo for this pass survives
the forty-five-minute one. Neither substitutes for the other.

The third part is the one the backlog said had no fix: telling "GitHub is
flaky, retry" from "the token can no longer read this repo, stop". Retrying a
404 forever is a WORSE failure than exiting, because it looks fine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nightshift import daemon, notify, queue
from nightshift.config import Config, Repo

SANDBOX = "matt/sandbox"
OTHER = "matt/other"

# Verbatim from the daemon log, by frequency: 31 TLS timeouts, 31 5xx,
# 21 connection errors, 19 EOFs, 2 i/o timeouts, 1 502.
TLS = "Get https://api.github.com: net/http: TLS handshake timeout"
FIVE_XX = "HTTP 503: No server is currently available to service your request"
EOF_ERR = "unexpected EOF"
NOT_FOUND = "HTTP 404: Not Found (https://api.github.com/repos/matt/gone)"
FORBIDDEN = "HTTP 403: Resource not accessible by personal access token"

# Both captured from real `gh` on 2026-09-05 rather than written from memory.
# The first is what a network failure ACTUALLY looks like — an earlier version
# of the marker list matched none of it, so the commonest real-world outage was
# the one not being retried. The second is what an out-of-reach repo says, and
# it is a GraphQL message rather than the `HTTP 404` the log led us to expect:
# it classifies as permanent only because unknown defaults to permanent.
REAL_NETWORK = (
    'Post "https://127.0.0.1:1/api/graphql": dial tcp 127.0.0.1:1: '
    "connect: connection refused"
)
REAL_UNREACHABLE_REPO = (
    "GraphQL: Could not resolve to a Repository with the name "
    "'example-owner/dotfiles'. (repository)"
)


class FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def runs(*results, monkeypatch) -> list[list[str]]:
    """Queue up `gh` outcomes; return the arg lists actually attempted."""
    attempts: list[list[str]] = []
    pending = list(results)

    def fake(args, **kw):
        attempts.append(args)
        return pending.pop(0)

    monkeypatch.setattr(queue.subprocess, "run", fake)
    return attempts


# --- half one: retry a recognised outage ------------------------------------


@pytest.mark.parametrize("stderr", [TLS, FIVE_XX, EOF_ERR, REAL_NETWORK])
def test_a_transient_outage_is_retried_and_can_succeed(stderr, monkeypatch):
    attempts = runs(
        FakeProc(1, stderr=stderr), FakeProc(0, stdout="[]"), monkeypatch=monkeypatch
    )
    assert queue._gh(["issue", "list"], sleep=lambda _s: None) == "[]"
    assert len(attempts) == 2


def test_it_gives_up_after_a_few_attempts_rather_than_hanging_forever(monkeypatch):
    attempts = runs(*[FakeProc(1, stderr=TLS)] * 5, monkeypatch=monkeypatch)
    with pytest.raises(queue.GhError) as caught:
        queue._gh(["issue", "list"], sleep=lambda _s: None)
    assert caught.value.transient is True
    assert len(attempts) == queue._RETRIES


def test_it_backs_off_between_attempts(monkeypatch):
    """Hammering an unavailable API is not a retry strategy."""
    runs(*[FakeProc(1, stderr=FIVE_XX)] * 3, monkeypatch=monkeypatch)
    waits: list[float] = []
    with pytest.raises(queue.GhError):
        queue._gh(["issue", "list"], sleep=waits.append)
    assert waits == [2, 8]


# --- the classifier: what must NOT be retried -------------------------------


@pytest.mark.parametrize(
    "stderr", [NOT_FOUND, FORBIDDEN, REAL_UNREACHABLE_REPO]
)
def test_a_permanent_error_fails_on_the_first_attempt(stderr, monkeypatch):
    """A token that can no longer read a repo is somebody's problem NOW.

    Retrying it until the heat death of the universe is worse than the crash it
    replaced, because a daemon quietly retrying a 404 looks like a healthy one.
    """
    attempts = runs(FakeProc(1, stderr=stderr), monkeypatch=monkeypatch)
    with pytest.raises(queue.GhError) as caught:
        queue._gh(["issue", "list"], sleep=lambda _s: None)
    assert caught.value.transient is False
    assert len(attempts) == 1


def test_an_unrecognised_error_is_treated_as_permanent(monkeypatch):
    """Only a RECOGNISED outage is retried.

    This cannot be worse than the behaviour it replaced, where every error was
    fatal — and it is the safe direction: a new failure mode surfaces on the
    first pass instead of being retried into a silence that looks like work.
    """
    attempts = runs(
        FakeProc(1, stderr="gh: something nobody has seen before"),
        monkeypatch=monkeypatch,
    )
    with pytest.raises(queue.GhError) as caught:
        queue._gh(["issue", "list"], sleep=lambda _s: None)
    assert caught.value.transient is False
    assert len(attempts) == 1


# --- half two: one repo's outage is not the daemon's ------------------------


def cfg(**kw) -> Config:
    kw.setdefault(
        "repos", [Repo(name=SANDBOX, verify="true"), Repo(name=OTHER, verify="true")]
    )
    return Config(**kw)


def test_startup_skips_the_failing_repo_and_still_reconciles_the_next(monkeypatch):
    """The 105-traceback path. This call was unguarded until 2026-09-05."""
    seen: list[str] = []

    def reconcile(repo, labels):
        seen.append(repo)
        if repo == SANDBOX:
            raise queue.GhError("gh issue list failed: " + TLS, transient=True)
        return []

    monkeypatch.setattr(daemon.queue, "reconcile", reconcile)
    repairs = daemon.startup(cfg(), {SANDBOX: Path("/tmp/a"), OTHER: Path("/tmp/b")})

    assert seen == [SANDBOX, OTHER]  # the second repo was still reached
    assert repairs == []


def test_claim_next_skips_a_failing_repo_and_claims_from_the_next(monkeypatch):
    """Same fairness mechanism as the per-repo concurrency cap: skip, do not stop."""
    claimed_from: list[str] = []

    def claim(repo, root, labels):
        if repo == SANDBOX:
            raise queue.GhError("gh issue list failed: " + FIVE_XX, transient=True)
        claimed_from.append(repo)
        return None

    monkeypatch.setattr(daemon.queue, "claim", claim)
    result = daemon.claim_next(
        cfg(), {SANDBOX: Path("/tmp/a"), OTHER: Path("/tmp/b")}
    )

    assert result is None
    assert claimed_from == [OTHER]


# --- part three: skipped forever, in silence, is worse ----------------------


def test_a_brief_outage_is_not_worth_waking_anyone(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda hook, text: sent.append(text))
    health = daemon.RepoHealth()

    health.record(SANDBOX, queue.GhError(TLS, transient=True), "hook")
    health.record(SANDBOX, queue.GhError(TLS, transient=True), "hook")

    assert sent == []


def test_an_outage_that_outlives_a_few_polls_reaches_a_human_once(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda hook, text: sent.append(text))
    health = daemon.RepoHealth()

    for _ in range(10):
        health.record(SANDBOX, queue.GhError(TLS, transient=True), "hook")

    assert len(sent) == 1, "a long outage is one message, not one per poll"
    assert SANDBOX in sent[0]
    assert "Other repos keep running" in sent[0]


def test_a_permanent_error_is_announced_on_the_first_pass(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda hook, text: sent.append(text))
    health = daemon.RepoHealth()

    health.record(SANDBOX, queue.GhError(FORBIDDEN, transient=False), "hook")

    assert len(sent) == 1
    assert "will not clear on its own" in sent[0]


def test_recovery_re_arms_so_the_next_outage_is_news_again(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda hook, text: sent.append(text))
    health = daemon.RepoHealth()

    for _ in range(5):
        health.record(SANDBOX, queue.GhError(TLS, transient=True), "hook")
    health.recovered(SANDBOX)
    for _ in range(5):
        health.record(SANDBOX, queue.GhError(TLS, transient=True), "hook")

    assert len(sent) == 2
    assert health.failures[SANDBOX] == 5


def test_a_repo_that_never_failed_is_untouched_by_recovery():
    """`recovered` runs on every successful pass, so it must be free."""
    health = daemon.RepoHealth()
    health.recovered(SANDBOX)
    assert health.failures == {} and health.announced == set()

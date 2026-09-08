"""More than one task at a time — and the four things that breaks.

`concurrency` sat in `config.toml` and in `Config` since the beginning and was
read by nothing: `daemon.loop` called a blocking `run_one` and then
`break`-ed with the comment "concurrency 1: one task at a time". Setting the
knob to 3 changed nothing at all, which is worse than it not existing, because
the config file documented a behaviour the code did not have.

Making it real is not just "run N of them". Four invariants have to be restated
for N > 1, and each one has a test here:

  1. **Claiming stays serial.** `queue.claim` is check-then-act against the
     GitHub API and `queue.py` says outright that it is undefended. Two threads
     inside it hand the same issue to two workers.
  2. **The cap counts in-flight work.** It is checked before dispatch, so N
     slots would each see room and overshoot by up to N-1.
  3. **"Queue drained" waits for the work.** It was edge-triggered on "no repo
     had anything ready", which becomes a lie the moment dispatch and
     completion stop being the same instant.
  4. **Repo priority survives.** `config.toml` documents that sample drains
     before swift-app *because* concurrency was 1. That ordering has to be
     rebuilt deliberately rather than inherited.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from nightshift import daemon
from nightshift.config import Config, Repo

SAMPLE = "matt/sample"
OTHER = "matt/other"


class Stop(Exception):
    """Breaks out of a loop whose only real exit is the nightly cap."""


def cfg(**kw) -> Config:
    return Config(repos=[Repo(name=SAMPLE, verify="true")], **kw)


def harness(monkeypatch, *, work: int, run, stop_after_sleeps: int = 3):
    """Wire the loop up to fakes. `run` is the body of a worker thread."""
    sent: list[str] = []
    state = {"work": work, "sleeps": 0}

    monkeypatch.setattr(daemon, "startup", lambda *a, **k: None)
    monkeypatch.setattr(daemon, "paused", lambda: False)
    monkeypatch.setattr(daemon.notify, "send", lambda hook, text: sent.append(text))

    def claim_next(cfg_, repo_dirs, running=None, **kw):
        if not state["work"]:
            return None
        state["work"] -= 1
        return daemon.Claimed(
            repo=cfg_.repos[0], repo_dir=Path("/tmp/x"), issue=None, claim=None
        )

    def sleep(_seconds):
        state["sleeps"] += 1
        if state["sleeps"] >= stop_after_sleeps:
            raise Stop

    monkeypatch.setattr(daemon, "claim_next", claim_next)
    monkeypatch.setattr(daemon, "run_claimed", run)
    monkeypatch.setattr(daemon.time, "sleep", sleep)
    return sent


def test_three_tasks_actually_run_at_the_same_time(monkeypatch):
    """The knob is live.

    A `Barrier(3)` is the assertion: it only releases when three threads are
    inside `run_claimed` simultaneously. Under the old loop — or any future
    change that re-serialises dispatch — the first worker waits for two that
    cannot arrive and the barrier breaks on its timeout.
    """
    barrier = threading.Barrier(3, timeout=10)

    def run(cfg_, claimed, tally):
        barrier.wait()
        tally.shipped += 1

    harness(monkeypatch, work=3, run=run)

    with pytest.raises(Stop):
        daemon.loop(cfg(concurrency=3), {SAMPLE: Path("/tmp/x")})

    assert not barrier.broken


def test_a_worker_never_claims(monkeypatch):
    """Claiming is the loop thread's job and nobody else's.

    Not a style point. `queue.claim` reads the ready label and then writes the
    working label; two threads interleaving there both come away believing they
    own the same issue, and the second worktree lands on a branch that already
    exists.
    """
    claim_threads: list[str] = []
    release = threading.Event()

    def claim_next(cfg_, repo_dirs, running=None, **kw):
        claim_threads.append(threading.current_thread().name)
        if len(claim_threads) > 3:
            return None
        return daemon.Claimed(
            repo=cfg_.repos[0], repo_dir=Path("/tmp/x"), issue=None, claim=None
        )

    def run(cfg_, claimed, tally):
        release.wait(timeout=10)
        tally.shipped += 1

    harness(monkeypatch, work=99, run=run)
    monkeypatch.setattr(daemon, "claim_next", claim_next)

    def sleep(_seconds):
        release.set()  # let the workers finish, then end the run
        raise Stop

    monkeypatch.setattr(daemon.time, "sleep", sleep)

    with pytest.raises(Stop):
        daemon.loop(cfg(concurrency=3), {SAMPLE: Path("/tmp/x")})

    assert claim_threads, "claim_next was never called"
    pool_claims = [n for n in claim_threads if n.startswith("nightshift")]
    assert pool_claims == [], f"claimed from a worker thread: {pool_claims}"


def test_the_cap_counts_work_that_is_still_running(monkeypatch):
    """Three slots and a cap of two must dispatch two, not three.

    The cap is checked before dispatch and satisfied from `tally`, which a
    running task has not touched yet. Without counting in-flight work every
    free slot reads the same stale total and the cap overshoots by up to N-1 —
    on a subscription window, that is real money.
    """
    started = threading.Semaphore(0)
    runs = {"n": 0}
    lock = threading.Lock()

    def run(cfg_, claimed, tally):
        with lock:
            runs["n"] += 1
        tally.shipped += 1
        started.release()

    sent = harness(monkeypatch, work=10, run=run, stop_after_sleeps=2)

    with pytest.raises(Stop):
        daemon.loop(
            cfg(concurrency=3, max_tasks_per_night=2), {SAMPLE: Path("/tmp/x")}
        )

    assert runs["n"] == 2, f"cap of 2 dispatched {runs['n']}"
    assert [t for t in sent if "hit the cap of 2" in t], sent


def test_drained_waits_for_in_flight_work_and_counts_it(monkeypatch):
    """The wrap-up must not fire while tasks are still running.

    "Drained" used to mean "no repo had anything ready", which was the same
    instant as "nothing is running" only because dispatch blocked. Now it is
    reachable with three tasks still going, and the message reports `tally` —
    so firing early both lies about being finished and undercounts.
    """
    gate = threading.Event()

    def run(cfg_, claimed, tally):
        gate.wait(timeout=10)
        tally.shipped += 1

    sent = harness(monkeypatch, work=3, run=run)

    # Release the workers only once the loop has stopped claiming, so the
    # window where a premature drain could fire is as wide as possible.
    threading.Timer(0.1, gate.set).start()

    with pytest.raises(Stop):
        daemon.loop(cfg(concurrency=3), {SAMPLE: Path("/tmp/x")})

    drained = [t for t in sent if "queue drained" in t]
    assert len(drained) == 1, sent
    assert "3 shipped" in drained[0], drained


def test_config_order_is_priority_order_when_nothing_is_running(monkeypatch):
    """With no in-flight work, the walk is top-down: config order is priority.

    This is what config order still means after per-repo caps — it decides who
    gets the FIRST free slot, not who gets all of them. The starvation half is
    `test_a_repo_at_its_cap_is_skipped_not_the_end_of_the_walk` below.
    """
    ready = {SAMPLE: 2, OTHER: 5}
    claimed_from: list[str] = []

    def fake_claim(repo_name, worktree_root, labels):
        if not ready[repo_name]:
            return None
        ready[repo_name] -= 1
        claimed_from.append(repo_name)
        return (
            daemon.queue.Issue(repo=repo_name, number=1, title="t", body=""),
            daemon.queue.Claim(
                repo=repo_name, number=1, branch="b",
                worktree="/tmp/w", started_at="now",
            ),
        )

    monkeypatch.setattr(daemon.queue, "claim", fake_claim)

    two_repos = Config(
        repos=[Repo(name=SAMPLE, verify="true"), Repo(name=OTHER, verify="true")]
    )
    dirs = {SAMPLE: Path("/tmp/a"), OTHER: Path("/tmp/b")}

    for _ in range(4):
        assert daemon.claim_next(two_repos, dirs) is not None

    assert claimed_from == [SAMPLE, SAMPLE, OTHER, OTHER], claimed_from


def test_claim_next_is_none_when_every_repo_is_dry(monkeypatch):
    """An empty queue is not an error, and must not be a busy loop."""
    monkeypatch.setattr(daemon.queue, "claim", lambda *a, **k: None)
    assert daemon.claim_next(cfg(), {SAMPLE: Path("/tmp/x")}) is None


def test_a_repo_at_its_cap_is_skipped_not_the_end_of_the_walk(monkeypatch):
    """The fairness mechanism, in one assertion.

    Before per-repo caps, `claim_next` re-walked from the top on every free
    slot, so a repo with a deep queue took them all and everything below it
    starved — swift-app sat at "0 working, 2 ready" for a full day behind
    sample on 2026-08-07, with the swift grant it had been waiting for already
    live.

    A repo at its cap must be SKIPPED rather than ending the walk. Skipping and
    stopping look identical when the first repo is dry, which is why this asserts
    against a first repo with work available.
    """
    claimed_from: list[str] = []

    def fake_claim(repo_name, worktree_root, labels):
        claimed_from.append(repo_name)
        return (
            daemon.queue.Issue(repo=repo_name, number=1, title="t", body=""),
            daemon.queue.Claim(
                repo=repo_name, number=1, branch="b",
                worktree="/tmp/w", started_at="now",
            ),
        )

    monkeypatch.setattr(daemon.queue, "claim", fake_claim)
    two = Config(
        repos=[Repo(name=SAMPLE, verify="true"), Repo(name=OTHER, verify="true")]
    )
    dirs = {SAMPLE: Path("/tmp/a"), OTHER: Path("/tmp/b")}

    # sample is busy and has plenty more ready; the next claim must be OTHER.
    got = daemon.claim_next(two, dirs, {SAMPLE: 1})
    assert got is not None, "walk ended at the capped repo instead of skipping it"
    assert got.repo.name == OTHER
    assert SAMPLE not in claimed_from, "claimed from a repo already at its cap"


def test_every_repo_at_its_cap_claims_nothing(monkeypatch):
    """Full is full — and it must not spin claiming."""
    calls: list[str] = []

    def fake_claim(repo_name, worktree_root, labels):
        calls.append(repo_name)
        return None

    monkeypatch.setattr(daemon.queue, "claim", fake_claim)
    two = Config(
        repos=[Repo(name=SAMPLE, verify="true"), Repo(name=OTHER, verify="true")]
    )
    dirs = {SAMPLE: Path("/tmp/a"), OTHER: Path("/tmp/b")}

    assert daemon.claim_next(two, dirs, {SAMPLE: 1, OTHER: 1}) is None
    assert calls == [], "hit the GitHub API for repos that were already full"


def test_a_repo_may_be_given_more_than_one_slot(monkeypatch):
    """Per-repo caps are a knob, not a hardcoded 1."""
    monkeypatch.setattr(
        daemon.queue,
        "claim",
        lambda repo_name, wt, labels: (
            daemon.queue.Issue(repo=repo_name, number=1, title="t", body=""),
            daemon.queue.Claim(
                repo=repo_name, number=1, branch="b",
                worktree="/tmp/w", started_at="now",
            ),
        ),
    )
    cfg_ = Config(repos=[Repo(name=SAMPLE, verify="true", concurrency=2)])
    dirs = {SAMPLE: Path("/tmp/a")}

    assert daemon.claim_next(cfg_, dirs, {SAMPLE: 1}).repo.name == SAMPLE
    assert daemon.claim_next(cfg_, dirs, {SAMPLE: 2}) is None


def test_two_repos_run_at_the_same_time(monkeypatch):
    """End to end: one slot each, both in flight together.

    The `Barrier(2)` only releases when a worker from EACH repo is running
    simultaneously — the thing a global pool in config order could never do.
    """
    barrier = threading.Barrier(2, timeout=10)
    seen: list[str] = []
    lock = threading.Lock()

    def fake_claim(repo_name, worktree_root, labels):
        return (
            daemon.queue.Issue(repo=repo_name, number=1, title="t", body=""),
            daemon.queue.Claim(
                repo=repo_name, number=1, branch="b",
                worktree="/tmp/w", started_at="now",
            ),
        )

    def run(cfg_, claimed, tally):
        with lock:
            seen.append(claimed.repo.name)
        barrier.wait()
        tally.shipped += 1

    monkeypatch.setattr(daemon, "startup", lambda *a, **k: None)
    monkeypatch.setattr(daemon, "paused", lambda: False)
    monkeypatch.setattr(daemon.notify, "send", lambda hook, text: None)
    monkeypatch.setattr(daemon.queue, "claim", fake_claim)
    monkeypatch.setattr(daemon, "run_claimed", run)

    def sleep(_seconds):
        raise Stop

    monkeypatch.setattr(daemon.time, "sleep", sleep)

    two = Config(
        repos=[Repo(name=SAMPLE, verify="true"), Repo(name=OTHER, verify="true")],
        concurrency=2,
    )
    with pytest.raises(Stop):
        daemon.loop(two, {SAMPLE: Path("/tmp/a"), OTHER: Path("/tmp/b")})

    assert not barrier.broken, "the two repos never ran concurrently"
    assert set(seen) == {SAMPLE, OTHER}, seen

"""The poll loop's exits and its wrap-up.

The cap is the only way out of `loop`. That is deliberate — a launchd agent
that exited on an empty queue would stay down, because `KeepAlive` only
restarts an unsuccessful exit — but it used to mean the wrap-up notification
was unreachable in practice: it fired after the loop ended, and a run that
shipped four tasks out of a queue of four never ended. Four "shipped" messages
and then silence, which is the state the wrap-up exists to distinguish from a
daemon that never woke up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nightshift import daemon
from nightshift.config import Config, Repo
from nightshift.trace import RateLimit

SANDBOX = "matt/sandbox"


class Stop(Exception):
    """Breaks out of a loop whose only real exit is the nightly cap."""


def harness(
    monkeypatch,
    *,
    work: int,
    stop_after_sleeps: int = 3,
    quota_resets_at: int | None = None,
):
    sent: list[str] = []
    state = {"work": work, "sleeps": 0}

    monkeypatch.setattr(daemon, "startup", lambda *a, **k: None)
    monkeypatch.setattr(daemon, "paused", lambda: False)
    monkeypatch.setattr(daemon.notify, "send", lambda hook, text: sent.append(text))

    # Claiming and running are separate seams now: `claim_next` is serial on the
    # loop thread, `run_claimed` goes to the pool. The fakes split the old
    # `run_one` along the same line — "is there work" here, "do it" below.
    def claim_next(cfg, repo_dirs, running=None, **kw):
        if not state["work"]:
            return None
        state["work"] -= 1
        return daemon.Claimed(
            repo=cfg.repos[0], repo_dir=Path("/tmp/x"), issue=None, claim=None
        )

    def run_claimed(cfg, claimed, tally):
        tally.shipped += 1
        if quota_resets_at is not None:
            # What `_record_quota` does on a real run: the window the loop
            # caps against comes off the run's own telemetry.
            tally.quota = RateLimit(
                status="allowed",
                type="five_hour",
                resets_at=quota_resets_at,
                using_overage=False,
            )

    def sleep(_seconds):
        state["sleeps"] += 1
        if state["sleeps"] >= stop_after_sleeps:
            raise Stop

    monkeypatch.setattr(daemon, "claim_next", claim_next)
    monkeypatch.setattr(daemon, "run_claimed", run_claimed)
    monkeypatch.setattr(daemon.time, "sleep", sleep)
    return sent


def cfg(**kw) -> Config:
    return Config(repos=[Repo(name=SANDBOX, verify="true")], **kw)


def test_the_wrap_up_fires_when_the_queue_goes_quiet(monkeypatch):
    sent = harness(monkeypatch, work=2)

    with pytest.raises(Stop):
        daemon.loop(cfg(), {SANDBOX: Path("/tmp/x")})

    drained = [t for t in sent if "queue drained" in t]
    assert len(drained) == 1, sent
    assert "2 shipped" in drained[0]


def test_it_says_so_once_not_every_poll(monkeypatch):
    """Edge-triggered. An idle weekend must not be a message every 120s."""
    sent = harness(monkeypatch, work=1, stop_after_sleeps=8)

    with pytest.raises(Stop):
        daemon.loop(cfg(), {SANDBOX: Path("/tmp/x")})

    assert len([t for t in sent if "queue drained" in t]) == 1, sent


def test_an_idle_daemon_that_never_worked_stays_silent(monkeypatch):
    """Nothing shipped means nothing to report — the queue was empty all along."""
    sent = harness(monkeypatch, work=0)

    with pytest.raises(Stop):
        daemon.loop(cfg(), {SANDBOX: Path("/tmp/x")})

    assert sent == []


def test_the_cap_holds_rather_than_exits(monkeypatch):
    """The cap is a hold. Exiting would be permanent under KeepAlive."""
    sent = harness(monkeypatch, work=4)

    with pytest.raises(Stop):
        daemon.loop(cfg(max_tasks_per_night=2), {SANDBOX: Path("/tmp/x")})

    capped = [t for t in sent if "hit the cap of 2" in t]
    assert len(capped) == 1, sent
    assert "resuming" in capped[0]


def test_the_window_rolling_over_resets_the_cap(monkeypatch):
    """The window is the runs' own `resets_at`, not a wall clock.

    Two tasks fill a cap of two, the loop holds, the subscription window then
    rolls over and the remaining two run. Before this, the counter was per
    process lifetime: the daemon retired after N tasks however long that took.
    """
    reset_at = 1_000_000
    clock = {"now": reset_at - 500}
    sent = harness(
        monkeypatch, work=4, stop_after_sleeps=99, quota_resets_at=reset_at
    )
    monkeypatch.setattr(daemon.time, "time", lambda: clock["now"])

    real_sleep = daemon.time.sleep

    def sleep(seconds):
        clock["now"] += 600  # each hold advances past the reset
        real_sleep(seconds)

    monkeypatch.setattr(daemon.time, "sleep", sleep)

    with pytest.raises(Stop):
        daemon.loop(cfg(max_tasks_per_night=2), {SANDBOX: Path("/tmp/x")})

    assert [t for t in sent if "hit the cap of 2" in t], sent
    assert [t for t in sent if "queue drained" in t], sent

"""`status` must show the task that is actually running.

For most of Phase 1 it did not. It asked GitHub for `agent:ready` issues and
printed ready/blocked counts — but a claimed issue carries `agent:working`, so
`ready` cannot see it, and the one task consuming a serial daemon was the one
thing the status command could not tell you. On 2026-08-06 it reported
"3 ready, 1 blocked" while a worker had been on #26 for eleven minutes.

The claim file already held the answer (number, branch, phase, started_at,
revise) and nothing read it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nightshift import cli, queue
from nightshift.queue import Claim

REPO = "o/r"
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def claim_dir(tmp_path, monkeypatch):
    d = tmp_path / "claims"
    monkeypatch.setattr(queue, "CLAIM_DIR", d)
    return d


def make_claim(number: int, **kw) -> Claim:
    record = Claim(
        repo=REPO,
        number=number,
        branch=f"claude/{number}",
        worktree=f"/wt/{number}",
        started_at=(NOW - timedelta(minutes=11)).isoformat(),
        **kw,
    )
    record.write()
    return record


def test_in_flight_finds_the_running_task():
    make_claim(26)
    running, unreadable = queue.in_flight(REPO)
    assert [c.number for c in running] == [26]
    assert running[0].branch == "claude/26"
    assert unreadable == []


def test_in_flight_is_empty_before_anything_has_ever_run(claim_dir):
    """No claims directory at all is the first-boot state, not an error."""
    assert not claim_dir.exists()
    assert queue.in_flight(REPO) == ([], [])


def test_in_flight_ignores_another_repo():
    make_claim(26)
    Claim(
        repo="other/repo",
        number=99,
        branch="claude/99",
        worktree="/wt/99",
        started_at=NOW.isoformat(),
    ).write()
    running, _ = queue.in_flight(REPO)
    assert [c.number for c in running] == [26]


def test_a_bad_claim_file_is_reported_and_NOT_deleted(claim_dir):
    """The whole reason this is not `_load_claims`.

    `_load_claims` repairs as it reads: an unparseable claim file is deleted.
    That is right for `reconcile`, which was asked to fix things, and wrong for
    `status`, which was asked a question — a claim file names the worktree
    holding a running task's only copy of its work, and asking where something
    is must never be able to destroy the answer.
    """
    make_claim(26)
    bad = claim_dir / f"{REPO.replace('/', '__')}#31.json"
    bad.write_text("{not json")

    running, unreadable = queue.in_flight(REPO)

    assert [c.number for c in running] == [26]
    assert unreadable == [bad.name]
    assert bad.exists(), "status must not repair"


def test_elapsed_reads_as_duration_not_a_timestamp():
    assert cli._elapsed((NOW - timedelta(minutes=11)).isoformat(), NOW) == "11m"
    assert cli._elapsed((NOW - timedelta(minutes=59)).isoformat(), NOW) == "59m"
    assert cli._elapsed((NOW - timedelta(minutes=64)).isoformat(), NOW) == "1h 04m"


def test_elapsed_survives_a_clock_it_cannot_read():
    """An unreadable clock must not cost the line saying WHICH issue is running."""
    assert cli._elapsed("not a date", NOW) == "?"
    assert cli._elapsed("", NOW) == "?"
    # Clock skew between the writing daemon and the reading terminal.
    assert cli._elapsed((NOW + timedelta(minutes=5)).isoformat(), NOW) == "?"


def test_a_naive_timestamp_is_read_as_utc():
    """Claims are written with a tz, but a hand-edited one may not be."""
    assert cli._elapsed("2026-08-06T11:49:00", NOW) == "11m"


def test_title_of_degrades_to_empty_when_github_is_unreachable():
    def offline(_args):
        raise RuntimeError("gh: connection refused")

    assert queue.title_of(REPO, 26, runner=offline) == ""


def test_title_of_survives_junk_on_stdout():
    assert queue.title_of(REPO, 26, runner=lambda _a: "not json") == ""
    assert queue.title_of(REPO, 26, runner=lambda _a: "") == ""

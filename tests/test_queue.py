from __future__ import annotations

import json

import pytest

from nightshift import queue
from nightshift.queue import Claim, Labels

REPO = "matt/sandbox"
LABELS = Labels()


class FakeGh:
    """Records `gh` invocations and answers `issue list` from a fixed state."""

    def __init__(self, by_label: dict[str, list[int]] | None = None):
        self.by_label = by_label or {}
        self.calls: list[list[str]] = []
        self.fail_on: str | None = None

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        joined = " ".join(args)
        if self.fail_on and self.fail_on in joined:
            raise RuntimeError("gh exploded")
        if args[:2] == ["issue", "list"]:
            label = args[args.index("--label") + 1]
            return json.dumps(
                [
                    {"number": n, "title": f"issue {n}", "body": "body"}
                    for n in self.by_label.get(label, [])
                ]
            )
        if args[:2] == ["issue", "view"]:
            number = int(args[2])
            names = [lb for lb, ns in self.by_label.items() if number in ns]
            return json.dumps({"labels": [{"name": n} for n in names]})
        return ""


@pytest.fixture(autouse=True)
def claim_dir(tmp_path, monkeypatch):
    d = tmp_path / "claims"
    monkeypatch.setattr(queue, "CLAIM_DIR", d)
    return d


def make_claim(number: int, worktree: str = "/wt/x") -> Claim:
    record = Claim(
        repo=REPO,
        number=number,
        branch=f"claude/{number}",
        worktree=worktree,
        started_at="2026-08-02T00:00:00+00:00",
    )
    record.write()
    return record


# --- contradictory labels ------------------------------------------------
#
# Observed 2026-08-03: `agent:ready` was added by hand to an issue the daemon
# was mid-implement on, leaving it carrying both labels at once.


def test_conflicting_labels_ignores_an_issue_that_is_not_ready():
    """`working` alone is the normal in-flight state, not a contradiction."""
    assert queue.conflicting_labels({LABELS.working}, LABELS) == set()


def test_conflicting_labels_names_every_clash():
    present = {LABELS.ready, LABELS.working, LABELS.needs_human}
    assert queue.conflicting_labels(present, LABELS) == {
        LABELS.working,
        LABELS.needs_human,
    }


def test_contradictions_reports_the_issue_and_its_clashing_labels():
    gh = FakeGh({LABELS.ready: [3, 8], LABELS.working: [3]})
    assert queue.contradictions(REPO, LABELS, runner=gh) == [(3, {LABELS.working})]


def test_claim_write_leaves_no_temp_file(claim_dir):
    make_claim(3)
    assert list(claim_dir.glob("*.tmp")) == []


# --- dependency edges ----------------------------------------------------
#
# `depends on #N` was prose until 2026-08-04. Three tasks were claimed against
# unmet dependencies, branched off a base without them, and escalated — about
# $4 of quota spent discovering something the queue already knew.


def test_blockers_are_parsed_from_an_explicit_marker():
    assert queue.blockers("blocked-by: #15") == [15]


def test_several_blockers_on_one_line():
    assert queue.blockers("blocked-by: #9, #12") == [9, 12]


def test_several_blocked_by_lines_accumulate():
    assert queue.blockers("blocked-by: #9\nsome prose\nblocked-by: #12") == [9, 12]


def test_a_blocker_inside_a_callout_still_parses():
    """The line is usually written inside a `>` block or a bullet."""
    assert queue.blockers("> **blocked-by: #15** — do not start before it merges") == [15]


def test_prose_mentions_are_not_blockers():
    """The whole reason the marker is explicit. Bodies cite issues constantly."""
    body = "Ported from sandbox PR #4, see #12 for context. Depends on the voice-count issue (#5)."
    assert queue.blockers(body) == []


def test_duplicates_collapse():
    assert queue.blockers("blocked-by: #9\nblocked-by: #9") == [9]


def test_no_marker_is_no_blockers():
    assert queue.blockers("a perfectly ordinary issue body") == []
    assert queue.blockers("") == []


class StateGh(FakeGh):
    def __init__(self, states: dict[int, str]):
        super().__init__({})
        self.states = states

    def __call__(self, args):
        if args[:2] == ["issue", "view"] and "state" in args:
            self.calls.append(args)
            return json.dumps({"state": self.states.get(int(args[2]), "OPEN")})
        return super().__call__(args)


def test_a_closed_blocker_is_satisfied():
    """An issue closes when its PR merges — `Closes #N` — so CLOSED means the
    dependency's work is actually on main, which is what a dependent needs."""
    gh = StateGh({15: "CLOSED"})
    assert queue.open_blockers(REPO, [15], runner=gh) == []


def test_an_open_blocker_is_unmet():
    gh = StateGh({15: "OPEN"})
    assert queue.open_blockers(REPO, [15], runner=gh) == [15]


def test_an_unreadable_blocker_blocks():
    """Unreadable is not provably satisfied, and guessing costs a full cycle."""
    gh = StateGh({})
    gh.fail_on = "issue view"
    assert queue.open_blockers(REPO, [15], runner=gh) == [15]


# --- base: — threading an issue onto a shared theme branch (2026-08-09) ---


def test_base_branch_is_none_without_a_marker():
    """The repo default stays the normal case; prose must not opt in."""
    assert queue.base_branch("") is None
    assert queue.base_branch("port this from the narrow-tier work") is None
    assert queue.base_branch("see the narrow-tier branch for context") is None


def test_base_branch_is_parsed_from_an_explicit_marker():
    assert queue.base_branch("base: narrow-tier") == "narrow-tier"


def test_base_branch_tolerates_callout_and_backticks():
    """Written inside a bullet or quote as often as not, same as blocked-by."""
    assert queue.base_branch("> **base:** `narrow-tier`") == "narrow-tier"
    assert queue.base_branch("- base: narrow-tier") == "narrow-tier"


def test_base_branch_takes_the_first_and_ignores_the_rest():
    """Strict beats clever: two bases is a mistake, not a merge strategy."""
    body = "base: narrow-tier\nsome prose\nbase: something-else"
    assert queue.base_branch(body) == "narrow-tier"


def test_base_branch_ignores_a_bare_colon_with_no_value():
    assert queue.base_branch("base:") is None
    assert queue.base_branch("base:   ") is None


def test_base_branch_does_not_swallow_a_following_word():
    """`base: x y` is a typo, not a branch called "x y" — take the first token
    so a stray trailing word can't silently become part of the name."""
    assert queue.base_branch("base: narrow-tier and then merge") == "narrow-tier"

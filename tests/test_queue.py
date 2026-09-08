"""Crash-safety tests for the claim/reconcile cycle.

These exist because the failure they guard against cannot be hand-run: the
whole point is what happens when the daemon dies between two writes. Every test
kills the process at a specific instant by simply not performing the next step.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nightshift import queue
from nightshift.queue import Claim, Labels, Repair

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
            # The index and the object agree here — this fake has no lag. Tests
            # that need them to disagree use LaggyGh.
            number = int(args[2])
            names = [lb for lb, ns in self.by_label.items() if number in ns]
            return json.dumps({"labels": [{"name": n} for n in names]})
        return ""

    def edits(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["issue", "edit"]]


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


# --- claiming ------------------------------------------------------------


def test_claim_takes_oldest_ready_issue(tmp_path):
    gh = FakeGh({LABELS.ready: [7, 3, 5]})
    issue, record = queue.claim(REPO, tmp_path, runner=gh)
    assert issue.number == 3
    assert record.branch == "claude/3"


def test_claim_returns_none_on_empty_queue(tmp_path):
    gh = FakeGh({LABELS.ready: []})
    assert queue.claim(REPO, tmp_path, runner=gh) is None


def test_claim_skips_an_issue_that_already_has_a_claim_file(tmp_path):
    """`gh issue list` reads a lagging search index, so a just-claimed issue can
    still look ready. The local claim file is the authoritative guard."""
    make_claim(3)
    gh = FakeGh({LABELS.ready: [3, 8]})

    issue, _ = queue.claim(REPO, tmp_path, runner=gh)

    assert issue.number == 8, "must skip the stale-index entry, not re-claim it"


def test_claim_returns_none_when_every_ready_issue_is_already_claimed(tmp_path):
    make_claim(3)
    gh = FakeGh({LABELS.ready: [3]})
    assert queue.claim(REPO, tmp_path, runner=gh) is None


# --- contradictory labels ------------------------------------------------
#
# Observed 2026-08-03: `agent:ready` was added by hand to an issue the daemon
# was mid-implement on, leaving it carrying both labels at once.


def test_claim_refuses_an_issue_labelled_both_ready_and_working(tmp_path):
    gh = FakeGh({LABELS.ready: [3], LABELS.working: [3]})
    assert queue.claim(REPO, tmp_path, runner=gh) is None
    assert gh.edits() == [], "must not relabel an issue it refused"


def test_claim_refuses_a_ready_issue_that_is_already_done(tmp_path):
    """The dangerous window, and the reason the claim file is not enough.

    While the task runs, its claim file suppresses the stale `ready`. But
    `complete()` DELETES that file, so a moment later the only thing standing
    between a finished issue and a second claim is this check — and a re-claim
    would run the whole task again against a branch whose PR is already open.
    """
    gh = FakeGh({LABELS.ready: [3], LABELS.done: [3]})
    assert queue.claim(REPO, tmp_path, runner=gh) is None


def test_claim_takes_the_next_issue_rather_than_bailing(tmp_path):
    """One poisoned issue must not stall the whole queue behind it."""
    gh = FakeGh({LABELS.ready: [3, 8], LABELS.working: [3]})

    issue, _ = queue.claim(REPO, tmp_path, runner=gh)

    assert issue.number == 8


def test_claim_skips_an_issue_the_index_still_lists_as_ready(tmp_path):
    """Read-through also covers plain lag: listed as ready, no longer is."""
    gh = LaggyGh({LABELS.ready: [3, 8]}, truth={3: [], 8: [LABELS.ready]})

    issue, _ = queue.claim(REPO, tmp_path, runner=gh)

    assert issue.number == 8


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


def test_claim_file_is_written_before_the_label_swap(tmp_path, claim_dir):
    """The ordering invariant. A label with no claim file is the worse state."""
    observed: list[bool] = []

    class OrderingGh(FakeGh):
        def __call__(self, args):
            if args[:2] == ["issue", "edit"]:
                observed.append(queue.claim_path(REPO, 3).exists())
            return super().__call__(args)

    queue.claim(REPO, tmp_path, runner=OrderingGh({LABELS.ready: [3]}))
    assert observed == [True], "claim file must exist before the swap is attempted"


def test_failed_label_swap_rolls_back_the_claim_file(tmp_path):
    """We do not own the issue, so we must not leave a claim implying we do."""
    gh = FakeGh({LABELS.ready: [3]})
    gh.fail_on = "issue edit"
    with pytest.raises(RuntimeError):
        queue.claim(REPO, tmp_path, runner=gh)
    assert not queue.claim_path(REPO, 3).exists()


def test_claim_write_leaves_no_temp_file(claim_dir):
    make_claim(3)
    assert list(claim_dir.glob("*.tmp")) == []


# --- reconcile: the four states -----------------------------------------


def test_working_with_claim_and_worktree_is_resumable():
    make_claim(3, "/wt/exists")
    gh = FakeGh({LABELS.working: [3]})
    repairs = queue.reconcile(REPO, runner=gh, worktree_exists=lambda p: True)

    assert [r.repair for r in repairs] == [Repair.RESUMABLE]
    assert gh.edits() == [], "a resumable task must not be relabelled"
    assert queue.claim_path(REPO, 3).exists()


def test_working_with_claim_but_no_worktree_is_released():
    """Crashed between the label swap and the worktree create."""
    make_claim(3, "/wt/never-created")
    gh = FakeGh({LABELS.working: [3]})
    repairs = queue.reconcile(REPO, runner=gh, worktree_exists=lambda p: False)

    assert [r.repair for r in repairs] == [Repair.RELEASED]
    edit = gh.edits()[0]
    assert LABELS.ready in edit and LABELS.working in edit
    assert not queue.claim_path(REPO, 3).exists()


def test_working_with_no_claim_file_is_released():
    """Orphan: the daemon lost its local state, or another machine claimed it."""
    gh = FakeGh({LABELS.working: [9]})
    repairs = queue.reconcile(REPO, runner=gh)

    assert [r.repair for r in repairs] == [Repair.RELEASED]
    assert "no claim file" in repairs[0].detail


def test_claim_file_with_no_working_label_is_litter():
    """Crashed after the claim file but before the swap — inert, just delete it."""
    make_claim(4)
    gh = FakeGh({LABELS.working: []})
    repairs = queue.reconcile(REPO, runner=gh)

    assert [r.repair for r in repairs] == [Repair.LITTER]
    assert not queue.claim_path(REPO, 4).exists()
    assert gh.edits() == [], "litter must not touch GitHub"


def test_unparseable_claim_file_is_deleted_not_guessed_at(claim_dir):
    claim_dir.mkdir(parents=True, exist_ok=True)
    bad = claim_dir / f"{REPO.replace('/', '__')}#5.json"
    bad.write_text('{"repo": "matt/sandbox", "number":')  # truncated mid-write

    repairs = queue.reconcile(REPO, runner=FakeGh({LABELS.working: []}))

    assert [r.repair for r in repairs] == [Repair.LITTER]
    assert not bad.exists()


def test_reconcile_ignores_other_repos_claim_files(claim_dir):
    claim_dir.mkdir(parents=True, exist_ok=True)
    Claim(
        repo="matt/other", number=3, branch="claude/3",
        worktree="/wt/other", started_at="x",
    ).write()

    repairs = queue.reconcile(REPO, runner=FakeGh({LABELS.working: []}))

    assert repairs == []
    assert queue.claim_path("matt/other", 3).exists()


def test_reconcile_is_idempotent():
    """Two daemon restarts in a row must not double-release."""
    make_claim(3, "/wt/gone")
    gh = FakeGh({LABELS.working: [3]})
    queue.reconcile(REPO, runner=gh, worktree_exists=lambda p: False)

    gh2 = FakeGh({LABELS.working: []})  # release moved it back to ready
    assert queue.reconcile(REPO, runner=gh2) == []


# --- terminal transitions ------------------------------------------------


def test_escalate_comments_relabels_and_clears_the_claim():
    make_claim(3)
    gh = FakeGh()
    queue.escalate(REPO, 3, "conflicts with TROPISMS.md", runner=gh)

    assert gh.calls[0][:2] == ["issue", "comment"]
    edit = gh.edits()[0]
    assert LABELS.needs_human in edit and LABELS.working in edit
    assert not queue.claim_path(REPO, 3).exists()


def test_complete_relabels_and_clears_the_claim():
    make_claim(3)
    gh = FakeGh()
    queue.complete(REPO, 3, "https://github.com/x/y/pull/4", runner=gh)

    edit = gh.edits()[0]
    assert LABELS.done in edit and LABELS.working in edit
    assert not queue.claim_path(REPO, 3).exists()


# --- label-index lag -----------------------------------------------------


class LaggyGh(FakeGh):
    """`issue list` misses a just-written label; `issue view` reads through."""

    def __init__(self, by_label, truth: dict[int, list[str]]):
        super().__init__(by_label)
        self.truth = truth

    def __call__(self, args):
        if args[:2] == ["issue", "view"]:
            self.calls.append(args)
            number = int(args[2])
            return json.dumps(
                {"labels": [{"name": n} for n in self.truth.get(number, [])]}
            )
        return super().__call__(args)


def test_stale_label_index_does_not_destroy_a_claim_file():
    """The bug this guards: a daemon restarting straight after a crash sees its
    own in-flight issue as not-working, deletes the claim as litter, and loses
    the worktree pointer and phase needed to recover."""
    make_claim(3, "/wt/live")
    gh = LaggyGh({LABELS.working: []}, truth={3: [LABELS.working]})

    repairs = queue.reconcile(REPO, runner=gh, worktree_exists=lambda p: True)

    assert [r.repair for r in repairs] == [Repair.RESUMABLE]
    assert queue.claim_path(REPO, 3).exists(), "claim file must survive"


def test_genuinely_orphaned_claim_is_still_litter():
    """The read-through must not make litter collection impossible."""
    make_claim(4)
    gh = LaggyGh({LABELS.working: []}, truth={4: ["agent:done"]})

    repairs = queue.reconcile(REPO, runner=gh)

    assert [r.repair for r in repairs] == [Repair.LITTER]
    assert not queue.claim_path(REPO, 4).exists()


def test_read_through_is_only_consulted_for_claims_the_index_missed():
    """One extra API call per unmatched claim file, not per issue."""
    make_claim(5, "/wt/live")
    gh = LaggyGh({LABELS.working: [5]}, truth={5: [LABELS.working]})

    queue.reconcile(REPO, runner=gh, worktree_exists=lambda p: True)

    views = [c for c in gh.calls if c[:2] == ["issue", "view"]]
    assert views == [], "index already showed it; no read-through needed"


# --- issue comments ------------------------------------------------------
#
# Observed 2026-08-04: `escalate()` posts findings "so the work is not
# repeated", but the worker was handed only title and body — so on re-queue it
# repeated the work. Comments are also how a human answers an escalation.


class CommentGh(FakeGh):
    def __init__(self, comments):
        super().__init__({})
        self.comments = comments

    def __call__(self, args):
        if args[:2] == ["issue", "view"] and "comments" in args:
            self.calls.append(args)
            return json.dumps({"comments": self.comments})
        return super().__call__(args)


def test_comments_are_returned_oldest_first_with_authors():
    gh = CommentGh(
        [
            {"author": {"login": "mattpolicastro"}, "body": "widen the scope"},
            {"author": {"login": "nightshift"}, "body": "escalated: here is why"},
        ]
    )
    assert queue.comments_of(REPO, 6, runner=gh) == [
        "**mattpolicastro:**\n\nwiden the scope",
        "**nightshift:**\n\nescalated: here is why",
    ]


def test_empty_comments_are_dropped_not_rendered_as_blank_blocks():
    gh = CommentGh([{"author": {"login": "x"}, "body": "   "}])
    assert queue.comments_of(REPO, 6, runner=gh) == []


def test_a_missing_author_does_not_crash_the_payload():
    gh = CommentGh([{"body": "posted by a deleted account"}])
    assert queue.comments_of(REPO, 6, runner=gh) == [
        "**unknown:**\n\nposted by a deleted account"
    ]


def test_no_comments_is_an_empty_list_not_a_failure():
    gh = CommentGh([])
    assert queue.comments_of(REPO, 6, runner=gh) == []


def test_an_api_failure_reading_comments_is_survivable():
    """Comments are context, not correctness — losing them must not kill a run."""
    gh = CommentGh([])
    gh.fail_on = "issue view"
    assert queue.comments_of(REPO, 6, runner=gh) == []


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


class BlockedGh(FakeGh):
    """Ready issues whose bodies carry blocked-by markers, plus issue states."""

    def __init__(self, ready_bodies: dict[int, str], states: dict[int, str]):
        super().__init__({Labels().ready: list(ready_bodies)})
        self.bodies = ready_bodies
        self.states = states

    def __call__(self, args):
        if args[:2] == ["issue", "list"]:
            self.calls.append(args)
            # Only the `ready` label. `FakeGh` has always filtered on it and
            # this override did not, which stopped being harmless the moment
            # admission grew a second label: every issue came back for the
            # `revise` query too, and was then correctly rejected by the gate
            # recheck, so `claim` found nothing.
            if args[args.index("--label") + 1] != Labels().ready:
                return json.dumps([])
            return json.dumps(
                [
                    {"number": n, "title": f"issue {n}", "body": b}
                    for n, b in self.bodies.items()
                ]
            )
        if args[:2] == ["issue", "view"] and "state" in args:
            self.calls.append(args)
            return json.dumps({"state": self.states.get(int(args[2]), "OPEN")})
        return super().__call__(args)


def test_claim_skips_an_issue_with_an_unmet_blocker(tmp_path):
    gh = BlockedGh({9: "blocked-by: #15"}, states={15: "OPEN"})
    assert queue.claim(REPO, tmp_path, runner=gh) is None


def test_claim_takes_an_issue_once_its_blocker_merges(tmp_path):
    gh = BlockedGh({9: "blocked-by: #15"}, states={15: "CLOSED"})
    issue, _ = queue.claim(REPO, tmp_path, runner=gh)
    assert issue.number == 9


def test_claim_passes_over_a_blocked_issue_to_an_unblocked_one(tmp_path):
    """Order is by issue number, so a blocked low number must not stall the rest."""
    gh = BlockedGh(
        {9: "blocked-by: #15", 11: "no dependencies here"}, states={15: "OPEN"}
    )
    issue, _ = queue.claim(REPO, tmp_path, runner=gh)
    assert issue.number == 11


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

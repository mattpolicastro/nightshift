from __future__ import annotations

import json

from nightshift import queue
from nightshift.queue import Labels

REPO = "o/r"


class Gh:
    """Answers `issue list` per label and `issue view` from a label map."""

    def __init__(self, by_label: dict[str, list[int]], labels_of: dict[int, list[str]]):
        self.by_label = by_label
        self.labels = labels_of
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            label = args[args.index("--label") + 1]
            return json.dumps(
                [
                    {"number": n, "title": f"issue {n}", "body": "body"}
                    for n in self.by_label.get(label, [])
                ]
            )
        if args[:2] == ["issue", "view"] and "state" in args:
            return json.dumps({"state": "CLOSED"})
        if args[:2] == ["issue", "view"]:
            n = int(args[2])
            return json.dumps({"labels": [{"name": x} for x in self.labels.get(n, [])]})
        return ""


# ── admission ────────────────────────────────────────────────────────────────


def test_a_revise_issue_is_claimable_and_tagged():
    gh = Gh({Labels().revise: [7]}, {7: [Labels().revise, Labels().done]})
    found = queue.ready(REPO, runner=gh)
    assert [(i.number, i.revise) for i in found] == [(7, True)]


def test_ready_and_revise_are_listed_together_in_number_order():
    gh = Gh({Labels().ready: [9], Labels().revise: [7]}, {})
    assert [(i.number, i.revise) for i in queue.ready(REPO, runner=gh)] == [
        (7, True),
        (9, False),
    ]


def test_an_issue_carrying_both_labels_is_treated_as_a_revise():
    """The narrower reading. Branching from base would discard the diff."""
    gh = Gh({Labels().ready: [7], Labels().revise: [7]}, {})
    found = queue.ready(REPO, runner=gh)
    assert [(i.number, i.revise) for i in found] == [(7, True)]


def test_done_does_not_contradict_revise():
    """It is the normal state of the thing being revised."""
    assert queue.conflicting_labels({Labels().revise, Labels().done}) == set()


def test_working_still_contradicts_revise():
    """A human re-labelling an in-flight task is the hazard either way."""
    assert queue.conflicting_labels({Labels().revise, Labels().working}) == {
        Labels().working
    }


def test_done_still_contradicts_ready():
    """The original rule is untouched: `ready` on finished work is an error."""
    assert queue.conflicting_labels({Labels().ready, Labels().done}) == {Labels().done}


# ── claiming ─────────────────────────────────────────────────────────────────


def test_a_claim_file_without_the_field_still_loads(tmp_path):
    """Written before `revise` existed. Absent means not a revise.

    `reconcile` splats the whole file into the dataclass, so a field without a
    default would make every in-flight claim from the previous version
    unreadable at exactly the moment recovery needs it.
    """
    stale = {
        "repo": REPO,
        "number": 7,
        "branch": "claude/7",
        "worktree": str(tmp_path / "wt"),
        "started_at": "2026-08-06T00:00:00Z",
        "phase": "claimed",
    }
    assert queue.Claim(**stale).revise is False


# ── the no-instructions guard ────────────────────────────────────────────────


def test_the_daemons_own_escalation_is_not_an_instruction():
    """It comments AS Matt, so only the body prefix distinguishes it."""
    own = f"**matt:**\n\n{queue.SELF_COMMENT_PREFIX} escalated, not implemented."
    assert queue.has_instructions([own]) is False


def test_a_human_comment_is_an_instruction():
    assert queue.has_instructions(["**matt:**\n\nplease widen the band"]) is True


def test_no_comments_at_all_is_not_an_instruction():
    assert queue.has_instructions([]) is False


def test_a_human_comment_alongside_the_daemons_still_counts():
    own = f"**matt:**\n\n{queue.SELF_COMMENT_PREFIX} escalated."
    assert queue.has_instructions([own, "**matt:**\n\nchange line 40"]) is True

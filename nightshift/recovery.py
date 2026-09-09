"""What to do with a task the daemon was killed in the middle of.

`queue.reconcile` reports that a claim is RESUMABLE — label, claim file and
worktree all present. That says the work exists; it does not say what to do
with it. This module decides, from the phase recorded in the claim file plus
what is observably true of the branch.

The bias throughout is **do not re-run work that may already have landed**.
Re-reviewing a committed diff is free and idempotent; re-implementing over a
branch that already has commits is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .queue import Phase


class Action(Enum):
    RETAIN = "retain"  # native execution evidence requires explicit inspection
    RELEASE = "release"  # nothing was produced — back to agent:ready
    ESCALATE = "escalate"  # work exists but its state is unknowable
    RE_REVIEW = "re_review"  # diff is committed; review is safe to redo
    OPEN_PR = "open_pr"  # branch pushed, PR never opened — finish the step
    COMPLETE = "complete"  # PR already exists; only the label is missing


@dataclass(frozen=True)
class Recovery:
    action: Action
    reason: str


def decide(
    phase: str,
    *,
    has_commits: bool,
    branch_pushed: bool,
    pr_url: str | None,
    native_pending: bool = False,
) -> Recovery:
    """Pure. `pr_url` is None when no PR exists for the branch."""

    if native_pending:
        return Recovery(Action.RETAIN, "native execution or cleanup evidence requires inspection")

    # Order matters: an existing PR is the strongest evidence available, and it
    # can be true at any phase if the crash landed between `gh pr create` and
    # the label swap. Checking it first means that window is never re-run.
    if pr_url:
        return Recovery(Action.COMPLETE, f"PR already open at {pr_url}")

    if phase == Phase.SHIPPING.value:
        if branch_pushed and has_commits:
            return Recovery(Action.OPEN_PR, "branch pushed, PR not opened")
        # Told to ship, but nothing is on the remote and nothing is committed —
        # the claim file disagrees with the branch, so trust neither.
        return Recovery(
            Action.ESCALATE, "shipping phase but no pushed commits to ship"
        )

    if phase == Phase.REVIEWING.value:
        if has_commits:
            # The diff is committed and immutable; reviewing it again costs one
            # reviewer run and cannot double-commit.
            return Recovery(Action.RE_REVIEW, "diff committed, review not finished")
        return Recovery(Action.ESCALATE, "reviewing phase but nothing committed")

    if phase == Phase.IMPLEMENTING.value:
        if has_commits:
            # A partial implement is the one genuinely ambiguous case. The
            # worker may have been mid-sequence, and nothing on disk says
            # whether it considered itself finished. Re-running risks
            # duplicate or conflicting commits; a human is cheaper than either.
            return Recovery(
                Action.ESCALATE, "died mid-implement with commits on the branch"
            )
        return Recovery(Action.RELEASE, "died mid-implement, nothing committed")

    if phase == Phase.CLAIMED.value:
        return Recovery(Action.RELEASE, "died before implementing")

    # An unrecognised phase means a claim file this version did not write.
    return Recovery(Action.ESCALATE, f"unrecognised phase {phase!r}")

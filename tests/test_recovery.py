"""Every point the daemon can be killed at, and what should happen next.

There is no way to reach these by running the system — you would have to kill
the process at an exact instant, repeatedly. Each test names the instant.
"""

from __future__ import annotations

from nightshift.queue import Phase
from nightshift.recovery import Action, decide


def d(phase, *, has_commits=False, branch_pushed=False, pr_url=None):
    return decide(
        phase.value if isinstance(phase, Phase) else phase,
        has_commits=has_commits,
        branch_pushed=branch_pushed,
        pr_url=pr_url,
    )


# --- nothing was produced ------------------------------------------------


def test_killed_right_after_claiming_releases():
    assert d(Phase.CLAIMED).action is Action.RELEASE


def test_killed_mid_implement_with_no_commits_releases():
    """The worker ran but produced nothing — re-running costs one cycle."""
    r = d(Phase.IMPLEMENTING)
    assert r.action is Action.RELEASE
    assert "nothing committed" in r.reason


# --- work exists, state unknowable ---------------------------------------


def test_killed_mid_implement_with_commits_escalates():
    """The one genuinely ambiguous case: nothing on disk says whether the
    worker considered itself finished, so re-running risks duplicate or
    conflicting commits."""
    r = d(Phase.IMPLEMENTING, has_commits=True)
    assert r.action is Action.ESCALATE
    assert r.action is not Action.RE_REVIEW


def test_reviewing_with_nothing_committed_escalates():
    """The claim file disagrees with the branch — trust neither."""
    assert d(Phase.REVIEWING).action is Action.ESCALATE


def test_shipping_with_nothing_pushed_escalates():
    assert d(Phase.SHIPPING, has_commits=True).action is Action.ESCALATE


def test_unrecognised_phase_escalates_rather_than_guessing():
    """A claim file written by a different version of this code."""
    r = d("halfway-through-something")
    assert r.action is Action.ESCALATE
    assert "unrecognised" in r.reason


# --- safe to redo --------------------------------------------------------


def test_killed_mid_review_re_reviews():
    """A committed diff is immutable: reviewing again cannot double-commit."""
    r = d(Phase.REVIEWING, has_commits=True)
    assert r.action is Action.RE_REVIEW


# --- finish the last step ------------------------------------------------


def test_pushed_but_no_pr_opens_the_pr():
    r = d(Phase.SHIPPING, has_commits=True, branch_pushed=True)
    assert r.action is Action.OPEN_PR


def test_existing_pr_just_needs_the_label():
    r = d(Phase.SHIPPING, has_commits=True, branch_pushed=True, pr_url="…/pull/9")
    assert r.action is Action.COMPLETE


def test_existing_pr_wins_from_any_phase():
    """The crash window between `gh pr create` and the label swap is real: the
    PR exists while the claim file still says an earlier phase. Re-running from
    that phase would open a second PR."""
    for phase in Phase:
        r = decide(
            phase.value, has_commits=True, branch_pushed=True, pr_url="…/pull/9"
        )
        assert r.action is Action.COMPLETE, phase


def test_existing_pr_wins_even_without_local_commits():
    """A fresh checkout, or a worktree recreated after the push, still must not
    re-open a PR that already exists."""
    r = d(Phase.IMPLEMENTING, has_commits=False, pr_url="…/pull/9")
    assert r.action is Action.COMPLETE

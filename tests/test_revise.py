"""`agent:revise` — revising a shipped task on the branch it already produced.

Before this, the only way to act on PR feedback was to re-label the issue
`agent:ready`, which cuts a fresh branch from base. That silently restarted
work that was mostly correct, and then died late: the new branch shares no
history with the remote, so `vcs.push` (deliberately not a force push) was
rejected after the whole implement-and-review cycle had already run.

The flow this supports: close the PR, comment the changes you want on the
ISSUE, swap `agent:done` for `agent:revise`. The worker resumes the existing
branch, adds commits on top, and opens a fresh PR.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from nightshift import queue, vcs
from nightshift.queue import Labels

REPO = "o/r"


@pytest.fixture(autouse=True)
def claim_dir(tmp_path, monkeypatch):
    """Never let a test write into the live daemon's claim directory."""
    monkeypatch.setattr(queue, "CLAIM_DIR", tmp_path / "claims")


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


def test_claiming_a_revise_records_it_and_sheds_done(tmp_path):
    gh = Gh({Labels().revise: [7]}, {7: [Labels().revise, Labels().done]})
    issue, claim = queue.claim(REPO, tmp_path, runner=gh)

    assert issue.revise is True
    assert claim.revise is True

    edit = next(c for c in gh.calls if c[:2] == ["issue", "edit"])
    assert "--add-label" in edit and Labels().working in edit
    # Both come off: leaving `done` would read as a contradiction to anyone
    # who has not internalised the exception in `conflicting_labels`.
    removed = [edit[i + 1] for i, a in enumerate(edit) if a == "--remove-label"]
    assert set(removed) == {Labels().revise, Labels().done}


def test_a_revise_claim_survives_a_reload(tmp_path):
    """Recovery after a crash must still know this was a revise.

    Reloaded the way `reconcile` does it, which is the only reader there is.
    """
    gh = Gh({Labels().revise: [7]}, {7: [Labels().revise]})
    _, claim = queue.claim(REPO, tmp_path, runner=gh)

    reloaded = queue.Claim(**json.loads(Path(claim.path).read_text()))
    assert reloaded.revise is True


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


# ── resuming the branch ──────────────────────────────────────────────────────


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def clone(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(["init", "-q", "-b", "main"], seed)
    git(["config", "user.email", "t@t"], seed)
    git(["config", "user.name", "t"], seed)
    (seed / "base.txt").write_text("one\n")
    git(["add", "."], seed)
    git(["commit", "-qm", "first"], seed)
    git(["clone", "-q", "--bare", str(seed), str(origin)], tmp_path)

    clone = tmp_path / "clone"
    git(["clone", "-q", str(origin), str(clone)], tmp_path)
    return clone


def _shipped(clone: Path, root: Path, branch: str) -> str:
    """A branch as a shipped task leaves it: commits, pushed, worktree gone."""
    wt = root / "task"
    vcs.add_worktree(clone, wt, branch, "origin/main", allowed_root=root)
    (wt / "shipped.txt").write_text("the reviewed diff\n")
    git(["add", "."], wt)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "shipped"], wt)
    sha = git(["rev-parse", "HEAD"], wt).strip()
    git(["push", "-q", "origin", branch], wt)
    vcs.remove_worktree(clone, wt, allowed_root=root)
    return sha


def test_resuming_keeps_the_shipped_commit(clone, tmp_path):
    root = tmp_path / "wt"
    sha = _shipped(clone, root, "claude/7")

    wt = root / "again"
    vcs.resume_worktree(clone, wt, "claude/7", allowed_root=root)

    assert (wt / "shipped.txt").exists()
    assert git(["rev-parse", "HEAD"], wt).strip() == sha


def test_resuming_is_not_branching_from_base(clone, tmp_path):
    """The distinction the whole label exists for."""
    root = tmp_path / "wt"
    _shipped(clone, root, "claude/7")

    resumed = root / "resumed"
    vcs.resume_worktree(clone, resumed, "claude/7", allowed_root=root)
    assert git(["log", "--oneline", "origin/main..HEAD"], resumed).strip() != ""


def test_resuming_prefers_origin_over_a_stale_local_ref(clone, tmp_path):
    """The remote is what the PR is built from; a local ref is this clone's."""
    root = tmp_path / "wt"
    sha = _shipped(clone, root, "claude/7")
    git(["branch", "-f", "claude/7", "origin/main"], clone)  # stale local

    wt = root / "again"
    vcs.resume_worktree(clone, wt, "claude/7", allowed_root=root)

    assert git(["rev-parse", "HEAD"], wt).strip() == sha


def test_resuming_a_branch_that_never_existed_raises(clone, tmp_path):
    """A revise with nothing to revise is a labelling mistake, not a fresh start."""
    root = tmp_path / "wt"
    with pytest.raises(vcs.NoBranchToResume):
        vcs.resume_worktree(clone, root / "wt", "claude/999", allowed_root=root)


def test_resuming_twice_is_idempotent(clone, tmp_path):
    root = tmp_path / "wt"
    sha = _shipped(clone, root, "claude/7")

    wt = root / "again"
    vcs.resume_worktree(clone, wt, "claude/7", allowed_root=root)
    vcs.resume_worktree(clone, wt, "claude/7", allowed_root=root)

    assert git(["rev-parse", "HEAD"], wt).strip() == sha


# ── PR feedback ──────────────────────────────────────────────────────────────


class PrGh:
    """`pr list` / `pr view` / `api …/comments`, the three shapes of feedback."""

    def __init__(self, numbers=(15,), comments=(), reviews=(), inline=()):
        self.numbers = numbers
        self.comments = list(comments)
        self.reviews = list(reviews)
        self.inline = list(inline)
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["pr", "list"]:
            return json.dumps([{"number": n} for n in self.numbers])
        if args[:2] == ["pr", "view"]:
            return json.dumps({"comments": self.comments, "reviews": self.reviews})
        if args[0] == "api":
            return json.dumps(self.inline)
        return ""


def _c(login, body, at="2026-08-06T10:00:00Z"):
    return {"author": {"login": login}, "body": body, "createdAt": at}


def test_inline_comments_carry_their_file_and_line():
    """The reason this exists — anchoring an issue would have to reconstruct."""
    gh = PrGh(
        inline=[
            {
                "user": {"login": "matt"},
                "path": "src/lean.ts",
                "line": 42,
                "body": "this band is too wide",
                "created_at": "2026-08-06T10:00:00Z",
            }
        ]
    )
    blocks = queue.pr_feedback(REPO, "claude/7", runner=gh)
    assert "`src/lean.ts:42`" in blocks[0]
    assert "this band is too wide" in blocks[0]


def test_all_three_comment_kinds_are_collected():
    gh = PrGh(
        comments=[_c("matt", "conversation note")],
        reviews=[
            {
                "author": {"login": "matt"},
                "body": "review body",
                "state": "CHANGES_REQUESTED",
                "submittedAt": "2026-08-06T10:00:00Z",
            }
        ],
        inline=[
            {
                "user": {"login": "matt"},
                "path": "a.ts",
                "line": 1,
                "body": "inline note",
                "created_at": "2026-08-06T10:00:00Z",
            }
        ],
    )
    joined = "\n".join(queue.pr_feedback(REPO, "claude/7", runner=gh))
    assert "conversation note" in joined
    assert "review body" in joined
    assert "changes requested" in joined
    assert "inline note" in joined


def test_only_the_most_recent_pr_is_read():
    """A revise opens a new PR; the closed one holds the previous round."""
    gh = PrGh(numbers=(15, 21, 18))
    queue.pr_feedback(REPO, "claude/7", runner=gh)
    view = next(c for c in gh.calls if c[:2] == ["pr", "view"])
    assert view[2] == "21"


def test_feedback_older_than_the_branch_tip_is_ignored():
    """It is what the branch was already built from, not a request."""
    gh = PrGh(
        comments=[
            _c("matt", "before the last commit", at="2026-08-06T09:00:00Z"),
            _c("matt", "after the last commit", at="2026-08-06T11:00:00Z"),
        ]
    )
    blocks = queue.pr_feedback(REPO, "claude/7", "2026-08-06T10:00:00Z", runner=gh)
    joined = "\n".join(blocks)
    assert "after the last commit" in joined
    assert "before the last commit" not in joined


def test_a_branch_with_no_pr_yields_nothing():
    assert queue.pr_feedback(REPO, "claude/7", runner=PrGh(numbers=())) == []


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


# ── the since boundary ───────────────────────────────────────────────────────


def test_branch_tip_time_is_zulu_and_sorts_against_github(clone, tmp_path):
    """Compared to GitHub's timestamps as TEXT, so the format has to match."""
    root = tmp_path / "wt"
    _shipped(clone, root, "claude/7")
    wt = root / "again"
    vcs.resume_worktree(clone, wt, "claude/7", allowed_root=root)

    stamp = vcs.branch_tip_time(wt)
    assert stamp and stamp.endswith("Z")
    assert "2000-01-01T00:00:00Z" < stamp < "2100-01-01T00:00:00Z"


def test_branch_tip_time_survives_a_missing_directory(tmp_path):
    """A `cwd` that does not exist raises OSError before git is spawned."""
    assert vcs.branch_tip_time(tmp_path / "nope") is None

"""Worktree lifecycle and the push/PR step.

Worktrees live outside the repo (`~/Projects/nightshift-wt/`) so that a stray
`git add -A` in one task cannot sweep a sibling task's files into a commit.
AGENTS.md forbids `git add -A`, but the layout should not depend on an agent
following instructions.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class UnsafePath(Exception):
    """A teardown target that is not obviously a disposable worktree."""


class RefusedPush(Exception):
    """A push at the base branch rather than a task branch."""


def _run(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}… failed: {proc.stderr.strip()}")
    return proc.stdout


def assert_disposable(worktree: Path, allowed_root: Path) -> Path:
    """Refuse to delete anything that is not a worktree under `allowed_root`.

    `remove_worktree` falls back to `rmtree`, and its argument ultimately comes
    from a claim file on disk — which `queue.reconcile` deliberately tolerates
    being corrupt or hand-edited. A bad value there would otherwise be a silent
    recursive delete of whatever it points at.

    Belt and braces, because the cost of being wrong is unrecoverable:
      - must resolve to a strict descendant of `allowed_root`
      - `allowed_root` itself is never disposable
      - must be at least two levels below the filesystem root
      - must not be, or contain, a real `.git` directory (a worktree has a
        `.git` *file*, a clone has a directory — this is what separates the
        sandbox worktree from `~/Projects/sample` itself)
    """
    target = worktree.expanduser().resolve()
    root = allowed_root.expanduser().resolve()

    if target == root or root not in target.parents:
        raise UnsafePath(f"{target} is not under {root}")
    if len(target.parts) < 3:
        raise UnsafePath(f"{target} is too close to the filesystem root")
    if (target / ".git").is_dir():
        raise UnsafePath(f"{target} looks like a real clone, not a worktree")

    return target


def fetch(repo_dir: Path) -> None:
    """Bring remote-tracking refs up to date before anything reads a base.

    Observed 2026-08-03: the daemon shipped and Matt merged a PR, then the next
    two tasks branched off a LOCAL `main` that had never been fetched — so the
    dependency they needed was on GitHub but not in their worktrees. Both
    escalated, correctly, and the cycle was wasted.

    A local branch ref is whatever this clone last pulled. Nothing in the
    daemon's own flow ever updates it, because the daemon pushes branches and
    never checks out `main`. So without this, base drifts further behind with
    every merge and never recovers.
    """
    _run(["git", "fetch", "--prune", "origin"], cwd=repo_dir)


def branch_exists(repo_dir: Path, branch: str) -> bool:
    return bool(_run(["git", "branch", "--list", branch], cwd=repo_dir).strip())


def remote_ref_exists(repo_dir: Path, ref: str) -> bool:
    """Whether a fully-qualified ref (`origin/narrow-tier`) is present locally.

    Answers the post-fetch question `branch_exists` cannot: that one lists
    LOCAL branches, and the daemon's clone has none but the task branches it
    cuts. Callers must `fetch` first — this reads the clone, not the network.

    Public because `task.py` asks it of a theme branch declared by an issue
    (`base:`), and a caller reaching into `_ref_exists` for that would be
    depending on a private helper across a module boundary.

    Takes the SHORTHAND (`origin/narrow-tier`) and qualifies it, because that
    is the form its callers hold: `task.py` builds `f"origin/{base}"` to hand
    to `add_worktree` and asks about the same string. `git show-ref --verify`
    accepts only fully-qualified refs and reports a shorthand as ABSENT rather
    than erroring — so the first cut of this shipped a guard that refused
    every theme branch, including ones that existed. It escalated #128 against
    a `narrow-tier` that was pushed and reachable.
    """
    qualified = ref if ref.startswith("refs/") else f"refs/remotes/{ref}"
    return _ref_exists(repo_dir, qualified)


def branch_has_commits(repo_dir: Path, branch: str, base: str) -> bool:
    """Whether `branch` holds anything beyond `base`, without a worktree.

    `has_commits` answers the same question from inside a worktree, which is no
    use here: the case that matters is a branch whose worktree is already gone.
    """
    try:
        out = _run(["git", "log", "--oneline", f"{base}..{branch}"], cwd=repo_dir)
        return bool(out.strip())
    except RuntimeError:
        # Unreadable is not "empty". Same rule as `_branch_to_delete`: failing
        # to answer must never be the same as answering "throw it away".
        return True


def preserve_branch(repo_dir: Path, branch: str, base: str) -> str | None:
    """Move a branch holding work out of the way. Returns the new name.

    `None` when there was nothing to preserve — no such branch, or one sitting
    exactly on `base` — in which case the caller may reuse the name freely.

    The kept name carries the tip sha, so re-running is idempotent rather than
    accumulating `-kept-kept-kept`: if the destination already exists, this
    branch's work is already saved under it and the duplicate ref goes.
    """
    if not branch_exists(repo_dir, branch):
        return None
    if not branch_has_commits(repo_dir, branch, base):
        _run(["git", "branch", "-D", branch], cwd=repo_dir)
        return None
    sha = _run(["git", "rev-parse", branch], cwd=repo_dir).strip()
    kept = f"{branch}-kept-{sha[:7]}"
    if branch_exists(repo_dir, kept):
        _run(["git", "branch", "-D", branch], cwd=repo_dir)
    else:
        _run(["git", "branch", "-m", branch, kept], cwd=repo_dir)
    return kept


def add_worktree(
    repo_dir: Path, worktree: Path, branch: str, base: str, *, allowed_root: Path
) -> str | None:
    """Create a fresh worktree on a new branch. Idempotent for a crashed retry.

    Returns the name a pre-existing branch's work was preserved under, or
    `None` if nothing had to be moved.

    `base` is expected to be a remote-tracking ref (`origin/main`) — see
    `Repo.base_ref`. Call `fetch()` first or it is just as stale as the local
    branch it replaced.

    Idempotency used to be keyed on the WORKTREE alone, which is not the thing
    that survives. An escalation deliberately keeps its branch and drops its
    worktree (see `daemon._branch_to_delete`), so re-arming an escalated issue
    hit `git worktree add -b` against a branch that already existed and the
    task crashed — sample #5, 2026-08-05. Recovery then released the claim and
    deleted the branch, which is exactly the work `_branch_to_delete` exists to
    protect; the retry only "succeeded" because the escalation had just been
    destroyed.

    So the branch is resolved on its own terms: empty ones are reused, ones
    holding commits are renamed aside rather than deleted or reused. Reusing
    them is not an option either — the task branches from `base`, and a worker
    resuming on top of a previous attempt's diff is a different task than the
    issue describes.
    """
    if worktree.exists():
        # No branch argument: teardown here must not delete work either, and
        # the collision is resolved immediately below on its own terms.
        remove_worktree(repo_dir, worktree, allowed_root=allowed_root)
    kept = preserve_branch(repo_dir, branch, base)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "worktree", "add", "-b", branch, str(worktree), base], cwd=repo_dir)
    return kept


class NoBranchToResume(Exception):
    """A revise was asked for against a branch that does not exist."""


def resume_worktree(
    repo_dir: Path, worktree: Path, branch: str, *, allowed_root: Path
) -> None:
    """Put an EXISTING branch back in a worktree, for a revise.

    The opposite of `add_worktree`: no `-b`, no base. The branch already
    carries the shipped diff and the revision lands as further commits on top,
    which is the whole point — `ready` cuts from base and would silently
    restart work that was mostly correct.

    `origin/<branch>` wins over a local ref of the same name. The remote is
    what the PR is built from and what another machine would have pushed; a
    local ref is whatever this clone last did, possibly a `-kept-` era leftover
    or a stale copy from before someone amended the branch on GitHub.

    Raises `NoBranchToResume` rather than inventing an empty branch. A revise
    with nothing to revise is a labelling mistake, and the expensive version of
    that mistake is a worker cheerfully re-implementing from scratch and
    opening a second PR.
    """
    if worktree.exists():
        remove_worktree(repo_dir, worktree, allowed_root=allowed_root)
    worktree.parent.mkdir(parents=True, exist_ok=True)

    remote = f"origin/{branch}"
    if _ref_exists(repo_dir, f"refs/remotes/{remote}"):
        # Detached-then-named: `git worktree add <path> <branch>` refuses when
        # the local branch is checked out elsewhere, and `-B` from the remote
        # is what makes this idempotent across repeated revises.
        _run(
            ["git", "worktree", "add", "-B", branch, str(worktree), remote],
            cwd=repo_dir,
        )
        return
    if branch_exists(repo_dir, branch):
        _run(["git", "worktree", "add", str(worktree), branch], cwd=repo_dir)
        return
    raise NoBranchToResume(
        f"{branch!r} exists neither on origin nor locally — nothing to revise"
    )


def _ref_exists(repo_dir: Path, ref: str) -> bool:
    try:
        _run(["git", "show-ref", "--verify", "--quiet", ref], cwd=repo_dir)
        return True
    except RuntimeError:
        return False


def remove_worktree(
    repo_dir: Path,
    worktree: Path,
    branch: str | None = None,
    *,
    allowed_root: Path,
) -> None:
    """Best-effort teardown. A failure here must not abort the task's outcome.

    The safety check is NOT best-effort: an unsafe path raises rather than
    falling back to a delete.
    """
    target = assert_disposable(worktree, allowed_root)
    try:
        _run(["git", "worktree", "remove", "--force", str(target)], cwd=repo_dir)
    except RuntimeError:
        shutil.rmtree(target, ignore_errors=True)
        try:
            _run(["git", "worktree", "prune"], cwd=repo_dir)
        except RuntimeError:
            pass
    if branch:
        try:
            _run(["git", "branch", "-D", branch], cwd=repo_dir)
        except RuntimeError:
            pass  # branch was pushed, or never created


def install(worktree: Path, command: str) -> None:
    subprocess.run(command, cwd=worktree, shell=True, check=False, capture_output=True)


def has_commits(worktree: Path, base: str) -> bool:
    return bool(_run(["git", "log", "--oneline", f"{base}..HEAD"], cwd=worktree).strip())


def changed_files(worktree: Path, base: str) -> list[str]:
    out = _run(["git", "diff", base, "--name-only"], cwd=worktree)
    return [line for line in out.splitlines() if line.strip()]


def dirty(worktree: Path) -> list[str]:
    """Untracked or uncommitted leftovers — a worker that left scratch behind."""
    out = _run(["git", "status", "--porcelain"], cwd=worktree)
    return [line for line in out.splitlines() if line.strip()]


_WORKFLOW_REFUSAL = "without `workflow` scope"


class WorkflowScopeRefusal(RuntimeError):
    """The remote refused a push that touches `.github/workflows/`.

    Deliberate, and correct. This exists so the refusal can be reported in its
    own words instead of as a raw git error — and, more importantly, so it
    ESCALATES rather than reaching the daemon's crash handler, which releases
    the issue and deletes the branch. That turns one doomed task into an
    unbounded retry: released work is claimable again, fails again at the same
    push, and spends another full budget and another cap slot doing it.
    """


def push(worktree: Path, branch: str, base: str | None = None) -> None:
    """Push a task branch. Refuses to be the thing that pushes the base.

    "Workers never push to main" is enforced at three layers, and this is the
    one that was missing. `worker._DENIED` stops the AGENT running `git push`
    at all, and AGENTS.md tells it not to — but the HARNESS pushes, and nothing
    checked what it was handed. A wrong `claim.branch` (a corrupt claim file, a
    reconcile bug) would have pushed whatever that named, with the credential
    that has Contents write.

    GitHub-side branch protection cannot cover this. The daemon authenticates
    as Matt — a fine-grained PAT acts as its owner — so any rule strong enough
    to stop the harness pushing `main` also stops Matt pushing `main`, which is
    rule 1 of sample's own CLAUDE.md. There is no actor to distinguish. So the
    check belongs here, where the two are actually different code paths.
    """
    protected = {"main", "master", "trunk"}
    if base:
        protected.add(base)
    if branch in protected:
        raise RefusedPush(
            f"refusing to push {branch!r}: that is a base branch, not a task branch"
        )
    try:
        _run(["git", "push", "-q", "origin", branch], cwd=worktree)
    except RuntimeError as exc:
        if _WORKFLOW_REFUSAL in str(exc):
            # Not an infrastructure fault, and not something to fix by granting
            # the scope: withholding Workflows from the PAT is WHY CI is a
            # genuinely held-out verifier. An agent physically cannot edit the
            # thing that judges its own work, so the guarantee rests on the
            # credential rather than on the agent choosing to respect it.
            raise WorkflowScopeRefusal(str(exc)) from exc
        raise


def open_pr(worktree: Path, repo: str, branch: str, base: str, title: str, body: str) -> str:
    out = _run(
        [
            "gh", "pr", "create",
            "--repo", repo,
            "--base", base,
            "--head", branch,
            "--title", title,
            "--body", body,
        ],
        cwd=worktree,
    )
    return out.strip().splitlines()[-1] if out.strip() else ""


def branch_tip_time(worktree: Path) -> str | None:
    """The tip commit's timestamp, ISO-8601 Zulu, or None if unreadable.

    The revise guard's "since" boundary: feedback that predates the last commit
    is what the branch was ALREADY built from, so only comments after it can be
    asking for a change.

    Forced to UTC and `iso-strict` so it sorts as a string against GitHub's
    Zulu timestamps — see `queue._newer_than`, which compares them as text
    rather than parsing two formats to reach the same answer.
    """
    try:
        out = _run(
            ["git", "log", "-1", "--date=iso-strict", "--format=%cd"], cwd=worktree
        )
    except (RuntimeError, OSError):
        # OSError too: a `cwd` that does not exist raises FileNotFoundError
        # before git is ever spawned. Same pairing, same reason, as
        # `daemon._branch_to_delete`.
        return None
    stamp = out.strip()
    if not stamp:
        return None
    # `%cd` with iso-strict renders the committer's offset (`+02:00`). GitHub
    # is always `Z`, and "2026-08-06T09:00:00+02:00" > "2026-08-06T08:30:00Z"
    # is the wrong answer as text even though it is the right one in time.
    try:
        return (
            datetime.fromisoformat(stamp)
            .astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    except ValueError:
        return None


def branch_on_remote(repo_dir: Path, branch: str) -> bool:
    """Did the branch reach origin? Asks the remote, not a local ref."""
    try:
        out = _run(["git", "ls-remote", "--heads", "origin", branch], cwd=repo_dir)
    except RuntimeError:
        return False
    return bool(out.strip())


def pr_for_branch(repo: str, branch: str, state: str = "all") -> str | None:
    """The PR URL for a branch, or None. Used to avoid opening a second one.

    `state` defaults to `all` because recovery's question is "did this branch
    ever get a PR" — a closed one still means the run got that far. The ship
    path asks a different question and passes `open`: a CLOSED PR must not
    suppress opening a new one, which is exactly what a revise produces when
    the human closed the PR before re-labelling.
    """
    try:
        out = _run(
            [
                "gh", "pr", "list",
                "--repo", repo,
                "--head", branch,
                "--state", state,
                "--json", "url",
                "--limit", "1",
            ]
        )
    except RuntimeError:
        return None

    found = json.loads(out or "[]")
    return found[0]["url"] if found else None

"""A worker must be able to remove a tracked file.

Observed 2026-08-06 on sample #9. The task moved a module into a package and
its final step was deleting the husk left behind — ten lines nothing imported.
The worker could not do it, and neither could the two sessions before it:
`git rm`, `git rm --cached`, `git update-index --force-remove`, a bare `rm`,
`mv`, and a Python `os.remove` were all refused between them. Three sessions,
at least eight strategies, zero successes, one escalation, and a human ran a
single command.

`--permission-mode acceptEdits` does not cover Bash mutations and headless has
no prompt to resolve, so an unlisted command is a hard deny — the deletion
verbs were simply never listed. That is the same class of gap as the original
"a worker cannot `git commit`" finding, and it fails the same way: the whole
task gets done and then cannot be finished.

Only the git forms are permitted. `git rm` touches files git already tracks and
every removal it makes is recoverable from the index, so the blast radius is
the branch's own history. A bare `rm` is a different risk and stays out.

That fix was necessary and NOT sufficient, and sample #23 proved it the same
day. The file that task could not delete was UNTRACKED — a scratch test it had
written into the worktree — so `git rm` did not apply. It had finished the
work, committed it, and swept green; it then spent its remaining turns on `rm`,
`rm --`, `mv` to /tmp, `git clean -fn` and `git clean -f`, truncated at turn
101, and the PR was never opened.

Two separate gates refused it, and reading the two messages apart is the whole
lesson:

- `git clean` → "This command requires approval". That is the ALLOW-LIST, and
  a permission entry fixes it.
- `rm`/`mv` → "Claude Code may only remove files from the allowed working
  directories for this session". That is a FILESYSTEM guard, it fires on paths
  outside the session's directories, and no permission entry can grant it.

The second is why `Bash(rm /tmp/*)` never worked: /tmp is outside the worktree,
so AGENTS.md's "put scratch in /tmp" was an instruction workers could not
follow. `--add-dir /tmp` is what makes it true.
"""

from __future__ import annotations

from nightshift import worker


class FakePopen:
    """Stands in for `claude -p`, which is now streamed rather than captured.

    `stdout` must be a line ITERABLE: `_run` reads it line by line so a reader
    can watch the transcript arrive, which is the whole point of the change.
    """

    def __init__(self, lines=(), returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self):
        return self.returncode


def test_a_worker_may_remove_a_tracked_file():
    assert "Bash(git rm:*)" in worker._IMPLEMENT_ALLOWED


def test_a_worker_may_rename_a_tracked_file():
    """`mv` was refused too; `git mv` is the tracked-file equivalent."""
    assert "Bash(git mv:*)" in worker._IMPLEMENT_ALLOWED


def test_a_worker_still_cannot_rm_outside_tmp():
    """The permission is deliberately narrow: git-tracked files, not the disk.

    If a bare `rm` glob ever appears here for anything but /tmp, that is a
    widening nobody asked for — scratch belongs in /tmp and AGENTS.md says so.
    """
    bare = [f for f in worker._IMPLEMENT_ALLOWED if f.startswith("Bash(rm")]
    assert bare == ["Bash(rm /tmp/*)"]


def test_the_reviewer_cannot_delete_anything():
    """Read-only is the reviewer's whole contract."""
    assert "Bash(git rm:*)" not in worker._REVIEW_ALLOWED
    assert "Bash(git mv:*)" not in worker._REVIEW_ALLOWED
    assert "Bash(git rm:*)" in worker._REVIEW_DENIED
    assert "Bash(git mv:*)" in worker._REVIEW_DENIED


def test_a_worker_may_remove_an_untracked_file():
    """`git rm` does not cover scratch, which is exactly what gets left behind."""
    assert "Bash(git clean:*)" in worker._IMPLEMENT_ALLOWED


def test_the_reviewer_cannot_clean():
    assert "Bash(git clean:*)" not in worker._REVIEW_ALLOWED


def test_scratch_dir_is_actually_reachable(monkeypatch):
    """The allow-list is not the only gate — /tmp needs `--add-dir` too.

    Asserted against the real argv rather than the constant, because the
    constant existing while the flag is unpassed is precisely the state that
    cost sample #23: `Bash(rm /tmp/*)` sat in the allow-list looking like a
    granted permission for days.
    """
    seen: dict[str, list[str]] = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        return FakePopen()

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    worker.implement(worker.Path("/nowhere"), "p")

    argv = seen["argv"]
    assert "--add-dir" in argv
    assert argv[argv.index("--add-dir") + 1] == worker.SCRATCH_DIR

    # Variadic: anything between --add-dir and the next flag is swallowed into
    # it, so the directory must be the ONLY thing there.
    assert argv[argv.index("--add-dir") + 2].startswith("--")


def test_deletion_did_not_arrive_with_a_push():
    """A regression guard on the thing that must never be granted.

    These lists are edited by hand and this file is where someone widening
    them will look, so the invariant that matters most is restated here.
    """
    assert "Bash(git push:*)" in worker._DENIED
    assert not any("push" in f for f in worker._IMPLEMENT_ALLOWED)

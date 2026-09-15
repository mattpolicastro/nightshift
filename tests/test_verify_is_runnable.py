"""A worker must be able to run its own repo's verify command.

Observed 2026-08-07 on swift-app #1, the first task in the first non-JS
repo enrolled here. The worker read the issue and the requirements, arrived at
a correct implementation plan, and then could not run `swift` at all — not the
bare name, not `/usr/bin/swift`, not via `sh -c`, not from a subagent, and not
with `dangerouslyDisableSandbox` (which is gated the same way). Plain reads and
`git add` worked in the same session, so this was not a Bash outage; `swift`
was simply never listed.

`_IMPLEMENT_ALLOWED` was written for sample and every build verb in it is JS —
`pnpm`, `npm`, `node`. swift-app was enrolled with
`verify = "swift build && swift test && swift format lint ..."` and nothing
checked that the enrolled verify command and the hand-kept allow-list agreed.
Enrolling a repo is an edit to one file, granting a toolchain is an edit to
another, and the two were joined only by whoever remembered.

The worker's response was the correct one — it escalated rather than commit
code it had never built, on the biometrics module every downstream gate reads
from, which is exactly the plausible-but-wrong outcome the harness exists to
prevent. The bug is that the gap was invisible until a run was spent finding
it, and that the escalation reads as task ambiguity until you check the config.

So the fix is two-sided: grant `swift`, and make the mismatch a preflight
failure so the NEXT language enrolled here fails loudly at the check the README
already tells you to run before reloading, instead of quietly on a worker.
"""

from __future__ import annotations

import pytest

from nightshift import config, preflight, worker
from nightshift.config import Config, Repo


def test_a_worker_may_run_the_swift_toolchain():
    assert "Bash(swift:*)" in worker._IMPLEMENT_ALLOWED


def test_the_reviewer_may_rerun_the_verify_command_too():
    """A reviewer that cannot re-run verify can only take the claim on trust."""
    assert "Bash(swift:*)" in worker._REVIEW_ALLOWED


def test_every_enrolled_repos_verify_command_is_runnable():
    """The guard that generalises. Reads the REAL config, not a fixture.

    This is the assertion that would have caught the original gap at the point
    the repo was enrolled rather than one wasted run later.

    `config.toml` is gitignored, so on a machine that has not configured one —
    CI, or a fresh clone — there are no enrolled repos and nothing to guard.
    Skipping is right: the other tests here cover `unrunnable_verify_clauses`
    against fixtures, and this one is specifically about *your* enrollment.
    """
    if not config.DEFAULT_PATH.exists():
        pytest.skip("no config.toml — nothing is enrolled on this machine")
    cfg = config.load()
    for repo in cfg.repos:
        assert worker.unrunnable_verify_clauses(repo.verify) == [], (
            f"{repo.name}'s verify command has clauses its workers cannot run"
        )


def test_each_clause_is_checked_not_just_the_first():
    """`swift build` being granted says nothing about `swift format`.

    A verb-level grant happens to cover both, but the check must not depend on
    that — a repo whose verify ends in a tool nothing granted is the same bug.
    """
    blocked = worker.unrunnable_verify_clauses(
        "pnpm test && cargo clippy", allowed=["Bash(pnpm:*)"]
    )
    assert blocked == ["cargo clippy"]


def test_a_prefix_grant_does_not_leak_across_words():
    """`Bash(git rm:*)` must not be read as permission for `git rmdir-ish`.

    Prefix matching is on whole words, so a longer verb that merely starts with
    a granted one is still blocked.
    """
    assert worker.unrunnable_verify_clauses(
        "swiftlint --strict", allowed=["Bash(swift:*)"]
    ) == ["swiftlint --strict"]


def test_permission_does_not_prove_complete_verification():
    """Allowed partial command requests cannot satisfy the complete chain."""
    from nightshift import task, trace

    verify = "swift build && swift test && swift format lint --strict Sources"
    assert worker.unrunnable_verify_clauses(verify) == []

    ran = trace.Result(
        ok=True, stop_reason=None, turns=1, duration_s=1.0, cost_usd=0.0,
        output_tokens=0, cache_read_tokens=0, text="",
        commands=["swift build", "swift test 2>&1 | tail -20"],
    )
    assert not task.ran_verification(ran, verify)


def test_preflight_reports_an_unrunnable_verify_command():
    """The check has to FAIL on a bad config, not just pass on a good one."""
    cfg = Config(
        repos=[Repo(name="owner/rustish", verify="cargo test", base="main")],
        forbidden_probe="",
    )
    checks = preflight.run(cfg, {}, runner=lambda args: "{}")
    verify_checks = [c for c in checks if c.name.startswith("verify runnable")]

    assert len(verify_checks) == 1
    assert not verify_checks[0].ok
    assert "cargo test" in verify_checks[0].detail

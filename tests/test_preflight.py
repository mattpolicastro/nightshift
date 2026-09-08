"""What must be true before the daemon is left alone overnight.

The check that carries its weight is the negative control. A token that can
read the sandbox has proven nothing about what else it can read — the same
shape as the vault's auth finding, where `claude -p` succeeded on a bogus
token because an interactive login was quietly answering instead.
"""

from __future__ import annotations

import pytest

from nightshift import preflight
from nightshift.config import Config, Repo

SANDBOX = "matt/sandbox"
OFF_LIMITS = "matt/dotfiles"


def cfg(**kw) -> Config:
    # A REAL verify command, not a `true` placeholder: preflight now also
    # checks that workers are permitted to run what a repo enrolls, so a dummy
    # verb would fail the all-checks-pass case for an unrelated reason.
    return Config(repos=[Repo(name=SANDBOX, verify="pnpm test")], **kw)


def reader(readable: set[str]):
    def runner(args):
        if args[:3] == ["gh", "repo", "view"] and args[3] in readable:
            return '{"name":"x"}'
        raise RuntimeError("not found")

    return runner


def find(checks, fragment: str) -> preflight.Check:
    for c in checks:
        if fragment in c.name:
            return c
    raise AssertionError(f"no check matching {fragment!r} in {[c.name for c in checks]}")


def test_a_gho_token_is_flagged_however_well_it_works():
    """`gh auth login`'s credential reaches every repo the account owns."""
    checks = preflight.run(
        cfg(), {"GH_TOKEN": "gho_abc"}, runner=reader({SANDBOX})
    )
    assert find(checks, "GH_TOKEN scope").ok is False


def test_a_fine_grained_pat_passes_the_shape_check():
    checks = preflight.run(
        cfg(), {"GH_TOKEN": "github_pat_abc"}, runner=reader({SANDBOX})
    )
    assert find(checks, "GH_TOKEN scope").ok is True


def test_an_unreachable_enrolled_repo_fails():
    checks = preflight.run(cfg(), {"GH_TOKEN": "github_pat_x"}, runner=reader(set()))
    assert find(checks, f"reach {SANDBOX}").ok is False


def test_a_token_that_reaches_the_forbidden_repo_fails():
    """The whole point: over-scoping is invisible until something reads it."""
    checks = preflight.run(
        cfg(forbidden_probe=OFF_LIMITS),
        {"GH_TOKEN": "github_pat_x"},
        runner=reader({SANDBOX, OFF_LIMITS}),
    )
    assert find(checks, "cannot reach").ok is False


def test_a_properly_scoped_token_passes_both_directions():
    checks = preflight.run(
        cfg(forbidden_probe=OFF_LIMITS, slack_webhook="https://hooks/x"),
        {"GH_TOKEN": "github_pat_x", "CLAUDE_CODE_OAUTH_TOKEN": "t"},
        runner=reader({SANDBOX}),
    )
    assert [c for c in checks if not c.ok] == []


def test_no_forbidden_probe_is_itself_a_failure():
    """Asserted scoping is not proven scoping."""
    checks = preflight.run(
        cfg(slack_webhook="https://hooks/x"),
        {"GH_TOKEN": "github_pat_x", "CLAUDE_CODE_OAUTH_TOKEN": "t"},
        runner=reader({SANDBOX}),
    )
    assert find(checks, "negative control").ok is False


def test_an_inherited_api_key_is_reported():
    """It outranks the OAuth token and moves workers onto metered billing."""
    checks = preflight.run(
        cfg(), {"GH_TOKEN": "github_pat_x", "ANTHROPIC_API_KEY": "sk-x"},
        runner=reader({SANDBOX}),
    )
    assert find(checks, "ANTHROPIC_API_KEY").ok is False


def test_base_url_is_reported_too():
    """Would silently redirect workers at Ollama."""
    checks = preflight.run(
        cfg(), {"GH_TOKEN": "github_pat_x", "ANTHROPIC_BASE_URL": "http://evo:11434"},
        runner=reader({SANDBOX}),
    )
    assert find(checks, "ANTHROPIC_BASE_URL").ok is False


def test_unconfigured_alerting_fails_rather_than_passing_quietly():
    checks = preflight.run(
        cfg(forbidden_probe=OFF_LIMITS),
        {"GH_TOKEN": "github_pat_x", "CLAUDE_CODE_OAUTH_TOKEN": "t"},
        runner=reader({SANDBOX}),
    )
    assert find(checks, "alerts").ok is False


@pytest.mark.parametrize("token", ["", "ghp_classic"])
def test_a_missing_or_classic_token_still_reports_presence(token):
    checks = preflight.run(cfg(), {"GH_TOKEN": token}, runner=reader({SANDBOX}))
    assert find(checks, "GH_TOKEN").ok is bool(token)

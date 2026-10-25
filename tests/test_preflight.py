"""What must be true before the daemon is left alone overnight.

The check that carries its weight is the negative control. A token that can
read the sandbox has proven nothing about what else it can read — the same
shape as an earlier auth finding, where `claude -p` succeeded on a bogus
token because an interactive login was quietly answering instead.
"""

from __future__ import annotations

import os
import subprocess
import urllib.request

import pytest

from manifest_fixtures import manifest_text
from nightshift import cli, config, preflight
from nightshift.config import Config, Repo

SANDBOX = "matt/sandbox"
OFF_LIMITS = "matt/dotfiles"


def cfg(**kw) -> Config:
    # Legacy verify is no longer checked against worker permissions.
    return Config(repos=[Repo(name=SANDBOX, verify="retired-unrunnable")], **kw)


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
        {"GH_TOKEN": "github_pat_x"},
        runner=reader({SANDBOX}),
    )
    assert [c for c in checks if not c.ok] == []


def test_no_forbidden_probe_is_itself_a_failure():
    """Asserted scoping is not proven scoping."""
    checks = preflight.run(
        cfg(slack_webhook="https://hooks/x"),
        {"GH_TOKEN": "github_pat_x"},
        runner=reader({SANDBOX}),
    )
    assert find(checks, "negative control").ok is False


@pytest.mark.parametrize("variable", [
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
])
def test_retired_worker_environment_does_not_affect_preflight(variable):
    configuration = cfg(forbidden_probe=OFF_LIMITS, slack_webhook="https://hooks/x")
    env = {"GH_TOKEN": "github_pat_x"}
    baseline = preflight.run(configuration, env, runner=reader({SANDBOX}))
    checks = preflight.run(
        configuration, {**env, variable: "unused-worker-value"},
        runner=reader({SANDBOX}),
    )
    assert checks == baseline and all(check.ok for check in checks)


def test_unconfigured_alerting_fails_rather_than_passing_quietly():
    checks = preflight.run(
        cfg(forbidden_probe=OFF_LIMITS),
        {"GH_TOKEN": "github_pat_x"},
        runner=reader({SANDBOX}),
    )
    assert find(checks, "alerts").ok is False


@pytest.mark.parametrize("token", ["", "ghp_classic"])
def test_a_missing_or_classic_token_still_reports_presence(token):
    checks = preflight.run(cfg(), {"GH_TOKEN": token}, runner=reader({SANDBOX}))
    assert find(checks, "GH_TOKEN").ok is bool(token)


@pytest.mark.scenario("worker-retirement/service-prerequisites-match-the-surviving-engine/codex-enrollment-needs-no-worker-login")
@pytest.mark.parametrize("deep", [False, True])
@pytest.mark.parametrize("failure", [None, "missing-token", "scope", "reach", "negative-control", "alerts"])
def test_cli_preflight_needs_only_front_door_prerequisites(tmp_path, monkeypatch, capsys, deep, failure):
    manifest = tmp_path / "repository.toml"
    manifest.write_text(manifest_text(tmp_path / "source"))
    selected = tmp_path / "config.toml"
    selected.write_text(f'''[daemon]
forbidden_probe = "{OFF_LIMITS}"
[notify]
slack_webhook = "{'' if failure == 'alerts' else 'https://hooks/x'}"
[[repos]]
name = "{SANDBOX}"
manifest = "repository.toml"
verify = "retired-unrunnable"
[repos.models]
implement = "unused:retired"
[[endpoints]]
name = "unused"
base_url = "http://unused.invalid"
models = ["retired"]
''')
    token = "" if failure == "missing-token" else "gho_bad" if failure == "scope" else "github_pat_x"
    env_file = tmp_path / "env"
    env_file.write_text(f"export GH_TOKEN='{token}'\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setattr(os, "environ", {"HOME": str(tmp_path), "PATH": ""})
    calls = []

    def run(args, **kwargs):
        assert args[:3] == ["gh", "repo", "view"]
        assert args[4:] == ["--json", "name"]
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        assert os.environ["GH_TOKEN"] == token
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
        calls.append(args[3])
        readable = {SANDBOX} if failure != "reach" else set()
        if failure == "negative-control":
            readable.add(OFF_LIMITS)
        return subprocess.CompletedProcess(args, 0 if args[3] in readable else 1, '{"name":"x"}', "not found")

    def no_probe(*args, **kwargs):
        pytest.fail("preflight launched a worker/model probe")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", no_probe)
    monkeypatch.setattr(urllib.request, "urlopen", no_probe)
    assert cli.main(["preflight", "--config", str(selected), *(["--deep"] if deep else [])]) == int(failure is not None)
    output = capsys.readouterr()
    assert calls == [SANDBOX, OFF_LIMITS]
    assert output.err == ""
    assert ("worker probes are retired" in output.out) == deep
    assert "verify runnable" not in output.out and "CLAUDE_CODE_OAUTH_TOKEN" not in output.out
    assert ("checks failed" in output.out) == (failure is not None)
    if failure is None:
        assert output.out.endswith("\nready\n")
    else:
        failed_check = {
            "missing-token": "GH_TOKEN",
            "scope": "GH_TOKEN scope",
            "reach": f"reach {SANDBOX}",
            "negative-control": f"cannot reach {OFF_LIMITS}",
            "alerts": "alerts",
        }[failure]
        assert f"FAIL  {failed_check}:" in output.out


def test_deep_help_describes_the_retired_probe(capsys):
    with pytest.raises(SystemExit) as help_exit:
        cli.main(["--help"])
    assert help_exit.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--deep" in help_text and "worker probes are retired" in help_text

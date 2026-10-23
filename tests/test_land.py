"""Scripted, offline landing tests: unexpected calls (including mutations) fail."""

from collections import deque
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from nightshift import land


REPO = "owner/project"
URL = f"https://github.com/{REPO}/pull/78"
HEAD = "a" * 40
BASE = "b" * 40
VIEW = ["gh", "pr", "view", "78", "--repo", REPO, "--json",
        "number,url,state,isDraft,headRefOid,baseRefName"]
MAIN = ["gh", "api", f"repos/{REPO}/git/ref/heads/main"]
PR = {"number": 78, "url": URL, "state": "OPEN", "isDraft": False,
      "headRefOid": HEAD, "baseRefName": "main"}
MAIN_REF = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": BASE}}


class ScriptedRunner:
    """Expect exact argv/order and subprocess options, with no live fallback.

    Responses may be JSON values, raw CompletedProcess results, or exceptions.
    This also models reads failing without accidentally retrying a mutation.
    """

    def __init__(self):
        self.steps = deque()
        self.calls = []

    def expect(self, argv, response, *, timeout=30):
        self.steps.append((argv, response, timeout))
        return self

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        assert self.steps, f"unexpected external operation: {argv}"
        expected, response, timeout = self.steps.popleft()
        assert isinstance(argv, list)
        assert argv == expected
        assert kwargs == {"capture_output": True, "text": True, "check": False,
                          "timeout": timeout}
        if isinstance(response, Exception):
            raise response
        if isinstance(response, subprocess.CompletedProcess):
            return response
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")


@pytest.fixture
def scripted_runner():
    runner = ScriptedRunner()
    yield runner
    assert not runner.steps, f"unconsumed commands: {list(runner.steps)}"


@pytest.fixture(autouse=True)
def no_live_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("external operations must use the scripted runner")

    monkeypatch.setattr(subprocess, "run", forbidden)


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[[repos]]\nname = "owner/project"\nverify = "true"\n')
    return path


def invoke(config_path, runner, *argv):
    def no_wait(*args):
        pytest.fail("identity validation must not wait")

    return land.main([*argv, "--config", str(config_path)], runner=runner,
                     sleep=no_wait, monotonic=lambda: 100.0)


@pytest.mark.scenario("reviewed-pr-landing/operator-entry-point/explicit-invocation")
def test_parser_defaults_and_optional_arguments():
    defaults = land.parser().parse_args([URL])
    assert defaults.pr == URL
    assert defaults.repo is None
    assert defaults.config == land.config.DEFAULT_PATH
    assert defaults.timeout == 1800
    assert defaults.checks_done is False
    options = land.parser().parse_args([
        "78", "--repo", REPO, "--config", "custom.toml", "--checks-done", "--timeout", "0.5",
    ])
    assert options.pr == "78"
    assert options.repo == REPO
    assert options.config == Path("custom.toml")
    assert options.timeout == 0.5
    assert options.checks_done is True


@pytest.mark.scenario("reviewed-pr-landing/operator-entry-point/explicit-invocation")
@pytest.mark.parametrize("argv", [
    [], [URL, "--unknown"], [URL, "--repo"], [URL, "--checks"],
    *[[URL, "--timeout", value] for value in ["0", "-1", "nan", "inf", "1e999", "oops"]],
])
def test_invalid_arguments_exit_two(argv, scripted_runner, capsys):
    with pytest.raises(SystemExit) as error:
        land.main(argv, runner=scripted_runner)
    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err
    assert scripted_runner.calls == []


@pytest.mark.scenario("reviewed-pr-landing/operator-entry-point/explicit-invocation")
def test_module_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["nightshift.land", "--help"])
    monkeypatch.delitem(sys.modules, "nightshift.land")
    with pytest.raises(SystemExit) as error:
        runpy.run_module("nightshift.land", run_name="__main__")
    assert error.value.code == 0
    output = capsys.readouterr()
    for flag in ["--repo", "--config", "--checks-done", "--timeout"]:
        assert flag in output.out
    assert output.err == ""


@pytest.mark.scenario("reviewed-pr-landing/operator-entry-point/explicit-invocation")
@pytest.mark.parametrize("checks_done", [False, True])
def test_default_subprocess_runner_and_config(
    config_path, scripted_runner, monkeypatch, capsys, checks_done,
):
    monkeypatch.setattr(land.config, "DEFAULT_PATH", config_path)
    monkeypatch.setattr(subprocess, "run", scripted_runner)
    monkeypatch.setattr(sys, "argv", ["nightshift.land", URL, "--timeout", "20",
                                     *(["--checks-done"] if checks_done else [])])
    scripted_runner.expect(VIEW, PR).expect(MAIN, MAIN_REF)
    assert land.main() == 1
    output = capsys.readouterr()
    assert "mutations disabled" in output.err
    assert output.out == ""


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/repository-resolves")
@pytest.mark.parametrize("argv, inferred", [
    ([URL], False), (["78", "--repo", REPO], False), (["78"], True),
    ([URL, "--repo", "OWNER/PROJECT"], False),
    (["https://github.com/Owner/Project/pull/078/"], False),
])
def test_repository_resolves(config_path, scripted_runner, capsys, argv, inferred):
    # Enrollment matching and all subsequent commands are case-insensitive and explicit.
    config_path.write_text('[[repos]]\nname = "Owner/Project"\nverify = "true"\n')
    if inferred:
        scripted_runner.expect(["gh", "repo", "view", "--json", "nameWithOwner"],
                               {"nameWithOwner": "Owner/Project"})
    scripted_runner.expect(VIEW, PR).expect(MAIN, MAIN_REF)
    assert invoke(config_path, scripted_runner, *argv) == 1
    assert f"{URL}: mutations disabled" in capsys.readouterr().err


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("argv, reason", [
    (["0", "--repo", REPO], "positive PR number"),
    (["-1", "--repo", REPO], "invalid PR identity"),
    (["1.5", "--repo", REPO], "invalid PR identity"),
    (["１２", "--repo", REPO], "invalid PR identity"),
    (["http://github.com/owner/project/pull/78"], "invalid PR identity"),
    (["https://example.com/owner/project/pull/78"], "invalid PR identity"),
    (["https://github.com/owner/project/issues/78"], "invalid PR identity"),
    ([URL + "/files"], "invalid PR identity"),
    ([URL + "?repo=other/project"], "invalid PR identity"),
    ([URL + "#discussion"], "invalid PR identity"),
    (["https://github.com@evil.example/owner/project/pull/78"], "invalid PR identity"),
    ([URL, "--repo", "other/project"], "contradicts --repo"),
    (["78", "--repo", "github.com/owner/project"], "invalid repository identity"),
    (["78", "--repo", "owner/.."], "invalid repository identity"),
    (["78", "--repo", "owner/project; echo unsafe"], "invalid repository identity"),
])
def test_invalid_identity_refused(config_path, scripted_runner, capsys, argv, reason):
    assert invoke(config_path, scripted_runner, *argv, "--checks-done") == 1
    assert reason in capsys.readouterr().err
    assert scripted_runner.calls == []


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("text, reason", [
    ("", "not enrolled"),
    ('[[repos]]\nname = "other/project"\nverify = "true"\n', "not enrolled"),
    ('[[repos]]\nname = "owner/project"\nverify = "true"\n'
     '[[repos]]\nname = "OWNER/PROJECT"\nverify = "true"\n', "ambiguous enrollment"),
    ('[[repos]]\nname = 42\nverify = "true"\n', "invalid repository identity"),
    ('[[repos]]\nname = "owner/project"\n', "cannot read configuration"),
    ('repos = "wrong shape"', "cannot read configuration"),
    ('[invalid toml', "cannot read configuration"),
])
def test_enrollment_refused(config_path, scripted_runner, capsys, text, reason):
    config_path.write_text(text)
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert reason in capsys.readouterr().err
    assert scripted_runner.calls == []


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("problem", ["missing", "directory", "permission", "encoding"])
def test_unreadable_config(config_path, scripted_runner, capsys, monkeypatch, problem):
    if problem == "missing":
        config_path.unlink()
    elif problem == "directory":
        config_path.unlink()
        config_path.mkdir()
    elif problem == "encoding":
        config_path.write_bytes(b"\xff")
    else:
        original = Path.read_text

        def denied(path, *args, **kwargs):
            if path == config_path:
                raise PermissionError("permission denied")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
    assert invoke(config_path, scripted_runner, URL) == 1
    assert "cannot read configuration" in capsys.readouterr().err
    assert scripted_runner.calls == []


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("patch, reason", [
    ({"state": "CLOSED"}, "PR must be open"),
    ({"state": "MERGED"}, "PR must be open"),
    ({"state": None}, "PR must be open"),
    ({"baseRefName": "develop"}, "PR must target main"),
    ({"baseRefName": None}, "PR must target main"),
    ({"url": "https://github.com/other/project/pull/78"}, "contradictory PR identity"),
    ({"url": "https://github.com/owner/project/pull/79"}, "contradictory PR identity"),
    ({"url": None}, "invalid PR identity"),
    ({"number": 79}, "contradictory PR identity"),
    ({"number": "78"}, "contradictory PR identity"),
    ({"headRefOid": "abc"}, "malformed PR metadata"),
    ({"isDraft": "false"}, "malformed PR metadata"),
])
def test_invalid_remote_pr(config_path, scripted_runner, capsys, patch, reason):
    scripted_runner.expect(VIEW, {**PR, **patch})
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert reason in capsys.readouterr().err


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("response", [
    subprocess.CompletedProcess(MAIN, 1, "", "HTTP 404: Not Found"),
    {}, {"ref": "refs/heads/other", "object": MAIN_REF["object"]},
    {"ref": "refs/heads/main", "object": {"type": "tag", "sha": BASE}},
    {"ref": "refs/heads/main", "object": {"type": "commit", "sha": "short"}},
    {"ref": "refs/heads/main", "object": None},
])
def test_main_must_exist(config_path, scripted_runner, capsys, response):
    scripted_runner.expect(VIEW, PR).expect(MAIN, response)
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "main" in capsys.readouterr().err


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("response, reason", [
    ({}, "invalid repository identity"),
    ({"nameWithOwner": "github.com/owner/project"}, "invalid repository identity"),
    ([], "expected a JSON object"),
    (subprocess.CompletedProcess([], 1, "", "not a git repository"), "not a git repository"),
])
def test_repository_inference_fails_closed(config_path, scripted_runner, capsys, response, reason):
    scripted_runner.expect(["gh", "repo", "view", "--json", "nameWithOwner"], response)
    assert invoke(config_path, scripted_runner, "78") == 1
    assert reason in capsys.readouterr().err


@pytest.mark.scenario("reviewed-pr-landing/enrolled-pr-identity/invalid-target-refused")
@pytest.mark.parametrize("response, reason", [
    (subprocess.TimeoutExpired(VIEW, 30), "timed out"),
    (FileNotFoundError("gh executable missing"), "gh executable missing"),
    (subprocess.CompletedProcess(VIEW, 1, "", "authentication required"), "authentication required"),
    (subprocess.CompletedProcess(VIEW, 0, "not json", ""), "malformed JSON"),
    ([], "expected a JSON object"),
    (None, "expected a JSON object"),
])
def test_external_read_failures(config_path, scripted_runner, capsys, response, reason):
    scripted_runner.expect(VIEW, response)
    assert invoke(config_path, scripted_runner, URL) == 1
    output = capsys.readouterr()
    assert "PR read" in output.err and reason in output.err
    assert output.out == ""

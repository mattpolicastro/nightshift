"""Scripted, offline landing tests: unexpected calls (including mutations) fail."""

import ast
from collections import deque
from importlib.util import resolve_name
import json
from pathlib import Path
import re
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
STATUS = ["specloop", "status", "--json"]
EMPTY_STATUS = {"version": 1, "supervisor": {"state": "not running"}, "runs": []}
PR = {"number": 78, "url": URL, "state": "OPEN", "isDraft": False,
      "headRefOid": HEAD, "baseRefName": "main"}
MAIN_REF = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": BASE}}
RUN = {"change": "reviewed-change", "pr": URL, "head": HEAD,
       "status": "paused", "pause": {"reason": "human_checks"}}


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
    scripted_runner.expect(VIEW, PR).expect(MAIN, MAIN_REF).expect(STATUS, EMPTY_STATUS)
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
    scripted_runner.expect(VIEW, PR).expect(MAIN, MAIN_REF).expect(STATUS, EMPTY_STATUS)
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


@pytest.mark.scenario("reviewed-pr-landing/reliable-run-discovery/uncertain-discovery-refused")
@pytest.mark.parametrize("response, reason", [
    (subprocess.CompletedProcess(STATUS, 1, "", "cannot read runs"), "cannot read runs"),
    (subprocess.CompletedProcess(STATUS, 1, json.dumps(EMPTY_STATUS), "failed"), "exit 1"),
    (subprocess.CompletedProcess(STATUS, 0, "{broken", ""), "malformed JSON"),
    (subprocess.TimeoutExpired(STATUS, 30), "timed out"),
    (FileNotFoundError("specloop missing"), "specloop missing"),
    ([], "JSON object"), (None, "JSON object"),
    ({}, "version"),
    *[({**EMPTY_STATUS, "version": version}, "version")
      for version in [0, 2, "1", True, 1.0, None]],
    ({"version": 1}, "runs list"),
    *[({**EMPTY_STATUS, "runs": rows}, "runs list") for rows in [None, {}, ""]],
    ({**EMPTY_STATUS, "error": "collection failed"}, "collection failed"),
    ({**EMPTY_STATUS, "runs": [RUN, {**RUN, "change": "second"}]}, "multiple matching runs"),
    ({**EMPTY_STATUS, "runs": [RUN, RUN]}, "multiple matching runs"),
])
def test_uncertain_discovery_refused(config_path, scripted_runner, capsys, response, reason):
    scripted_runner.expect(VIEW, PR).expect(MAIN, MAIN_REF).expect(STATUS, response)
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    output = capsys.readouterr()
    assert "run discovery" in output.err and reason in output.err
    assert "mutations disabled" not in output.err
    assert output.out == ""
    assert [argv for argv, _ in scripted_runner.calls] == [VIEW, MAIN, STATUS]


@pytest.mark.scenario("reviewed-pr-landing/reliable-run-discovery/uncertain-discovery-refused")
@pytest.mark.parametrize("bad_row", [
    None, [], "run", {},
    *[{**RUN, key: value} for key, value in [
        ("change", ""), ("change", "  "), ("change", None), ("change", 42),
        ("pr", None), ("pr", 78), ("pr", "not a PR"), ("pr", URL + "/files"),
        ("head", None), ("head", "short"),
        ("status", None), ("status", []), ("status", "unknown"),
        ("pause", None), ("pause", "human_checks"), ("pause", {}),
        ("pause", {"reason": []}), ("pause", {"reason": ""}),
        ("pause", {"reason": "  "}),
    ]],
    *[{key: value for key, value in RUN.items() if key != missing}
      for missing in RUN],
    {**RUN, "pr": "", "head": "", "pause": {"reason": "status_error"},
     "error": "FileNotFoundError: unreadable run"},
    *[{**RUN, "error": value} for value in ["broken", "", None, False]],
])
@pytest.mark.parametrize("position", ["only", "before", "after"])
def test_every_status_row_must_be_readable(
    config_path, scripted_runner, capsys, bad_row, position,
):
    rows = {"only": [bad_row], "before": [bad_row, RUN], "after": [RUN, bad_row]}[position]
    scripted_runner.expect(VIEW, PR).expect(MAIN, MAIN_REF).expect(
        STATUS, {**EMPTY_STATUS, "runs": rows})
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    error = capsys.readouterr().err
    assert "run discovery: status row" in error
    assert "mutations disabled" not in error
    assert [argv for argv, _ in scripted_runner.calls] == [VIEW, MAIN, STATUS]


@pytest.mark.parametrize("engine", ["worker", "specloop"])
@pytest.mark.parametrize("matching", [False, True])
def test_discovery_uses_pr_identity_regardless_of_engine(
    config_path, scripted_runner, capsys, engine, matching,
):
    config_path.write_text(f'[[repos]]\nname = "{REPO}"\nverify = "true"\nengine = "{engine}"\n'
                          'manifest = "manifest.toml"\n')
    config_path.with_name("manifest.toml").write_text("")
    # Similar branches/change names and the same PR number in another repo
    # cannot identify a front-door PR. Unpublished runs have an empty URL.
    rows = [{**RUN, "pr": pr} for pr in [
        "", f"https://github.com/{REPO}/pull/780",
        "https://github.com/other/project/pull/78",
    ]]
    if matching:
        rows.append(RUN)
    scripted_runner.expect(STATUS, {**EMPTY_STATUS, "runs": rows})
    operations = land.Operations(scripted_runner, lambda _: None, lambda: 100.0)
    identity = land.Identity(REPO, 78)
    assert land.enrolled_repo(land.load_config(config_path), identity).engine == engine
    found = land.discover_run(identity, operations)
    if matching:
        assert found == land.LandingRun("reviewed-change", URL, HEAD, "paused", "human_checks")
    else:
        assert found is None
    scripted_runner.expect(VIEW, PR).expect(MAIN, MAIN_REF).expect(
        STATUS, {**EMPTY_STATUS, "runs": rows})
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/wrong-state-refused")
@pytest.mark.parametrize("status, pause", [
    ("running", None), ("aborted", None),
    ("running", {"reason": "human_checks"}), ("aborted", {"reason": "human_checks"}),
    *[("paused", {"reason": reason}) for reason in [
        "ready_to_publish", "requested", "infra", "usage_limit", "phase_gate",
        "needs_human", "status_error", "future_reason",
    ]],
])
@pytest.mark.parametrize("checks_done", [False, True])
def test_wrong_run_state_refused(
    config_path, scripted_runner, capsys, status, pause, checks_done,
):
    scripted_runner.expect(VIEW, PR).expect(MAIN, MAIN_REF).expect(
        STATUS, {**EMPTY_STATUS, "runs": [{**RUN, "status": status, "pause": pause}]})
    assert invoke(config_path, scripted_runner, URL,
                  *(["--checks-done"] if checks_done else [])) == 1
    error = capsys.readouterr().err
    assert f"run reviewed-change is {status}" in error
    if pause:
        assert pause["reason"] in error
    assert "retry land only when paused at human_checks or done" in error
    assert [argv for argv, _ in scripted_runner.calls] == [VIEW, MAIN, STATUS]


@pytest.mark.parametrize("status", ["paused", "done"])
@pytest.mark.parametrize("draft", [False, True])
def test_eligible_run_reaches_remaining_gates(config_path, scripted_runner, capsys, status, draft):
    row = {**RUN, "status": status, "pause": RUN["pause"] if status == "paused" else None,
           "head": "c" * 40}  # A done run's local head need not equal the remote head.
    scripted_runner.expect(VIEW, {**PR, "isDraft": draft}).expect(MAIN, MAIN_REF).expect(
        STATUS, {**EMPTY_STATUS, "runs": [row]})
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err


def test_direct_draft_refused_after_successful_discovery(config_path, scripted_runner, capsys):
    scripted_runner.expect(VIEW, {**PR, "isDraft": True}).expect(MAIN, MAIN_REF).expect(
        STATUS, EMPTY_STATUS)
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "direct PR is draft" in capsys.readouterr().err


def background_references(root, entry_points):
    """Follow local imports and literal module dispatch without executing them.

    Includes imports inside functions, package initializers, and subprocess
    module targets such as the supervisor's `python -m specloop.cli`.
    """
    pending = list(entry_points)
    visited, violations = set(), []
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        if module == "nightshift.land":
            violations.append(module)
            continue
        path = root.joinpath(*module.split(".")).with_suffix(".py")
        package = module.rpartition(".")[0]
        if not path.is_file():
            path = root.joinpath(*module.split("."), "__init__.py")
            package = module
        if not path.is_file():
            continue
        visited.add(module)
        if package and package != module:
            pending.append(package)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                target = "." * node.level + (node.module or "")
                target = resolve_name(target, package) if node.level else target
                pending.append(target)
                pending.extend(f"{target}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Literal dynamic imports and argv tokens, plus shell command
                # strings. A standalone land token also catches CLI dispatch maps.
                if (node.value == "land" or re.search(
                        r"\bnightshift(?:\.land\b|\s+land\b)", node.value)):
                    violations.append(f"{module}:{node.lineno}: {node.value}")
                if re.fullmatch(r"(?:nightshift|specloop)(?:\.[a-z_]+)+", node.value):
                    pending.append(node.value)
    return visited, violations


@pytest.mark.scenario("reviewed-pr-landing/operator-entry-point/background-paths-excluded")
def test_daemon_and_supervisor_never_import_land():
    root = Path(__file__).resolve().parents[1]
    visited, violations = background_references(root, ["nightshift.daemon", "specloop.serve"])
    assert {"nightshift.daemon", "nightshift.specloop_intake", "nightshift.specloop_job",
            "specloop.serve", "specloop.cli"} <= visited
    assert not violations, "background landing references: " + "; ".join(violations)


@pytest.mark.parametrize("reference", [
    "import nightshift.land as helper", "from nightshift import land as helper",
    "from .land import main", "from . import land",
    "importlib.import_module('nightshift.land')",
    "subprocess.run(['python', '-m', 'nightshift.land', '78'])",
    "subprocess.run('uv run python -m nightshift.land 78', shell=True)",
    "subprocess.run(['nightshift', 'land', '78'])",
])
def test_background_inspection_detects_indirect_landing(tmp_path, reference):
    package = tmp_path / "nightshift"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "daemon.py").write_text("from . import helper\n")
    (package / "helper.py").write_text(reference + "\n")
    _, violations = background_references(tmp_path, ["nightshift.daemon"])
    assert violations


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

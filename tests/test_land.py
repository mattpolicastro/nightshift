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
        "number,url,state,isDraft,headRefOid,baseRefName,body"]
MAIN = ["gh", "api", f"repos/{REPO}/git/ref/heads/main"]
COMMENTS = ["gh", "api", f"repos/{REPO}/issues/78/comments", "--paginate", "--slurp"]
STATUS = ["specloop", "status", "--json"]
ACTOR = ["gh", "api", "user"]
EMPTY_STATUS = {"version": 1, "supervisor": {"state": "not running"}, "runs": []}
PR = {"number": 78, "url": URL, "state": "OPEN", "isDraft": False,
      "headRefOid": HEAD, "baseRefName": "main", "body": ""}
MAIN_REF = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": BASE}}
RUN = {"change": "reviewed-change", "pr": URL, "head": HEAD,
       "status": "paused", "pause": {"reason": "human_checks"}}
FINISH = ["specloop", "finish", "reviewed-change"]
FINISHED_HEAD = "c" * 40


class ScriptedRunner:
    """Expect exact argv/order and subprocess options, with no live fallback.

    Responses may be JSON values, raw CompletedProcess results, or exceptions.
    Callbacks model remote writes, including writes whose response was lost.
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
        if callable(response):
            response = response()
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
    expect_finish_gate(scripted_runner)
    assert land.main() == 1
    output = capsys.readouterr()
    assert "mutations disabled" in output.err
    assert output.out == "No human checks listed\n"


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
    expect_finish_gate(scripted_runner)
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
    if matching:
        scripted_runner.expect(COMMENTS, [[]])
    expect_finish_gate(scripted_runner, comments=[[]] if matching else None)
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
    scripted_runner.expect(VIEW, {**PR, "body": "- [ ] Must not be edited"}).expect(MAIN, MAIN_REF).expect(
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
    scripted_runner.expect(COMMENTS, [[]])
    expect_finish_gate(scripted_runner, comments=[[]], row=row, draft=draft)
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    error = capsys.readouterr().err
    assert ("PR remains draft" if status == "done" and draft else "mutations disabled") in error


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


def publication(comment_id=1, *, checks="- [ ] Inspect the UI", change="reviewed-change",
                round_id="round-1", created_at="2026-10-22T12:00:00Z", **extra):
    suffix = "" if round_id is None else f":{round_id}"
    return {"id": comment_id, "created_at": created_at, "author_association": "COLLABORATOR",
            "body": f"**nightshift** — Human checks\n\n{checks}\n\n"
                    f"<!-- nightshift:publish:{change}{suffix} -->", **extra}


def expect_check_sources(runner, body, comments=None):
    runner.expect(VIEW, {**PR, "body": body}).expect(MAIN, MAIN_REF).expect(
        STATUS, {**EMPTY_STATUS, "runs": [] if comments is None else [RUN]})
    if comments is not None:
        runner.expect(COMMENTS, comments)


def expect_finish_gate(runner, body="", comments=None, *, row=None, head=HEAD, draft=False):
    """Explicitly script the new gate for existing checklist/identity tests."""
    if row is None and comments is not None:
        row = RUN
    runner.expect(STATUS, {**EMPTY_STATUS, "runs": [row] if row else []})
    runner.expect(VIEW, {**PR, "body": body, "headRefOid": head, "isDraft": draft})
    if row is not None:
        runner.expect(COMMENTS, comments if comments is not None else [[]])
        if row["status"] == "paused":
            runner.expect(FINISH, subprocess.CompletedProcess(FINISH, 0, "Finished\n", ""), timeout=300)
            runner.expect(STATUS, {**EMPTY_STATUS, "runs": [
                {**row, "status": "done", "pause": None, "head": FINISHED_HEAD}]})
            runner.expect(VIEW, {**PR, "body": body, "headRefOid": FINISHED_HEAD})


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/unticked-checks-refuse")
@pytest.mark.parametrize("source", ["body", "comment", "both", "direct"])
def test_land_refuses_unticked_human_checks(config_path, scripted_runner, capsys, source):
    body = "Introduction\r\n- [X] Already done\r\n"
    if source in {"body", "both", "direct"}:
        body += "- [ ] Check the body requirement\r\n"
    comment = publication(checks="- [x] Completed comment check")
    if source in {"comment", "both"}:
        comment = publication(checks="- [ ] Check the publication requirement")
    expect_check_sources(scripted_runner, body, None if source == "direct" else [[comment]])
    assert invoke(config_path, scripted_runner, URL) == 1
    output = capsys.readouterr()
    assert "unticked human checks remain" in output.err
    assert ("PR body: Check the body requirement" in output.err) == (source != "comment")
    assert ("publication comment 1: Check the publication requirement" in output.err) == (
        source in {"comment", "both"})
    assert "Already done" not in output.err and "Completed comment check" not in output.err
    assert "mutations disabled" not in output.err
    assert output.out == ""
    # The exact script allows only reads: no edits, finish, CI, or merge.
    assert len(scripted_runner.calls) == (3 if source == "direct" else 4)


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/publication-selection")
@pytest.mark.parametrize("legacy", [False, True])
def test_latest_publication_across_pages(config_path, scripted_runner, capsys, legacy):
    old = publication(900, round_id=None if not legacy else "previous",
                      created_at="2026-10-21T12:00:00Z", updated_at="2026-10-23T12:00:00Z")
    tied = publication(9, round_id="zzz")
    newest = publication(10, round_id=None if legacy else "aaa",
                         checks="Prose stays.\r\n- [x] Current round complete\r\n")
    unrelated = publication(999, change="another-change", created_at="2026-10-23T12:00:00Z")
    untrusted = publication(1000, author_association="CONTRIBUTOR",
                           created_at="2026-10-23T12:00:00Z")
    # Neither API order, round lexicographic order, updated_at, nor highest ID
    # alone establishes the current publication. Page one is a full REST page.
    pages = [[old] + [{"body": "Unrelated conversation"}] * 99,
             [newest, unrelated], [untrusted, tied]]
    original = json.dumps(pages)
    selected = land.select_publication([c for page in pages for c in page], RUN["change"])
    assert selected == land.CheckSource(newest["body"], 10)
    expect_check_sources(scripted_runner, "Body prose\r\n- [X] Body complete\r\n", pages)
    expect_finish_gate(scripted_runner, "Body prose\r\n- [X] Body complete\r\n", pages)
    assert invoke(config_path, scripted_runner, URL) == 1
    output = capsys.readouterr()
    assert "mutations disabled" in output.err  # The human-check gate passed.
    assert "unticked" not in output.err
    assert output.out == ""
    assert json.dumps(pages) == original


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/publication-selection")
@pytest.mark.parametrize("association, trusted", [
    ("OWNER", True), ("MEMBER", True), ("COLLABORATOR", True), ("collaborator", True),
    ("CONTRIBUTOR", False), ("FIRST_TIMER", False), ("FIRST_TIME_CONTRIBUTOR", False),
    ("NONE", False), ("MANNEQUIN", False), ("", False), (None, False), ([], False),
    ("missing", False),
])
def test_publication_trust_uses_production_policy(
    config_path, scripted_runner, capsys, association, trusted,
):
    comment = publication(author_association=association)
    if association == "missing":
        del comment["author_association"]
    expect_check_sources(scripted_runner, "", [[comment]])
    if not trusted:
        expect_finish_gate(scripted_runner, comments=[[comment]])
    assert invoke(config_path, scripted_runner, URL) == 1
    output = capsys.readouterr()
    assert ("publication comment 1: Inspect the UI" in output.err) == trusted
    assert ("mutations disabled" in output.err) == (not trusted)
    assert output.out == ("" if trusted else "No human checks listed\n")


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/publication-selection")
@pytest.mark.parametrize("body", [
    "Human checks\n- [ ] Spoof\n<!-- nightshift:publish:reviewed-change:r1 -->",
    " **nightshift** —\n- [ ] Spoof\n<!-- nightshift:publish:reviewed-change:r1 -->",
    "**nightshift** -\n- [ ] Spoof\n<!-- nightshift:publish:reviewed-change:r1 -->",
    *[f"**nightshift** —\n- [ ] Spoof\n{marker}" for marker in [
        "<!-- nightshift:publish:reviewed-change-extra:r1 -->",
        "<!-- nightshift:publish:reviewed-change:r1:extra -->",
        "<!-- nightshift:publish:reviewed-change: -->",
        "<!-- nightshift:publish:reviewed-change:two words -->",
        "<!-- nightshift:publish:reviewed-change:r<1 -->",
        "<!-- nightshift:revise:reviewed-change:r1 -->",
        "<!-- nightshift:publish:reviewed-change:r1-->",
        "<!--nightshift:publish:reviewed-change:r1 -->",
    ]],
])
def test_publication_requires_prefix_and_exact_marker(body):
    assert land.select_publication([publication(body=body)], RUN["change"]) is None


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/publication-selection")
def test_publication_matches_literal_change_and_creation_instant():
    literal = publication(3, change="reviewed.change", created_at="2026-10-22T11:00:00-02:00")
    newer_looking_clock = publication(4, change="reviewed.change",
                                     created_at="2026-10-22T12:00:00Z")
    lookalike = publication(5, change="reviewed-change", created_at="2026-10-23T12:00:00Z")
    assert land.select_publication([literal, newer_looking_clock, lookalike], "reviewed.change") == (
        land.CheckSource(literal["body"], 3))


@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/publication-selection")
def test_task_line_rule_preserves_prose_and_counts_all_containers():
    body = (
        "Before prose\r\n- [ ] Plain **text**\r\n"
        "```markdown\n\t* [ ] Fenced example\n```\n"
        "~~~\r\n+ [X] Other fence\r\n~~~\r\n"
        "> > - [ ] Nested quote\n"
        "<div>\n    - [x] HTML check\n</div>\n"
        "    + [ ] Indented code\n"
        "- [ ] \n"
        "Inline - [ ] prose\n- [ ]No following space\n- [y] Wrong tick\n"
        "1. [ ] Numbered\n-  [ ] Two spaces\n-\t[ ] Tab separator\n"
        "- [x]\n\u00a0- [ ] Non-rule indentation\n"
        "After prose and <!-- nightshift:publish:reviewed-change:round -->"
    )
    source = land.CheckSource(body, 17)
    assert source.checks == (
        land.HumanCheck("Plain **text**", False), land.HumanCheck("Fenced example", False),
        land.HumanCheck("Other fence", True), land.HumanCheck("Nested quote", False),
        land.HumanCheck("HTML check", True), land.HumanCheck("Indented code", False),
        land.HumanCheck("", False),
    )
    assert source.body == body
    assert source.comment_id == 17


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/unticked-checks-refuse")
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/publication-selection")
@pytest.mark.parametrize("container", ["```\n{}\n```", "~~~\n{}\n~~~", "> > {}", "<div>\n{}\n</div>"])
@pytest.mark.parametrize("source", ["body", "comment"])
def test_container_task_lines_block_landing(config_path, scripted_runner, capsys, container, source):
    text = container.format("* [ ] Count this example too")
    expect_check_sources(scripted_runner, text if source == "body" else "",
                         [[publication(checks=text if source == "comment" else "No checks")]])
    assert invoke(config_path, scripted_runner, URL) == 1
    error = capsys.readouterr().err
    assert "Count this example too" in error
    assert "unticked human checks remain" in error
    assert "mutations disabled" not in error


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/no-listed-checks")
@pytest.mark.parametrize("checks_done", [False, True])
@pytest.mark.parametrize("kind", ["direct", "no-comments", "unrelated", "empty-publication"])
@pytest.mark.parametrize("body", ["", "Only prose\r\nInline - [ ] example\r\n"])
def test_no_listed_checks_reaches_remaining_gates(
    config_path, scripted_runner, capsys, checks_done, kind, body,
):
    comments = {"direct": None, "no-comments": [[]],
                "unrelated": [[publication(change="different-change")]],
                "empty-publication": [[publication(checks="Publication prose only")]]}[kind]
    expect_check_sources(scripted_runner, body, comments)
    expect_finish_gate(scripted_runner, body, comments)
    assert invoke(config_path, scripted_runner, URL, *(["--checks-done"] if checks_done else [])) == 1
    output = capsys.readouterr()
    assert output.out == "No human checks listed\n"
    assert "mutations disabled" in output.err
    assert "unticked" not in output.err
    assert len(scripted_runner.calls) == (5 if kind == "direct" else 10)


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/unticked-checks-refuse")
@pytest.mark.parametrize("checks_done", [False, True])
def test_missing_publication_still_checks_body(config_path, scripted_runner, capsys, checks_done):
    expect_check_sources(scripted_runner, "- [ ] Body check remains", [[]])
    if checks_done:
        scripted_runner.expect(ACTOR, {"login": "operator"})
        scripted_runner.expect(check_patch("- [x] Body check remains"), {})
        expect_attestation(scripted_runner, [("PR body", ["Body check remains"])])
    if checks_done:
        expect_finish_gate(scripted_runner, "- [x] Body check remains", [[]])
    assert invoke(config_path, scripted_runner, URL, *(["--checks-done"] if checks_done else [])) == 1
    output = capsys.readouterr()
    assert ("PR body: Body check remains" in output.err) == (not checks_done)
    assert ("mutations disabled" in output.err) == checks_done
    assert output.out == ""


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/human-check-sources/publication-selection")
@pytest.mark.parametrize("response, reason", [
    (subprocess.CompletedProcess(COMMENTS, 1, "", "later page failed"), "later page failed"),
    (subprocess.CompletedProcess(COMMENTS, 0, "[]\n[]", ""), "malformed JSON"),
    (subprocess.TimeoutExpired(COMMENTS, 30), "timed out"),
    ({}, "JSON array"), ([{}], "comment page"), ([[None]], "comment body"),
    ([[publication()], [{"body": None}]], "comment body"),
    *[([[publication(**patch)]], "publication") for patch in [
        {"id": "1"}, {"id": True}, {"id": -1}, {"id": None},
        {"created_at": None}, {"created_at": "invalid"}, {"created_at": "2026-10-22"},
    ]],
])
def test_comment_discovery_fails_closed(config_path, scripted_runner, capsys, response, reason):
    expect_check_sources(scripted_runner, "", response)
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    output = capsys.readouterr()
    assert "publication comment discovery" in output.err and reason in output.err
    assert "mutations disabled" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("body", [None, [], 42])
def test_unreadable_pr_body_refuses(config_path, scripted_runner, capsys, body):
    scripted_runner.expect(VIEW, {**PR, "body": body})
    assert invoke(config_path, scripted_runner, URL) == 1
    assert "malformed PR body" in capsys.readouterr().err


def check_patch(body, comment_id=None):
    endpoint = (f"repos/{REPO}/pulls/78" if comment_id is None
                else f"repos/{REPO}/issues/comments/{comment_id}")
    return ["gh", "api", "--method", "PATCH", endpoint, "-f", f"body={body}"]


def attestation_body(sources, *, actor="operator", head=HEAD):
    body = (f"**nightshift** — Human checks\n\n@{actor} recorded completion through `land`.\n"
            f"PR: {URL}\nHead: `{head}`\n\n")
    for reference, items in sources:
        body += f"Source: {reference}\n" + "".join(f"- [x] {item}\n" for item in items) + "\n"
    return body + f"<!-- nightshift:land-checks:78:{head} -->"


def attestation(body, comment_id=501, **extra):
    return {"body": body, "id": comment_id, "author_association": "COLLABORATOR", **extra}


def attestation_post(body):
    return ["gh", "api", "--method", "POST", f"repos/{REPO}/issues/78/comments", "-f", f"body={body}"]


def expect_attestation(runner, sources, *, actor="operator", existing=None):
    body = attestation_body(sources, actor=actor)
    runner.expect(COMMENTS, [[]] if existing is None else [[existing]])
    runner.expect(attestation_post(body) if existing is None else check_patch(body, existing["id"]),
                  attestation(body))
    return attestation(body)


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/record-checks-in-place/tick-each-source-once")
@pytest.mark.parametrize("complete", ["neither", "body", "comment", "both"])
def test_checks_done_ticks_each_source_once_and_retains_snapshot(
    config_path, scripted_runner, capsys, monkeypatch, complete,
):
    body = ("Prose\r\n- [X] Keep uppercase\r\n- [x] Keep lowercase\n"
            "> * [ ] First\r\n```\n\t+ [ ] Second\n```\nInline - [ ] prose\n"
            "- [ ]No space\n- [y] Unrecognized\nEnd")
    ticked_body = body.replace("> * [ ]", "> * [x]").replace("\t+ [ ]", "\t+ [x]")
    comment = publication(42, checks="Prose\r\n- [X] Old\r\n- [ ] New\r\n+ [ ] More")
    ticked_comment = comment["body"].replace("- [ ] New", "- [x] New").replace(
        "+ [ ] More", "+ [x] More")
    if complete in {"body", "both"}:
        body = ticked_body
    if complete in {"comment", "both"}:
        comment["body"] = ticked_comment
    old = publication(1, created_at="2026-10-21T12:00:00Z")
    unrelated = publication(99, change="another-change")
    untrusted = publication(100, author_association="CONTRIBUTOR")
    pages = [[old, unrelated], [comment, untrusted]]
    original_pages = json.dumps(pages)
    snapshots = []
    prepare = land.prepare_human_checks

    def retain(*args, **kwargs):
        snapshot = prepare(*args, **kwargs)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(land, "prepare_human_checks", retain)
    expect_check_sources(scripted_runner, body, pages)
    scripted_runner.expect(ACTOR, {"login": "operator"})
    if complete not in {"body", "both"}:
        scripted_runner.expect(check_patch(ticked_body), {})
    if complete not in {"comment", "both"}:
        scripted_runner.expect(check_patch(ticked_comment, 42), {})
    check_items = [("PR body", ["Keep uppercase", "Keep lowercase", "First", "Second"]),
                   ("publication comment 42", ["Old", "New", "More"])]
    recorded = expect_attestation(scripted_runner, check_items)
    expect_finish_gate(scripted_runner, ticked_body, [[{**comment, "body": ticked_comment}]])
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err
    assert snapshots == [land.CheckSnapshot(HEAD, (
        land.CheckSource(ticked_body), land.CheckSource(ticked_comment, 42)), "operator")]
    assert json.dumps(pages) == original_pages

    # A retry reads completed remote sources and still identifies its actor for
    # the later attestation step, without writing any source a second time.
    expect_check_sources(scripted_runner, ticked_body, [[{**comment, "body": ticked_comment}]])
    scripted_runner.expect(ACTOR, {"login": "second-operator"})
    expect_attestation(scripted_runner, check_items, actor="second-operator", existing=recorded)
    expect_finish_gate(scripted_runner, ticked_body, [[{**comment, "body": ticked_comment}]])
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err
    assert snapshots[1].actor == "second-operator"
    assert snapshots[1].sources == snapshots[0].sources
    assert snapshots[1].head == HEAD


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/record-checks-in-place/partial-edits-recover")
@pytest.mark.parametrize("failed_stage", ["actor", "body", "comment"])
@pytest.mark.parametrize("failure", ["rejected", "timeout", "lost-response"])
def test_partial_check_edits_recover_from_remote_state(
    config_path, scripted_runner, capsys, failed_stage, failure,
):
    remote = {"body": "Before\r\n- [ ] First\r\n- [X] Existing\r\n",
              "comment": publication(42)["body"]}
    complete = {key: value.replace("- [ ]", "- [x]") for key, value in remote.items()}

    def edit(source):
        if source != failed_stage or failure == "lost-response":
            remote[source] = complete[source]
        if source == failed_stage:
            return error
        return {}

    error = (subprocess.CompletedProcess([], 1, "", "permission denied")
             if failure == "rejected" else subprocess.TimeoutExpired([], 30))
    expect_check_sources(scripted_runner, remote["body"], [[publication(42, body=remote["comment"])]])
    scripted_runner.expect(ACTOR, error if failed_stage == "actor" else {"login": "operator"})
    if failed_stage != "actor":
        scripted_runner.expect(check_patch(complete["body"]), lambda: edit("body"))
    if failed_stage == "comment":
        scripted_runner.expect(check_patch(complete["comment"], 42), lambda: edit("comment"))
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    reason = capsys.readouterr().err
    assert ("permission denied" if failure == "rejected" else "timed out") in reason
    assert ("authenticated actor lookup" if failed_stage == "actor" else
            "PR body" if failed_stage == "body" else "publication comment 42") in reason
    assert "mutations disabled" not in reason  # Stopped at the failed operation.
    assert not scripted_runner.steps
    if failed_stage == "comment":
        assert remote["body"] == complete["body"]

    # Build the next read from actual remote state, including writes applied
    # before a lost acknowledgement. Only unfinished sources may be patched.
    expect_check_sources(scripted_runner, remote["body"], [[publication(42, body=remote["comment"])]])
    scripted_runner.expect(ACTOR, {"login": "operator"})
    failed_stage = None
    for source, comment_id in [("body", None), ("comment", 42)]:
        if remote[source] != complete[source]:
            scripted_runner.expect(check_patch(complete[source], comment_id),
                                   lambda source=source: edit(source))
    expect_attestation(scripted_runner, [("PR body", ["First", "Existing"]),
                                        ("publication comment 42", ["Inspect the UI"])])
    expect_finish_gate(scripted_runner, complete["body"], [[publication(42, body=complete["comment"])]])
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err
    assert remote == complete


@pytest.mark.scenario("reviewed-pr-landing/record-checks-in-place/partial-edits-recover")
@pytest.mark.parametrize("response", [
    {}, {"login": None}, {"login": 3}, {"login": ""}, {"login": "  "}, {"login": "two users"},
    [], subprocess.CompletedProcess(ACTOR, 0, "not json", ""), FileNotFoundError("gh missing"),
])
def test_invalid_actor_refuses_before_check_writes(config_path, scripted_runner, capsys, response):
    expect_check_sources(scripted_runner, "- [ ] Check")
    scripted_runner.expect(ACTOR, response)
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "authenticated actor lookup" in capsys.readouterr().err
    assert [argv for argv, _ in scripted_runner.calls] == [VIEW, MAIN, STATUS, ACTOR]


@pytest.mark.scenario("reviewed-pr-landing/record-checks-in-place/tick-each-source-once")
@pytest.mark.parametrize("checks_done", [False, True])
def test_complete_direct_sources_only_lookup_actor_when_recording(
    config_path, scripted_runner, capsys, checks_done,
):
    expect_check_sources(scripted_runner, "- [X] Complete\r\n")
    if checks_done:
        scripted_runner.expect(ACTOR, {"login": "operator"})
        expect_attestation(scripted_runner, [("PR body", ["Complete"])])
    expect_finish_gate(scripted_runner, "- [X] Complete\r\n")
    assert invoke(config_path, scripted_runner, URL, *(["--checks-done"] if checks_done else [])) == 1
    assert "mutations disabled" in capsys.readouterr().err


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/repeated-attestation-updates")
def test_land_checks_done_ticks_once_and_comments(config_path, scripted_runner, capsys):
    body = "Intro\r\n- [ ] Body check\r\n- [X] Existing\r\n"
    comment = publication(42)
    completed_body = body.replace("[ ]", "[x]")
    completed_comment = {**comment, "body": comment["body"].replace("[ ]", "[x]")}
    sources = [("PR body", ["Body check", "Existing"]),
               ("publication comment 42", ["Inspect the UI"])]
    expect_check_sources(scripted_runner, body, [[comment]])
    scripted_runner.expect(ACTOR, {"login": "operator"})
    scripted_runner.expect(check_patch(completed_body), {})
    scripted_runner.expect(check_patch(completed_comment["body"], 42), {})
    existing = expect_attestation(scripted_runner, sources)
    expect_finish_gate(scripted_runner, completed_body, [[completed_comment]])
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err

    expect_check_sources(scripted_runner, completed_body, [[completed_comment, existing]])
    scripted_runner.expect(ACTOR, {"login": "next-operator"})
    updated = expect_attestation(scripted_runner, sources, actor="next-operator", existing=existing)
    expect_finish_gate(scripted_runner, completed_body, [[completed_comment, updated]])
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err
    assert "@next-operator recorded completion through `land`." in updated["body"]
    calls = [argv for argv, _ in scripted_runner.calls]
    assert calls.count(check_patch(completed_body)) == 1
    assert calls.count(check_patch(completed_comment["body"], 42)) == 1
    assert sum("POST" in argv for argv in calls) == 1
    assert calls[-7] == check_patch(updated["body"], existing["id"])


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/repeated-attestation-updates")
@pytest.mark.parametrize("marker_suffix", ["", ":1"])
@pytest.mark.parametrize("quoted_in", ["body", "publication"])
def test_attestation_quoted_markers_cannot_replace_publication(
    config_path, scripted_runner, capsys, marker_suffix, quoted_in,
):
    publish_marker = f"<!-- nightshift:publish:reviewed-change{marker_suffix} -->"
    land_marker = f"<!-- nightshift:land-checks:78:{HEAD} -->"
    quoted = f"Inspect {publish_marker} & <example>"
    # Literal marker text must remain readable without becoming metadata in
    # the copied attestation.
    escaped = (f"Inspect &lt;!-- nightshift:publish:reviewed-change{marker_suffix} --&gt;"
               " &amp; &lt;example&gt;")
    body = f"- [ ] {quoted}" if quoted_in == "body" else ""
    comment = publication(42, checks=f"- [ ] {quoted}" if quoted_in == "publication"
                          else "- [ ] Inspect the UI")
    completed_body = body.replace("[ ]", "[x]")
    completed_comment = {**comment, "body": comment["body"].replace("[ ]", "[x]")}
    sources = ([("PR body", [escaped])] if body else []) + [
        ("publication comment 42", [escaped if quoted_in == "publication" else "Inspect the UI"]),
    ]
    expect_check_sources(scripted_runner, body, [[comment]])
    scripted_runner.expect(ACTOR, {"login": "operator"})
    if body:
        scripted_runner.expect(check_patch(completed_body), {})
    scripted_runner.expect(check_patch(completed_comment["body"], 42), {})
    recorded = attestation(attestation_body(sources), created_at="2026-10-23T12:00:00Z")
    scripted_runner.expect(COMMENTS, [[completed_comment]])
    scripted_runner.expect(attestation_post(recorded["body"]), recorded)
    expect_finish_gate(scripted_runner, completed_body, [[completed_comment], [recorded]])
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err
    assert publish_marker not in recorded["body"]
    assert recorded["body"].count(land_marker) == 1

    # Discovery sees the newer, trusted attestation, but must keep selecting
    # the real publication and update only the existing attestation by ID.
    expect_check_sources(scripted_runner, completed_body, [[completed_comment], [recorded]])
    scripted_runner.expect(ACTOR, {"login": "operator"})
    scripted_runner.expect(COMMENTS, [[completed_comment], [recorded]])
    scripted_runner.expect(check_patch(recorded["body"], 501), {})
    expect_finish_gate(scripted_runner, completed_body, [[completed_comment], [recorded]])
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err

    # An operator unticks the original publication after recording checks.
    # The attestation must never hide that check on the next invocation.
    expect_check_sources(scripted_runner, completed_body, [[comment], [recorded]])
    assert invoke(config_path, scripted_runner, URL) == 1
    error = capsys.readouterr().err
    assert "unticked human checks remain" in error
    assert "publication comment 42:" in error
    assert "mutations disabled" not in error
    assert sum("POST" in argv for argv, _ in scripted_runner.calls) == 1


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/repeated-attestation-updates")
def test_attestation_changed_head_creates_distinct_marker(config_path, scripted_runner, capsys):
    sources = [("PR body", ["Complete"])]
    remote = []
    for head in [HEAD, "c" * 40]:
        scripted_runner.expect(VIEW, {**PR, "headRefOid": head, "body": "- [X] Complete"})
        scripted_runner.expect(MAIN, MAIN_REF).expect(STATUS, EMPTY_STATUS)
        scripted_runner.expect(ACTOR, {"login": "operator"}).expect(COMMENTS, [list(remote)])
        body = attestation_body(sources, head=head)

        def create():
            remote.append(attestation(body, 501 + len(remote)))
            return remote[-1]

        scripted_runner.expect(attestation_post(body), create)
        expect_finish_gate(scripted_runner, "- [X] Complete", head=head)
        assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
        assert "mutations disabled" in capsys.readouterr().err
    assert len(remote) == 2
    assert remote[0]["body"].endswith(f"<!-- nightshift:land-checks:78:{HEAD} -->")
    assert remote[1]["body"].endswith(f"<!-- nightshift:land-checks:78:{'c' * 40} -->")


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/lost-comment-response")
@pytest.mark.parametrize("production_runner", [False, True])
@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired([], 30), OSError("connection lost"),
    subprocess.CompletedProcess([], 1, "", "connection reset"),
    subprocess.CompletedProcess([], 0, "truncated JSON", ""), {},
    {"id": 501, "body": None},
])
def test_attestation_lost_response_reconciles_later_page(
    config_path, scripted_runner, monkeypatch, capsys, production_runner, failure,
):
    body = attestation_body([("PR body", ["Complete"])])
    remote = [{"body": "Unrelated discussion"}] * 100
    expect_check_sources(scripted_runner, "- [x] Complete")
    scripted_runner.expect(ACTOR, {"login": "operator"})
    scripted_runner.expect(COMMENTS, lambda: [remote[:100], remote[100:]])

    def lost_create():
        remote.append(attestation(body))
        return failure

    scripted_runner.expect(attestation_post(body), lost_create)
    scripted_runner.expect(COMMENTS, lambda: [remote[:100], remote[100:]])
    scripted_runner.expect(check_patch(body, 501), {})
    expect_finish_gate(scripted_runner, "- [x] Complete")
    if production_runner:
        # Exercise the actual default adapter, not just the injected-runner path.
        monkeypatch.setattr(subprocess, "run", scripted_runner)
        result = land.main([URL, "--checks-done", "--config", str(config_path)])
    else:
        result = invoke(config_path, scripted_runner, URL, "--checks-done")
    assert result == 1
    assert "mutations disabled" in capsys.readouterr().err
    assert len(remote) == 101
    assert sum("POST" in argv for argv, _ in scripted_runner.calls) == 1
    assert scripted_runner.calls[-3][0] == check_patch(body, 501)


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/uncertain-attestation-refused")
@pytest.mark.parametrize("after_post", [False, True])
@pytest.mark.parametrize("problem", [
    "failed-read", "timeout", "malformed-json", "malformed-page", "malformed-body", "duplicate",
    "repeated-marker", "untrusted", "missing-trust", "bad-prefix", "bad-id", "absent",
])
def test_uncertain_attestation_refuses_without_retry_or_later_mutations(
    config_path, scripted_runner, capsys, after_post, problem,
):
    body = attestation_body([("PR body", ["Complete"])])
    existing = attestation(body)
    responses = {
        "failed-read": subprocess.CompletedProcess([], 1, "", "later page failed"),
        "timeout": subprocess.TimeoutExpired([], 30),
        "malformed-json": subprocess.CompletedProcess([], 0, "[]\n[]", ""),
        "malformed-page": [[existing], {}],
        "malformed-body": [[existing], [{"body": None}]],
        "duplicate": [[existing], [attestation(body, 502)]],
        "repeated-marker": [[attestation(body + "\n" + body)]],
        "untrusted": [[attestation(body, author_association="CONTRIBUTOR")]],
        "missing-trust": [[{"id": 501, "body": body}]],
        "bad-prefix": [[attestation(body.replace("**nightshift** —", "Lookalike"))]],
        "bad-id": [[attestation(body, True)]],
        "absent": [[]],
    }
    expect_check_sources(scripted_runner, "- [x] Complete")
    scripted_runner.expect(ACTOR, {"login": "operator"})
    if after_post or problem == "absent":
        scripted_runner.expect(COMMENTS, [[]])
        scripted_runner.expect(attestation_post(body), subprocess.TimeoutExpired([], 30))
    scripted_runner.expect(COMMENTS, responses[problem])
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    error = capsys.readouterr().err
    assert "attestation" in error and "mutations disabled" not in error
    if after_post or problem == "absent":
        assert "reconcil" in error
    if problem in {"duplicate", "repeated-marker"}:
        assert "duplicate marker" in error
    if problem in {"untrusted", "missing-trust", "bad-prefix"}:
        assert "untrusted marker" in error
    # The exact script stops at the failed lookup: no PATCH, finish, CI or merge.
    assert scripted_runner.calls[-1][0] == COMMENTS
    assert sum("POST" in argv for argv, _ in scripted_runner.calls) == int(after_post or problem == "absent")


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/repeated-attestation-updates")
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/uncertain-attestation-refused")
@pytest.mark.parametrize("failed_method", ["POST", "PATCH"])
def test_attestation_failure_after_check_edits_recovers_on_next_invocation(
    config_path, scripted_runner, capsys, failed_method,
):
    sources = [("PR body", ["Check"])]
    body = attestation_body(sources)
    existing = attestation(body)
    expect_check_sources(scripted_runner, "- [ ] Check")
    scripted_runner.expect(ACTOR, {"login": "operator"})
    scripted_runner.expect(check_patch("- [x] Check"), {})
    scripted_runner.expect(COMMENTS, [[]] if failed_method == "POST" else [[existing]])
    scripted_runner.expect(attestation_post(body) if failed_method == "POST" else check_patch(body, 501),
                           subprocess.CompletedProcess([], 1, "", "permission denied"))
    if failed_method == "POST":
        scripted_runner.expect(COMMENTS, [[]])
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    error = capsys.readouterr().err
    assert "permission denied" in error and "mutations disabled" not in error

    expect_check_sources(scripted_runner, "- [x] Check")
    scripted_runner.expect(ACTOR, {"login": "new-operator"})
    expect_attestation(scripted_runner, sources, actor="new-operator",
                       existing=existing if failed_method == "PATCH" else None)
    expect_finish_gate(scripted_runner, "- [x] Check")
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err
    assert sum(argv == check_patch("- [x] Check") for argv, _ in scripted_runner.calls) == 1


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/stable-check-attestation/repeated-attestation-updates")
@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR", "collaborator"])
def test_existing_attestation_on_later_page_uses_production_trust(
    config_path, scripted_runner, capsys, association,
):
    body = attestation_body([("PR body", ["Complete"])])
    marker = f"<!-- nightshift:land-checks:78:{HEAD} -->"
    unrelated = [attestation(body.replace(marker, other), index, author_association="NONE")
                 for index, other in enumerate([
                     f"<!-- nightshift:land-checks:79:{HEAD} -->",
                     f"<!-- nightshift:land-checks:78:{'c' * 40} -->",
                     f"<!-- nightshift:land-checks:78:{HEAD}extra -->",
                 ], 1)]
    expect_check_sources(scripted_runner, "- [X] Complete")
    scripted_runner.expect(ACTOR, {"login": "operator"})
    scripted_runner.expect(COMMENTS, [unrelated + [{"body": "Discussion"}] * 97,
                                     [attestation(body, author_association=association)]])
    scripted_runner.expect(check_patch(body, 501), {})
    expect_finish_gate(scripted_runner, "- [X] Complete")
    assert invoke(config_path, scripted_runner, URL, "--checks-done") == 1
    assert "mutations disabled" in capsys.readouterr().err
    assert not any("POST" in argv for argv, _ in scripted_runner.calls)


# CI/update/merge belong to subsequent tasks. These lifecycle tests inject that
# downstream boundary, while keeping discovery, human checks, finish, and all
# external calls on the real production path and the strict scripted runner.
CHECKS = ["gh", "pr", "checks", "78", "--repo", REPO, "--json", "name,bucket,state,link"]


def status_rows(*rows):
    return {**EMPTY_STATUS, "runs": list(rows)}


def expect_initial_run(runner, pr, row, comments=None):
    runner.expect(VIEW, pr).expect(MAIN, MAIN_REF).expect(STATUS, status_rows(row))
    runner.expect(COMMENTS, [[]] if comments is None else comments)


def expect_before_finish(runner, pr, row=RUN, comments=None):
    runner.expect(STATUS, status_rows(row)).expect(VIEW, pr)
    runner.expect(COMMENTS, [[]] if comments is None else comments)


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-at-human-checks")
@pytest.mark.parametrize("checks_done", [False, True])
@pytest.mark.parametrize("draft", [False, True])
def test_land_finishes_only_at_human_checks(
    config_path, scripted_runner, monkeypatch, capsys, checks_done, draft,
):
    body = "- [ ] Body check" if checks_done else "- [x] Body check"
    comment = publication(42, checks="- [x] Publication check")
    pr = {**PR, "body": body, "isDraft": draft}
    expect_initial_run(scripted_runner, pr, RUN, [[comment]])
    recorded = None
    if checks_done:
        scripted_runner.expect(ACTOR, {"login": "operator"})
        scripted_runner.expect(check_patch("- [x] Body check"), {})
        recorded = expect_attestation(scripted_runner, [
            ("PR body", ["Body check"]), ("publication comment 42", ["Publication check"]),
        ])
    completed = {**pr, "body": "- [x] Body check"}
    expect_before_finish(scripted_runner, completed, comments=[[comment]])
    scripted_runner.expect(FINISH, subprocess.CompletedProcess(FINISH, 0, "Archived and ready\n", ""),
                           timeout=300)
    done = {**RUN, "status": "done", "pause": None, "head": FINISHED_HEAD}
    scripted_runner.expect(STATUS, status_rows(done))
    scripted_runner.expect(VIEW, {**completed, "headRefOid": FINISHED_HEAD, "isDraft": False})
    scripted_runner.expect(CHECKS, subprocess.CompletedProcess(CHECKS, 1, "", "CI failure"))

    def downstream(pr, run, snapshot, operations, *, timeout):
        assert timeout == 42
        assert run.status == "done" and run.change == RUN["change"]
        assert pr.head == run.head == snapshot.head == FINISHED_HEAD
        assert not pr.draft
        assert snapshot.sources == (
            land.CheckSource("- [x] Body check"), land.CheckSource(comment["body"], 42),
        )
        assert snapshot.actor == ("operator" if checks_done else None)
        operations.run(CHECKS, stage="CI")

    monkeypatch.setattr(land, "continue_landing", downstream)
    assert invoke(config_path, scripted_runner, URL, "--timeout", "42",
                  *(["--checks-done"] if checks_done else [])) == 1
    assert "CI failure" in capsys.readouterr().err
    calls = [argv for argv, _ in scripted_runner.calls]
    assert calls.count(FINISH) == 1
    assert calls[-4:] == [FINISH, STATUS, VIEW, CHECKS]
    if recorded:
        assert f"<!-- nightshift:land-checks:78:{HEAD} -->" in recorded["body"]
        assert FINISHED_HEAD not in recorded["body"]


@pytest.mark.scenario("reviewed-pr-landing/reliable-run-discovery/direct-pr-skips-finish")
@pytest.mark.parametrize("listed", [False, True])
def test_land_direct_pr_skips_finish(config_path, scripted_runner, monkeypatch, listed):
    pr = {**PR, "body": "- [x] Direct body check" if listed else "No checklist"}
    unrelated = {**RUN, "pr": "https://github.com/other/project/pull/78"}
    scripted_runner.expect(VIEW, pr).expect(MAIN, MAIN_REF).expect(STATUS, status_rows(unrelated))
    scripted_runner.expect(STATUS, status_rows(unrelated)).expect(VIEW, pr)
    reached = []

    def downstream(pr, run, snapshot, operations, *, timeout):
        reached.append(pr.head)
        assert run is None
        assert snapshot.sources == (land.CheckSource(pr.body),)
        assert snapshot.head == pr.head == HEAD
        raise land.Refusal("reached downstream CI and merge gates")

    monkeypatch.setattr(land, "continue_landing", downstream)
    assert invoke(config_path, scripted_runner, URL) == 1
    assert reached == [HEAD]
    assert [argv for argv, _ in scripted_runner.calls] == [VIEW, MAIN, STATUS, STATUS, VIEW]


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/wrong-state-refused")
@pytest.mark.parametrize("row", [
    {**RUN, "status": "running", "pause": None},
    {**RUN, "status": "aborted", "pause": None},
    {**RUN, "pause": {"reason": "ready_to_publish"}},
    {**RUN, "pause": {"reason": "needs_human"}},
])
def test_land_refreshes_run_eligibility_before_finish(config_path, scripted_runner, capsys, row):
    pr = {**PR, "body": "- [x] Reviewed"}
    expect_initial_run(scripted_runner, pr, RUN)
    scripted_runner.expect(STATUS, status_rows(row)).expect(VIEW, pr)
    assert invoke(config_path, scripted_runner, URL) == 1
    error = capsys.readouterr().err
    assert f"run reviewed-change is {row['status']}" in error
    if row["pause"]:
        assert row["pause"]["reason"] in error
    assert [argv for argv, _ in scripted_runner.calls] == [VIEW, MAIN, STATUS, COMMENTS, STATUS, VIEW]


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-at-human-checks")
@pytest.mark.parametrize("change", ["head", "closed", "body", "publication", "new-publication"])
def test_land_rechecks_human_inputs_before_finish(config_path, scripted_runner, capsys, change):
    comment = publication(checks="- [x] Reviewed publication")
    pr = {**PR, "body": "- [x] Reviewed body"}
    expect_initial_run(scripted_runner, pr, RUN, [[comment]])
    refreshed = dict(pr)
    comments = [[comment]]
    if change == "head":
        refreshed["headRefOid"] = "e" * 40
    elif change == "closed":
        refreshed["state"] = "CLOSED"
    elif change == "body":
        refreshed["body"] += "\n- [ ] Newly required"
    elif change == "publication":
        comments = [[{**comment, "body": comment["body"].replace("[x]", "[ ]")}]]
    else:
        comments = [[comment, publication(2, checks="- [x] New publication")]]
    scripted_runner.expect(STATUS, status_rows(RUN)).expect(VIEW, refreshed)
    if change not in {"head", "closed"}:
        scripted_runner.expect(COMMENTS, comments)
    assert invoke(config_path, scripted_runner, URL) == 1
    error = capsys.readouterr().err
    assert {"head": "head changed", "closed": "PR must be open"}.get(
        change, "human check sources changed") in error
    assert not any(argv == FINISH for argv, _ in scripted_runner.calls)


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-fails-safely")
@pytest.mark.parametrize("response, reason", [
    (subprocess.CompletedProcess(FINISH, 1, "", "push rejected"), "push rejected"),
    (subprocess.CompletedProcess(FINISH, 2, "archive failed", ""), "archive failed"),
    (subprocess.TimeoutExpired(FINISH, 300), "timed out after 300s"),
    (OSError("finish unavailable"), "finish unavailable"),
])
def test_land_finish_failure_stops_before_ci(config_path, scripted_runner, capsys, response, reason):
    expect_initial_run(scripted_runner, PR, RUN)
    expect_before_finish(scripted_runner, PR)
    scripted_runner.expect(FINISH, response, timeout=300)
    assert invoke(config_path, scripted_runner, URL) == 1
    error = capsys.readouterr().err
    assert "finish reviewed-change" in error and reason in error and "rerun land" in error
    assert scripted_runner.calls[-1][0] == FINISH


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-fails-safely")
@pytest.mark.parametrize("rows, reason", [
    ([], "run identity changed"),
    ([{**RUN, "change": "replacement"}], "run identity changed"),
    ([{**RUN, "pr": "https://github.com/other/project/pull/78"}], "run identity changed"),
    ([RUN], "expected the same run done"),
    ([{**RUN, "status": "aborted", "pause": None}], "expected the same run done"),
    ([{**RUN, "status": "running", "pause": None}], "expected the same run done"),
    ([{**RUN, "pause": {"reason": "ready_to_publish"}}], "expected the same run done"),
    ([{**RUN, "error": "unreadable"}], "unreadable"),
    ([{**RUN, "head": ""}], "head SHA"),
    ([RUN, RUN], "multiple matching runs"),
])
def test_land_partial_finish_must_establish_same_done_run(
    config_path, scripted_runner, capsys, rows, reason,
):
    expect_initial_run(scripted_runner, PR, RUN)
    expect_before_finish(scripted_runner, PR)
    scripted_runner.expect(FINISH, subprocess.CompletedProcess(FINISH, 0, "Finished", ""), timeout=300)
    scripted_runner.expect(STATUS, status_rows(*rows))
    assert invoke(config_path, scripted_runner, URL) == 1
    assert reason in capsys.readouterr().err
    assert scripted_runner.calls[-1][0] == STATUS


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-fails-safely")
@pytest.mark.parametrize("patch, reason", [
    ({"headRefOid": HEAD}, "does not equal remote PR head"),
    ({"headRefOid": "e" * 40}, "does not equal remote PR head"),
    ({"isDraft": True}, "PR remains draft"),
    ({"state": "CLOSED"}, "PR must be open"),
    ({"url": "https://github.com/other/project/pull/78"}, "contradictory PR identity"),
    ({"baseRefName": "other"}, "PR must target main"),
])
def test_land_finished_head_and_remote_pr_must_agree(
    config_path, scripted_runner, capsys, patch, reason,
):
    expect_initial_run(scripted_runner, PR, RUN)
    expect_before_finish(scripted_runner, PR)
    scripted_runner.expect(FINISH, subprocess.CompletedProcess(FINISH, 0, "Finished", ""), timeout=300)
    scripted_runner.expect(STATUS, status_rows(
        {**RUN, "status": "done", "pause": None, "head": FINISHED_HEAD}))
    scripted_runner.expect(VIEW, {**PR, "headRefOid": FINISHED_HEAD, **patch})
    assert invoke(config_path, scripted_runner, URL) == 1
    assert reason in capsys.readouterr().err
    assert scripted_runner.calls[-1][0] == VIEW


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-fails-safely")
@pytest.mark.parametrize("stage", ["status", "pr"])
def test_land_post_finish_read_failure_stops_before_ci(config_path, scripted_runner, capsys, stage):
    expect_initial_run(scripted_runner, PR, RUN)
    expect_before_finish(scripted_runner, PR)
    scripted_runner.expect(FINISH, subprocess.CompletedProcess(FINISH, 0, "Finished", ""), timeout=300)
    failure = subprocess.CompletedProcess([], 1, "", "post-finish read failed")
    if stage == "status":
        scripted_runner.expect(STATUS, failure)
    else:
        scripted_runner.expect(STATUS, status_rows(
            {**RUN, "status": "done", "pause": None, "head": FINISHED_HEAD}))
        scripted_runner.expect(VIEW, failure)
    assert invoke(config_path, scripted_runner, URL) == 1
    assert "post-finish read failed" in capsys.readouterr().err


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-fails-safely")
@pytest.mark.parametrize("saved_done", [False, True])
def test_land_interrupted_finish_uses_existing_recovery(
    config_path, scripted_runner, capsys, saved_done,
):
    remote = {"pr": dict(PR), "run": dict(RUN)}
    expect_initial_run(scripted_runner, remote["pr"], remote["run"])
    expect_before_finish(scripted_runner, remote["pr"])

    def partial_finish():
        remote["pr"] = {**PR, "headRefOid": FINISHED_HEAD}
        remote["run"] = {**RUN, "head": FINISHED_HEAD}
        if saved_done:
            remote["run"].update(status="done", pause=None)
        return subprocess.TimeoutExpired(FINISH, 300)

    scripted_runner.expect(FINISH, partial_finish, timeout=300)
    assert invoke(config_path, scripted_runner, URL) == 1
    assert "timed out" in capsys.readouterr().err
    assert remote["run"]["status"] == ("done" if saved_done else "paused")

    expect_initial_run(scripted_runner, remote["pr"], remote["run"])
    expect_finish_gate(scripted_runner, comments=[[]], row=remote["run"], head=FINISHED_HEAD)
    assert invoke(config_path, scripted_runner, URL) == 1
    assert "mutations disabled beyond run completion" in capsys.readouterr().err
    assert sum(argv == FINISH for argv, _ in scripted_runner.calls) == (1 if saved_done else 2)


@pytest.mark.scenario("reviewed-pr-landing/done-run-continuation/retry-after-finish")
@pytest.mark.parametrize("failure_stage", ["ci", "update", "after-update", "merge"])
def test_land_done_run_retry_after_downstream_failure(
    config_path, scripted_runner, monkeypatch, capsys, failure_stage,
):
    # Model downstream outcomes without implementing the later CI/update/merge
    # tasks here. The durable remote run/head are carried into a real second
    # invocation, including an accepted update which changes only the PR head.
    remote = {"pr": {**PR, "body": "- [x] Reviewed"}, "run": dict(RUN)}
    received = []
    attempts = 0
    update = ["gh", "api", "--method", "PUT", f"repos/{REPO}/pulls/78/update-branch",
              "-f", f"expected_head_sha={FINISHED_HEAD}"]

    def merge(head):
        return ["gh", "pr", "merge", "78", "--repo", REPO, "--merge", "--match-head-commit", head]

    def downstream(pr, run, snapshot, operations, *, timeout):
        nonlocal attempts
        attempts += 1
        received.append((pr.head, run.head, snapshot.head))
        assert run.status == "done"
        operations.run(CHECKS, stage="CI")
        if attempts == 1 and failure_stage in {"update", "after-update"}:
            operations.run(update, stage="branch update")
            if failure_stage == "after-update":
                operations.run(CHECKS, stage="CI after accepted update")
        operations.run(merge(pr.head), stage="merge")
        return 0

    monkeypatch.setattr(land, "continue_landing", downstream)
    expect_initial_run(scripted_runner, remote["pr"], remote["run"])
    expect_before_finish(scripted_runner, remote["pr"])

    def finish():
        remote["pr"] = {**remote["pr"], "headRefOid": FINISHED_HEAD}
        remote["run"] = {**RUN, "status": "done", "pause": None, "head": FINISHED_HEAD}
        return subprocess.CompletedProcess(FINISH, 0, "Finished", "")

    scripted_runner.expect(FINISH, finish, timeout=300)
    scripted_runner.expect(STATUS, lambda: status_rows(remote["run"]))
    scripted_runner.expect(VIEW, lambda: remote["pr"])
    failure = subprocess.CompletedProcess([], 1, "", f"{failure_stage} failed")
    scripted_runner.expect(CHECKS, failure if failure_stage == "ci" else {})
    if failure_stage == "update":
        scripted_runner.expect(update, failure)
    elif failure_stage == "after-update":
        def accepted_update():
            remote["pr"] = {**remote["pr"], "headRefOid": "d" * 40}
            return {}
        scripted_runner.expect(update, accepted_update)
        scripted_runner.expect(CHECKS, failure)
    elif failure_stage == "merge":
        scripted_runner.expect(merge(FINISHED_HEAD), failure)
    assert invoke(config_path, scripted_runner, URL) == 1
    assert f"{failure_stage} failed" in capsys.readouterr().err
    assert remote["run"]["status"] == "done"

    expect_initial_run(scripted_runner, remote["pr"], remote["run"])
    expect_finish_gate(scripted_runner, remote["pr"]["body"], [[]],
                       row=remote["run"], head=remote["pr"]["headRefOid"])
    scripted_runner.expect(CHECKS, {})
    scripted_runner.expect(merge(remote["pr"]["headRefOid"]), {})
    assert invoke(config_path, scripted_runner, URL) == 0
    assert capsys.readouterr().err == ""
    assert received == [
        (FINISHED_HEAD, FINISHED_HEAD, FINISHED_HEAD),
        (remote["pr"]["headRefOid"], FINISHED_HEAD, remote["pr"]["headRefOid"]),
    ]
    assert sum(argv == FINISH for argv, _ in scripted_runner.calls) == 1
    assert sum(argv == CHECKS for argv, _ in scripted_runner.calls) == (
        3 if failure_stage == "after-update" else 2)


@pytest.mark.real_trust
@pytest.mark.scenario("reviewed-pr-landing/done-run-continuation/retry-after-finish")
@pytest.mark.parametrize("source", ["body", "publication"])
def test_land_done_run_still_requires_fresh_human_checks(
    config_path, scripted_runner, capsys, source,
):
    done = {**RUN, "status": "done", "pause": None, "head": FINISHED_HEAD}
    pr = {**PR, "headRefOid": "d" * 40, "body": "- [ ] New check" if source == "body" else ""}
    comment = publication(checks="- [ ] New check" if source == "publication" else "- [x] Done")
    expect_initial_run(scripted_runner, pr, done, [[comment]])
    assert invoke(config_path, scripted_runner, URL) == 1
    assert "unticked human checks remain" in capsys.readouterr().err
    assert not any(argv == FINISH for argv, _ in scripted_runner.calls)


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/wrong-state-refused")
@pytest.mark.scenario("reviewed-pr-landing/done-run-continuation/retry-after-finish")
def test_land_done_run_cannot_regress_and_repeat_finish(config_path, scripted_runner, capsys):
    done = {**RUN, "status": "done", "pause": None, "head": FINISHED_HEAD}
    expect_initial_run(scripted_runner, PR, done)
    scripted_runner.expect(STATUS, status_rows(RUN)).expect(VIEW, PR)
    assert invoke(config_path, scripted_runner, URL) == 1
    assert "no longer done" in capsys.readouterr().err
    assert not any(argv == FINISH for argv, _ in scripted_runner.calls)


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-fails-safely")
@pytest.mark.parametrize("rows", [
    [], [{**RUN, "change": "replacement"}],
    [{**RUN, "pr": "https://github.com/other/project/pull/78"}],
])
def test_land_run_identity_must_survive_pre_finish_refresh(
    config_path, scripted_runner, capsys, rows,
):
    expect_initial_run(scripted_runner, PR, RUN)
    scripted_runner.expect(STATUS, status_rows(*rows))
    assert invoke(config_path, scripted_runner, URL) == 1
    assert "run identity changed" in capsys.readouterr().err
    assert scripted_runner.calls[-1][0] == STATUS


@pytest.mark.scenario("reviewed-pr-landing/eligible-run-state/finish-at-human-checks")
@pytest.mark.parametrize("finished_head, accepted", [(HEAD, True), (FINISHED_HEAD, False)])
def test_land_concurrent_done_refresh_still_requires_finished_head(
    config_path, scripted_runner, capsys, finished_head, accepted,
):
    expect_initial_run(scripted_runner, PR, RUN)
    done = {**RUN, "status": "done", "pause": None, "head": finished_head}
    expect_before_finish(scripted_runner, PR, row=done)
    assert invoke(config_path, scripted_runner, URL) == 1
    error = capsys.readouterr().err
    assert ("mutations disabled beyond run completion" if accepted
            else "does not equal remote PR head") in error
    assert not any(argv == FINISH for argv, _ in scripted_runner.calls)

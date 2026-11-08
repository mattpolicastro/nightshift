"""Offline invocation contracts; upstream parser compatibility is an operator check."""

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from specloop import openspec
from test_specloop_command_json import validator_process


@pytest.mark.scenario("openspec-binary/trusted-executable-selection/default-and-overridden-binary")
@pytest.mark.parametrize("env,expected", [
    ({}, "/opt/homebrew/bin/openspec"),
    ({"NIGHTSHIFT_BIN_DIR": "/custom tools"}, "/custom tools/openspec"),
    ({"NIGHTSHIFT_OPENSPEC_BIN": "~/tools/a $literal; binary", "NIGHTSHIFT_BIN_DIR": "invalid"},
     "~/tools/a $literal; binary"),
])
def test_resolver(env, expected):
    assert openspec.resolve_binary(env) == expected.replace("~/", str(Path.home()) + "/", 1)


@pytest.mark.scenario("openspec-binary/trusted-executable-selection/invalid-binary-configuration-refuses")
@pytest.mark.parametrize("setting", ["NIGHTSHIFT_OPENSPEC_BIN", "NIGHTSHIFT_BIN_DIR"])
@pytest.mark.parametrize("value", ["", " ", "relative", "~", "~other/tool", "/a\0b", "/a\nb", "/a\rb"])
def test_invalid_setting_never_spawns(tmp_path, monkeypatch, setting, value):
    env = {setting: value}
    monkeypatch.setattr(openspec.os, "environ", env)
    process = Mock(side_effect=AssertionError("invalid configuration spawned"))
    monkeypatch.setattr(openspec.subprocess, "run", process)
    with pytest.raises(openspec.SpecError) as raised:
        openspec._run(tmp_path, "validate", "change")
    assert type(raised.value) is openspec.SpecError
    assert str(raised.value) == f"invalid host path setting: {setting}"
    process.assert_not_called()


@pytest.mark.scenario("openspec-binary/pinned-direct-invocation/version-probe-is-reused")
def test_cache_is_per_path_and_concurrent(tmp_path, monkeypatch, validator_process):
    validator_process.return_value = subprocess.CompletedProcess([], 0, '{"archive": {"ok": true}}', "")
    start = threading.Barrier(4)

    def probe(*args, **kwargs):
        time.sleep(0.05)  # Allow the other first-use callers to reach the gate.
        return validator_process.probe.return_value

    validator_process.probe.side_effect = probe

    def invoke(_):
        start.wait(timeout=5)
        return openspec.validate(tmp_path, "change")

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(invoke, range(4))) == [[], [], [], []]
    assert openspec.scenarios(tmp_path, "change") == {}
    assert openspec.archive(tmp_path, "change", True) == {"ok": True}
    with pytest.raises(openspec.SpecError, match="tasks are unfinished"):
        openspec.archive(tmp_path, "change", False)
    validator_process.probe.assert_called_once()
    assert validator_process.call_count == 6
    assert [c.args[0][1] for c in validator_process.mock_calls] == ["--version"] + ["validate"] * 4 + ["show", "archive"]
    original = openspec.resolve_binary()
    monkeypatch.setenv("NIGHTSHIFT_OPENSPEC_BIN", "/other/openspec")
    openspec.validate(tmp_path, "change")
    assert validator_process.probe.call_args.args == (["/other/openspec", "--version"],)
    monkeypatch.setenv("NIGHTSHIFT_OPENSPEC_BIN", original)
    openspec.validate(tmp_path, "change")
    assert validator_process.probe.call_count == 2


@pytest.mark.scenario("openspec-binary/pinned-direct-invocation/version-mismatch-refuses")
@pytest.mark.scenario("openspec-binary/pinned-direct-invocation/version-probe-is-reused")
@pytest.mark.parametrize("code,stdout", [(1, openspec.VERSION), (0, ""), (0, "1.14.3"),
                                          (0, f"OpenSpec {openspec.VERSION} (standalone)")])
def test_failed_probe_is_not_cached(tmp_path, validator_process, code, stdout):
    validator_process.probe.return_value = subprocess.CompletedProcess([], code, stdout, "failed probe")
    with pytest.raises(openspec.SpecError) as raised:
        openspec.validate(tmp_path, "change")
    assert type(raised.value) is openspec.SpecError
    assert openspec.VERSION in str(raised.value) and "docs/deploy.md#install-openspec" in str(raised.value)
    validator_process.assert_not_called()
    validator_process.probe.return_value.stdout = " \n" + openspec.VERSION + "\n"
    validator_process.probe.return_value.returncode = 0
    validator_process.return_value = subprocess.CompletedProcess([], 0, "{}", "")
    assert openspec.validate(tmp_path, "change") == []
    assert validator_process.probe.call_count == 2


@pytest.mark.scenario("openspec-binary/pinned-direct-invocation/version-mismatch-refuses")
@pytest.mark.scenario("openspec-binary/pinned-direct-invocation/version-probe-is-reused")
@pytest.mark.parametrize("stream", [1, 2])
def test_undecodable_probe_refuses_without_caching(tmp_path, monkeypatch, stream):
    monkeypatch.setattr(openspec, "_checked_paths", set())
    monkeypatch.setenv("NIGHTSHIFT_OPENSPEC_BIN", "/fixture tools/openspec")
    run = subprocess.run
    process = Mock(side_effect=lambda argv, **kwargs: run(
        [sys.executable, "-c", f"import os; os.write({stream}, b'\\xff')"], **kwargs))
    monkeypatch.setattr(openspec.subprocess, "run", process)
    for _ in range(2):
        with pytest.raises(openspec.SpecError) as raised:
            openspec.validate(tmp_path, "change")
        assert type(raised.value) is openspec.SpecError
        assert isinstance(raised.value.__cause__, UnicodeDecodeError)
        assert openspec.VERSION in str(raised.value)
        assert "docs/deploy.md#install-openspec" in str(raised.value)
        assert openspec._checked_paths == set()
    assert process.call_count == 2
    assert all(c.args == ([openspec.resolve_binary(), "--version"],) for c in process.call_args_list)


@pytest.mark.scenario("openspec-binary/pinned-direct-invocation/missing-binary-is-not-transient")
@pytest.mark.scenario("specloop-interface/validator-availability-classification/validator-timeout-is-transient")
@pytest.mark.parametrize("probe", [True, False])
@pytest.mark.parametrize("failure", [FileNotFoundError(), PermissionError(), OSError("exec format"),
                                     subprocess.TimeoutExpired("fixture", 120)])
def test_launch_and_timeout_failures(tmp_path, validator_process, probe, failure):
    process = validator_process.probe if probe else validator_process
    process.side_effect = failure
    expected = openspec.ValidatorUnavailable if isinstance(failure, subprocess.TimeoutExpired) else openspec.SpecError
    with pytest.raises(expected) as raised:
        openspec.validate(tmp_path, "change")
    assert type(raised.value) is expected and raised.value.__cause__ is failure
    process.assert_called_once()
    assert process.call_args.kwargs["timeout"] == 120
    assert validator_process.call_count == (0 if probe else 1)
    assert openspec._checked_paths == (set() if probe else {openspec.resolve_binary()})


@pytest.mark.scenario("openspec-binary/strict-validator-compatibility/incomplete-scenarios-fail-strict-validation")
@pytest.mark.scenario("openspec-binary/strict-validator-compatibility/unknown-requirement-sections-fail")
@pytest.mark.parametrize("issue", ["scenario missing WHEN or GIVEN", "scenario missing THEN", "unknown requirements section"])
def test_strict_issues_are_forwarded(tmp_path, validator_process, issue):
    validator_process.return_value = subprocess.CompletedProcess([], 1, json.dumps({
        "items": [{"issues": [{"path": "spec.md", "message": issue}]}], "status": [{"message": "strict failure"}],
    }), "")
    assert openspec.validate(tmp_path, "change") == [f"spec.md: {issue}", "strict failure"]
    assert validator_process.call_args.args == ([openspec.resolve_binary(), "validate", "change", "--strict", "--json"],)


@pytest.mark.scenario("openspec-binary/strict-validator-compatibility/closing-hashes-and-local-lint-coexist")
@pytest.mark.parametrize("wrapper", ["", "```markdown\n", "<!--\n"])
def test_physical_line_lint_is_unchanged(tmp_path, validator_process, wrapper):
    validator_process.return_value = subprocess.CompletedProcess([], 0, "{}", "")
    spec = openspec.change_dir(tmp_path, "change") / "specs/cap/spec.md"
    spec.parent.mkdir(parents=True)
    valid = "## ADDED Requirements ##\n### Requirement: Valid ###\n#### Scenario: Example ####\n- **WHEN** used\n- **THEN** works\n"
    spec.write_text(valid)
    archived = openspec.change_dir(tmp_path, "archive/old") / "specs/cap/spec.md"
    archived.parent.mkdir(parents=True)
    archived.write_text("### Scenario: Historical example\n")
    assert openspec.validate(tmp_path, "change") == []
    spec.write_text(valid + wrapper + "### Scenario: Literal example\n")
    problems = openspec.validate(tmp_path, "change")
    assert len(problems) == 1 and "scenario needs exactly four '#'" in problems[0]

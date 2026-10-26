"""The one-release alias adds only a stderr notice at the public boundary."""

import importlib
import json
import runpy
import shlex
import sys
import tomllib
from pathlib import Path

import pytest

from nightshift import cli as canonical
from specloop import cli, serve
from test_specloop_command_json import CONTRADICTION, admission, review, template
from test_specloop_status_json import admitted, home  # Isolated contract fixtures.


NOTICE = "specloop has been renamed to nightshift; use nightshift instead.\n"
PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["script", "module", "explicit"])
def alias(request, monkeypatch):
    def invoke(argv):
        monkeypatch.setattr(sys, "argv", ["specloop", *argv])
        if request.param == "explicit":
            monkeypatch.setattr(sys, "argv", ["specloop", "--help"])
            return cli.main(argv)
        if request.param == "module":
            # Execute the actual __main__ guard without runpy's already-imported warning.
            monkeypatch.delitem(sys.modules, "specloop.cli", raising=False)
            runpy.run_module("specloop.cli", run_name="__main__")
        else:
            project = tomllib.loads((PROJECT / "pyproject.toml").read_text())
            module, function = project["project"]["scripts"]["specloop"].split(":")
            return getattr(importlib.import_module(module), function)()

    return invoke


def result(entry, argv):
    try:
        return entry(argv)
    except SystemExit as exc:
        return exc.code


@pytest.mark.scenario("nightshift-cli/one-release-alias/specloop-alias-forwards")
@pytest.mark.parametrize("code", [0, 1, 7])
def test_specloop_alias_forwards(alias, monkeypatch, capsys, code):
    argv = ["act", "a-change", "resume", "--note", 'Keep "café"; $(literal)\nnext line', "--json"]
    calls = []
    stdout = '{"version": 1, "outcome": "resumed"}\n'

    def handler(args=None):
        calls.append(list(sys.argv[1:] if args is None else args))
        print(stdout, end="")
        return code

    monkeypatch.setattr(canonical, "main", handler)
    for _ in range(2):
        assert result(alias, argv) == code
        output = capsys.readouterr()
        assert output.out == stdout
        assert output.err == NOTICE
    assert calls == [argv, argv]


@pytest.mark.scenario("nightshift-cli/one-release-alias/specloop-alias-forwards")
@pytest.mark.parametrize("argv", [["--help"], ["status", "--help"], ["unknown"],
                                  ["status", "--events", "bad", "--json"], ["--json"]])
def test_alias_help_and_errors_match_canonical(alias, capsys, argv):
    code = result(canonical.main, argv)
    expected = capsys.readouterr()
    assert result(alias, argv) == code
    actual = capsys.readouterr()
    assert actual.out == expected.out
    assert actual.err == NOTICE + expected.err


@pytest.mark.scenario("nightshift-cli/one-release-alias/packaging-retains-frozen-callers")
def test_frozen_alias_serve_reaches_supervisor(alias, monkeypatch, capsys):
    project = tomllib.loads((PROJECT / "pyproject.toml").read_text())
    assert project["project"]["scripts"] == {
        "nightshift": "nightshift.cli:main", "specloop": "specloop.cli:main",
    }
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["nightshift", "specloop"]
    calls = []
    monkeypatch.setattr(cli, "load_env", lambda path: {})
    monkeypatch.setattr(cli, "notifier", lambda secrets: None)
    monkeypatch.setattr(serve, "serve", lambda poll, notify, *, once: calls.append((poll, notify, once)) or 7)
    wrapper = (PROJECT / "bin" / "specloop-serve").read_text().splitlines()
    command, = [shlex.split(line) for line in wrapper if line.startswith("exec ")]
    assert command == ["exec", "/opt/homebrew/bin/uv", "run", "--no-sync", "specloop", "serve"]
    assert result(alias, command[5:]) == 7
    assert calls == [(60, None, False)]
    output = capsys.readouterr()
    assert output.out == "" and output.err == NOTICE


@pytest.mark.scenario("nightshift-cli/canonical-build-commands/both-names-honor-machine-contracts")
@pytest.mark.parametrize("change, code", [(None, 0), ("paused-change", 0), ("unknown", 1)])
def test_both_names_share_isolated_status_contract(admitted, alias, capsys, change, code):
    run, _ = admitted("paused-change")
    run.pause("stuck", detail='Preserve "quoted" feedback')
    argv = ["status", "--json", *([change] if change else [])]
    assert canonical.main(argv) == code
    expected = capsys.readouterr()
    assert expected.err == ""
    assert result(alias, argv) == code
    actual = capsys.readouterr()
    assert json.loads(actual.out) == json.loads(expected.out)
    assert actual.out == expected.out
    assert actual.err == NOTICE


@pytest.mark.scenario("nightshift-cli/canonical-build-commands/both-names-honor-machine-contracts")
def test_both_names_share_isolated_admission_contract(template, alias, monkeypatch, capfd):
    path, _ = template
    review(monkeypatch, [CONTRADICTION])
    argv = admission(path, "--json")
    assert canonical.main(argv) == 1
    expected = capfd.readouterr()
    assert result(alias, argv) == 1
    actual = capfd.readouterr()
    assert json.loads(actual.out) == json.loads(expected.out)
    assert actual.out == expected.out
    assert actual.err == NOTICE + expected.err

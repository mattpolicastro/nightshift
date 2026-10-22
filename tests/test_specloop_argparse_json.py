import json
import sys

import pytest

from specloop import cli


INVALID_ARGUMENTS = [
    pytest.param(["propose"], "manifest", id="propose-missing-manifest"),
    pytest.param(["admit"], "manifest", id="admit-missing-manifest"),
    pytest.param(["revise", "change"], "--feedback", id="revise-missing-feedback"),
    pytest.param(["publish"], "change", id="publish-missing-change"),
    pytest.param(["act", "change"], "verb", id="act-missing-verb"),
    pytest.param(["revise", "change", "--feedback", "feedback.md", "--feedback-format", "yaml"],
                 "invalid choice", id="invalid-choice"),
    pytest.param(["status", "--events", "many"], "invalid int value", id="invalid-number"),
    pytest.param(["status", "--events"], "expected one argument", id="missing-option-value"),
    pytest.param(["propose", "manifest.toml", "--unknown"], "unrecognized arguments", id="propose-option"),
    pytest.param(["admit", "manifest.toml", "--unknown"], "unrecognized arguments", id="admit-option"),
    pytest.param(["revise", "change", "--feedback", "feedback.md", "--unknown"],
                 "unrecognized arguments", id="revise-option"),
    pytest.param(["publish", "change", "--unknown"], "unrecognized arguments", id="publish-option"),
    pytest.param(["status", "--unknown"], "unrecognized arguments", id="status-option"),
    pytest.param(["act", "change", "resume", "--unknown"], "unrecognized arguments", id="act-option"),
    pytest.param(["unknown"], "invalid choice", id="unknown-subcommand"),
    pytest.param([], "required", id="absent-subcommand"),
]


@pytest.fixture(autouse=True)
def forbid_command_side_effects(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Argument parsing must not dispatch or perform command side effects")

    for name in (
        "admit", "run_cmd", "status", "act", "resume", "revise", "accept", "waive",
        "abort", "publish", "finish", "serve_cmd", "propose", "drill_cmd", "pause_cmd",
        "load_env", "notifier", "runs_dir", "_ctx", "_manifest",
    ):
        monkeypatch.setattr(cli, name, unexpected)
    for name in ("for_change", "save", "reload", "event"):
        monkeypatch.setattr(cli.Run, name, unexpected)


@pytest.fixture(params=["explicit", "process"])
def invoke(request, monkeypatch):
    def call(argv):
        if request.param == "process":
            monkeypatch.setattr(sys, "argv", ["specloop", *argv])
            return cli.main()
        # Explicit argv must take precedence over the process arguments.
        monkeypatch.setattr(sys, "argv", ["specloop", "--help"])
        return cli.main(argv)

    return call


@pytest.mark.scenario("specloop-interface/json-argument-errors/invalid-arguments-produce-a-document")
@pytest.mark.parametrize("argv, diagnostic", INVALID_ARGUMENTS)
@pytest.mark.parametrize("flag_position", ["first", "after-command", "last"])
def test_argparse_errors_emit_json(invoke, capsys, argv, diagnostic, flag_position):
    position = {"first": 0, "after-command": min(1, len(argv)), "last": len(argv)}[flag_position]
    argv = [*argv[:position], "--json", *argv[position:]]
    assert invoke(argv) == 2
    output = capsys.readouterr()
    # Loading the entire stdout rejects progress, usage, or a second document.
    document = json.loads(output.out)
    assert set(document) == {"version", "error"}
    assert document["version"] == 1
    assert isinstance(document["error"], str) and diagnostic in document["error"]
    assert "Traceback" not in output.err


@pytest.mark.scenario("specloop-interface/json-argument-errors/human-errors-and-help-remain-compatible")
@pytest.mark.parametrize("argv, diagnostic", INVALID_ARGUMENTS + [
    pytest.param(["status", "--jsonish"], "unrecognized arguments", id="json-substring"),
    pytest.param(["status", "--events", "bad", "--jso"], "invalid int value", id="json-abbreviation"),
    pytest.param(["status", "--json=true"], "ignored explicit argument", id="json-assignment"),
    pytest.param(["status", "--events", "bad", "--", "--json"],
                 "invalid int value", id="json-after-terminator"),
    pytest.param(["status", "--", "--json", "extra"], "unrecognized arguments", id="terminated-extra"),
    pytest.param(["--", "--json"], "invalid choice", id="terminated-subcommand"),
])
def test_human_argument_errors(invoke, capsys, argv, diagnostic):
    with pytest.raises(SystemExit) as error:
        invoke(argv)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "usage: specloop" in output.err
    assert "error:" in output.err and diagnostic in output.err


@pytest.mark.scenario("specloop-interface/json-argument-errors/human-errors-and-help-remain-compatible")
@pytest.mark.parametrize("command", [[], ["propose"], ["admit"], ["revise"], ["publish"], ["status"], ["act"]])
@pytest.mark.parametrize("flags", [["--help"], ["--help", "--json"], ["--json", "--help"]])
def test_help_remains_ordinary_help(invoke, capsys, command, flags):
    with pytest.raises(SystemExit) as error:
        invoke([*command, *flags])
    assert error.value.code == 0
    output = capsys.readouterr()
    assert output.out.startswith("usage: specloop")
    assert "--help" in output.out
    assert output.err == ""


@pytest.mark.scenario("specloop-interface/json-argument-errors/invalid-arguments-produce-a-document")
@pytest.mark.parametrize("argv", [
    ["status", "--json", "--", "one", "two"],
    ["--json", "status"],
])
def test_json_before_terminator_and_unsupported_global_syntax(invoke, capsys, argv):
    assert invoke(argv) == 2
    document = json.loads(capsys.readouterr().out)
    assert document == {"version": 1, "error": document["error"]}
    assert "unrecognized arguments" in document["error"]


@pytest.mark.scenario("specloop-interface/json-argument-errors/human-errors-and-help-remain-compatible")
@pytest.mark.parametrize("argv, change, json_mode", [
    (["status", "--json", "change"], "change", True),
    (["status", "change", "--json"], "change", True),
    (["status", "--", "--json"], "--json", False),
    (["status", "--json", "--", "--json"], "--json", True),
])
def test_successful_flag_order_and_option_termination(invoke, monkeypatch, capsys, argv, change, json_mode):
    calls = []

    def status(args):
        calls.append(args)
        return 0

    monkeypatch.setattr(cli, "status", status)
    assert invoke(argv) == 0
    args, = calls
    assert args.change == change
    assert args.json is json_mode
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("exception", [RuntimeError("command failure"), SystemExit(7), KeyboardInterrupt()])
def test_command_exceptions_are_not_parser_errors(invoke, monkeypatch, capsys, exception):
    def status(args):
        raise exception

    monkeypatch.setattr(cli, "status", status)
    with pytest.raises(type(exception)) as error:
        invoke(["status", "--json"])
    assert error.value is exception
    assert capsys.readouterr().out == ""

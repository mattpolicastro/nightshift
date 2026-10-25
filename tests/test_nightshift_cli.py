"""Canonical routing preserves build contracts and daemon boundaries."""

import json
import sys

import pytest

from nightshift import cli
from specloop import cli as build
from test_specloop_argparse_json import INVALID_ARGUMENTS


def forbidden(*args, **kwargs):
    pytest.fail("parsing/build dispatch touched daemon state or configuration")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for target, names in ((cli.config, ("load", "load_env_file")),
                          (cli.daemon, ("loop", "startup")),
                          (cli.specloop_job, ("list_jobs", "stop_job", "release_job")),
                          (build, ("load_env", "runs_dir", "_ctx", "admit", "propose", "revise",
                                   "act", "status", "publish", "run_cmd", "pause_cmd", "resume",
                                   "accept", "waive", "abort", "finish", "drill_cmd", "serve_cmd"))):
        for name in names:
            monkeypatch.setattr(target, name, forbidden)
    monkeypatch.setattr(cli, "_daemon_command", forbidden)


@pytest.mark.scenario("nightshift-cli/explicit-daemon-controls/daemon-controls-retain-boundaries")
def test_parser_is_side_effect_free():
    parser = cli.build_parser()
    for argv in (["daemon"], ["daemon", "status"], ["run"], ["jobs"], ["attention"]):
        assert parser.parse_args(argv).handler is cli._daemon_command
    for argv in ([], ["status"], ["run", "change"], ["pause", "change"]):
        assert parser.parse_args(argv).handler is build.dispatch


@pytest.mark.scenario("nightshift-cli/canonical-build-commands/build-commands-retain-their-contract")
@pytest.mark.parametrize("argv, handler", [
    (["admit", "m.toml", "--implement", "impl", "--review", "review", "--head", "abc", "--json"], "admit"),
    (["propose", "m.toml", "change", "--idea-file", "idea.txt", "--source", "a", "--source", "b", "--json"], "propose"),
    (["revise", "change", "--feedback", "f.json", "--feedback-format", "json", "--round-id", "round", "--json"], "revise"),
    (["act", "change", "resume", "--expect", "pause-id", "--note", "quoted note", "--json"], "act"),
    (["status", "change", "--events", "20", "--json"], "status"),
    (["publish", "change", "--json"], "publish"),
    (["run", "change"], "run_cmd"), (["pause", "change"], "pause_cmd"),
    (["resume", "change", "--no-run", "--note", "a note"], "resume"),
    (["accept", "change", "1.1", "--no-run"], "accept"),
    (["waive", "change", "1.1", "a reason"], "waive"),
    (["abort", "change"], "abort"), (["finish", "change"], "finish"),
    (["drill", "change"], "drill_cmd"), (["serve", "--poll", "2.5", "--once"], "serve_cmd"),
])
def test_build_dispatch_equivalence(monkeypatch, capsys, argv, handler):
    calls = []
    def command(args):
        calls.append({k: v for k, v in vars(args).items() if k != "handler"})
        if hasattr(args, "document"):
            args.document["outcome"] = "refused"
        else:
            print(json.dumps({"version": 1, "error": "refusal"}))
        return 1
    monkeypatch.setattr(build, handler, command)
    assert build.main(argv) == 1
    legacy = capsys.readouterr()
    assert cli.main(argv) == 1
    assert capsys.readouterr() == legacy
    assert calls[0] == calls[1]
    assert json.loads(legacy.out)["version"] == 1


@pytest.mark.scenario("nightshift-cli/canonical-build-commands/build-commands-retain-their-contract")
@pytest.mark.parametrize("argv", [[], ["status"]])
def test_default_build_status(monkeypatch, argv):
    calls = []
    monkeypatch.setattr(build, "status", lambda args: calls.append(args) or 7)
    assert cli.main(argv) == 7
    args, = calls
    assert (args.cmd, args.change, args.events, args.json) == ("status", None, 6, False)


@pytest.mark.scenario("nightshift-cli/explicit-daemon-controls/bare-run-and-named-run-dispatch-separately")
@pytest.mark.parametrize("options", [[], ["--once"], ["--config", "chosen.toml", "--once"]])
def test_run_routing(monkeypatch, options):
    calls = []
    monkeypatch.setattr(cli, "_daemon_command", lambda args: calls.append(("daemon", args.command)) or 3)
    monkeypatch.setattr(build, "run_cmd", lambda args: calls.append(("build", args.change)) or 4)
    assert cli.main(["run", *options]) == 3
    assert cli.main(["daemon", "run", *options]) == 3
    assert cli.main(["run", "example-change"]) == 4
    assert calls == [("daemon", "run"), ("daemon", "run"), ("build", "example-change")]


@pytest.mark.scenario("nightshift-cli/explicit-daemon-controls/other-missing-changes-refuse")
@pytest.mark.parametrize("argv", [["pause"], ["resume"], ["run", "change", "--once"],
                                  ["run", "--config", "c.toml", "change"],
                                  ["--config", "c.toml", "run", "change"]])
def test_invalid_controls_refuse_before_dispatch(monkeypatch, capsys, argv):
    monkeypatch.setattr(build, "dispatch", forbidden)
    with pytest.raises(SystemExit) as error:
        cli.main(argv)
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.scenario("nightshift-cli/canonical-build-commands/machine-readable-errors-stay-singular")
@pytest.mark.parametrize("argv, diagnostic", INVALID_ARGUMENTS)
@pytest.mark.parametrize("process", [False, True])
def test_json_errors(monkeypatch, capsys, argv, diagnostic, process):
    monkeypatch.setattr(build, "dispatch", forbidden)
    argv = [*argv, "--json"]
    monkeypatch.setattr(sys, "argv", ["nightshift", *argv])
    assert (cli.main() if process else cli.main(argv)) == 2
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document == {"version": 1, "error": document["error"]}
    assert diagnostic in document["error"] and output.err == ""


@pytest.mark.scenario("nightshift-cli/canonical-build-commands/machine-readable-errors-stay-singular")
@pytest.mark.parametrize("argv", [["--json"], ["--json", "status"],
                                  ["status", "--json", "--", "one", "two"],
                                  ["run", "change", "--once", "--json"]])
def test_json_token_before_terminator(capsys, argv):
    assert cli.main(argv) == 2
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document == {"version": 1, "error": document["error"]}
    assert output.err == ""


@pytest.mark.scenario("nightshift-cli/canonical-build-commands/machine-readable-errors-stay-singular")
@pytest.mark.parametrize("argv, code", [(["--help", "--json"], 0), (["daemon", "--help"], 0),
                                       (["status", "--events", "bad", "--", "--json"], 2),
                                       (["status", "--events", "bad", "--jso"], 2),
                                       (["status", "--json=true"], 2)])
def test_help_and_human_errors(monkeypatch, capsys, argv, code):
    monkeypatch.setattr(build, "dispatch", forbidden)
    with pytest.raises(SystemExit) as error:
        cli.main(argv)
    assert error.value.code == code
    output = capsys.readouterr()
    assert "usage: nightshift" in (output.out if code == 0 else output.err)
    assert (output.err if code == 0 else output.out) == ""

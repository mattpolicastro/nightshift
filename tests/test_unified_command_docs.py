"""Validate command structure and routing without running operator commands."""

import re
import tomllib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from doc_commands import argv_cases, parse_command, parse_report, physical_commands
from nightshift import cli, config, deploy, land, timings, touchpoints
from specloop import cli as build_cli
from specloop.migrate import MAPPING
from test_specloop_documentation_integrity import LINK, ROOT, heading_ids


GUIDES = ("README.md", "docs/specloop/README.md", "docs/specloop/command-json.md")
REPORTS = {"nightshift.timings": timings.main, "nightshift.touchpoints": touchpoints.main}
ROLLOUT = ("KNOWN-LIMITATIONS.md", "docs/specloop/prepare.md", "docs/landing.md",
           "plugins/nightshift/README.md", "docs/validation.md", "docs/deploy.md",
           "config.example.toml")


@pytest.mark.scenario("specloop-documentation/unified-command-guidance/guides-distinguish-daemon-and-build")
def test_documented_state_mapping_matches_migration():
    rows = re.findall(r"^\| `(~/.specloop/[^`]+)` \| `(~/.nightshift/[^`]+)` \|$",
                      (ROOT / "docs/specloop/README.md").read_text(), re.M)
    expected = [(f"~/.specloop/{old}/<{item}>", f"~/.nightshift/{new}/<{item}>")
                for old, new in MAPPING.items()
                for item in ("entry" if old == "homes" else "change",)]
    assert rows == expected


@pytest.mark.scenario("specloop-documentation/unified-command-guidance/guides-distinguish-daemon-and-build")
@pytest.mark.scenario("specloop-documentation/active-job-status-guidance/operator-reads-job-status")
@pytest.mark.parametrize("path", GUIDES)
def test_core_commands_parse_without_dispatch(path, pytestconfig):
    parsed = check_commands(path, pytestconfig)
    for verb in ("run", "status", "pause", "resume"):
        assert any(args.handler is build_cli.dispatch and args.cmd == verb for args in parsed)
        assert any(args.handler is cli._daemon_command and args.command == verb for args in parsed)
    if path != "docs/specloop/command-json.md":
        assert any(getattr(args, "command", None) == "jobs" and getattr(args, "all", False)
                   for args in parsed)


def check_commands(path, pytestconfig):
    parsed = []
    for number, command in physical_commands((ROOT / path).read_text(),
                                             comments=path.endswith(".toml")):
        try:
            argv, *remaining = argv_cases(command)
            if argv[0] == "pytest":
                assert not remaining
                pytestconfig._parser.parse(argv[1:])
                continue
            if argv[:2] == ["python", "-m"] and argv[2] in REPORTS:
                assert not remaining
                parse_report(REPORTS[argv[2]], argv[3:])
                continue
            if argv[:3] == ["python", "-m", "nightshift.deploy"]:
                assert not remaining
                deploy.parse_options(argv[3:], SimpleNamespace(cwd=ROOT, env={}))
                continue
            if argv[:3] == ["python", "-m", "nightshift.land"]:
                assert not remaining
                try:
                    land.parser().parse_args(argv[3:])
                except SystemExit as result:
                    assert result.code == 0 and argv[3:] == ["--help"]
                continue
            parsed.extend(parse_command(command))
        except (AssertionError, ValueError, SystemExit) as error:
            pytest.fail(f"{path}:{number}: {command}: {error}")
    return parsed


@pytest.mark.scenario("specloop-documentation/safe-migration-guidance/documentation-checks-are-structural")
@pytest.mark.parametrize("path", ROLLOUT)
def test_rollout_commands_and_links(path, pytestconfig):
    check_commands(path, pytestconfig)
    document = ROOT / path
    for _, target in LINK.findall(document.read_text()):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        destination = document.parent / unquote(url.path) if url.path else document
        assert destination.resolve().is_relative_to(ROOT) and destination.exists(), target
        if url.fragment:
            assert unquote(url.fragment) in heading_ids(destination.read_text()), target


@pytest.mark.scenario("specloop-documentation/unified-command-guidance/example-config-points-to-daemon-status")
def test_config_settings_and_line_keyed_handlers(tmp_path):
    text = (ROOT / "config.example.toml").read_text()
    assert tomllib.loads(text) == {
        "daemon": {"poll_seconds": 120, "forbidden_probe": "example-owner/dotfiles"},
        "repos": [{"name": "example-owner/sample-sandbox", "base": "main",
                   "manifest": "~/.config/specloop/manifests/sample-sandbox.toml"}],
        "notify": {"slack_webhook": ""},
        "labels": {"ready": "agent:ready", "working": "agent:working", "done": "agent:done",
                   "needs_human": "needs-human", "spec_draft": "spec:draft",
                   "spec_review": "spec:review", "spec_approved": "spec:approved"},
    }
    commands = list(physical_commands(text, comments=True))
    # Physical line keys identify the output/alerting examples without reading prose.
    for line in (67, 116):
        command, = [command for number, command in commands if number == line]
        assert list(argv_cases(command)) == [["nightshift", "daemon", "status"]]
        args, = parse_command(command)
        assert args.handler is cli._daemon_command and args.command == "status"
    supervisor, = [command for number, command in commands if number == 66]
    assert list(argv_cases(supervisor)) == [["nightshift", "serve"]]
    args, = parse_command(supervisor)
    assert args.handler is build_cli.dispatch and args.cmd == "serve"
    selected = tmp_path / "config.toml"
    for enrollment in ('# engine = "specloop"', 'engine = "specloop"'):
        selected.write_text(text.replace('# engine = "specloop"', enrollment))
        loaded = config.load(selected)
        assert loaded.repos[0].engine == "specloop"
        assert loaded.worktree_root == Path.home() / "Projects/nightshift-wt"
    selected.write_text(text.replace('# engine = "specloop"', 'engine = "worker"'))
    with pytest.raises(ValueError, match="worker engine is retired"):
        config.load(selected)


@pytest.mark.scenario("specloop-documentation/safe-migration-guidance/deployment-and-validation-use-safe-controls")
@pytest.mark.parametrize("path", ("docs/deploy.md", "docs/validation.md"))
def test_rollout_control_sequence(path):
    cases = [argv[1:] for _, command in physical_commands((ROOT / path).read_text())
             for argv in argv_cases(command) if argv[0] == "nightshift"]
    sequence = [["migrate"], ["status", "--json"],
                ["resume", "example-change", "--no-run"], ["pause", "example-change"],
                ["status", "example-change", "--json"],
                ["resume", "example-change", "--no-run"], ["daemon", "resume"]]
    position = 0
    for argv in sequence:
        position = cases.index(argv, position) + 1
    assert ["daemon", "pause"] in cases
    assert ["daemon", "status"] in cases
    assert ["serve", "--once"] not in cases


@pytest.mark.parametrize("surround", ["{}", "  {}", "```sh\n{}\n```", "<!--\n{}\n-->",
                                    "prose `{}`", "    `{}`", "<!-- `{}` -->", "# {}"])
@pytest.mark.parametrize("command", ["nightshift status", "specloop status",
                                   "uv run nightshift status", "python -m nightshift.cli status"])
def test_physical_extraction_ignores_markdown_context(surround, command):
    matches = list(physical_commands(surround.format(command), comments=True))
    assert len(matches) == 1 and matches[0][1] == command


@pytest.mark.parametrize("surround", ["{}", "    {}", "```sh\n{}\n```", "<!--\n{}\n-->"])
@pytest.mark.scenario("specloop-documentation/safe-migration-guidance/documentation-checks-are-structural")
@pytest.mark.parametrize("command", ["`nightshift status", "nightshift status`",
    "nightshift pause", "nightshift resume", "nightshift bogus", "nightshift run <unknown>",
    "nightshift status --config config.toml", "nightshift status --once",
    "nightshift pause --config config.toml", "nightshift resume --config config.toml",
    "nightshift status && nightshift serve", "nightshift status;nightshift serve",
    "nightshift run example#tag; nightshift serve",
    "nightshift run $(whoami)", "nightshift run [CHANGE]", "nightshift run CHANGE",
    "nightshift run prefix[<change>]", "nightshift run [<change>]suffix",
    'nightshift run "[<change>]"', "nightshift run <change> > output",
    'nightshift run "prefix [<change>] suffix"',
    "nightshift run *", "nightshift run foo(bar)", "nightshift status \\"])
def test_ambiguous_commands_fail_closed(command, surround):
    with pytest.raises((AssertionError, ValueError, SystemExit)):
        for _, candidate in physical_commands(surround.format(command)):
            parse_command(candidate)


@pytest.mark.parametrize("command,expected", [
    ("uv run nightshift status [<change>]", [["status"], ["status", "example-change"]]),
    ('nightshift resume <change> [--note "..."]', [
        ["resume", "example-change"], ["resume", "example-change", "--note", "Review feedback"]]),
    ("nightshift admit <manifest> [--pr <url>]", [
        ["admit", "repo.toml"], ["admit", "repo.toml", "--pr", "https://github.com/o/r/pull/1"]]),
    ("python -m nightshift.cli jobs stop <id>", [["jobs", "stop", "0123456789abcdef0123456789abcdef"]]),
    ('nightshift resume <change> --note "Keep #123" # operator note', [
        ["resume", "example-change", "--note", "Keep #123"]]),
])
def test_enumerated_argv_cases(command, expected):
    assert list(argv_cases(command)) == [["nightshift", *args] for args in expected]
    assert len(parse_command(command)) == len(expected)


def test_comment_rule_strips_at_most_one_hash_and_preserves_every_span():
    assert list(physical_commands("# # nightshift status", comments=True)) == []
    assert list(physical_commands("# nightshift status")) == []
    assert list(physical_commands("`nightshift status` and `nightshift status`")) == [
        (1, "nightshift status"), (1, "nightshift status")]


def test_parser_checks_never_dispatch(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("documentation parsing dispatched an operator command")

    monkeypatch.setattr(build_cli, "dispatch", forbidden)
    monkeypatch.setattr(cli, "_daemon_command", forbidden)
    for command in ("nightshift run example-change", "nightshift daemon run",
                    "nightshift migrate", "specloop serve"):
        args, = parse_command(command)
        assert args.handler is forbidden

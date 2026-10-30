"""Literal physical boundaries and executable examples, independent of prose."""

import re
import shlex
from pathlib import Path

import pytest

from nightshift import arm, config
from test_arm import issue_body
from test_arm_forge import arming_io, configured
from test_forge_publication_docs import commands_parse, links_resolve, passage
from test_specloop_documentation_integrity import ROOT

HEADING = "## Configured Gitea"
COMMAND = "uv run python -m nightshift.arm --config /absolute/path/to/config.toml"


def arming_commands(text):
    lines = passage(text, HEADING).splitlines()
    fences = [line for line in lines if re.search(r"`{3,}|~{3,}", line)]
    assert fences == ["```sh", "```"], "ambiguous physical fences"
    start, end = lines.index("```sh"), lines.index("```")
    assert start < end
    commands = []
    for line in lines[start + 1:end]:
        if not line.strip():
            continue
        assert not re.search(r"[;&|<>\\$`{}]", line), "shell syntax or placeholder"
        argv = shlex.split(line)
        assert argv[:5] == ["uv", "run", "python", "-m", "nightshift.arm"]
        commands.append(argv[5:])
    assert commands
    return commands


@pytest.mark.scenario("specloop-documentation/configured-arming-guidance/operator-configures-arming")
@pytest.mark.scenario("specloop-documentation/configured-arming-guidance/arming-examples-are-structurally-checked")
def test_configured_examples_run(arming_io, configured, monkeypatch):
    fake, _, args, _ = arming_io
    document = ROOT / "docs/arming.md"
    assert links_resolve(document.read_text(), document)
    commands = arming_commands(document.read_text())
    assert len(commands) == 2 and ["--check" in argv for argv in commands] == [True, False]
    load = config.load
    def example_config(path):
        assert path == Path("/absolute/path/to/config.toml")
        return load(Path(args[1]))
    monkeypatch.setattr(config, "load", example_config)
    for index, argv in enumerate(commands):
        assert arm.main(argv) == 0  # Real argparse, selection, adapter and fake HTTP IO.
        assert len([c for c in fake.calls if c.method != "GET"]) == index


@pytest.mark.scenario("specloop-documentation/gitea-arming-support-boundary/operator-understands-the-continuing-hold")
@pytest.mark.scenario("specloop-documentation/explicit-forge-command-guidance/operator-selects-and-publishes")
@pytest.mark.scenario("specloop-documentation/explicit-forge-command-guidance/explicit-commands-do-not-enable-the-daemon")
def test_shared_guidance_links_and_publication_commands():
    for path, heading in (("KNOWN-LIMITATIONS.md", "## Open gaps"),
                          ("docs/specloop/README.md", "### Explicit forge publication")):
        document = ROOT / path
        text = passage(document.read_text(), heading)
        links = links_resolve(text, document, nonlinks=("[--note TEXT]",))
        assert any((document.parent / link[2].split("#")[0]).resolve() == ROOT / "docs/arming.md"
                   for link in links)
        if path.startswith("docs/"):
            assert {args.cmd for args in commands_parse(text)} == {"publish", "finish"}


@pytest.mark.scenario("specloop-documentation/configured-arming-guidance/arming-examples-are-structurally-checked")
@pytest.mark.parametrize("surround", ["{}", "```markdown\n{}\n```", "<!--\n{}\n-->"])
def test_physical_boundaries_fail_closed(surround):
    valid = f"{HEADING}\n```sh\n{COMMAND} --check 76\n```\n## End\nignored"
    expected = arming_commands(valid)
    assert arming_commands(surround.format(valid)) == expected
    assert arming_commands(valid.replace("## End", "# End")) == expected
    bad = ["", valid.replace(HEADING, " " + HEADING), valid + "\n" + HEADING,
           valid.replace("```sh", "```"), valid.replace("```sh", " ```sh"),
           valid.replace("\n```\n", "\n ```\n"), valid.replace("```sh\n", ""),
           valid.replace("\n```\n", "\n"), valid.replace("```sh", "```sh\n```sh"),
           valid.replace(COMMAND, COMMAND + " ```"), valid.replace("```sh", "~~~sh"),
           valid.replace("```sh", "```\n```sh"), valid.replace("## End", "## End\n" + HEADING)]
    bad += [valid.replace(COMMAND, command) for command in (
        COMMAND + " && echo x", COMMAND + " \\", COMMAND + " <issue>",
        COMMAND + " $(id)", COMMAND.replace("nightshift.arm", "nightshift"))]
    for text in bad:
        with pytest.raises((AssertionError, ValueError)):
            arming_commands(surround.format(text))

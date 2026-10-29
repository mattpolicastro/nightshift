"""Physical passage boundaries and executable structure, never prose wording."""

import re
from urllib.parse import unquote, urlsplit

import pytest

from doc_commands import TICK, argv_cases, physical_commands
from nightshift import cli
from specloop import cli as build_cli
from test_specloop_documentation_integrity import LINK, ROOT, heading_ids


def passage(text, heading):
    lines = text.splitlines()
    assert lines.count(heading) == 1, heading
    start = lines.index(heading) + 1
    level = len(heading.split()[0])
    end = next((i for i in range(start, len(lines))
                if re.match(rf"^#{{1,{level}}} \S", lines[i])), len(lines))
    return "\n".join(lines[start:end])


def links_resolve(text, document, *, nonlinks=()):
    matches = list(LINK.finditer(text))
    remainder = LINK.sub("", text)
    for literal in nonlinks:
        remainder = remainder.replace(literal, "")
    assert not re.search(r"[\[\]]|\]\(", remainder), "ambiguous Markdown link"
    for match in matches:
        target = match[2]
        assert not re.search(r'''[()<>\\'"`]''', target)
        assert text[max(0, match.start() - 1):match.start()] not in ("!", "\\")
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        destination = document.parent / unquote(url.path) if url.path else document
        assert destination.resolve().is_relative_to(ROOT) and destination.is_file(), target
        if url.fragment:
            assert unquote(url.fragment) in heading_ids(destination.read_text()), target
    return matches


def commands_parse(text):
    for line in text.splitlines():
        assert len(TICK.findall(line)) % 2 == 0, "unmatched backtick"
    parsed = []
    for _, command in physical_commands(text):
        command = command.replace("<change>", "example-change")
        assert not re.search(r"[<>\[\]{}]|\b[A-Z][A-Z_]+\b", command)
        argv, = argv_cases(command)
        assert argv[0] in ("nightshift", "specloop")
        parsed.append(cli.build_parser().parse_args(argv[1:]))
    return parsed


@pytest.mark.scenario("specloop-documentation/explicit-forge-command-guidance/operator-selects-and-publishes")
@pytest.mark.scenario("specloop-documentation/explicit-forge-command-guidance/explicit-commands-do-not-enable-the-daemon")
@pytest.mark.scenario("specloop-documentation/forge-guidance-integrity/examples-remain-structurally-valid")
def test_explicit_publication_examples_parse_without_dispatch(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("documentation parsing dispatched a command")

    monkeypatch.setattr(build_cli, "dispatch", forbidden)
    document = ROOT / "docs/specloop/README.md"
    text = passage(document.read_text(), "### Explicit forge publication")
    assert links_resolve(text, document)
    parsed = commands_parse(text)
    assert {args.cmd for args in parsed} == {"publish", "finish"}
    assert all(args.handler is forbidden and args.change == "example-change" for args in parsed)


@pytest.mark.scenario("specloop-documentation/factory-forge-action-guidance/factory-operator-understands-the-destination")
@pytest.mark.scenario("specloop-documentation/gitea-operation-limitations/operator-understands-residual-risks")
@pytest.mark.scenario("specloop-documentation/current-limitations/fixed-limitations-leave-the-open-list")
@pytest.mark.scenario("specloop-documentation/current-limitations/remaining-ledger-gaps-are-explicit")
@pytest.mark.scenario("specloop-documentation/gitea-readiness-evidence-limits/preflight-does-not-enable-execution")
@pytest.mark.parametrize("path,heading", [
    ("plugins/nightshift/README.md", "## Factory"),
    ("KNOWN-LIMITATIONS.md", "## Open gaps"),
])
def test_guidance_references_resolve(path, heading):
    document = ROOT / path
    # The existing act synopsis has one literal optional argument, not a link.
    assert links_resolve(passage(document.read_text(), heading), document,
                         nonlinks=("[--note TEXT]",))


@pytest.mark.scenario("specloop-documentation/forge-guidance-integrity/examples-remain-structurally-valid")
@pytest.mark.parametrize("surround", ["{}", "    {}", "```sh\n{}\n```", "<!--\n{}\n-->"])
def test_physical_rules_and_fail_closed_ambiguity(surround):
    heading = "### Explicit forge publication"
    body = surround.format("nightshift publish <change>")
    text = heading + "\n" + body + "\n#### Child\n`nightshift finish <change>`"
    assert len(commands_parse(passage(text + "\n## End\ninvalid", heading))) == 2
    for bad in ("", text + "\n" + surround.format(heading).replace("    ", "")):
        with pytest.raises(AssertionError):
            passage(bad, heading)
    for command in ("`nightshift publish <change>", "nightshift publish <id>",
                    "nightshift publish NAME", "nightshift publish <change> && nightshift serve",
                    "nightshift publish $(whoami)", "nightshift publish <change> > out",
                    "nightshift publish", "nightshift unknown <change>"):
        with pytest.raises((AssertionError, ValueError, SystemExit)):
            commands_parse(surround.format(command))
    document = ROOT / "docs/specloop/README.md"
    assert links_resolve(surround.format("[guide](README.md#explicit-forge-publication)"), document)
    for link in ("[x][ref]", "[x](README.md", "[x](README.md (title))", "[x](missing)",
                 "[x](README.md#missing)", "[x](README.md(foo))", "[ref]: README.md"):
        with pytest.raises(AssertionError):
            links_resolve(surround.format(link), document)

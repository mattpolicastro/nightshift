"""Physical documentation contracts; prose and device review remain human checks.

Headings, table columns/keys and argv are structure. Fences, HTML and indentation
do not suppress recognized lines. Public examples use existing parsers only.
"""

from dataclasses import fields
import re
from urllib.parse import unquote, urlsplit

import pytest

from doc_commands import argv_cases, parse_command, physical_commands
from nightshift import cli, factory_land, land
from specloop import cli as build_cli
from specloop import messages
from test_specloop_documentation_integrity import LINK, ROOT, heading_ids
from test_unified_command_docs import check_commands


DOCS = ("plugins/nightshift/README.md", "docs/specloop/README.md", "README.md",
        "KNOWN-LIMITATIONS.md", "docs/landing.md")
RECOVERY = {"docs/landing.md": "Recovering from a refusal",
            "KNOWN-LIMITATIONS.md": "Factory landing"}
CASES = {"lost-response", "stopped", "worker-death", "duplicate-markers",
         "revised-head", "unpushed-archive", "pushed-archive-ready-failure", "done-run"}
HEADING = re.compile(r"^(#{1,6}) +(\S.*)$", re.M)
RECOVERY_COLUMNS = ("Recovery case", "Inspect and resolve", "Next step")


def section(document, title):
    headings = list(HEADING.finditer(document))
    selected = [h for h in headings if h[2] == title]
    assert len(selected) == 1, (title, "missing/duplicate heading")
    start, = selected
    end = next((h.start() for h in headings if h.start() > start.start()
                and len(h[1]) <= len(start[1])), len(document))
    return document[start.end():end]


def table(document, columns):
    # A row is a physical line, including in fences/HTML or under indentation.
    def cells(line):
        trimmed = line.strip()
        if trimmed.startswith("|") and trimmed.endswith("|"):
            return tuple(cell.strip() for cell in trimmed[1:-1].split("|"))
        return None

    rows = [row for line in document.splitlines() if (row := cells(line)) is not None]
    headers = [i for i, row in enumerate(rows) if row == columns]
    assert len(headers) == 1, (columns, "missing/duplicate table")
    index, = headers

    def is_separator(row):
        return all(re.fullmatch(r":?-{3,}:?", cell) for cell in row)

    separator = rows[index + 1] if index + 1 < len(rows) else None
    assert separator and len(separator) == len(columns)
    assert is_separator(separator)
    result = {}
    for position in range(index + 2, len(rows)):
        row = rows[position]
        # The next column schema starts another table. Prose, fences and HTML
        # between rows do not hide duplicates or otherwise terminate selection.
        if position + 1 < len(rows) and is_separator(rows[position + 1]):
            break
        assert len(row) == len(columns) and all(row), row
        key = row[0].strip("`")
        assert key not in result, (key, "duplicate row")
        result[key] = row[1:]
    assert result, (columns, "empty table")
    return result


def recovery(path):
    rows = table(section((ROOT / path).read_text(), RECOVERY[path]), RECOVERY_COLUMNS)
    assert set(rows) == CASES
    return rows


def commands(text):
    return [argv for _, command in physical_commands(text) for argv in argv_cases(command)]


def links_resolve(path, document):
    for _, target in LINK.findall(document):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        destination = ROOT / path
        if url.path:
            destination = destination.parent / unquote(url.path)
        assert destination.resolve().is_relative_to(ROOT) and destination.exists(), target
        if url.fragment:
            assert unquote(url.fragment) in heading_ids(destination.read_text()), target


@pytest.mark.scenario("specloop-documentation/factory-landing-entry/operator-chooses-land-or-finish")
@pytest.mark.parametrize("path,title", [
    (DOCS[0], "Confirmed landing"), (DOCS[1], "Factory landing"),
])
def test_frozen_offer_schema_and_recovery_link(path, title):
    passage = section((ROOT / path).read_text(), title)
    offer = table(passage, ("Offer field", "Bound value"))
    assert set(offer) == {field.name for field in fields(factory_land.Offer)}
    assert any(target.endswith("landing.md#recovering-from-a-refusal")
               for _, target in LINK.findall(passage))
    # Land remains outside the canonical act vocabulary.
    verbs = {verb for reason in ("phase_gate", "stuck", "ready_to_publish", "human_checks")
             for verb in messages.actions_for_pause(reason)}
    actions = table(section((ROOT / DOCS[0]).read_text(), "Factory"),
                    ("Action", "When and what it does"))
    assert {action.lower() for action in actions} == verbs


@pytest.mark.scenario("specloop-documentation/landing-control-boundaries/operator-handles-a-stopped-or-interrupted-landing")
@pytest.mark.parametrize("path", RECOVERY)
def test_control_recovery_cases_and_adjacent_intake_reference(path):
    rows = recovery(path)
    for key in ("lost-response", "stopped", "worker-death"):
        # Control recovery is UI/prose only; no invented terminal control route.
        assert commands(" ".join(rows[key])) == []
    passage = section((ROOT / "README.md").read_text(), "Pause and job control")
    assert any(target == "docs/landing.md#recovering-from-a-refusal"
               for _, target in LINK.findall(passage))
    assert ["nightshift", "jobs", "release", "0123456789abcdef0123456789abcdef"] in commands(passage)


@pytest.mark.scenario("specloop-documentation/landing-evidence-retention/operator-interprets-landing-history")
def test_evidence_schema_and_display_limits():
    passage = section((ROOT / DOCS[0]).read_text(), "Landing evidence")
    artifacts = table(passage, ("Evidence artifact", "Retention"))
    assert set(artifacts) == {"request.json", "process.json", "progress.json",
                              "output.log", "result.json"}
    settings = table(passage, ("Landing setting", "Value"))
    assert {key: int(value[0]) for key, value in settings.items()} == {
        "History limit": factory_land.MAX_LIMIT, "Overdue seconds": factory_land.OVERDUE_SECONDS,
    }
    for path, title in ((DOCS[1], "Factory landing"), ("KNOWN-LIMITATIONS.md", "Factory landing")):
        assert any(target.endswith("plugins/nightshift/README.md#landing-evidence")
                   for _, target in LINK.findall(section((ROOT / path).read_text(), title)))


@pytest.mark.scenario("specloop-documentation/delayed-attestation-recovery-guidance/operator-resolves-duplicate-markers")
@pytest.mark.parametrize("path", RECOVERY)
def test_duplicate_marker_recovery_parses_existing_terminal_land(path):
    argv, = commands(" ".join(recovery(path)["duplicate-markers"]))
    assert argv[:3] == ["python", "-m", "nightshift.land"]
    args = land.parser().parse_args(argv[3:])
    assert args.checks_done and args.pr == "https://github.com/o/r/pull/1"


@pytest.mark.scenario("specloop-documentation/guarded-finish-guidance/operator-encounters-a-revised-head")
def test_documented_guard_is_optional_and_uses_real_finish_parser():
    passage = section((ROOT / "docs/landing.md").read_text(), "Finish, CI, and merge")
    example, = [c for _, c in physical_commands(passage)]
    guarded, = parse_command(example)
    assert guarded.cmd == "finish" and guarded.handler is build_cli.dispatch
    assert guarded.expect_head == "0123456789abcdef0123456789abcdef01234567"
    for path in RECOVERY:
        argv, = commands(" ".join(recovery(path)["revised-head"]))
        assert argv == ["nightshift", "status", "example-change", "--events", "20"]


@pytest.mark.scenario("specloop-documentation/guarded-finish-guidance/operator-recovers-an-unpushed-archive")
@pytest.mark.parametrize("path", RECOVERY)
def test_unpushed_archive_has_explicit_unguarded_finish_then_done_land(path):
    rows = recovery(path)
    examples = list(physical_commands(" ".join(rows["unpushed-archive"])))
    direct, act = [parse_command(command)[0] for _, command in examples[:2]]
    assert direct.cmd == "finish" and direct.expect_head is None
    assert direct.handler is build_cli.dispatch
    assert act.cmd == "act" and act.verb == "finish" and act.json
    assert act.handler is build_cli.dispatch
    if path == "docs/landing.md":
        # Recovery also works when only the interpreter can import Nightshift.
        assert examples[0][1] == "python -m nightshift.cli finish <change>"
    assert len(examples) == 3
    assert commands(examples[2][1]) == commands(" ".join(rows["done-run"]))
    assert commands(" ".join(rows["pushed-archive-ready-failure"])) == []


@pytest.mark.scenario("specloop-documentation/landing-documentation-structure/examples-describe-supported-behavior")
@pytest.mark.parametrize("path", DOCS)
def test_public_examples_and_links_without_dispatch(path, pytestconfig, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("documentation validation dispatched a command")

    # Parser routing compares handler identity; keep the sentinels distinct.
    monkeypatch.setattr(build_cli, "dispatch", lambda *a, **kw: forbidden(*a, **kw))
    monkeypatch.setattr(cli, "_daemon_command", lambda *a, **kw: forbidden(*a, **kw))
    monkeypatch.setattr(land, "main", forbidden)
    monkeypatch.setattr(factory_land, "main", forbidden)
    check_commands(path, pytestconfig)  # Existing routing; private transport has no examples.
    links_resolve(path, (ROOT / path).read_text())


@pytest.mark.scenario("specloop-documentation/landing-documentation-structure/examples-describe-supported-behavior")
def test_live_review_checklist_remains_unperformed():
    passage = section((ROOT / "docs/landing.md").read_text(), "Remaining human validation")
    items = re.findall(r"^- \[([ xX])\] ", passage, re.M)
    assert len(items) >= 4 and set(items) == {" "}


@pytest.mark.scenario("specloop-documentation/landing-documentation-structure/examples-describe-supported-behavior")
@pytest.mark.parametrize("surround", ["{}", "```md\n{}\n```", "<!--\n{}\n-->"])
def test_physical_structure_counts_hidden_headings_and_indented_rows(surround):
    content = "###   Selected\n    | Key | Value |\n    | --- | --- |\n    | item | prose |"
    assert table(section(surround.format(content), "Selected"), ("Key", "Value")) == {
        "item": ("prose",),
    }
    with pytest.raises(AssertionError):
        section(content + "\n" + surround.format(content), "Selected")


@pytest.mark.scenario("specloop-documentation/landing-documentation-structure/examples-describe-supported-behavior")
@pytest.mark.parametrize("document", [
    "", "    ### Selected\n", "####Selected\n", "### Selected\n### Selected\n",
    "### Selected\n| Wrong | Value |\n| --- | --- |\n| item | prose |",
    "### Selected\n| Key | Value |\n| --- | --- |",
    "### Selected\n| Key | Value |\n| --- | --- |\n| item | prose |\n| item | other |",
    "### Selected\n| Key | Value |\n| --- | --- |\n| item | prose |\n\n"
    "<!--\n    | item | other |\n-->",
    "### Selected\n| Key | Value |\n| --- | --- |\n| item | prose |\n\n"
    "<!--\n| Key | Value |\n| --- | --- |\n| other | prose |\n-->",
    "### Selected\n| Key | Value |\n| --- | --- |\n| item | prose | extra |",
])
def test_missing_or_ambiguous_selections_fail_closed(document):
    with pytest.raises(AssertionError):
        table(section(document, "Selected"), ("Key", "Value"))


@pytest.mark.scenario("specloop-documentation/landing-documentation-structure/examples-describe-supported-behavior")
@pytest.mark.parametrize("surround", ["{}", "    {}", "```sh\n{}\n```", "<!--\n{}\n-->",
                                    "prose `{}`", "    `{}`", "<!-- `{}` -->"])
@pytest.mark.parametrize("command", ["uv run python -m nightshift.land <url> --checks-done",
                                   "nightshift finish <change>"])
def test_supported_examples_are_not_hidden_by_markdown(surround, command):
    extracted, = physical_commands(surround.format(command))
    assert extracted[1] == command
    argv, = argv_cases(extracted[1])
    if argv[:3] == ["python", "-m", "nightshift.land"]:
        assert land.parser().parse_args(argv[3:]).checks_done
    else:
        parsed, = parse_command(extracted[1])
        assert parsed.cmd == "finish" and parsed.expect_head is None


@pytest.mark.scenario("specloop-documentation/landing-documentation-structure/examples-describe-supported-behavior")
@pytest.mark.parametrize("surround", ["{}", "    {}", "```sh\n{}\n```", "<!--\n{}\n-->"])
@pytest.mark.parametrize("command", [
    "`uv run python -m nightshift.land <url>",
    "uv run python -m nightshift.land <url>`",
    "uv run python -m nightshift.land <unknown>",
    "nightshift finish <change> --expect-head <unknown>",
    "nightshift finish <change> \\",
    "uv run python -m nightshift.land <url> \\",
])
def test_ambiguous_commands_fail_closed(surround, command):
    with pytest.raises((AssertionError, ValueError, SystemExit)):
        for argv in commands(surround.format(command)):
            if argv[:3] == ["python", "-m", "nightshift.land"]:
                land.parser().parse_args(argv[3:])
            else:
                cli.build_parser().parse_args(argv[1:])

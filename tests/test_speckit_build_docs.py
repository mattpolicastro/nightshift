"""Native build documentation: physical boundaries, parsers and local links.

These checks deliberately do not interpret Markdown containers, HTML or fences
when selecting headings, and do not pin explanatory prose.
"""

import re
import shlex
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from nightshift import cli
from specloop import cli as build_cli, manifest, speckit, spec_format, tasks


ROOT = Path(__file__).resolve().parents[1]
GUIDE = "docs/specloop/README.md"
PRESET = "presets/nightshift-speckit/README.md"
TEMPLATE = "presets/nightshift-speckit/templates/tasks-template.md"
HOUSE = "docs/specloop/speckit.md"
TITLE = "# Nightshift spec-kit preset"
HARNESS = "## Harness-owned Review and Completion"
LINK = re.compile(r"\[[^\[\]\n]+\]\(([^\s)]+)\)")


def read(path):
    return (ROOT / path).read_text()


def unique_line(lines, boundary):
    assert lines.count(boundary) == 1, (boundary, "missing/duplicate boundary")
    # Reject whitespace lookalikes rather than silently selecting a nearby line.
    assert [line for line in lines if line.strip() == boundary.strip()] == [boundary]
    return lines.index(boundary)


def passage(text, heading):
    assert re.fullmatch(r"#{1,6} \S.*", heading)
    lines = text.split("\n")
    start = unique_line(lines, heading) + 1
    level = len(heading.split(" ", 1)[0])
    end = next((i for i in range(start, len(lines))
                if re.match(rf"#{{1,{level}}}[ \t]", lines[i])), len(lines))
    if end < len(lines):
        unique_line(lines, lines[end])
    return "\n".join(lines[start:end])


def introduction(text):
    lines = text.split("\n")
    start = unique_line(lines, TITLE) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), None)
    assert end is not None, "missing introduction end"
    unique_line(lines, lines[end])
    assert not any(line.startswith("# ") for line in lines[start:end])
    return "\n".join(lines[start:end])


def fenced(text, language):
    lines = text.split("\n")
    opening = "```" + language
    delimiters = [line for line in lines if re.match(r"\s*(?:`{3,}|~{3,})", line)]
    assert delimiters == [opening, "```"], "missing/ambiguous physical fence pair"
    start = unique_line(lines, opening)
    end = unique_line(lines, "```")
    assert start < end
    return "\n".join(lines[start + 1:end])


def local_links(path, text):
    targets = LINK.findall(text)
    for target in targets:
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        destination = ROOT / path
        if url.path:
            destination = destination.parent / unquote(url.path)
        assert destination.resolve().is_relative_to(ROOT) and destination.is_file(), target
        if url.fragment:
            headings = re.findall(r"^#{1,6} (.*)$", destination.read_text(), re.M)
            anchors = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
            assert unquote(url.fragment) in anchors, target
    return set(targets)


def inline(text):
    return set(re.findall(r"(?<!`)`([^`\n]+)`(?!`)", text))


def parse_commands(text, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("documentation command dispatched")

    monkeypatch.setattr(build_cli, "dispatch", forbidden)
    parsed = []
    for line in text.split("\n"):
        argv = shlex.split(line, comments=True)
        if not argv:
            continue
        if argv[:2] == ["uv", "run"]:
            argv = argv[2:]
        assert argv.pop(0) == "nightshift"
        args = cli.build_parser().parse_args(argv)
        assert args.handler is forbidden
        parsed.append(args)
    return parsed


@pytest.mark.scenario("specloop-documentation/native-manifest-example/example-selects-native-build-and-coverage")
@pytest.mark.scenario("specloop-documentation/format-selection-guidance/operator-selects-the-supported-format")
def test_complete_native_manifest_and_default_selection():
    native = manifest.load(ROOT / "examples/specloop/speckit.toml")
    assert spec_format.get(native) is spec_format.SPECKIT
    assert {"specs/**", ".specify/**", ".github/**", ".gitea/workflows/**", ".forgejo/**"} <= set(native.frozen)
    assert native.phase_gates and all(n > 0 for n in native.phase_gates)
    fixture = ROOT / "tests/fixtures/speckit"
    fmt = spec_format.get(native)
    assert fmt.validate(fixture, native.change) == []
    feature_tasks = fmt.parse_tasks((fixture / fmt.tasks_path(native.change)).read_text())
    assert set(native.phase_gates) <= {task.section for task in feature_tasks}
    assert {sid for task in feature_tasks for sid in task.scenarios} == set(fmt.scenarios(fixture, native.change))
    assert native.roles["implement"].adapter in manifest.ADAPTERS
    assert native.roles["review"].adapter in manifest.ADAPTERS
    step, = [step for step in native.verify if step.id == native.coverage_step]
    assert ("-p", "scenario_plugin") in list(zip(step.run, step.run[1:]))
    assert "PYTHONPATH={src}:{checks}" in step.run
    assert "{src}/tests" in step.run
    assert {"venv", "checks"} <= native.vars.keys()
    # This is a configuration example, not permission to execute a verifier.
    values = {**native.vars, "src": "/source", "build": "/build", "sha": "0" * 40}
    assert all(not re.search(r"\{[^}]+\}", manifest.expand(arg, values)) for arg in step.run)
    old = manifest.load(ROOT / "examples/specloop/self-hosting.toml")
    assert old.format == "openspec" and spec_format.get(old) is spec_format.OPENSPEC
    raw = tomllib.loads(read("examples/specloop/self-hosting.toml"))
    del raw["run"]["format"]
    assert manifest.parse(raw) == old


@pytest.mark.scenario("specloop-documentation/native-build-operator-guidance/operator-finishes-the-selected-format")
def test_native_commands_parse_without_dispatch(monkeypatch):
    body = passage(read(GUIDE), "### Native spec-kit builds")
    parsed = parse_commands(fenced(body, "sh"), monkeypatch)
    assert [args.cmd for args in parsed] == ["admit", "run", "publish", "finish"]
    m = manifest.load(ROOT / "examples/specloop/speckit.toml")
    assert all(args.change == m.change for args in parsed)
    assert parsed[0].branch == m.branch and parsed[0].skip_feasibility and parsed[0].json
    assert Path(parsed[0].manifest).name == "speckit.toml"
    assert parsed[2].json
    contract = passage(read("docs/specloop/command-json.md"), "## Manifest format")
    admission, = parse_commands(fenced(contract, "sh"), monkeypatch)
    assert admission.cmd == "admit" and admission.skip_feasibility and admission.json
    assert Path(admission.manifest).name == "speckit.toml"
    assert {"specs/**", ".specify/**", "[paths] frozen", "[gates] phases",
            "## Phase N: Title", '[coverage] step = "unit-tests"'} <= inline(body)
    assert "../../examples/specloop/scenario_plugin.py" in local_links(GUIDE, body)


@pytest.mark.scenario("specloop-documentation/native-build-operator-guidance/operator-finishes-the-selected-format")
@pytest.mark.scenario("specloop-documentation/factory-forge-action-guidance/factory-operator-understands-the-destination")
@pytest.mark.parametrize("path,heading", [
    ("README.md", "### Enroll a repository"),
    (GUIDE, "## Publication and base recovery"),
    (GUIDE, "### Explicit forge publication"),
    (GUIDE, "## How the engine works"),
    ("plugins/nightshift/README.md", "### Confirmed landing"),
    ("KNOWN-LIMITATIONS.md", "## Factory landing"),
])
def test_finish_lifecycle_tokens_and_links(path, heading):
    body = passage(read(path), heading)
    assert {"openspec/specs/", "**Status**: Complete"} <= inline(body)
    local_links(path, body)


@pytest.mark.scenario("specloop-documentation/factory-forge-action-guidance/factory-operator-understands-the-destination")
def test_plugin_finish_table_and_confirmation_links():
    path = "plugins/nightshift/README.md"
    body = passage(read(path), "## Factory")
    finish, = [line for line in body.split("\n") if line.startswith("| Finish |")]
    assert finish.endswith("|") and len(finish.split("|")) == 4
    assert {"human_checks", "openspec/specs/", "**Status**: Complete"} <= inline(finish)
    assert "NIGHTSHIFT_FORGE_CONFIG" in inline(body)
    assert "../../docs/specloop/README.md#explicit-forge-publication" in local_links(path, body)


@pytest.mark.scenario("specloop-documentation/source-local-credential-guidance/operator-publishes-and-finishes-with-local-helpers")
def test_local_helper_publication_examples(monkeypatch):
    body = passage(read(GUIDE), "### Explicit forge publication")
    commands = fenced(body, "sh").replace("<change>", "042-add-a-gate")
    assert [args.cmd for args in parse_commands(commands, monkeypatch)] == ["publish", "finish"]
    assert {"credential.helper", "repo.git/config", "gh", "openspec/specs/", "**Status**: Complete"} <= inline(body)
    assert "https://github.com/mattpolicastro/nightshift/issues/174" in local_links(GUIDE, body)


@pytest.mark.scenario("specloop-documentation/format-selection-guidance/operator-selects-the-supported-format")
@pytest.mark.scenario("specloop-documentation/format-selection-guidance/compatibility-claims-retain-their-limits")
def test_format_references_and_pending_legacy_resume():
    for path, heading in ((GUIDE, "## How the engine works"),
                          ("docs/specloop/command-json.md", "## Manifest format")):
        body = passage(read(path), heading)
        assert {'format = "openspec"', 'format = "speckit"', "npx"} <= inline(body)
        links = local_links(path, body)
        assert {"../../examples/specloop/self-hosting.toml", "../../examples/specloop/speckit.toml"} <= links
        assert {f"https://github.com/mattpolicastro/nightshift/issues/{n}" for n in (184, 187, 188)} <= links
    engine = passage(read(GUIDE), "## How the engine works")
    # Keep the single legacy checklist throughout the section, including children.
    checks = re.findall(r"^- \[([ Xx])\] (.*(?:\n  .*)*)", engine, re.M)
    assert len(checks) == 1 and checks[0][0] == " "
    assert "format" in inline(checks[0][1])
    assert {f"https://github.com/mattpolicastro/nightshift/issues/{n}" for n in (184, 187)} <= local_links(GUIDE, checks[0][1])


@pytest.mark.scenario("specloop-documentation/native-build-operator-guidance/flow-forward-context-limits-are-explicit")
def test_flow_forward_references_and_unchecked_live_observation():
    for path, heading in ((GUIDE, "### Native spec-kit builds"),
                          ("KNOWN-LIMITATIONS.md", "## Open gaps")):
        body = passage(read(path), heading)
        assert {".specify/memory/constitution.md", "## Assumptions", "Supersedes 042-add-a-gate"} <= inline(body)
        assert "https://github.com/mattpolicastro/nightshift/issues/184" in local_links(path, body)
    native = passage(read(GUIDE), "### Native spec-kit builds")
    assert not re.search(r"^- \[[Xx]\]", native, re.M)
    assert "speckit.md#flow-forward-persistence" in local_links(GUIDE, native)


@pytest.mark.scenario("speckit-preset/native-build-support-guidance/preset-reader-finds-the-native-build-path")
def test_preset_support_configuration_and_links():
    body = introduction(read(PRESET))
    assert {'format = "speckit"', "[paths] frozen", "specs/**", ".specify/**",
            "--skip-feasibility", "**Status**: Complete", "SPECKIT_PYTHON_EXECUTABLE",
            "nightshift admit", "nightshift run", "nightshift publish", "nightshift finish"} <= inline(body)
    assert {"../../docs/specloop/README.md#native-spec-kit-builds",
            "../../examples/specloop/speckit.toml"} <= local_links(PRESET, body)
    # Installation and baseline links are still checked by the existing integrity suite.
    assert "../../docs/specloop/speckit.md" in local_links(PRESET, read(PRESET))


@pytest.mark.scenario("specloop-documentation/spec-kit-discovery/find-authoring-guidance")
def test_authoring_discovery_and_eight_rules():
    assert "speckit.md" in local_links(GUIDE, passage(read(GUIDE), "## How the engine works"))
    layout = passage(read("README.md"), "## Layout")
    row, = [line for line in layout.split("\n") if line.startswith("| `presets/` |")]
    assert {"presets/nightshift-speckit/README.md", HOUSE} <= local_links("README.md", row)
    for heading in ("Feature directory and branch", "Open the issue first", "Labelled acceptance scenarios",
                    "Tasks cite scenarios", "Human-only checks", "Flow-forward persistence", "Frozen paths",
                    "No [NEEDS CLARIFICATION] at approval"):
        assert passage(read(HOUSE), "## " + heading).strip()
    assert "042-add-a-gate/us1/as2" in inline(read(HOUSE))
    local_links(HOUSE, read(HOUSE))


@pytest.mark.scenario("specloop-documentation/spec-kit-task-traceability/author-links-tasks-and-scenarios")
@pytest.mark.scenario("speckit-preset/scenario-citing-task-scaffold/harness-additions-use-the-matching-phase-form")
@pytest.mark.parametrize("path,heading", [(HOUSE, "## Tasks cite scenarios"), (TEMPLATE, HARNESS)])
def test_distinct_harness_addition_forms(path, heading):
    body = passage(read(path), heading)
    # Three physical list entries, with the phase syntax attached to each form.
    entries = re.findall(r"^- (?!\[).*(?:\n  .*)*", body, re.M)
    assert len(entries) == 3
    forms = [{token for token in inline(entry) if token.startswith("## Phase")} for entry in entries]
    assert forms == [{"## Phase N: Review round K"}, {"## Phase N: Title"},
                     {"## Phase N: Coverage", "## Phase N: Acceptance fixes"}]
    assert {"T{M+1:03d}", "[X]"} <= inline(body)
    assert "[x]" not in inline(body)
    if path == TEMPLATE:
        assert {"specs/**", ".specify/**", "## Human checks", "/speckit.converge"} <= inline(body)
        assert not re.search(r"^- \[[ Xx]\]", body, re.M)


@pytest.mark.scenario("specloop-documentation/spec-kit-task-traceability/author-links-tasks-and-scenarios")
@pytest.mark.scenario("speckit-preset/scenario-citing-task-scaffold/task-examples-preserve-scenario-traceability")
def test_task_scaffold_and_house_examples_keep_citations():
    scaffold = read(TEMPLATE)
    assert tasks.SCENARIO_REF.search(passage(scaffold, "## Format"))
    parsed = speckit.parse_tasks(scaffold)
    assert [task.label for task in parsed] == [f"T{n:03d}" for n in range(1, 8)]
    assert {task.section for task in parsed} == set(range(1, 7))
    assert not any(task.done for task in parsed)
    expected = {"042-add-a-gate/us1/as1", "042-add-a-gate/us1/as2",
                "042-add-a-gate/us2/as1", "042-add-a-gate/us3/as1"}
    assert {sid for task in parsed for sid in task.scenarios} == expected
    house_examples = fenced(passage(read(HOUSE), "## Tasks cite scenarios"), "markdown")
    examples = speckit.parse_tasks("## Phase 1: Examples\n" + house_examples)
    assert len(examples) == 3
    for group in (parsed, examples):
        assert any("[P]" in task.text for task in group)
        for task in group:
            story = re.search(r"\[US([1-9][0-9]*)\]", task.text)
            assert re.search(r"\b(?:src|tests|docs)/", task.text)
            if story:
                assert task.scenarios and all(f"/us{story[1]}/" in sid for sid in task.scenarios)
            else:
                assert not task.scenarios


@pytest.mark.scenario("specloop-documentation/native-documentation-verification/documentation-remains-structurally-reviewable")
@pytest.mark.parametrize("wrapper", ["{}", "```md\n{}\n```", "<!--\n{}\n-->"])
def test_physical_heading_boundaries_refuse_ambiguity(wrapper):
    body = "text\n### Child\nchild\n  ## Indented\ntext\u2028## Not-a-line"
    for end in ("## Next", "# Next", "##   Next", "## ", "##\tNext"):
        assert passage(wrapper.format(HARNESS + "\n" + body + "\n" + end + "\noutside"), HARNESS) == body
    assert passage(HARNESS + "\n" + body, HARNESS) == body
    for bad in ("", " " + HARNESS, HARNESS + " ", "prefix\u2028" + HARNESS,
                HARNESS + "\n" + wrapper.format(HARNESS), HARNESS + "\n " + HARNESS,
                HARNESS + "\nbody\n## Next\nother\n## Next"):
        with pytest.raises(AssertionError):
            passage(bad, HARNESS)


@pytest.mark.scenario("specloop-documentation/native-documentation-verification/documentation-remains-structurally-reviewable")
@pytest.mark.scenario("speckit-preset/native-build-support-guidance/preset-reader-finds-the-native-build-path")
def test_introduction_requires_unique_title_and_first_level_two_boundary():
    text = TITLE + "\nintro\n### Child\nchild\n## Installation\noutside\n## Later\nmore"
    assert introduction(text) == "intro\n### Child\nchild"
    for bad in ("", TITLE + "\nintro", " " + text, text.replace(TITLE, TITLE + " "),
                text + "\n" + TITLE, text + "\n## Installation",
                text.replace("## Installation", "  ## Installation").split("\n## Later")[0]):
        with pytest.raises(AssertionError):
            introduction(bad)
    # Physical lines inside HTML/fences remain boundaries; there is no Markdown parser.
    assert introduction(TITLE + "\n<!--\n## End\n-->") == "<!--"
    assert introduction(TITLE + "\n```md\n## End\n```") == "```md"


@pytest.mark.scenario("specloop-documentation/native-documentation-verification/documentation-remains-structurally-reviewable")
def test_physical_fences_refuse_missing_duplicate_and_lookalike_delimiters():
    block = "```sh\nnightshift finish 042-add-a-gate\n```"
    assert fenced(block, "sh") == "nightshift finish 042-add-a-gate"
    assert fenced("<!--\n" + block + "\n-->", "sh") == fenced(block, "sh")
    for bad in ("", block + "\n" + block, block.replace("```sh", "~~~sh"),
                block.replace("```sh", " ```sh"), block.replace("```sh", "```sh "),
                block.removesuffix("```"), "```\n```sh", block + "\n```"):
        with pytest.raises(AssertionError):
            fenced(bad, "sh")


@pytest.mark.scenario("specloop-documentation/native-documentation-verification/documentation-remains-structurally-reviewable")
def test_relative_links_refuse_missing_files_and_anchors():
    for target in ("missing.md", "README.md#missing", "../../../outside.md"):
        with pytest.raises(AssertionError):
            local_links(PRESET, f"[link]({target})")

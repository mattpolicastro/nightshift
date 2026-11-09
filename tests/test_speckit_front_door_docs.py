"""Offline front-door examples and physical documentation boundaries."""

from dataclasses import replace
import re
import tomllib

import pytest

from nightshift import arm, specloop_intake
from specloop import manifest, propose, speckit, state
import test_speckit_build_docs as doc


FRONT = "## The front door"
ENROLLMENT = "specloop-documentation/native-front-door-guidance/operator-finds-native-enrollment"
LIVE = "specloop-documentation/native-front-door-guidance/live-enrollment-remains-pending"


def section(text, heading):
    """Require both unique physical boundaries, including the terminating heading."""
    lines = text.split("\n")
    start = doc.unique_line(lines, heading)
    level = len(heading.split(" ", 1)[0])
    end = next((i for i in range(start + 1, len(lines))
                if re.match(rf"#{{1,{level}}}[ \t]", lines[i])), None)
    assert end is not None, "missing terminating heading"
    doc.unique_line(lines, lines[end])
    return "\n".join(lines[start + 1:end])


def body(path, heading):
    return section(doc.read(path), heading)


@pytest.mark.scenario(ENROLLMENT)
def test_native_identity_and_workspace_tables_match_code(monkeypatch, tmp_path):
    front = body(doc.HOUSE, FRONT)
    rows = [line.split("|")[1:-1] for line in front.split("\n") if line.startswith("| ")]
    names = [row for row in rows if row[0].strip().isdigit()]
    assert len(names) == 2
    for number, title, change, branch, directory in names:
        number = int(number)
        change, branch, directory = (cell.strip().strip("`") for cell in (change, branch, directory))
        assert change == specloop_intake._change_name("owner/sample", number, title.strip(), format="speckit")
        assert branch == f"spec/{change}" and directory == f"specs/{change}/"
        assert arm.front_door_issue("owner/sample", change) == number
        assert arm.front_door_issue("owner/sample", f"owner__sample-{change}") == number
    native = manifest.load(doc.ROOT / "examples/specloop/speckit.toml")
    monkeypatch.setattr(state, "proposals_dir", lambda: tmp_path / "drafts")
    workspaces = [row for row in rows if row[0].strip().startswith("`a")]
    assert len(workspaces) == 2
    for repository, path in workspaces:
        m = replace(native, github=repository.strip().strip("`"), change=names[0][2].strip().strip("`"))
        assert propose.draft_output(m) == tmp_path / path.strip().strip("`")
    assert state.SAFE_CHANGE.fullmatch("@speckit") is None
    parsed = speckit.parse_spec(doc.fenced(front, "markdown"))
    scenarios = speckit.scenario_ids("007-add-a-gate", parsed)
    assert list(scenarios) == ["007-add-a-gate/us1/as1"]
    assert "<!-- nightshift:post-deploy -->" in scenarios["007-add-a-gate/us1/as1"]


@pytest.mark.scenario(ENROLLMENT)
@pytest.mark.scenario("specloop-documentation/native-build-operator-guidance/operator-finishes-the-selected-format")
@pytest.mark.scenario("specloop-documentation/native-drafting-guidance/author-finds-the-native-workflow")
def test_enrollment_and_explicit_commands_parse_offline(monkeypatch):
    tables = []
    for path, heading, pairs in (
        ("README.md", "### Enroll a repository", 1),
        (doc.GUIDE, "## GitHub front door: Nightshift → specloop", 2),
    ):
        lines = body(path, heading).split("\n")
        fences = [i for i, line in enumerate(lines) if re.match(r"\s*(?:`{3,}|~{3,})", line)]
        assert [lines[i] for i in fences] == ["```toml", "```"] * pairs
        example = "\n".join(lines[fences[0]:fences[1] + 1])
        tables.append(tomllib.loads(doc.fenced(example, "toml")))
    assert tables[0] == tables[1]
    repo, = tables[0]["repos"]
    assert set(repo) == {"name", "engine", "manifest", "base"}
    native = manifest.load(doc.ROOT / "examples/specloop/speckit.toml")
    assert native.format == "speckit"
    commands = doc.parse_commands(doc.fenced(body(doc.GUIDE, "#### Draft and admit"), "sh"), monkeypatch)
    assert [c.cmd for c in commands] == ["propose", "admit"]
    assert all(c.change == native.change and c.branch == native.branch for c in commands)
    assert commands[0].base_commit is None and not commands[1].skip_feasibility
    build = doc.parse_commands(doc.fenced(body(doc.GUIDE, "### Native spec-kit builds"), "sh"), monkeypatch)
    assert [c.cmd for c in build] == ["admit", "run", "publish", "finish"]
    assert build[0].skip_feasibility


@pytest.mark.scenario("specloop-documentation/spec-kit-discovery/find-authoring-guidance")
@pytest.mark.scenario("specloop-documentation/native-build-operator-guidance/flow-forward-context-limits-are-explicit")
@pytest.mark.scenario("speckit-preset/native-build-support-guidance/preset-reader-finds-the-native-build-path")
@pytest.mark.parametrize("path,heading", [
    (doc.HOUSE, FRONT), ("README.md", "### Enroll a repository"),
    ("README.md", "## Layout"), (doc.GUIDE, "## GitHub front door: Nightshift → specloop"),
    (doc.GUIDE, "### Native spec-kit builds"), ("KNOWN-LIMITATIONS.md", "## Open gaps"),
    (doc.PRESET, "## Installation"), ("docs/arming.md", "## Coverage limits"),
    ("docs/specloop/command-json.md", "## Manifest format"),
])
def test_scoped_sections_have_resolving_links(path, heading):
    assert doc.local_links(path, body(path, heading))


@pytest.mark.scenario(LIVE)
@pytest.mark.scenario("specloop-documentation/native-drafting-guidance/quality-evidence-remains-human")
def test_human_checks_remain_in_design_and_quality_table():
    designs = list((doc.ROOT / "openspec/changes").glob("**/*issue-190-*/design.md"))
    design, = designs
    checklist = doc.passage(design.read_text(), "## Human checks")
    checks = re.findall(r"^- \[([ Xx])\] (.+)$", checklist, re.M)
    assert len(checks) == 3 and all(status == " " for status, _ in checks)
    assert any(LIVE in doc.inline(text) for _, text in checks)
    assert sum(ENROLLMENT in doc.inline(text) for _, text in checks) == 2
    quality = body(doc.GUIDE, "#### Draft quality observation")
    row, = [line for line in quality.split("\n") if line.startswith("| ")][2:]
    assert row.split("|")[2].strip() == "Pending"
    assert "https://github.com/mattpolicastro/nightshift/issues/184" in doc.local_links(doc.GUIDE, quality)


@pytest.mark.scenario(ENROLLMENT)
@pytest.mark.parametrize("wrapper", ["{}", "```md\n{}\n```", "<!--\n{}\n-->"])
def test_boundaries_are_physical_and_fail_missing_or_duplicate(wrapper):
    text = FRONT + "\nbody\n### Child\nchild\n## End\noutside"
    assert section(wrapper.format(text), FRONT) == "body\n### Child\nchild"
    for bad in ("", text.split("\n## End")[0], text + "\n" + FRONT,
                text + "\n## End", text.replace(FRONT, " " + FRONT)):
        with pytest.raises(AssertionError):
            section(wrapper.format(bad), FRONT)
    fence = "```sh\nnightshift finish 007-add-a-gate\n```"
    assert doc.fenced(wrapper.format(fence) if wrapper.startswith("<!--") else fence, "sh")
    for bad in ("", fence + "\n" + fence, fence.replace("```sh", " ```sh"),
                fence.replace("```sh", "```sh "), fence.removesuffix("```")):
        with pytest.raises(AssertionError):
            doc.fenced(bad, "sh")

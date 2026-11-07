from pathlib import Path
import re

import pytest

from specloop import speckit, tasks
from specloop.openspec import SpecError


FIXTURE = Path(__file__).parent / "fixtures" / "speckit"
UPSTREAM = FIXTURE.parent / "speckit-upstream" / "v1.3.2"


@pytest.mark.scenario(
    "speckit-format/offline-fixture-provenance/copy-spec-and-plan-inputs-locally"
)
@pytest.mark.parametrize("name", ["spec-template.md", "plan-template.md"])
def test_spec_and_plan_templates_match_local_source(name):
    assert (FIXTURE / ".specify/templates" / name).read_bytes() == (
        UPSTREAM / "templates" / name
    ).read_bytes()


@pytest.mark.scenario(
    "speckit-format/offline-fixture-provenance/copy-spec-and-plan-inputs-locally"
)
def test_fixture_license_matches_local_source():
    assert (FIXTURE / "LICENSE").read_bytes() == (UPSTREAM / "LICENSE").read_bytes()


@pytest.mark.scenario(
    "speckit-format/offline-fixture-provenance/copy-spec-and-plan-inputs-locally"
)
def test_fixture_attribution_links_resolve_locally():
    readme = (FIXTURE / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme)
    assert links
    for target in links:
        assert (FIXTURE / target).exists(), target


@pytest.mark.scenario(
    "speckit-format/offline-fixture-provenance/copy-task-input-locally"
)
def test_tasks_template_matches_local_source_with_synthetic_constitution():
    assert (FIXTURE / ".specify/templates/tasks-template.md").read_bytes() == (
        UPSTREAM / "templates/tasks-template.md"
    ).read_bytes()
    constitution = FIXTURE / ".specify/memory/constitution.md"
    assert constitution.is_file()
    assert constitution.read_text(encoding="utf-8").strip()


CHANGE = "042-add-a-gate"
SPEC_FILE = FIXTURE / "specs" / CHANGE / "spec.md"
AS2 = "Given a failing check, when the gate runs, then it refuses the change."


@pytest.mark.scenario("speckit-format/local-feature-api/resolve-a-feature-without-integration")
def test_feature_dir_without_io(monkeypatch, tmp_path):
    import importlib
    import subprocess

    def forbidden(*args, **kwargs):
        pytest.fail("feature lookup and import must not perform I/O")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        importlib.reload(speckit)
        assert speckit.feature_dir(tmp_path, CHANGE) == tmp_path / "specs" / CHANGE


@pytest.mark.scenario("speckit-format/physical-line-spec-parsing/parse-the-authored-feature")
def test_fixture_parses_stories_and_scenarios():
    text = SPEC_FILE.read_text(encoding="utf-8")
    spec = speckit.parse_spec(text)
    assert (spec.title, spec.title_line) == ("Feature Specification: Add a gate", 1)
    assert (spec.status, spec.status_line) == ("Draft", 3)
    assert [(s.number, s.title, s.priority, s.line) for s in spec.stories] == [
        (1, "Check the gate", 1, 7), (2, "Explain a refusal", 2, 13),
        (3, "Retry a check", 3, 18),
    ]
    assert [[(s.number, s.line) for s in story.scenarios] for story in spec.stories] == [
        [(1, 10), (2, 11)], [(1, 16)], [(1, 21)],
    ]
    assert spec.stories[0].scenarios[1].text == AS2
    assert [(s.text, s.line) for s in spec.sections] == [
        ("User Scenarios & Testing", 5), ("Requirements", 23), ("Success Criteria", 28),
    ]
    for records, locations in ((spec.functional_requirements, [25, 26]),
                               (spec.success_criteria, [30, 31])):
        assert [r.line for r in records] == locations
        assert [r.text for r in records] == [text.splitlines()[n - 1] for n in locations]
    assert spec.clarifications == []


@pytest.mark.scenario("speckit-format/stable-scenario-identities/ids-fit-the-harness")
def test_scenario_ids_match_harness_regex():
    text = SPEC_FILE.read_text(encoding="utf-8")
    ids = speckit.scenario_ids(CHANGE, speckit.parse_spec(text))
    assert set(ids) == {f"{CHANGE}/us1/as1", f"{CHANGE}/us1/as2",
                        f"{CHANGE}/us2/as1", f"{CHANGE}/us3/as1"}
    assert ids[f"{CHANGE}/us1/as2"] == f"US1 — AS2\n{AS2}"
    assert all(tasks.SCENARIO_ID.fullmatch(sid) for sid in ids)
    edited = text.replace("Check the gate", "Renamed").replace("P1", "P9").replace("7. **AS2**", "99. **AS2**")
    assert speckit.scenario_ids(CHANGE, speckit.parse_spec(edited)) == ids


@pytest.mark.scenario("speckit-format/physical-line-spec-parsing/retain-invalid-and-literal-evidence")
@pytest.mark.parametrize("wrapper", [("```md\n", "\n```"), ("<!--\n", "\n-->"), ("", "")])
def test_literal_evidence_and_missing_priorities(wrapper):
    lines = ["# First", "# Later", "**Status**: Draft", "### User Story 2 - Missing",
             "**Acceptance Scenarios**:", "1. **AS01** first", "2. **AS1** repeated",
             "### User Story 3 - Bad (Priority: nope)", "### User Story 4 - Low (Priority: P10)",
             "### User Story 1 - High (Priority: P2)", "### User Story 5 - Tie (Priority: P2)",
             "FR-1 FR-2 SC-3", "FR-1 FR-2 SC-3", "XFR-4 XSC-5",
             "[NEEDS CLARIFICATION] [NEEDS CLARIFICATION: why?] [NEEDS CLARIFICATION]"]
    text = wrapper[0] + "\n".join(" \t" + line + "\t " for line in lines) + wrapper[1]
    spec = speckit.parse_spec(text)
    offset = wrapper[0].count("\n")
    assert spec.title == "First" and spec.status == "Draft" and spec.sections == []
    assert [(s.number, s.priority) for s in spec.stories] == [(1, 2), (5, 2), (4, 10), (2, None), (3, None)]
    assert [(s.number, s.text, s.line) for s in spec.stories[3].scenarios] == [
        (1, "first", 6 + offset), (1, "repeated", 7 + offset),
    ]
    for records in (spec.functional_requirements, spec.success_criteria):
        assert [(r.text, r.line) for r in records] == [
            (" \tFR-1 FR-2 SC-3\t ", 12 + offset), (" \tFR-1 FR-2 SC-3\t ", 13 + offset),
        ]
    assert [(m.text, m.line) for m in spec.clarifications] == [
        (marker, 15 + offset) for marker in
        ["[NEEDS CLARIFICATION]", "[NEEDS CLARIFICATION: why?]", "[NEEDS CLARIFICATION]"]
    ]


@pytest.mark.scenario("speckit-format/physical-line-spec-parsing/retain-invalid-and-literal-evidence")
@pytest.mark.parametrize("level", range(1, 8))
def test_acceptance_heading_boundaries(level):
    text = "\n".join(["### User Story 1 - Gate (Priority: P1)", "1. **AS8** outside",
                      "**Acceptance Scenarios**: extra", "1. **AS9** outside",
                      "**Acceptance Scenarios**:", "7. **AS2** exact text", "continued text",
                      "8. unlabelled", "9. **AS3**   ", "10. **AS4**", "split text",
                      "#" * level + " Boundary", "1. **AS5** outside",
                      "**Acceptance Scenarios**:", "1. **AS6** reopened"])
    scenarios = speckit.parse_spec(text).stories[0].scenarios
    assert [(s.number, s.text, s.line) for s in scenarios] == (
        [(2, "exact text", 6)] + ([(6, "reopened", 15)] if level > 3 else [])
    )


@pytest.mark.scenario("speckit-format/physical-line-spec-parsing/retain-invalid-and-literal-evidence")
@pytest.mark.parametrize("suffix, count", [("", 3), (" *(mandatory)*", 3), (" extra", 0), (" (mandatory)", 0)])
def test_exact_mandatory_headings(suffix, count):
    headings = ["## User Scenarios & Testing", "## Requirements", "## Success Criteria"]
    spec = speckit.parse_spec("\n".join(" \t" + h + suffix + "\t " for h in headings))
    assert len(spec.sections) == count
    assert speckit.parse_spec("\u00a0## Requirements\u00a0").sections == []


@pytest.mark.scenario("speckit-format/stable-scenario-identities/collisions-and-invalid-names-refuse")
@pytest.mark.parametrize("extra", ["2. **AS1** duplicate", "2. **AS01** duplicate",
    "### User Story 01 - Again (Priority: P2)\n**Acceptance Scenarios**:\n1. **AS1** duplicate"])
def test_duplicate_identities_refuse(extra):
    text = "### User Story 1 - Gate (Priority: P1)\n**Acceptance Scenarios**:\n1. **AS1** original\n" + extra
    spec = speckit.parse_spec(text)
    assert sum(len(s.scenarios) for s in spec.stories) == 2
    with pytest.raises(SpecError, match="duplicate scenario id"):
        speckit.scenario_ids(CHANGE, spec)


@pytest.mark.scenario("speckit-format/stable-scenario-identities/collisions-and-invalid-names-refuse")
@pytest.mark.parametrize("change", ["", "Bad-name", "café", "two words", "a/b/c", "a--b", "a-", "a\n"])
def test_invalid_change_names_refuse(change):
    with pytest.raises(SpecError, match="noncanonical change name|invalid scenario id"):
        speckit.scenario_ids(change, speckit.parse_spec(SPEC_FILE.read_text(encoding="utf-8")))
    with pytest.raises(SpecError, match="noncanonical change name"):
        speckit.scenario_ids(change, speckit.parse_spec(""))


@pytest.mark.scenario("speckit-format/stable-scenario-identities/collisions-and-invalid-names-refuse")
def test_scenario_identity_requires_whole_regex_match():
    spec = speckit.parse_spec(SPEC_FILE.read_text(encoding="utf-8"))
    spec.stories[0].scenarios.append(speckit.Scenario("9/trailing", "invalid", 32))
    with pytest.raises(SpecError, match="invalid scenario id"):
        speckit.scenario_ids(CHANGE, spec)

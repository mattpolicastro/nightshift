from pathlib import Path
import re
import shutil

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


@pytest.mark.scenario("speckit-format/shared-task-records/parse-phases-and-references")
def test_fixture_parses_stories_scenarios_and_tasks():
    spec = speckit.parse_spec(SPEC_FILE.read_text(encoding="utf-8"))
    ids = speckit.scenario_ids(CHANGE, spec)
    parsed = speckit.parse_tasks(SPEC_FILE.with_name("tasks.md").read_text(encoding="utf-8"))
    assert [(s.number, s.priority, len(s.scenarios)) for s in spec.stories] == [
        (1, 1, 2), (2, 2, 1), (3, 3, 1),
    ]
    assert all(type(task) is tasks.Task for task in parsed)
    assert [(t.section, t.section_title, t.label, t.line, t.done) for t in parsed] == [
        (1, "Setup", "T001", 4, False),
        (2, "Foundational", "T002", 8, False),
        (3, "User Story 1 - Check the gate", "T003", 12, False),
        (3, "User Story 1 - Check the gate", "T004", 13, False),
        (4, "User Story 2 - Explain a refusal", "T005", 17, False),
        (5, "User Story 3 - Retry a check", "T006", 21, False),
        (6, "Polish", "T007", 25, False),
    ]
    assert parsed[3].text == "[P] [US1] Refuse failing checks. Covers `042-add-a-gate/us1/as2`."
    assert parsed[3].scenarios == ["042-add-a-gate/us1/as2"]
    assert {sid for task in parsed for sid in task.scenarios} == set(ids)


@pytest.mark.scenario("speckit-format/shared-task-records/parse-phases-and-references")
@pytest.mark.parametrize("state, done", [(" ", False), ("x", True), ("X", True)])
@pytest.mark.parametrize("label", ["T001", "T1234", "T003.2", "T003.2.10"])
def test_task_labels_done_states_and_zero_based_lines(state, done, label):
    text = f"# Tasks\r\n\r\n##\tPhase 007: User Story 2 - Gate  \r\n\r\n\t- [{state}] {label}\t[P] [US2] Check `a/b/c`.  \r\ncontinuation"
    assert speckit.parse_tasks(text) == [
        tasks.Task(7, "User Story 2 - Gate", label, "[P] [US2] Check `a/b/c`.", done, 4),
    ]


@pytest.mark.scenario("speckit-format/shared-task-records/bound-phases-literally")
@pytest.mark.parametrize("body, owner", [
    ("Check", None), ("[P] Check", None), ("[US2] Check", "2"),
    ("[P] [US02] Check", "02"), ("Mention [US2] in prose", None),
    ("[P] Mention [US2] in prose", None), ("[US2] [P] Check", "2"),
    ("[US2]Check", None), ("[P][US2] Check", None),
    ("[USk] Check", None),
])
def test_task_flags_are_preserved_and_ownership_is_positional(body, owner):
    task, = speckit.parse_tasks(f"## Phase 1: User Story 2 - Gate\n- [ ] T001 {body}")
    assert task.text == body
    assert task.title() == f"T001 {body}"
    # Ownership is the label grammar's capture, never a search through prose.
    match = speckit.TASK_LABEL.fullmatch(task.title())
    assert match is not None
    assert match[2] == owner


@pytest.mark.scenario("speckit-format/shared-task-records/bound-phases-literally")
@pytest.mark.parametrize("level", range(1, 8))
@pytest.mark.parametrize("wrapper", [("", ""), ("```md\n", "\n```"), ("<!--\n", "\n-->")])
def test_task_phase_boundaries_and_literal_examples(level, wrapper):
    text = (wrapper[0] + "- [ ] T001 Outside\n## Phase 1: Setup\n- [ ] T002 Inside\n"
            + "#" * level + " Boundary\n- [x] T003 After heading\n"
            "## Phase 2: Polish\n- [X] T004 Reopened" + wrapper[1])
    offset = wrapper[0].count("\n")
    expected = [tasks.Task(1, "Setup", "T002", "Inside", False, 2 + offset)]
    if level > 2:
        expected.append(tasks.Task(1, "Setup", "T003", "After heading", True, 4 + offset))
    expected.append(tasks.Task(2, "Polish", "T004", "Reopened", True, 6 + offset))
    assert speckit.parse_tasks(text) == expected


@pytest.mark.scenario("speckit-format/shared-task-records/bound-phases-literally")
@pytest.mark.parametrize("heading", [
    " ## Phase 1: Setup", "\t## Phase 1: Setup", "### Phase 1: Setup",
    "## Phase 1 Setup", "## Phase one: Setup", "## Phase 1:", "## 1. Setup",
])
def test_only_recognized_phases_admit_tasks(heading):
    assert speckit.parse_tasks(f"{heading}\n- [ ] T001 Outside") == []


@pytest.mark.scenario("speckit-format/shared-task-records/bound-phases-literally")
@pytest.mark.parametrize("checkbox", [
    "- [ ] T01 Short", "- [ ] t001 Lowercase", "- [ ] 001 Numeric",
    "- [ ] T001. Broken", "- [ ] T001.a Broken", "- [ ] T001..2 Broken",
    "- [ ] T001Suffix Broken", "- [ ] Unlabelled", "- [ ] T001",
    "- [ ] [P] T001 Misplaced", "- [?] T001 Unknown state", "* [ ] T001 Bullet",
])
def test_invalid_task_labels_and_checkbox_forms_do_not_count(checkbox):
    assert speckit.parse_tasks(f"## Phase 1: Setup\n{checkbox}") == []


@pytest.mark.scenario("speckit-format/shared-task-records/parse-phases-and-references")
def test_task_keys_and_references_use_shared_record_behavior():
    body = ("[P] [US1] Check `042-add-a-gate/us1/as2` twice `042-add-a-gate/us1/as2`; "
            "also `unknown/us9/as1`. Ignore bare 042-add-a-gate/us1/as1, "
            "`Upper/us1/as1`, `a/b/c/d`, and `a/b`.")
    task, = speckit.parse_tasks(f"## Phase 0: Setup\n- [ ] T001 {body}")
    shared = tasks.Task(0, "Setup", "T001", body, False, 1)
    assert task == shared
    assert task.key == shared.key
    assert task.scenarios == tasks.SCENARIO_REF.findall(body) == [
        "042-add-a-gate/us1/as2", "042-add-a-gate/us1/as2", "unknown/us9/as1",
    ]
    assert task.known_scenarios({"042-add-a-gate/us1/as2"}) == [
        "042-add-a-gate/us1/as2", "042-add-a-gate/us1/as2",
    ]
    renumbered, = speckit.parse_tasks(f"\n## Phase 9: Setup\n- [X] T999.2 {body}")
    assert renumbered.key == task.key
    changed, = speckit.parse_tasks("## Phase 0: Setup\n- [ ] T001 " + body.replace("[P] ", ""))
    assert changed.key != task.key


@pytest.fixture
def feature_copy(tmp_path):
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    # Harness sources are read-only; copytree preserves those permissions.
    # Make only the disposable copy writable, including directories for unlink/rmtree.
    for path in [tmp_path, *tmp_path.rglob("*")]:
        path.chmod(path.stat().st_mode | 0o200)
    return tmp_path, speckit.feature_dir(tmp_path, CHANGE)


CHECKLIST = "checklists/readiness.md"


@pytest.mark.scenario("speckit-format/feature-validation/valid-feature-passes")
@pytest.mark.parametrize("checklists", ["original", "absent", "empty"])
@pytest.mark.parametrize("state", [" ", "x", "X"])
def test_validate_passes_on_fixture(feature_copy, checklists, state):
    repo, feature = feature_copy
    path = feature / "tasks.md"
    path.write_text(path.read_text().replace("[ ]", f"[{state}]"), encoding="utf-8")
    if checklists == "absent":
        shutil.rmtree(feature / "checklists")
    elif checklists == "empty":
        (feature / CHECKLIST).write_text("No checkboxes.\n", encoding="utf-8")
    # Templates still contain placeholders, and plan human checks remain pending.
    assert speckit.validate(repo, CHANGE) == []


def validation_case(scenario, edits, diagnostic):
    return pytest.param(edits, diagnostic, marks=pytest.mark.scenario(scenario))


@pytest.mark.parametrize("edits, diagnostic", [
    *[validation_case("speckit-format/required-spec-sections/missing-section-reports",
        [("spec.md", f"## {heading} *(mandatory)*", "")],
        f"spec.md:1: missing section: {heading}")
      for heading in ("User Scenarios & Testing", "Requirements", "Success Criteria")],
    *[validation_case("speckit-format/story-readiness/missing-priority-reports",
        [("spec.md", " (Priority: P1)", suffix)], "spec.md:7: US1: missing priority")
      for suffix in ("", " (Priority: nope)")],
    validation_case("speckit-format/story-readiness/missing-scenario-label-reports",
        [("spec.md", "1. **AS1** Given a refused", "1. Given a refused"),
         ("tasks.md", f"`{CHANGE}/us2/as1`", "")],
        "spec.md:13: US2: no labelled acceptance scenario"),
    *[validation_case("speckit-format/unique-scenario-labels/duplicate-label-reports",
        [("spec.md", "7. **AS2**", f"2. **{label}** duplicate\n7. **AS2**")],
        f"spec.md:11: duplicate scenario id: {CHANGE}/us1/as1")
      for label in ("AS1", "AS01")],
    *[validation_case("speckit-format/story-phase-ownership/missing-phase-ownership-reports",
        [("tasks.md", "[US1] ", replacement),
         ("tasks.md", "T001 Create", "T001 [US1] Create")],
        "tasks.md:11: user-story phase has no matching [US1] task")
      for replacement in ("", "Mention [US1] ", "[US2] ")],
    validation_case("speckit-format/scenario-task-coverage/uncovered-scenario-reports",
        [("tasks.md", f"`{CHANGE}/us1/as2`", f"{CHANGE}/us1/as2")],
        f"spec.md:11: uncovered scenario: {CHANGE}/us1/as2"),
    validation_case("speckit-format/scenario-task-coverage/unknown-scenario-reports",
        [("tasks.md", "T001 Create", "T001 See `unknown/us9/as1`. Create")],
        "tasks.md:5: unknown scenario: unknown/us9/as1"),
    *[validation_case("speckit-format/clarification-completion/clarification-reports",
        [(name, "\n", f"\n{opening}{marker}{closing}\n")],
        f"{name}:{2 + opening.count(chr(10))}: unresolved clarification: {marker}")
      for name in ("spec.md", "tasks.md", "plan.md", CHECKLIST)
      for marker in ("[NEEDS CLARIFICATION]", "[NEEDS CLARIFICATION: why?]")
      for opening, closing in (("", ""), ("```md\n", "\n```"), ("<!--\n", "\n-->"), (" \t", "\t "))],
    *[validation_case("speckit-format/checklist-completion/unchecked-checklist-reports",
        [(CHECKLIST, "\n", f"\n{opening}{bullet} [{state}] Example{closing}\n")],
        f"{CHECKLIST}:{2 + opening.count(chr(10))}: unchecked checklist item")
      for bullet in "-*+" for state in ("", " ", "?", "done", " x ")
      for opening, closing in (("", ""), ("```md\n", "\n```"), ("<!--\n", "\n-->"), (" \t", "\t "))],
    validation_case("speckit-format/plan-availability/missing-plan-reports",
        [("plan.md", None, None)], "plan.md:1: missing required file"),
])
def test_validate_reports_each_rule(feature_copy, edits, diagnostic):
    repo, feature = feature_copy
    for name, old, new in edits:
        path = feature / name
        if old is None:
            path.unlink()
        else:
            text = path.read_text(encoding="utf-8")
            assert old in text
            # Inserting a literal example changes just the first newline.
            path.write_text(text.replace(old, new, 1 if old == "\n" else -1), encoding="utf-8")
    assert speckit.validate(repo, CHANGE) == [f"specs/{CHANGE}/{diagnostic}"]


@pytest.mark.scenario("speckit-format/unique-scenario-labels/duplicate-label-reports")
def test_validate_duplicate_across_stories_uses_later_source_line(feature_copy):
    repo, feature = feature_copy
    path = feature / "spec.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "### User Story 01 - Again (Priority: P0)\n"
                    "**Acceptance Scenarios**:\n1. **AS01** duplicate\n", encoding="utf-8")
    assert speckit.validate(repo, CHANGE) == [
        f"specs/{CHANGE}/spec.md:34: duplicate scenario id: {CHANGE}/us1/as1"]


@pytest.mark.scenario("speckit-format/story-phase-ownership/missing-phase-ownership-reports")
def test_validate_empty_repeated_phase_needs_its_own_task(feature_copy):
    repo, feature = feature_copy
    path = feature / "tasks.md"
    path.write_text(path.read_text() + "## Phase 3: User Story 1 - Check the gate\n"
                    "### Details\nProse [US1]\n## Other\n- [ ] T008 [US1] Outside\n", encoding="utf-8")
    assert speckit.validate(repo, CHANGE) == [
        f"specs/{CHANGE}/tasks.md:27: user-story phase has no matching [US1] task"]


@pytest.mark.parametrize("empty", [
    pytest.param("stories", marks=pytest.mark.scenario(
        "speckit-format/minimum-feature-content/empty-story-content-refuses")),
    pytest.param("tasks", marks=pytest.mark.scenario(
        "speckit-format/minimum-feature-content/empty-user-story-work-refuses")),
    pytest.param("both", marks=pytest.mark.scenario(
        "speckit-format/minimum-feature-content/headings-alone-refuse")),
])
def test_validate_minimum_content(feature_copy, empty):
    repo, feature = feature_copy
    spec_path, task_path = feature / "spec.md", feature / "tasks.md"
    spec_text, task_text = spec_path.read_text(), task_path.read_text()
    expected = []
    if empty == "stories":
        spec_text = re.sub(r"^\d+\. .*\n", "", spec_text, flags=re.MULTILINE)
        task_text = tasks.SCENARIO_REF.sub("", task_text)
    elif empty == "tasks":
        # Setup work still cites every scenario, but cannot satisfy story ownership.
        task_text = "## Phase 1: Setup\n" + "\n".join(
            line for line in task_text.splitlines() if tasks.TASK.match(line))
    else:
        spec_text = "## User Scenarios & Testing\n## Requirements\n## Success Criteria\n"
        task_text = ""
        (feature / "plan.md").write_text("", encoding="utf-8")
        shutil.rmtree(feature / "checklists")
    if empty in ("stories", "both"):
        expected.append(f"specs/{CHANGE}/spec.md:1: no user story with a labelled acceptance scenario")
    if empty in ("tasks", "both"):
        expected.append(f"specs/{CHANGE}/tasks.md:1: no user-story task")
    spec_path.write_text(spec_text, encoding="utf-8")
    task_path.write_text(task_text, encoding="utf-8")
    assert speckit.validate(repo, CHANGE) == expected


@pytest.mark.scenario("speckit-format/minimum-feature-content/empty-user-story-work-refuses")
def test_validate_aggregate_keeps_independent_errors(feature_copy):
    repo, feature = feature_copy
    spec_path, task_path = feature / "spec.md", feature / "tasks.md"
    spec_path.write_text(spec_path.read_text().replace(" (Priority: P1)", ""), encoding="utf-8")
    text = re.sub(r"\[US\d+\] ", "", task_path.read_text())
    task_path.write_text(text.replace(f"`{CHANGE}/us1/as1`", "`unknown/us9/as1`"), encoding="utf-8")
    expected = [
        f"specs/{CHANGE}/spec.md:7: US1: missing priority",
        f"specs/{CHANGE}/spec.md:10: uncovered scenario: {CHANGE}/us1/as1",
        f"specs/{CHANGE}/tasks.md:1: no user-story task",
        f"specs/{CHANGE}/tasks.md:13: unknown scenario: unknown/us9/as1",
    ]
    assert speckit.validate(repo, CHANGE) == expected
    assert speckit.validate(repo, CHANGE) == expected


@pytest.mark.scenario("speckit-format/feature-validation/unavailable-inputs-refuse")
@pytest.mark.parametrize("name", ["spec.md", "tasks.md", "plan.md", CHECKLIST])
@pytest.mark.parametrize("failure, message", [
    ("missing", "missing required file"), ("unreadable", "cannot read file"),
    ("decode", "cannot decode UTF-8 file"),
])
def test_validate_unavailable_inputs(feature_copy, monkeypatch, name, failure, message):
    repo, feature = feature_copy
    path = feature / name
    if failure == "decode":
        path.write_bytes(b"\xff")
    elif failure == "missing" and name != CHECKLIST:
        path.unlink()
    else:
        original = Path.read_text

        def fail_read(self, *args, **kwargs):
            if self == path:
                # Also exercise a listed checklist disappearing before its read.
                raise FileNotFoundError() if failure == "missing" else PermissionError()
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fail_read)
    assert speckit.validate(repo, CHANGE) == [f"specs/{CHANGE}/{name}:1: {message}"]


@pytest.mark.scenario("speckit-format/clarification-questions/collect-questions-with-locations")
def test_questions_from_clarification_markers():
    bare = "[NEEDS CLARIFICATION]"
    question = "[NEEDS CLARIFICATION: which gate?]"
    spaced = "[NEEDS CLARIFICATION \t: retain spacing ]"
    text = (f"# Feature\n{bare} and {question} and {bare}\n"
            f"```md\n{question}\n```\n<!--\n{spaced}\n-->\n"
            f"    {bare}\n[NEEDS CLARIFICATION: incomplete\n[needs clarification]\n")
    spec = speckit.parse_spec(text)
    expected = [(bare, 2), (question, 2), (bare, 2), (question, 4), (spaced, 7), (bare, 9)]
    assert [(marker.text, marker.line) for marker in spec.clarifications] == expected
    assert speckit.questions(spec) == [marker for marker, _ in expected]
    assert speckit.questions(speckit.parse_spec("# Feature\nNo questions.\n")) == []
    assert speckit.questions(speckit.parse_spec("")) == []


@pytest.mark.scenario("speckit-format/plan-human-checks/collect-literal-pending-checks")
def test_human_checks_keep_order_duplicates_and_literal_examples():
    plan = ("- [ ] Outside\n### Verification and human rollout checklist\n- [ ] Legacy\n"
            " \t## Human checks\t \n \t- [ ] Repeated\t \n### Detail\n"
            "- [ ]\tKeep  internal\tspacing\n```md\n- [ ] Fenced\n```\n"
            "<!--\n    - [ ] HTML\n-->\n###### Deep detail\n- [ ] Deep\n"
            "## Other\n- [ ] Outside again\n## Human checks\n- [ ] Repeated\n"
            "# End\n- [ ] Outside last\n")
    assert speckit.human_checks(plan) == [
        "- [ ] Repeated", "- [ ]\tKeep  internal\tspacing", "- [ ] Fenced",
        "- [ ] HTML", "- [ ] Deep", "- [ ] Repeated",
    ]
    assert speckit.human_checks("") == []


@pytest.mark.scenario("speckit-format/plan-human-checks/collect-literal-pending-checks")
@pytest.mark.parametrize("heading", [
    "# Human checks", "### Human checks", "## Human checks extra", "## Human checks ##",
    "## Human Checks", "##  Human checks", "##\tHuman checks",
    "\u00a0## Human checks", "## Human checks\u00a0",
    "### Verification and human rollout checklist",
])
def test_human_checks_select_only_exact_heading(heading):
    assert speckit.human_checks(f"{heading}\n- [ ] Ignored") == []


@pytest.mark.scenario("speckit-format/plan-human-checks/collect-literal-pending-checks")
@pytest.mark.parametrize("checkbox", [
    "- [x] Done", "- [X] Done", "- [?] Unknown", "- [] Empty state", "- [  ] Wide state",
    "* [ ] Other bullet", "+ [ ] Other bullet", "-  [ ] Wide gap", "-\t[ ] Tab gap",
    "- [ ]No gap", "- [ ]\u00a0Unicode gap", "\u00a0- [ ] Unicode indent",
    "- [ ]", "- [ ] \t ", "- [ ] \u00a0",
])
def test_human_checks_require_literal_pending_checkbox_and_nonblank_text(checkbox):
    assert speckit.human_checks(f"## Human checks\n{checkbox}") == []


@pytest.mark.scenario("speckit-format/plan-human-checks/collect-literal-pending-checks")
@pytest.mark.parametrize("heading, closes", [
    ("# End", True), (" \t##\tEnd\t ", True), ("## Human checks extra", True),
    ("### Detail", False), ("###### Detail", False), ("####### Detail", False),
    ("#No gap", False), ("##\u00a0Unicode gap", False), ("\u00a0## End", False),
    ("### Verification and human rollout checklist", False),
])
@pytest.mark.parametrize("wrapper", [("", ""), ("```md\n", "\n```"), ("<!--\n", "\n-->")])
def test_human_checks_use_physical_heading_boundaries(heading, closes, wrapper):
    plan = ("## Human checks\n" + wrapper[0] + heading + wrapper[1]
            + "\n- [ ] After boundary\n## Human checks\n- [ ] Reopened")
    expected = [] if closes else ["- [ ] After boundary"]
    assert speckit.human_checks(plan) == expected + ["- [ ] Reopened"]


@pytest.mark.scenario("speckit-format/status-completion/replace-and-insert-idempotently")
@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("statuses", [
    ["**Status**: Draft"], ["**Status**: Complete"],
    ["**Status**: Draft", " \t**Status**:\tReview \t", "**Status**:"],
])
def test_mark_complete_is_idempotent(ending, statuses):
    unrelated = ["Status: Draft", "**Status** : Draft", "Prose **Status**: Draft",
                 "\u00a0**Status**: Draft", "## Other", "  untouched\t "]
    text = ending.join(["# Feature", *statuses, *unrelated])
    expected = ending.join(["# Feature", *["**Status**: Complete" for _ in statuses], *unrelated])
    completed = speckit.mark_complete(text)
    assert completed == expected
    assert speckit.mark_complete(completed) == completed
    assert speckit.parse_spec(completed).status == "Complete"


@pytest.mark.scenario("speckit-format/status-completion/replace-and-insert-idempotently")
@pytest.mark.parametrize("text, expected", [
    ("# Feature\n\nBody\n", "# Feature\n**Status**: Complete\n\nBody\n"),
    ("# Feature\r\nBody\nEnd\r", "# Feature\r\n**Status**: Complete\r\nBody\nEnd\r"),
    ("# Feature\rBody\r", "# Feature\r**Status**: Complete\rBody\r"),
    ("# Feature", "# Feature\n**Status**: Complete\n"),
    ("Preamble\r\n \t#\tFirst\t \n# Second\r\nBody",
     "Preamble\r\n \t#\tFirst\t \n**Status**: Complete\n# Second\r\nBody"),
    ("# Feature\n**Status**: Draft", "# Feature\n**Status**: Complete"),
    ("**Status**: Draft\r\n# Feature\n<!--\n\t**Status**: Review\r-->\n"
     "```md\r\n**Status**: Pending\n```",
     "**Status**: Complete\r\n# Feature\n<!--\n**Status**: Complete\r-->\n"
     "```md\r\n**Status**: Complete\n```"),
])
def test_mark_complete_inserts_after_first_title_and_preserves_line_endings(text, expected):
    assert speckit.mark_complete(text) == expected
    assert speckit.mark_complete(expected) == expected


@pytest.mark.scenario("speckit-format/status-completion/replace-and-insert-idempotently")
@pytest.mark.parametrize("text", [
    "", "Prose only", "## Feature\n", "#\n", "\u00a0# Feature\n",
    "**Status**: Draft\n", "**Status**: Complete\r\n## Feature\r\n",
])
def test_mark_complete_refuses_missing_title_even_with_status(text):
    with pytest.raises(SpecError, match="missing title"):
        speckit.mark_complete(text)

"""Native build fixtures are transformed only in disposable copies."""

from pathlib import Path
import shutil

import pytest

from specloop import speckit, spec_format
from specloop.seal import IDENTITY, git


FIXTURE = Path(__file__).parent / "fixtures" / "speckit"
CHANGE = "042-add-a-gate"


@pytest.fixture
def three_phase_feature(tmp_path):
    root = tmp_path / "source"
    shutil.copytree(FIXTURE, root)
    for path in [root, *root.rglob("*")]:
        path.chmod(path.stat().st_mode | 0o200)
    task_file = root / "specs" / CHANGE / "tasks.md"
    text = task_file.read_text()
    headings = {
        "## Phase 1: Setup": "## Phase 1: User Story 1 - Check the gate",
        "## Phase 2: Foundational": "",
        "## Phase 3: User Story 1 - Check the gate": "",
        "## Phase 4: User Story 2 - Explain a refusal": "## Phase 2: User Story 2 - Explain a refusal",
        "## Phase 5: User Story 3 - Retry a check": "## Phase 3: User Story 3 - Retry a check",
        "## Phase 6: Polish": "",
    }
    for before, after in headings.items():
        assert text.splitlines().count(before) == 1
        text = text.replace(before + "\n", after + "\n" if after else "")
    task_file.write_text(text)
    assert [t.title() for t in speckit.parse_tasks(text)] == [
        t.title() for t in speckit.parse_tasks((FIXTURE / "specs" / CHANGE / "tasks.md").read_text())
    ]
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "three-phase fixture", env=IDENTITY)
    return root, task_file


@pytest.mark.scenario("speckit-format/native-task-mutation/tick-split-extend-and-append")
def test_tick_split_extend_append_on_speckit_tasks(three_phase_feature):
    root, task_file = three_phase_feature
    fmt = spec_format.SPECKIT
    original = task_file.read_text()
    parsed = fmt.parse_tasks(original)
    assert len(parsed) == 7 and {t.section for t in parsed} == {1, 2, 3}
    known = speckit.scenario_ids(CHANGE, speckit.parse_spec((task_file.parent / "spec.md").read_text()))
    assert len(known) == 4
    ticked = fmt.tick(original, parsed[0])
    assert ticked == original.replace("- [ ] T001", "- [X] T001")
    assert fmt.tick(ticked, parsed[0]) == ticked
    children = [f"[US1] Permit checks. Covers `{CHANGE}/us1/as1`.",
                f"[P] [US1] Test checks. Covers `{CHANGE}/us1/as1`."]
    text = fmt.split(ticked, parsed[2], children)
    parent_line = ticked.splitlines(keepends=True)[parsed[2].line]
    assert text == ticked.replace(parent_line, "".join(
        f"- [ ] T003.{n} {body}\n" for n, body in enumerate(children, 1)))
    extended = fmt.extend_section(text, 1, ["More tests"])
    last = next(t for t in fmt.parse_tasks(text) if t.label == "T004")
    lines = text.splitlines(keepends=True)
    assert extended == "".join(lines[:last.line + 1] + ["- [ ] T008 More tests\n"] + lines[last.line + 1:])
    appended = fmt.append_section(extended, "Review round 1", ["Review fix"])
    assert appended.startswith(extended)
    assert appended.endswith("## Phase 4: Review round 1\n\n- [ ] T009 Review fix\n")
    result = fmt.parse_tasks(appended)
    assert result == speckit.parse_tasks(appended)
    assert {sid for task in result for sid in task.scenarios} == set(known)
    assert next(t for t in result if t.label == "T003.1").text == children[0]
    assert next(t for t in result if t.label == "T003.2").text == children[1]
    assert task_file.read_text() == original


@pytest.mark.scenario("speckit-format/native-task-mutation/tick-split-extend-and-append")
@pytest.mark.scenario("speckit-format/native-task-mutation/ambiguous-mutation-refuses")
@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_empty_phases_dotted_roots_and_unbounded_ids(ending):
    fmt = spec_format.SPECKIT
    text = "## Phase 8: Earlier\r\n- [ ] T0999.2 Existing\r\n## Phase 2: Empty" + ending
    extended = fmt.extend_section(text, 2, ["Next"])
    assert extended.startswith(text)
    assert [(t.section, t.label) for t in fmt.parse_tasks(extended)] == [(8, "T0999.2"), (2, "T1000")]
    empty = fmt.append_section(extended, "Empty addition", [])
    appended = fmt.append_section(empty, "Review round 1", ["Last"])
    assert "## Phase 9: Empty addition" in appended
    assert appended.endswith("## Phase 10: Review round 1\n\n- [ ] T1001 Last\n")


@pytest.mark.scenario("speckit-format/native-task-mutation/tick-split-extend-and-append")
@pytest.mark.parametrize("parent, child, expected", [
    ("[P] [US01] Original", "[P] [US1] Child", "[P] [US01] Child"),
    ("[P] [US1] Original", "Child mentions [US2]", "[P] [US1] Child mentions [US2]"),
    ("Original mentions [US1]", "[US2] Child", "[US2] Child"),
    ("[US1] [P] Original", "Child", "[US1] Child"),
])
def test_split_inherits_only_positional_flags(parent, child, expected):
    text = f"## Phase 0: Setup\n\t- [ ] T003 {parent}\nunchanged\r\n"
    task, = speckit.parse_tasks(text)
    moved = text.replace("T003", "T1234.2")
    result = spec_format.SPECKIT.split(moved, task, [child])
    assert result == f"## Phase 0: Setup\n\t- [ ] T1234.2.1 {expected}\nunchanged\r\n"

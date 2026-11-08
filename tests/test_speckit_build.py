"""Native build fixtures are transformed only in disposable copies."""

from pathlib import Path
from dataclasses import asdict, replace
import importlib
import json
import shutil
import subprocess
import tomllib
from unittest.mock import Mock, call

import pytest

from nightshift.cli import main
from specloop import agents, cli, manifest, openspec, speckit, spec_format, tasks
from specloop.manifest import Manifest
from specloop.seal import IDENTITY, git
from specloop.state import Run
from test_spec_format import saved_openspec_run
from test_specloop_command_json import template
import spec_format_legacy_fixtures as legacy


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


@pytest.fixture
def native_admission(three_phase_feature, tmp_path, monkeypatch):
    root, task_file = three_phase_feature
    path = tmp_path / "native.toml"
    path.write_text(f'''[run]
repo = {json.dumps(str(root))}
change = "{CHANGE}"
branch = "main"
format = "speckit"
[roles]
implement = "p"
review = ["p"]
[profiles.p]
adapter = "codex"
model = "test"
[paths]
allowed = ["src/**", "tests/**"]
frozen = ["specs/**", ".specify/**"]
[[verify]]
id = "tests"
run = ["python", "-c", "print('fixture')"]
''')
    for module, name in ((agents, "run"), (openspec, "validate"), (openspec, "scenarios")):
        monkeypatch.setattr(module, name, Mock(side_effect=AssertionError(f"unexpected {name}")))
    argv = []
    run = subprocess.run

    def capture(command, *args, **kwargs):
        argv.append(command)
        assert Path(command[0]).name not in {"npx", "openspec"}
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", capture)
    return root, task_file, path, argv


def commit_feature(root):
    git(root, "add", "-A")
    git(root, "commit", "--allow-empty", "-qm", "fixture edit", env=IDENTITY)
    return git(root, "rev-parse", "HEAD").decode().strip()


@pytest.mark.scenario("specloop-interface/native-admission/admit-fixture-counts")
@pytest.mark.parametrize("sections", [3, 6])
def test_admit_speckit_fixture(native_admission, capfd, sections):
    root, task_file, path, argv = native_admission
    fixture_tasks = FIXTURE / "specs" / CHANGE / "tasks.md"
    original = fixture_tasks.read_bytes()
    if sections == 6:
        task_file.write_bytes(original)
    path.write_text(path.read_text() + f"\n[gates]\nphases = {list(range(1, sections + 1))}\n")
    head = commit_feature(root)
    committed = task_file.read_bytes()
    # Uncommitted edits must not affect either validation or reported counts.
    task_file.write_text("## Phase 0: Uncommitted\n")
    refs = git(root, "show-ref")
    assert main(["admit", str(path), "--json", "--skip-feasibility"]) == 0
    document = json.loads(capfd.readouterr().out)
    assert document["version"] == 1 and document["outcome"] == "admitted"
    assert type(document["sections"]) is int and document["sections"] == sections
    assert type(document["scenarios"]) is int and document["scenarios"] == 4
    assert document["head"] == head
    run = Run.for_change(CHANGE, require_existing=True)
    assert run.manifest_path.read_text() == path.read_text()
    assert cli._manifest(run).format == "speckit"
    bare = run.dir / "repo.git"
    assert git(bare, "rev-parse", "main").decode().strip() == head
    assert git(bare, "show", f"{head}:specs/{CHANGE}/tasks.md") == committed
    assert len(spec_format.SPECKIT.parse_tasks(committed.decode())) == 7
    assert [line for line in committed.splitlines() if tasks.TASK.match(line.decode())] == [
        line for line in original.splitlines() if tasks.TASK.match(line.decode())]
    assert fixture_tasks.read_bytes() == original
    assert git(root, "show-ref") == refs
    assert argv and all(Path(command[0]).name in {"git", "tar"} for command in argv)


def assert_native_refusal(native_admission, monkeypatch, capfd, expected):
    root, task_file, path, _ = native_admission
    head = commit_feature(root)
    problems = cli.problems_with(root, head, manifest.load(path))
    assert any(expected in problem for problem in problems)
    run = Run.for_change(CHANGE)
    run.dir.mkdir(parents=True)
    (run.dir / "lock").touch()
    inode = (run.dir / "lock").stat().st_ino
    (run.dir / "admission").mkdir()
    (run.dir / "admission/partial.json").write_text("{}")
    run.manifest_path.write_text("partial")
    refs = git(root, "show-ref")
    source = task_file.read_bytes()
    save = Mock(side_effect=AssertionError("refusal must not save admitted state"))
    monkeypatch.setattr(Run, "save", save)
    assert main(["admit", str(path), "--json", "--skip-feasibility"]) == 1
    output = capfd.readouterr()
    document = json.loads(output.out)
    assert document["version"] == 1 and document["outcome"] == "refused"
    assert "error" not in document
    assert len(document["reasons"]) == 1 and expected in document["reasons"][0]
    assert expected in output.err
    assert "sections" not in document and "scenarios" not in document
    assert list(run.dir.iterdir()) == [run.dir / "lock"]
    assert (run.dir / "lock").stat().st_ino == inode
    assert git(root, "show-ref") == refs and task_file.read_bytes() == source
    save.assert_not_called()
    agents.run.assert_not_called()
    return document


@pytest.mark.scenario("specloop-interface/native-admission/broken-fixture-retains-source-diagnostic")
def test_admit_refuses_broken_fixture(native_admission, monkeypatch, capfd):
    root, task_file, _, _ = native_admission
    sid = f"{CHANGE}/us1/as2"
    text = task_file.read_text()
    assert text.count(f"`{sid}`") == 1
    task_file.write_text(text.replace(f"`{sid}`", ""))
    spec = speckit.parse_spec((task_file.parent / "spec.md").read_text())
    line = next(s.line for story in spec.stories if story.number == 1
                for s in story.scenarios if s.number == 2)
    diagnostic = f"specs/{CHANGE}/spec.md:{line}: uncovered scenario: {sid}"
    assert speckit.validate(root, CHANGE) == [diagnostic]
    document = assert_native_refusal(native_admission, monkeypatch, capfd, diagnostic)
    assert document["reasons"] == [f"the change is not admissible:\n  - {diagnostic}"]


@pytest.mark.scenario("specloop-interface/native-admission/invalid-task-structure-refuses")
@pytest.mark.parametrize("extra, replacement, gates, diagnostic", [
    ("", "", [], "no tasks under '## Phase N: Title' phases"),
    ("", "## 1. Numeric only\n- [ ] T001 Outside\n", [], "no tasks under '## Phase N: Title' phases"),
    ("## Phase 4: Empty\n", None, [4], "phase gate 4 names no populated phase"),
    ("## 9. Numeric\n- [ ] T900 Outside\n", None, [9], "phase gate 9 names no populated phase"),
    ("", None, [99], "phase gate 99 names no populated phase"),
    ("## Phase 01: Repeated\n", None, [], "duplicate native phase number: 1"),
    ("## Phase 4: Other\n- [ ] T0001 Reused\n", None, [], "duplicate native task label"),
    ("## Phase 4: Other\n- [ ] T010.01 One\n- [ ] T0010.1 Two\n", None, [], "duplicate native task label"),
    ("## Phase 4: Other\n- [ ] T010 Same\n- [ ] T011 Same\n", None, [], "ambiguous native task key"),
])
def test_admit_invalid_structure(native_admission, monkeypatch, capfd, extra, replacement, gates, diagnostic):
    _, task_file, path, _ = native_admission
    task_file.write_text(replacement if replacement is not None else task_file.read_text() + "\n" + extra)
    path.write_text(path.read_text() + f"\n[gates]\nphases = {gates}\n")
    assert_native_refusal(native_admission, monkeypatch, capfd, diagnostic)


@pytest.mark.scenario("specloop-interface/native-admission/phase-zero-refuses")
@pytest.mark.parametrize("extra, gates, diagnostic", [
    ("## Phase 0: Empty\n", [], "phase zero is not admissible"),
    ("## Phase 00: Populated\n- [ ] T008 Legal standalone task\n", [], "phase zero is not admissible"),
    ("```md\n## Phase 0: Literal fence\n```\n", [], "phase zero is not admissible"),
    ("<!--\n## Phase 0: Literal comment\n-->\n", [], "phase zero is not admissible"),
    ("", [0], "phase gate zero is not admissible"),
])
def test_admit_phase_zero(native_admission, monkeypatch, capfd, extra, gates, diagnostic):
    _, task_file, path, _ = native_admission
    text = task_file.read_text() + "\n" + extra
    task_file.write_text(text)
    parsed = speckit.parse_tasks(text)  # Phase zero remains legal in the standalone parser.
    if "T008" in extra:
        assert parsed[-1].section == 0
    path.write_text(path.read_text() + f"\n[gates]\nphases = {gates}\n")
    assert_native_refusal(native_admission, monkeypatch, capfd, diagnostic)


@pytest.mark.scenario("spec-format/native-adapter-operations/native-paths-and-labels")
def test_native_paths_and_labels(native_admission):
    root, task_file, path, _ = native_admission
    fmt = spec_format.get(manifest.load(path))
    assert fmt is spec_format.SPECKIT
    assert fmt.change_dir(root, CHANGE) == task_file.parent
    assert fmt.tasks_path(CHANGE) == f"specs/{CHANGE}/tasks.md"
    assert fmt.drafter_root(CHANGE) == f"specs/{CHANGE}/"
    assert fmt.pr_title(CHANGE) == f"{CHANGE}: spec-kit feature via specloop"
    assert fmt.pr_kind == "spec-kit feature"
    assert fmt.tasks_paths_at(root, "HEAD", CHANGE) == [fmt.tasks_path(CHANGE)]
    for archive in (root / "specs/archive" / f"2026-11-07-{CHANGE}",
                    root / "openspec/changes/archive" / f"2026-11-07-{CHANGE}"):
        archive.mkdir(parents=True)
        (archive / "tasks.md").write_bytes(task_file.read_bytes())
    task_file.unlink()
    head = commit_feature(root)
    assert fmt.tasks_paths_at(root, head, CHANGE) == [fmt.tasks_path(CHANGE)]
    with pytest.raises(RuntimeError):
        git(root, "show", f"{head}:{fmt.tasks_paths_at(root, head, CHANGE)[0]}")


@pytest.mark.scenario("spec-format/native-adapter-operations/native-delegation-remains-offline")
@pytest.mark.parametrize("method, backend, result", [
    ("validate", "validate", ["specs/example/spec.md:12: uncovered scenario"]),
    ("scenarios", "scenarios", {"example/us1/as1": "scenario"}),
    ("change_dir", "feature_dir", Path("replacement")),
])
def test_native_delegation_is_late_bound(tmp_path, monkeypatch, method, backend, result):
    delegated = getattr(spec_format.SPECKIT, method)
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=AssertionError("must stay offline")))
    replacement = Mock(return_value=result)
    monkeypatch.setattr(speckit, backend, replacement)
    assert delegated(tmp_path, CHANGE) is result
    replacement.assert_called_once_with(tmp_path, CHANGE)
    error = spec_format.SpecError("original diagnostic")
    replacement.side_effect = error
    with pytest.raises(spec_format.SpecError) as caught:
        delegated(tmp_path, CHANGE)
    assert caught.value is error


@pytest.mark.scenario("spec-format/native-adapter-operations/native-delegation-remains-offline")
def test_native_scenarios_compose_current_helpers(three_phase_feature, monkeypatch):
    root, task_file = three_phase_feature
    text = (task_file.parent / "spec.md").read_text()
    parsed = speckit.parse_spec(text)
    expected = speckit.scenario_ids(CHANGE, parsed)
    method = spec_format.SPECKIT.scenarios
    parser = Mock(return_value=parsed)
    ids = Mock(return_value=expected)
    monkeypatch.setattr(speckit, "parse_spec", parser)
    monkeypatch.setattr(speckit, "scenario_ids", ids)
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=AssertionError("must stay offline")))
    assert method(root, CHANGE) is expected
    parser.assert_called_once_with(text)
    ids.assert_called_once_with(CHANGE, parsed)


@pytest.mark.scenario("spec-format/native-adapter-operations/native-delegation-remains-offline")
@pytest.mark.parametrize("failure", ["missing", "directory", "utf8", "unreadable"])
def test_native_scenario_read_failure(tmp_path, monkeypatch, failure):
    path = tmp_path / "specs" / CHANGE / "spec.md"
    path.parent.mkdir(parents=True)
    if failure == "directory":
        path.mkdir()
    elif failure == "utf8":
        path.write_bytes(b"\xff")
    elif failure == "unreadable":
        monkeypatch.setattr(Path, "read_text", Mock(side_effect=PermissionError("denied")))
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=AssertionError("must stay offline")))
    with pytest.raises(spec_format.SpecError, match="cannot read native scenarios") as caught:
        spec_format.SPECKIT.scenarios(tmp_path, CHANGE)
    assert str(path) in str(caught.value)


@pytest.mark.scenario("speckit-format/local-feature-api/resolve-a-feature-without-integration")
def test_feature_lookup_is_pure(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        for method in ("exists", "stat", "read_text", "open"):
            patch.setattr(Path, method, Mock(side_effect=AssertionError("unexpected filesystem access")))
        patch.setattr(subprocess, "run", Mock(side_effect=AssertionError("unexpected dispatch")))
        patch.setattr(spec_format, "get", Mock(side_effect=AssertionError("unexpected selection")))
        importlib.reload(speckit)
        for root in (tmp_path, Path("relative/repo")):
            assert speckit.feature_dir(root, CHANGE) == root / "specs" / CHANGE


@pytest.mark.scenario("spec-format/format-seam/legacy-paths-and-publication-labels")
def test_legacy_paths_and_labels_still_use_openspec(tmp_path):
    m = Manifest(tmp_path, "sample-change", "main", "main", "", {}, (), (), ())
    for selected in (m, replace(m, format="openspec")):
        fmt = spec_format.get(selected)
        assert fmt is spec_format.OPENSPEC
        assert fmt.change_dir(tmp_path, m.change) == tmp_path / "openspec/changes/sample-change"
        assert fmt.tasks_path(m.change) == "openspec/changes/sample-change/tasks.md"
        assert fmt.drafter_root(m.change) == "openspec/changes/sample-change/"
        assert fmt.pr_title(m.change) == "sample-change: OpenSpec change via specloop"
        assert fmt.pr_kind == "OpenSpec change"


@pytest.mark.scenario("spec-format/format-seam/delegation-stays-late-bound")
@pytest.mark.parametrize("method, backend", [("validate", "validate"), ("scenarios", "scenarios"), ("finish", "archive")])
def test_legacy_delegation_preserves_backend_contract(tmp_path, monkeypatch, method, backend):
    delegated = getattr(spec_format.OPENSPEC, method)
    for completed in (False, True):
        args = (tmp_path, CHANGE, completed) if method == "finish" else (tmp_path, CHANGE)
        kwargs = {"all_tasks_done": completed} if method == "finish" else {}
        result = object()
        replacement = Mock(return_value=result)
        monkeypatch.setattr(openspec, backend, replacement)
        assert delegated(*args) is result
        replacement.assert_called_once_with(tmp_path, CHANGE, **kwargs)
        for cls in (openspec.SpecError, openspec.ValidatorUnavailable, OSError):
            error = cls("unchanged")
            replacement.side_effect = error
            with pytest.raises(cls) as caught:
                delegated(*args)
            assert caught.value is error
            assert replacement.call_args == call(tmp_path, CHANGE, **kwargs)


@pytest.mark.scenario("spec-format/manifest-format-selection/saved-openspec-runs-remain-compatible")
def test_native_selection_keeps_saved_openspec_schema(saved_openspec_run):
    run, expected, task, head = saved_openspec_run
    before = {p: p.read_bytes() for p in (run.manifest_path, run.dir / "state.json")}
    m = cli._manifest(run)
    fmt = spec_format.get(m)
    assert fmt is spec_format.OPENSPEC
    assert asdict(legacy.parse(tomllib.loads(run.manifest_path.read_text()))) == {
        key: value for key, value in asdict(m).items() if key != "format"}
    assert asdict(legacy.load_state(run.dir)) == asdict(Run(run.dir, require_existing=True).state) == expected
    text = git(run.dir / "repo.git", "show", f"{head}:{fmt.tasks_path(m.change)}").decode()
    assert fmt.parse_tasks(text)[0] == task
    assert {p: p.read_bytes() for p in before} == before

"""Task formats are selected by the caller, never inferred from headings."""

import ast
import json
import tomllib
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from nightshift.cli import main
from specloop import agents, cli, evidence, loop, manifest, openspec, speckit, spec_format, tasks
from specloop.manifest import Manifest
from specloop.openspec import SpecError
from specloop.seal import git
from specloop.state import Run
from test_spec_format import commit, repo, run_context
from test_specloop_command_json import BRANCH, CHANGE, template
from test_speckit_build import commit_feature, native_admission, three_phase_feature


OPERATIONS = ("parse_tasks", "tick", "split", "extend_section", "append_section")
LEGACY_SCENARIO = "spec-format/manifest-selected-task-operations/openspec-ignores-native-heading-declarations"
NATIVE_SCENARIO = "spec-format/manifest-selected-task-operations/native-ignores-openspec-phase-declarations"
CONSUMER_SCENARIO = "spec-format/manifest-selected-task-operations/consumers-use-selected-task-operations"
AMBIGUITY_SCENARIO = "speckit-format/native-task-mutation/ambiguous-mutation-refuses"


def operation_args(operation, text, task):
    return {
        "parse_tasks": (text,), "tick": (text, task),
        "split": (text, task, ["Child one", "Child two"]),
        "extend_section": (text, 1, ["Extended"]),
        "append_section": (text, "Appended", ["New task"]),
    }[operation]


@pytest.mark.scenario(LEGACY_SCENARIO)
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("wrapper", [("", ""), ("```md\n", "```\n"), ("<!--\n", "-->\n")])
def test_openspec_task_operations_match_unchanged_backend(operation, wrapper):
    text = ("## Phase 1: Before\n- [ ] T001 Outside\n## 1. Core\n- [ ] 1.1 First\n"
            + wrapper[0] + "## Phase 1: Example\n- [ ] T002 Literal\n" + wrapper[1]
            + "## 2. Last\n- [ ] 2.1 Last\n")
    args = operation_args(operation, text, tasks.parse(text)[0])
    backend = getattr(tasks, "parse" if operation == "parse_tasks" else operation)
    expected = backend(*args)
    actual = getattr(spec_format.OPENSPEC, operation)(*args)
    assert actual == expected
    if isinstance(expected, str):
        assert actual.encode() == expected.encode()


@pytest.mark.scenario(LEGACY_SCENARIO)
@pytest.mark.parametrize("operation", ["tick", "split", "extend_section"])
def test_openspec_missing_target_errors_are_unchanged(operation):
    text = "## Phase 1: Example\n- [ ] T001 Native only\n"
    task = tasks.Task(1, "Core", "1.1", "Missing", False, 0)
    args = operation_args(operation, text, task)
    with pytest.raises(KeyError) as expected:
        getattr(tasks, operation)(*args)
    with pytest.raises(KeyError) as actual:
        getattr(spec_format.OPENSPEC, operation)(*args)
    assert actual.value.args == expected.value.args


@pytest.mark.parametrize("adapter, backend", [
    pytest.param(spec_format.OPENSPEC, tasks, marks=pytest.mark.scenario(LEGACY_SCENARIO)),
    pytest.param(spec_format.SPECKIT, speckit, marks=pytest.mark.scenario(NATIVE_SCENARIO)),
])
@pytest.mark.parametrize("operation", OPERATIONS)
def test_task_delegation_is_late_bound(monkeypatch, adapter, backend, operation):
    task = tasks.Task(1, "Core", "T003", "Work", False, 1)
    args = operation_args(operation, "literal input", task)
    delegated = "parse" if backend is tasks and operation == "parse_tasks" else operation
    method = getattr(adapter, operation)  # Bind before replacing the backend.
    result = object()
    replacement = Mock(return_value=result)
    monkeypatch.setattr(backend, delegated, replacement)
    assert method(*args) is result
    assert replacement.call_args == call(*args)
    assert all(actual is expected for actual, expected in zip(replacement.call_args.args, args))
    error = SpecError("same exception")
    replacement.side_effect = error
    with pytest.raises(SpecError) as caught:
        method(*args)
    assert caught.value is error


@pytest.mark.scenario(NATIVE_SCENARIO)
def test_numeric_headings_close_native_phases_without_selecting_them():
    text = ("## 9. Outside\n- [ ] T001 Outside\n"
            "## Phase 1: Core\n- [ ] T003 [US1] Native\n"
            "### Nested\n- [ ] T004 Nested\n"
            "## 99. Boundary\n- [ ] T003 Outside duplicate ignored\n"
            " ## Phase 8: Indented\n- [ ] T005 Still outside\n"
            "## Phase 2: Empty\n")
    fmt = spec_format.SPECKIT
    parsed = fmt.parse_tasks(text)
    assert [(t.section, t.label) for t in parsed] == [(1, "T003"), (1, "T004")]
    ticked = fmt.tick(text, parsed[0])
    assert ticked == text.replace("- [ ] T003 [US1]", "- [X] T003 [US1]")
    split = fmt.split(ticked, parsed[0], ["One", "Two"])
    extended = fmt.extend_section(split, 1, ["Extra"])
    assert "- [ ] T005 Extra\n## 99. Boundary" in extended
    result = fmt.append_section(extended, "Review round 1", ["Review"])
    assert result.endswith("## Phase 3: Review round 1\n\n- [ ] T006 Review\n")
    assert fmt.parse_tasks(result) == speckit.parse_tasks(result)
    assert [t.label for t in fmt.parse_tasks(result)] == ["T003.1", "T003.2", "T004", "T005", "T006"]


@pytest.mark.scenario(AMBIGUITY_SCENARIO)
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("wrapper", [("", ""), ("```\n", "```\n"), ("<!--\n", "-->\n")])
@pytest.mark.parametrize("extra, error", [
    ("## Phase 01: Repeated empty phase\n", "duplicate native phase"),
    ("## Phase 2: Other\n- [ ] T0003 Reused\n", "duplicate native task label"),
    ("- [ ] T003.01 Dotted\n- [ ] T0003.1 Reused dotted\n", "duplicate native task label"),
    ("- [ ] T004 Original\n", "ambiguous native task key"),
    ("## Phase 2: Core\n- [ ] T004 Original\n", "ambiguous native task key"),
])
def test_native_ambiguity_refuses_all_operations(operation, wrapper, extra, error):
    original = "## Phase 1: Core\n- [ ] T003 Original\n"
    task, = speckit.parse_tasks(original)
    text = original + wrapper[0] + extra + wrapper[1]
    with pytest.raises(SpecError, match=error):
        getattr(spec_format.SPECKIT, operation)(*operation_args(operation, text, task))


@pytest.mark.scenario(AMBIGUITY_SCENARIO)
@pytest.mark.parametrize("operation", ["tick", "split", "extend_section"])
def test_missing_native_mutation_target_refuses(operation):
    text = "## Phase 2: Other\n- [ ] T002 Present\n"
    task = tasks.Task(1, "Core", "T001", "Missing", False, 1)
    with pytest.raises(SpecError, match="no native phase|no longer in tasks.md"):
        getattr(spec_format.SPECKIT, operation)(*operation_args(operation, text, task))


@pytest.mark.scenario(AMBIGUITY_SCENARIO)
@pytest.mark.parametrize("operation, extra, tail", [
    ("split", "- [ ] T003.1 Existing child\n", ["Child"]),
    ("split", "", ["Same", "Same"]),
    ("split", "", ["[US2] Conflicting"]),
    ("extend_section", "", ["[US1] Original"]),
    ("append_section", "", ["[US1] Original"]),
    ("extend_section", "", ["New\n- [ ] T003 Injected collision"]),
    ("append_section", "", ["New\n## Phase 1: Repeated"]),
])
def test_proposed_mutations_are_validated(operation, extra, tail):
    text = "## Phase 1: Core\n- [ ] T003 [US1] Original\n" + extra
    task = speckit.parse_tasks(text)[0]
    target = task if operation == "split" else (1 if operation == "extend_section" else "Core")
    with pytest.raises(SpecError):
        getattr(spec_format.SPECKIT, operation)(text, target, tail)


@pytest.mark.scenario(CONSUMER_SCENARIO)
def test_build_consumers_have_no_direct_task_operations():
    for module in (cli, loop):
        tree = ast.parse(Path(module.__file__).read_text())
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "tasks"
                    and node.func.attr in {"parse", *OPERATIONS}]
    tree = ast.parse(Path(loop.__file__).read_text())
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                and node.attr == "format" and isinstance(node.value, (ast.Name, ast.Attribute))
                and (getattr(node.value, "id", None) == "m" or getattr(node.value, "attr", None) == "m")]


@pytest.fixture
def native_context(repo, tmp_path, monkeypatch):
    """Select task operations independently of task 1.2's admission adapter."""
    adapter = Mock(wraps=spec_format.OPENSPEC)
    for operation in OPERATIONS:
        getattr(adapter, operation).side_effect = getattr(spec_format.SPECKIT, operation)
    path = "specs/sample-change/tasks.md"
    adapter.tasks_path.return_value = path
    adapter.tasks_paths_at.return_value = [path]
    adapter.validate.return_value = []
    adapter.scenarios.return_value = {"sample-change/us1/as1": "Scenario"}
    adapter.human_checks.return_value = []
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    text = "## Phase 1: Core\n- [ ] T003 [US1] Work `sample-change/us1/as1`\n"
    commit(repo, {path: text})
    ctx = run_context(repo, tmp_path)
    ctx.m = replace(ctx.m, format="speckit")
    monkeypatch.setattr(cli, "_ctx", lambda run: ctx)
    monkeypatch.setattr(cli, "_manifest", lambda run: ctx.m)
    monkeypatch.setattr(cli.Run, "for_change", lambda *a, **kw: ctx.run)
    return ctx, adapter, selection


@pytest.mark.scenario(CONSUMER_SCENARIO)
def test_admission_run_accept_and_candidate_replay_use_adapter(native_context, monkeypatch):
    ctx, adapter, selection = native_context
    head = ctx.head()
    text = ctx.tasks_text(head)
    assert cli.problems_with(ctx.repo, head, ctx.m) == []
    adapter.parse_tasks.assert_called_once_with(text)
    task, = speckit.parse_tasks(text)
    selected = []

    def stop(ctx, task, head):
        selected.append(task)
        ctx.run.state.status = "paused"

    monkeypatch.setattr(loop, "step", stop)
    ctx.run.state.status = "running"
    assert loop.run(ctx) == "paused"
    assert selected == [task]
    ts = ctx.run.state.task(task.key)
    ts.candidate_base = head
    ts.candidate = loop.seal.commit_files(ctx.repo, head, {"work.py": "pass\n"}, "candidate")
    args = SimpleNamespace(change=ctx.m.change, task=task.key, no_run=True)
    assert cli._accept(args, ctx.run) == 0
    adapter.tick.assert_called_once_with(text, task)
    assert ctx.tasks_text(ctx.head()) == text.replace("[ ]", "[X]")
    landed = ctx.head()
    ctx.run.pause("stuck", task.key, "interrupted accept")
    ts.status = "pending"
    assert cli._accept(args, ctx.run) == 0
    assert ctx.head() == landed and ts.status == "accepted"
    assert adapter.tick.call_count == 2
    assert all(c.args[0] is ctx.m for c in selection.call_args_list[1:])


@pytest.mark.scenario(CONSUMER_SCENARIO)
def test_status_pr_finish_and_append_use_adapter(native_context, monkeypatch, capsys):
    ctx, adapter, _ = native_context
    task, = speckit.parse_tasks(ctx.tasks_text(ctx.head()))
    ctx.run.pause("human_checks")
    assert cli._finish(SimpleNamespace(change=ctx.m.change, expect_head=None), ctx.run) == 1
    assert "tasks are unfinished" in capsys.readouterr().err
    adapter.parse_tasks.assert_called_once()
    loop._land(ctx, task, ctx.run.state.task(task.key), ctx.head(), ctx.head())
    assert cli._status_entry(ctx.m.change)["tasks"] == {"done": 1, "total": 1}
    assert cli.status(SimpleNamespace(change=ctx.m.change, json=False, events=0)) == 0
    assert "tasks: 1/1 done" in capsys.readouterr().out
    assert "1 tasks, each sealed" in cli._pr_body(ctx)
    ctx.pause("ready_to_publish")
    assert adapter.parse_tasks.call_count == 5
    head = ctx.head()
    before = ctx.tasks_text(head)
    assert loop._append(ctx, head, "Coverage", ["Add coverage"])
    adapter.append_section.assert_called_once_with(before, "Coverage", ["Add coverage"])
    assert speckit.parse_tasks(ctx.tasks_text(ctx.head()))[-1].label == "T004"


@pytest.mark.scenario(CONSUMER_SCENARIO)
def test_implementer_split_uses_adapter(native_context, monkeypatch):
    ctx, adapter, _ = native_context
    head = ctx.head()
    before = ctx.tasks_text(head)
    task, = speckit.parse_tasks(before)
    children = ["First `sample-change/us1/as1`", "Second `sample-change/us1/as1`"]
    ctx.agent = lambda call: agents.Result("ok", {"status": "too_big", "summary": "Split", "subtasks": children})
    monkeypatch.setattr(loop, "_call", lambda *a: SimpleNamespace(profile=SimpleNamespace(name="fake")))
    monkeypatch.setattr(loop, "_infra", lambda *a: False)
    loop.step(ctx, task, head)
    adapter.split.assert_called_once_with(before, task, children)
    parsed = speckit.parse_tasks(ctx.tasks_text(ctx.head()))
    assert [t.label for t in parsed] == ["T003.1", "T003.2"]
    assert all(t.text.startswith("[US1] ") for t in parsed)
    assert git(ctx.repo, "diff", "--name-only", head, ctx.head()).decode().splitlines() == [ctx.tasks_path]


@pytest.mark.scenario(CONSUMER_SCENARIO)
@pytest.mark.parametrize("source", ["evidence", "gate"])
def test_section_fixes_use_adapter_extension(native_context, monkeypatch, source):
    ctx, adapter, _ = native_context
    task, = speckit.parse_tasks(ctx.tasks_text(ctx.head()))
    loop._land(ctx, task, ctx.run.state.task(task.key), ctx.head(), ctx.head())
    head = ctx.head()
    before = ctx.tasks_text(head)
    parsed = speckit.parse_tasks(before)
    if source == "evidence":
        monkeypatch.setattr(loop.evidence, "gather", lambda *a, **kw: evidence.Evidence(
            1, uncovered=["sample-change/us1/as1"]))
        assert loop.section_evidence(ctx, 1, head, parsed)
    else:
        ctx.agent = lambda call: agents.Result("ok", {
            "verdict": "FAIL", "prior": [], "needs_human": [],
            "findings": [{"blocking": True, "basis": "scenario:sample-change/us1/as1", "text": "Fix it"}],
        })
        monkeypatch.setattr(loop, "_call", lambda *a: SimpleNamespace(profile=SimpleNamespace(name="fake")))
        monkeypatch.setattr(loop, "_infra", lambda *a: False)
        assert loop.gate_review(ctx, 1, head, parsed)
    adapter.extend_section.assert_called_once()
    assert adapter.extend_section.call_args.args[:2] == (before, 1)
    result = speckit.parse_tasks(ctx.tasks_text(ctx.head()))
    assert [(t.section, t.label, t.done) for t in result] == [(1, "T003", True), (1, "T004", False)]
    assert result[-1].text == adapter.extend_section.call_args.args[2][0]


@pytest.mark.scenario("specloop-documentation/format-selection-guidance/operator-selects-the-supported-format")
def test_complete_self_hosting_example_selects_supported_format(monkeypatch, tmp_path):
    example = Path(__file__).resolve().parents[1] / "examples/specloop/self-hosting.toml"
    data = tomllib.loads(example.read_text())
    assert data["run"]["format"] == "openspec"
    explicit = manifest.load(example)  # Parse every table, including verification and routes.
    assert spec_format.get(explicit) is spec_format.OPENSPEC
    del data["run"]["format"]
    assert manifest.parse(data) == explicit
    data["run"]["format"] = "speckit"
    reserved = manifest.parse(data)
    assert reserved == replace(explicit, format="speckit")
    assert spec_format.get(reserved) is spec_format.SPECKIT
    for module, name in ((openspec, "validate"), (agents, "run")):
        monkeypatch.setattr(module, name, Mock(side_effect=AssertionError(f"unexpected {name}")))
    # The example's source path is operator-owned; validate a disposable checkout.
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    head = commit(source, {"README.md": "missing native feature\n"})
    diagnostic = f"specs/{reserved.change}/spec.md:1: missing required file"
    assert diagnostic in cli.problems_with(source, head, reserved)



@pytest.mark.scenario("spec-format/manifest-format-selection/omitted-and-explicit-openspec-agree")
@pytest.mark.scenario("spec-format/manifest-format-selection/spec-kit-selects-the-native-adapter")
def test_manifest_construction_and_adapter_selection(tmp_path):
    m = Manifest(tmp_path, "sample-change", "spec/sample-change", "main", "", {}, (), (), ())
    assert fields(Manifest)[-1].name == "format"
    assert m.format == "openspec"
    assert spec_format.get(m) is spec_format.OPENSPEC
    assert spec_format.get(replace(m, format="openspec")) is spec_format.OPENSPEC
    assert spec_format.get(replace(m, format="speckit")) is spec_format.SPECKIT



@pytest.mark.scenario("spec-format/manifest-format-selection/spec-kit-is-refused-at-admission")
@pytest.mark.scenario("spec-format/manifest-format-selection/spec-kit-selects-the-native-adapter")
@pytest.mark.scenario("specloop-documentation/format-selection-guidance/operator-selects-the-supported-format")
@pytest.mark.parametrize("overrides", [False, True])
def test_manifest_format_speckit_refused_at_admission(template, monkeypatch, capfd, overrides):
    path, repo = template
    text = path.read_text().replace("[run]", '[run]\nformat = "speckit"')
    if not overrides:
        text = text.replace('change = "x"', f'change = "{CHANGE}"').replace(
            'branch = "spec/x"', f'branch = "{BRANCH}"')
    path.write_text(text)
    assert manifest.load(path).format == "speckit"
    kwargs = {"change": CHANGE, "branch": BRANCH} if overrides else {}
    m, effective = manifest.template(path, **kwargs)
    assert (m.format, m.change, m.branch) == ("speckit", CHANGE, BRANCH)
    assert tomllib.loads(effective)["run"]["format"] == "speckit"
    forbidden = {}
    for module, name in ((openspec, "validate"), (openspec, "scenarios"),
                         (cli.feasibility, "check"), (agents, "run"), (Run, "save")):
        forbidden[name] = Mock(side_effect=AssertionError(f"unexpected {name}"))
        monkeypatch.setattr(module, name, forbidden[name])
    assert spec_format.get(m) is spec_format.SPECKIT
    validator = Mock(wraps=speckit.validate)
    monkeypatch.setattr(speckit, "validate", validator)
    diagnostic = f"specs/{CHANGE}/spec.md:1: missing required file"
    problems = cli.problems_with(repo, BRANCH, m)
    assert diagnostic in problems
    validator.assert_called_once()

    run = Run.for_change(CHANGE)
    run.dir.mkdir(parents=True)
    (run.dir / "lock").touch()
    lock_inode = (run.dir / "lock").stat().st_ino
    (run.dir / "admission").mkdir()
    (run.dir / "admission/partial.json").write_text("{}")
    run.manifest_path.write_text("partial admission")
    refs = git(repo, "show-ref")
    flags = ["--change", CHANGE, "--branch", BRANCH] if overrides else []
    assert main(["admit", str(path), *flags, "--json"]) == 1
    output = capfd.readouterr()
    document = json.loads(output.out)  # Reject extra documents or stdout diagnostics.
    assert document["version"] == 1 and document["outcome"] == "refused"
    assert len(document["reasons"]) == 1
    assert document["reasons"][0].startswith("the change is not admissible:\n")
    assert diagnostic in document["reasons"][0]
    for problem in problems:
        if ":1: missing required file" in problem:
            assert problem in document["reasons"][0]
    assert "spec-kit format is not available yet" not in document["reasons"][0]
    assert validator.call_count == 2
    assert "error" not in document
    assert diagnostic in output.err
    for boundary in forbidden.values():
        boundary.assert_not_called()
    assert not (run.dir / "state.json").exists()
    assert list(run.dir.iterdir()) == [run.dir / "lock"]
    assert (run.dir / "lock").stat().st_ino == lock_inode
    assert git(repo, "show-ref") == refs
    assert path.read_text() == text


@pytest.mark.scenario("spec-format/manifest-format-selection/omitted-and-explicit-openspec-agree")
@pytest.mark.scenario("specloop-interface/native-admission/phase-zero-refuses")
@pytest.mark.parametrize("format_value", [None, "openspec"])
@pytest.mark.parametrize("gates", [[], [0]])
def test_legacy_admission_retains_zero_gate_and_json(template, capfd, format_value, gates):
    path, source = template
    text = path.read_text()
    if format_value:
        text = text.replace("[run]", '[run]\nformat = "openspec"')
    path.write_text(text + f"\n[gates]\nphases = {gates}\n")
    task_path = f"openspec/changes/{CHANGE}/tasks.md"
    original = (source / task_path).read_text()
    git(source, "checkout", "-q", BRANCH)
    commit(source, {task_path: "## 0. Setup\n- [ ] 0.1 Prepare\n" + original + "\n## Phase 0: Example\n"})
    assert main(["admit", str(path), "--change", CHANGE, "--branch", BRANCH,
                 "--skip-feasibility", "--json"]) == (1 if gates else 0)
    document = json.loads(capfd.readouterr().out)
    assert document["outcome"] == ("refused" if gates else "admitted")
    if gates:
        assert document["reasons"] == ["the change is not admissible:\n  - phase gate 0 names no section in tasks.md"]
    assert set(document) == {"version", "outcome", "head", "reasons", "risks"}


@pytest.mark.scenario("spec-format/manifest-format-selection/spec-kit-selects-the-native-adapter")
def test_native_admission_does_not_skip_requested_feasibility(native_admission, monkeypatch, capfd):
    _, _, path, _ = native_admission
    check = Mock(return_value={"error": "controlled feasibility stop"})
    monkeypatch.setattr(cli.feasibility, "check", check)
    assert main(["admit", str(path), "--json"]) == 1
    document = json.loads(capfd.readouterr().out)
    assert document["outcome"] == "refused"
    assert "controlled feasibility stop" in document["reasons"][0]
    check.assert_called_once()
    assert check.call_args.args[2].format == "speckit"
    assert not (Run.for_change(check.call_args.args[2].change).dir / "state.json").exists()


@pytest.mark.scenario("specloop-interface/native-admission/invalid-task-structure-refuses")
def test_native_admission_uses_only_native_phase_boundaries(native_admission, capfd):
    root, task_file, path, _ = native_admission
    task_file.write_text(task_file.read_text() + "\n## 99. Numeric heading\n"
                         "- [ ] T001 Outside native phases\n ## Phase 0: Indented example\n")
    commit_feature(root)
    assert main(["admit", str(path), "--skip-feasibility", "--json"]) == 0
    document = json.loads(capfd.readouterr().out)
    assert document["outcome"] == "admitted"
    assert document["sections"] == 3 and document["scenarios"] == 4

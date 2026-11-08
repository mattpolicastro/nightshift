"""Native build fixtures are transformed only in disposable copies."""

from pathlib import Path
from dataclasses import asdict, replace
import importlib
import json
import shutil
import subprocess
import tomllib
from unittest.mock import Mock, call
from types import SimpleNamespace

import pytest

from nightshift.cli import main
from specloop import agents, cli, loop, manifest, openspec, seal, speckit, spec_format, tasks, verify
from specloop.manifest import Manifest
from specloop.seal import IDENTITY, git
from specloop.state import Run, TaskState
from test_spec_format import saved_openspec_run
from test_specloop_command_json import template
from test_specloop_forge_selection import configured
from fake_gitea import FakeGitea, Reply
from nightshift.forge import http
import spec_format_legacy_fixtures as legacy

GITEA_REQUEST = cli.GiteaForge._request


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


@pytest.fixture
def native_ctx(three_phase_feature, tmp_path):
    root, _ = three_phase_feature
    run = Run(tmp_path / "run")
    run.dir.mkdir()
    git(tmp_path, "clone", "-q", "--bare", str(root), str(run.dir / "repo.git"))
    profile = manifest.Profile("fake", "codex", "test")
    m = Manifest(root, CHANGE, "main", "main", "", dict.fromkeys(("implement", "review", "gate"), profile),
                 ("src/**",), ("specs/**", ".specify/**"),
                 (manifest.Step("tests", ("/usr/bin/true",)),), format="speckit")
    run.save()
    return loop.Ctx(run, m, {})


def native_commit(ctx, files):
    head = ctx.head()
    commit = seal.commit_files(ctx.repo, head, files, "trusted fixture edit")
    git(ctx.repo, "update-ref", ctx.ref, commit, head)
    return commit


def context_aliases(root):
    return [(root / "specs" / CHANGE / "spec.md", "proposal.md"),
            (root / "specs" / CHANGE / "spec.md", "specs/spec.md"),
            (root / "specs" / CHANGE / "plan.md", "design.md"),
            (root / ".specify/memory/constitution.md", "specs/constitution.md")]


@pytest.mark.scenario("spec-format/native-context-and-human-checks/selected-feature-context")
@pytest.mark.parametrize("consumer", ["implementation", "review", "gate"])
def test_native_context_bytes(native_ctx, tmp_path, consumer):
    ctx = native_ctx
    # CRLF and non-ASCII bytes must survive disposable aliases unchanged.
    for path in dict(context_aliases(ctx.m.repo)):
        data = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(data.replace(b"\n", b"\r\n") + "\r\nNative café\r\n".encode())
    historical = ctx.m.repo / "specs/001-old/spec.md"
    historical.parent.mkdir()
    historical.write_text("Historical feature must not be traversed")
    before = {p: p.read_bytes() for p, _ in context_aliases(ctx.m.repo)}
    assert spec_format.SPECKIT.context_files(ctx.m.repo, CHANGE) == context_aliases(ctx.m.repo)
    task = spec_format.SPECKIT.parse_tasks(ctx.tasks_text(ctx.head()))[2]
    context = tmp_path / consumer / "context"
    if consumer == "implementation":
        loop._context(ctx, task, TaskState(), ctx.m.repo, context, False)
    elif consumer == "review":
        loop._review_context(ctx, task, TaskState(), ctx.head(), ctx.head(), ctx.m.repo, context)
    else:
        loop._gate_context(ctx, 1, ctx.head(), [task], TaskState(), ctx.m.repo, context)
    for source, dest in context_aliases(ctx.m.repo):
        assert (context / dest).read_bytes() == before[source]
        assert source.read_bytes() == before[source]
    assert sorted(p.name for p in (context / "specs").iterdir()) == ["constitution.md", "spec.md"]
    assert not (context / "tasks.md").exists()


@pytest.mark.scenario("spec-format/native-context-and-human-checks/selected-feature-context")
def test_context_copy_uses_source_type(native_ctx, tmp_path, monkeypatch):
    file = tmp_path / "file"
    file.write_bytes(b"literal\r\n\xff")
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "child").write_bytes(b"nested\r\n")
    adapter = Mock(wraps=spec_format.SPECKIT)
    adapter.context_files.return_value = [(file, "specs"), (directory, "nested/other")]
    monkeypatch.setattr(spec_format, "get", lambda m: adapter)
    context = tmp_path / "context"
    loop._copy_change(native_ctx.m.repo, native_ctx, context)
    assert (context / "specs").read_bytes() == file.read_bytes()
    assert (context / "nested/other/child").read_bytes() == (directory / "child").read_bytes()


@pytest.mark.scenario("spec-format/native-context-and-human-checks/plan-checks-use-the-committed-revision")
def test_human_checks_from_plan(native_ctx, monkeypatch):
    ctx = native_ctx
    plan = """- [ ] outside
## Human checks
 \t- [ ] first\t
### Nested
- [ ] second\u2028###### Deepest
- [ ] third
- [x] checked
- [X] checked uppercase
- [ ] \t
- [ ] \u00a0
- [ ]missing separator
* [ ] wrong bullet
## Other
- [ ] outside
### Verification and human rollout checklist
- [ ] not a native selected heading
## human checks
- [ ] wrong case
## Human checks ##
- [ ] decorated heading
\u00a0## Human checks
- [ ] non-ASCII padding
```md
## Human checks
- [ ] literal fenced example
```
<!--
## Human checks
- [ ] literal comment
-->
    ## Human checks
    - [ ] indented example
## Human checks
- [ ] first
####### Not a boundary
#No separator
- [ ] last
# End
- [ ] outside
"""
    path = f"specs/{CHANGE}/plan.md"
    head = native_commit(ctx, {path: plan.replace("\n", "\r\n")})
    (ctx.m.repo / path).write_text("## Human checks\n- [ ] dirty source\n")
    expected = [f"- [ ] {item}" for item in (
        "first", "second", "third", "literal fenced example", "literal comment", "indented example", "first", "last")]
    collector = Mock(wraps=speckit.human_checks)
    monkeypatch.setattr(speckit, "human_checks", collector)
    assert loop.design_human_checks(ctx, head) == expected
    collector.assert_called_once_with(plan.replace("\n", "\r\n"))
    originals = [" \t- [ ] first\t", "- [ ] first"]
    assert loop.append_design_checks(ctx, head, originals) == originals + list(dict.fromkeys(expected[1:-2])) + [expected[-1]]
    ctx.m = replace(ctx.m, coverage_exempt={f"{CHANGE}/us3/as1": "Check manually"})
    monkeypatch.setattr(loop, "prepare_publish", lambda ctx: True)
    assert loop.finals(ctx, head) is False
    assert ctx.run.state.pause.reason == "ready_to_publish"
    for text, separator in ((ctx.run.state.pause.detail, ":"), (cli._pr_body(ctx), " —")):
        rows = [line for line in text.splitlines() if line.startswith("- [ ]")]
        assert rows == [f"- [ ] `{CHANGE}/us3/as1`{separator} Check manually", *dict.fromkeys(expected)]
    newer = native_commit(ctx, {path: "## Human checks\n- [ ] newer\n"})
    assert loop.design_human_checks(ctx, newer) == ["- [ ] newer"]
    assert loop.design_human_checks(ctx, head) == expected


@pytest.mark.scenario("spec-format/native-context-and-human-checks/plan-failure-blocks-readiness")
@pytest.mark.parametrize("failure", ["missing", "lookup", "read", "os", "subprocess", "decode"])
def test_plan_failure_blocks_readiness(native_ctx, monkeypatch, failure):
    ctx = native_ctx
    path = f"specs/{CHANGE}/plan.md"
    if failure in {"missing", "decode"}:
        source = ctx.m.repo / path
        if failure == "missing":
            source.unlink()
        else:
            source.write_bytes(b"\xff")
        commit_feature(ctx.m.repo)
        git(ctx.repo, "fetch", "-q", str(ctx.m.repo), f"+main:{ctx.ref}")
        source.write_text("## Human checks\n- [ ] source cannot rescue committed plan\n")
    original = spec_format.git

    def fail(repo, *args, **kwargs):
        if args[0] == "show" and args[1].endswith(":" + path):
            if failure == "lookup":
                return original(repo, "show", f"missing-revision:{path}")
            if failure == "read":
                raise RuntimeError("blob read failed")
            if failure == "os":
                raise OSError("cannot start git")
            if failure == "subprocess":
                raise subprocess.SubprocessError("git failed")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(spec_format, "git", fail)
    monkeypatch.setattr(loop, "prepare_publish", lambda ctx: True)
    for render in (lambda: loop.finals(ctx, ctx.head()), lambda: cli._pr_body(ctx)):
        with pytest.raises(RuntimeError, match="could not collect human checks.*plan.md"):
            render()
        assert ctx.run.state.pause is None and ctx.run.state.pr == ""


@pytest.mark.scenario("spec-format/native-context-and-human-checks/plan-failure-blocks-readiness")
@pytest.mark.parametrize("plan", ["", "## Other\n- [ ] outside\n", "## Human checks\n- [x] done\n- [ ] \t\n"])
def test_readable_plan_without_pending_checks(native_ctx, plan):
    head = native_commit(native_ctx, {f"specs/{CHANGE}/plan.md": plan})
    assert loop.design_human_checks(native_ctx, head) == []


def fake_receipts(monkeypatch, ctx, seen=(), inspect=None, exit_code=0):
    calls = []

    def check(repo, head, steps, m, out, *, run_dir=None):
        assert repo == ctx.repo and m is ctx.m and run_dir == ctx.run.dir
        if inspect:
            inspect(head)
        calls.append(head)
        out.mkdir(parents=True, exist_ok=True)
        receipts = [verify.Receipt(s.id, list(s.run), s.expect, exit_code, s.expect == exit_code, 0.1, "fixture")
                    for s in steps]
        for step in steps:
            (out / f"{step.id}.log").write_text("\n".join(seen))
        (out / "receipts.json").write_text(json.dumps([asdict(r) for r in receipts]))
        return receipts

    monkeypatch.setattr(verify, "run_steps", check)
    return calls


@pytest.mark.scenario("specloop-interface/native-build-gates/evidence-groups-by-story")
def test_section_evidence_groups_by_story(native_ctx, monkeypatch):
    ctx = native_ctx
    ctx.m = replace(ctx.m, coverage_step="tests", evidence=True)
    fmt = spec_format.SPECKIT
    text = ctx.tasks_text(ctx.head())
    for task in fmt.parse_tasks(text):
        if task.section == 1:
            text = fmt.tick(text, task)
    text = text.replace("Permit passing checks.", f"Permit passing checks. Unknown `{CHANGE}/us1/as999`.")
    head = native_commit(ctx, {ctx.tasks_path: text})
    parsed = fmt.parse_tasks(text)
    fake_receipts(monkeypatch, ctx, [f"{CHANGE}/us1/as1"])
    extend = Mock(wraps=fmt.extend_section)
    monkeypatch.setattr(fmt, "extend_section", extend)
    assert loop.section_evidence(ctx, 1, head, parsed)
    event = next(e for e in agents.read_jsonl(ctx.run.dir / "events.jsonl") if e["event"] == "evidence")
    assert event["uncovered"] == [f"{CHANGE}/us1/as2"] and not event["passed"]
    assert ctx.run.state.evidenced == []
    added, = [t for t in fmt.parse_tasks(ctx.tasks_text(ctx.head())) if t.label == "T008"]
    assert added.section == 1 and added.scenarios == [f"{CHANGE}/us1/as2"]
    extend.assert_called_once_with(text, 1, [added.text])
    assert [(t.label, t.text, t.done) for t in fmt.parse_tasks(ctx.tasks_text(ctx.head())) if t.section != 1] == [
        (t.label, t.text, t.done) for t in parsed if t.section != 1]
    assert loop._group([f"{CHANGE}/us1/as1", f"{CHANGE}/us1/as2", f"{CHANGE}/us2/as1"]) == [
        [f"{CHANGE}/us1/as1", f"{CHANGE}/us1/as2"], [f"{CHANGE}/us2/as1"]]


@pytest.mark.scenario("specloop-interface/native-build-gates/evidence-groups-by-story")
@pytest.mark.parametrize("failure", ["missing", "decode"])
def test_failed_scenario_lookup_cannot_pass_or_append(native_ctx, monkeypatch, failure):
    ctx = native_ctx
    source = ctx.m.repo / "specs" / CHANGE / "spec.md"
    if failure == "missing":
        source.unlink()
    else:
        source.write_bytes(b"\xff")
    commit_feature(ctx.m.repo)
    git(ctx.repo, "fetch", "-q", str(ctx.m.repo), f"+main:{ctx.ref}")
    head = ctx.head()
    adapter = Mock(wraps=spec_format.SPECKIT)
    monkeypatch.setattr(spec_format, "get", lambda m: adapter)
    with pytest.raises(spec_format.SpecError, match="cannot read native scenarios"):
        loop.section_evidence(ctx, 1, head, adapter.parse_tasks(ctx.tasks_text(head)))
    assert ctx.head() == head and ctx.run.state.evidenced == [] and ctx.run.state.strengthen_rounds == {}
    adapter.extend_section.assert_not_called()
    adapter.append_section.assert_not_called()


@pytest.mark.scenario("specloop-interface/native-build-gates/native-notes-reach-the-phase-reviewer")
@pytest.mark.parametrize("format,label,other", [("speckit", "T003", "T005"), ("openspec", "1.1", "2.1")])
def test_gate_notes_match_exact_parsed_labels(native_ctx, tmp_path, monkeypatch, format, label, other):
    ctx = native_ctx
    ctx.m = replace(ctx.m, format=format)
    if format == "speckit":
        parsed = spec_format.SPECKIT.parse_tasks(ctx.tasks_text(ctx.head()))
    else:
        parsed = tasks.parse("## 1. First\n- [x] 1.1 Done\n- [x] Unlabelled\n## 2. Other\n- [ ] 2.1 Pending\n")
        monkeypatch.setattr(openspec, "scenarios", lambda *args: {})
    selected = f"- ({label}) [scope] retained"
    excluded = [f"- ({other}) other phase", f"- ({label}.1) child", f"- ({label}0) prefix",
                f" - ({label}) indented", f"- ({label})missing space", f"- ({label})\twrong separator",
                f"- ({label} ) padded", f"- ({label} missing close", f"- (({label}) nested",
                f"- ({label})bad) later close", "- () empty", "unlabelled"]
    notes = ctx.run.dir / "followups.md"
    original = "\n".join([selected, *excluded, selected]) + "\n"
    notes.write_text(original)
    section = [t for t in parsed if t.section == 1]
    context = tmp_path / "notes-context"
    loop._gate_context(ctx, 1, ctx.head(), section, TaskState(), ctx.m.repo, context)
    assert (context / "followups.md").read_text() == selected + "\n" + selected + "\n"
    assert notes.read_text() == original
    notes.write_text("\n".join(excluded))
    loop._gate_context(ctx, 1, ctx.head(), section, TaskState(), ctx.m.repo, context)
    assert (context / "followups.md").read_text() == "(none)\n"


def review_result(findings=()):
    return agents.Result("ok", {"verdict": "PASS", "prior": [], "findings": list(findings), "needs_human": []})


@pytest.mark.scenario("specloop-interface/native-build-gates/native-notes-reach-the-phase-reviewer")
def test_native_gate_fixes_extend_phase(native_ctx, monkeypatch):
    ctx = native_ctx
    head = ctx.head()
    fmt = spec_format.SPECKIT
    parsed = fmt.parse_tasks(ctx.tasks_text(head))
    finding = {"blocking": True, "basis": f"scenario:{CHANGE}/us1/as2", "text": "Refusal still succeeds"}
    ctx.agent = lambda call: review_result([finding])
    extend = Mock(wraps=fmt.extend_section)
    monkeypatch.setattr(fmt, "extend_section", extend)
    assert loop.gate_review(ctx, 1, head, parsed)
    added = fmt.parse_tasks(ctx.tasks_text(ctx.head()))[4]
    assert (added.label, added.section, added.done) == ("T008", 1, False)
    assert finding["text"] in added.text
    extend.assert_called_once_with(ctx.tasks_text(head), 1, [added.text])
    assert ctx.run.state.gates_passed == []


@pytest.mark.scenario("specloop-interface/native-build-gates/evidence-groups-by-story")
@pytest.mark.parametrize("title", ["Coverage", "Acceptance fixes"])
def test_native_final_additions_use_adapter(native_ctx, monkeypatch, title):
    ctx = native_ctx
    if title == "Coverage":
        ctx.m = replace(ctx.m, coverage_step="tests")
    else:
        ctx.m = replace(ctx.m, acceptance=(manifest.Step("acceptance", ("/usr/bin/true",)),))
    fake_receipts(monkeypatch, ctx, [f"{CHANGE}/us1/as1"], exit_code=0 if title == "Coverage" else 1)
    monkeypatch.setattr(loop, "prepare_publish", lambda ctx: True)
    fmt = spec_format.SPECKIT
    append = Mock(wraps=fmt.append_section)
    monkeypatch.setattr(fmt, "append_section", append)
    head = ctx.head()
    assert loop.finals(ctx, head)
    added = [t for t in fmt.parse_tasks(ctx.tasks_text(ctx.head())) if t.section == 4]
    assert [t.label for t in added] == (["T008", "T009", "T010"] if title == "Coverage" else ["T008"])
    assert all(t.section_title == title and not t.done for t in added)
    append.assert_called_once_with(ctx.tasks_text(head), title, [t.text for t in added])
    if title == "Coverage":
        assert [t.scenarios for t in added] == [[f"{CHANGE}/us1/as2"], [f"{CHANGE}/us2/as1"], [f"{CHANGE}/us3/as1"]]


@pytest.mark.scenario("specloop-interface/native-build-gates/real-story-build")
@pytest.mark.scenario("specloop-interface/native-build-gates/harness-writes-preserve-frozen-criteria")
@pytest.mark.scenario("specloop-interface/native-build-gates/native-notes-reach-the-phase-reviewer")
def test_fake_agent_build_through_native_phase_gates(native_ctx, monkeypatch):
    """Offline driver coverage; the real profiles, publication and finish remain a human check."""
    ctx = native_ctx
    ctx.m = replace(ctx.m, phase_gates=(1, 2, 3), gate_review="agent", evidence=True, coverage_step="tests")
    fmt = spec_format.SPECKIT
    base = ctx.head()
    original = ctx.tasks_text(base)
    frozen = {str(p.relative_to(ctx.m.repo)): p.read_bytes() for p, _ in context_aliases(ctx.m.repo)}
    implemented, reviewed, gates = [], [], []
    note = {"basis": "scope", "text": "Keep this T003 follow-up", "blocking": False}
    loop._followups(ctx, "T003.1", [{"text": "not T003"}])
    loop._followups(ctx, "T0030", [{"text": "not T003 either"}])
    loop._followups(ctx, "T005", [{"text": "phase two only"}])

    def inspect(head):
        for path, data in frozen.items():
            assert git(ctx.repo, "show", f"{head}:{path}") == data
        assert b"src/T001.txt" in git(ctx.repo, "ls-tree", "-r", "--name-only", head)

    verified = fake_receipts(monkeypatch, ctx, fmt.scenarios(ctx.m.repo, CHANGE), inspect)
    monkeypatch.setattr(loop, "prepare_publish", lambda ctx: True)

    def agent(call):
        context = call.cwd.parent / "context"
        for path, dest in context_aliases(ctx.m.repo):
            assert (context / dest).read_bytes() == frozen[str(path.relative_to(ctx.m.repo))]
        if call.access == "write":
            task = next(t for t in fmt.parse_tasks(ctx.tasks_text(ctx.head())) if not t.done)
            implemented.append(task.label)
            (call.cwd / "src").mkdir(exist_ok=True)
            (call.cwd / "src" / f"{task.label}.txt").write_text("allowed code\n")
            if task.label == "T001":
                for path in [*frozen, ctx.tasks_path]:
                    (call.cwd / path).write_text("agent-authored criteria or completion\n")
                spec_path = f"specs/{CHANGE}/spec.md"
                (call.cwd / spec_path).write_text(speckit.mark_complete(frozen[spec_path].decode()))
                (call.cwd / "specs/invented.md").write_text("invented criteria\n")
            return agents.Result("ok", {"status": "ready", "summary": "fixture edit", "subtasks": []})
        if (context / "section.md").exists():
            section = int((context / "section.md").read_text().split()[2].rstrip(":"))
            gates.append(section)
            notes = (context / "followups.md").read_text()
            assert notes == {1: "- (T003) [scope] Keep this T003 follow-up\n",
                             2: "- (T005) [] phase two only\n", 3: "(none)\n"}[section]
            assert (context / "evidence.md").read_text() == ctx.run.state.evidence_summary[str(section)] + "\n"
            return review_result()
        label = (context / "task.md").read_text().split()[2]
        reviewed.append(label)
        assert (context / "receipts.md").is_file()
        return review_result([note] if label == "T003" else [])

    ctx.agent = agent
    assert loop.run(ctx) == "paused"
    assert ctx.run.state.pause.reason == "ready_to_publish"
    assert implemented == reviewed == [f"T{n:03d}" for n in range(1, 8)]
    # Existing gate policy only reviews sections that still have pending work after them.
    assert gates == ctx.run.state.gates_passed == [1, 2]
    assert ctx.run.state.evidenced == [1, 2, 3]
    assert len(verified) == 11  # seven candidates, three section receipts, final coverage
    final = ctx.head()
    assert ctx.tasks_text(final) == original.replace("- [ ]", "- [X]")
    first = fmt.parse_tasks(original)[0]
    ts = ctx.run.state.tasks[first.key]
    assert sorted(ts.violations) == sorted(f"{path}: frozen path (change reverted)" for path in [
        *frozen, ctx.tasks_path, "specs/invented.md"])
    assert git(ctx.repo, "show", f"{ts.candidate}:{ctx.tasks_path}").decode() == original
    assert not git(ctx.repo, "ls-tree", final, "specs/invented.md")
    events = agents.read_jsonl(ctx.run.dir / "events.jsonl")
    assert sum(e["event"] == "landed" for e in events) == 7
    assert all(e["passed"] for e in events if e["event"] == "verified")
    assert "- (T003.1)" in cli._pr_body(ctx)  # PR retains the full follow-up file.
    # Native completion uses the same trusted writer after the candidate seal.
    spec_path = f"specs/{CHANGE}/spec.md"
    complete = speckit.mark_complete(frozen[spec_path].decode())
    completed = spec_format.SPECKIT.finish(ctx.repo, CHANGE, True, head=final)["commit"]
    assert loop._advance(ctx, final, completed)
    assert git(ctx.repo, "diff", "--name-only", final, completed).decode().splitlines() == [spec_path]
    assert git(ctx.repo, "show", f"{completed}:{spec_path}") == complete.encode()
    assert ctx.tasks_text(completed) == ctx.tasks_text(final)
    assert git(ctx.repo, "show", "-s", "--format=%an <%ae>", completed).decode().strip() == "specloop <specloop@localhost>"
    assert git(ctx.repo, "show", "-s", "--format=%B", completed).decode().strip() == f"specloop: complete spec-kit feature {CHANGE}"
    for path, data in frozen.items():
        if path != spec_path:
            assert git(ctx.repo, "show", f"{completed}:{path}") == data
    for path, data in frozen.items():
        assert (ctx.m.repo / path).read_bytes() == data
    assert (ctx.m.repo / ctx.tasks_path).read_text() == original


def record_native_commands(monkeypatch):
    """Record actual process argv, including Git object operations."""
    commands = []
    real = subprocess.run

    def run(argv, *args, **kwargs):
        commands.append(list(argv))
        assert Path(argv[0]).name not in {"npx", "openspec"}
        if Path(argv[0]).name == "git":
            assert not {"clone", "worktree", "archive"}.intersection(argv)
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return commands


def completed_tasks(ctx):
    text = ctx.tasks_text(ctx.head())
    for task in speckit.parse_tasks(text):
        text = speckit.tick(text, task)
    return text


@pytest.mark.scenario("speckit-format/native-completion-commit/complete-with-no-external-validator")
@pytest.mark.scenario("speckit-format/native-completion-commit/complete-status-is-idempotent")
@pytest.mark.parametrize("original,expected", [
    ("# Feature\r\n\r\n**Status**: Draft\r\nCriteria\n\t**Status**: Review\t\nTail",
     "# Feature\r\n\r\n**Status**: Complete\r\nCriteria\n**Status**: Complete\nTail"),
    ("Preamble\r\n# First\r\n# Second\nCriteria", "Preamble\r\n# First\r\n**Status**: Complete\r\n# Second\nCriteria"),
    ("# Feature", "# Feature\n**Status**: Complete\n"),
    ("# Feature\n**Status**: Complete\n", "# Feature\n**Status**: Complete\n"),
])
def test_finish_marks_complete_without_npx(native_ctx, monkeypatch, original, expected):
    ctx = native_ctx
    path = f"specs/{CHANGE}/spec.md"
    head = native_commit(ctx, {ctx.tasks_path: completed_tasks(ctx), path: original})
    source = {p: p.read_bytes() for p in ctx.m.repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    refs = git(ctx.repo, "show-ref")
    argv = record_native_commands(monkeypatch)
    commit_files = Mock(wraps=seal.commit_files)
    monkeypatch.setattr(seal, "commit_files", commit_files)
    result = spec_format.SPECKIT.finish(ctx.repo, CHANGE, True, head=head)
    commit = result["commit"]
    assert git(ctx.repo, "show", f"{commit}:{path}") == expected.encode()
    assert git(ctx.repo, "show-ref") == refs
    assert {p: p.read_bytes() for p in source} == source
    assert git(ctx.repo, "diff", "--name-only", head, commit).decode().splitlines() == ([] if original == expected else [path])
    if original != expected:
        commit_files.assert_called_once_with(ctx.repo, head, {path: expected}, f"specloop: complete spec-kit feature {CHANGE}")
        assert git(ctx.repo, "show", "-s", "--format=%P%n%B%n%an <%ae>%n%cn <%ce>", commit).decode().splitlines() == [
            head, f"specloop: complete spec-kit feature {CHANGE}", "", "specloop <specloop@localhost>",
            "specloop <specloop@localhost>"]
    else:
        assert commit == head
        commit_files.assert_not_called()
    count = commit_files.call_count
    assert spec_format.SPECKIT.finish(ctx.repo, CHANGE, True, head=commit) == result
    assert commit_files.call_count == count
    assert spec_format.SPECKIT.tasks_paths_at(ctx.repo, commit, CHANGE) == [ctx.tasks_path]
    assert all(t.done for t in speckit.parse_tasks(ctx.tasks_text(commit)))
    assert argv and any("show" in a for a in argv)


@pytest.mark.scenario("speckit-format/native-completion-commit/incomplete-or-unreadable-feature-refuses")
@pytest.mark.scenario("speckit-format/native-completion-commit/complete-status-is-idempotent")
@pytest.mark.parametrize("failure", ["false", "head", "missing-head", "unfinished", "empty", "duplicate-phase",
                                          "duplicate-label", "missing-spec", "missing-tasks", "decode-spec",
                                          "decode-tasks", "no-title"])
def test_native_completion_refuses(native_ctx, monkeypatch, failure):
    ctx = native_ctx
    path = f"specs/{CHANGE}/spec.md"
    text = completed_tasks(ctx)
    if failure == "unfinished":
        text = text.replace("[X]", "[ ]", 1)
    elif failure == "empty":
        text = "## Phase 1: Empty\n"
    elif failure == "duplicate-phase":
        text += "\n## Phase 1: Duplicate\n"
    elif failure == "duplicate-label":
        text += "- [X] T001 Duplicate\n"
    spec = "**Status**: Complete\n" if failure == "no-title" else "# Feature\n**Status**: Complete\n"
    head = native_commit(ctx, {ctx.tasks_path: text, path: spec})
    if failure.startswith(("missing-", "decode-")) and failure != "missing-head":
        (ctx.m.repo / ctx.tasks_path).write_text(text)
        (ctx.m.repo / path).write_text(spec)
        source = ctx.m.repo / (path if failure.endswith("spec") else ctx.tasks_path)
        if failure.startswith("missing-"):
            source.unlink()
        else:
            source.write_bytes(b"\xff")
        commit_feature(ctx.m.repo)
        git(ctx.repo, "fetch", "-q", str(ctx.m.repo), f"+main:{ctx.ref}")
        head = ctx.head()
    refs = git(ctx.repo, "show-ref")
    argv = record_native_commands(monkeypatch)
    commit_files = Mock(wraps=seal.commit_files)
    monkeypatch.setattr(seal, "commit_files", commit_files)
    with pytest.raises(spec_format.SpecError):
        spec_format.SPECKIT.finish(ctx.repo, CHANGE, failure != "false",
                                  head=None if failure == "head" else "absent" if failure == "missing-head" else head)
    assert git(ctx.repo, "show-ref") == refs
    commit_files.assert_not_called()
    assert not any("push" in a or "update-ref" in a for a in argv)


@pytest.fixture(params=["github", "gitea"])
def native_service(native_admission, configured, tmp_path, monkeypatch, capfd, request):
    root, _, path, _ = native_admission
    gitea = request.param == "gitea"
    repository = "forge.example/example/repo" if gitea else "example/repo"
    origin = ("https://forge.example:8443/git/example/repo.git" if gitea
              else "https://github.com/example/repo.git")
    url = ("https://forge.example:8443/git/example/repo/pulls/42" if gitea
           else "https://github.com/example/repo/pull/42")
    path.write_text(path.read_text().replace('[run]\n', f'[run]\ngithub = "{repository}"\n'))
    git(root, "remote", "add", "origin", origin)
    assert main(["admit", str(path), "--json", "--skip-feasibility"]) == 0
    capfd.readouterr()
    run = Run.for_change(CHANGE, require_existing=True)
    ctx = cli._ctx(run)
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(root), str(remote))
    native_commit(ctx, {ctx.tasks_path: completed_tasks(ctx)})
    run.state.pr = url
    run.pause("human_checks")
    s = SimpleNamespace(ctx=ctx, run=run, remote=remote, origin=origin, url=url, gitea=gitea,
                        effects=[], gh=[], failure=None, fake=FakeGitea("https://forge.example:8443/git"))
    monkeypatch.setattr(cli.GiteaForge, "_request", GITEA_REQUEST)
    monkeypatch.setattr(http, "build_opener", lambda: s.fake)
    real_push, real_fetch = cli._push, loop._fetch_publish_base

    def push(repo, url, ref, *, source=None):
        assert (repo, url, ref, source) == (ctx.repo, origin, ctx.ref, root)
        assert Run(run.dir).state.status == "paused"
        s.effects.append("push")
        if s.failure == "push":
            raise RuntimeError("push failed")
        real_push(repo, str(remote), ref, source=source)

    def fetch(ctx, url, **kw):
        assert url == origin
        s.effects.append("fetch")
        return real_fetch(ctx, str(remote), **kw)

    def gh(*args):
        assert not gitea
        s.gh.append(args)
        assert git(remote, "rev-parse", ctx.ref).decode().strip() == ctx.head()
        assert Run(run.dir).state.status == "paused"
        if args[:2] == ("pr", "ready"):
            s.effects.append("ready")
            if s.failure in {"lost-ready", "ready"}:
                raise RuntimeError("lost response")
            return ""
        if args[:2] == ("pr", "view"):
            return "true" if s.failure == "ready" else "false"
        if args[:2] == ("pr", "create"):
            return url
        assert args[0] == "api"
        return "[]"

    monkeypatch.setattr(cli, "_push", push)
    monkeypatch.setattr(loop, "_fetch_publish_base", fetch)
    monkeypatch.setattr(cli, "_gh", gh)
    s.argv = record_native_commands(monkeypatch)
    return s


@pytest.mark.scenario("specloop-interface/native-publication-lifecycle/finish-guards-and-ordering")
@pytest.mark.scenario("specloop-forge/verified-gitea-finish/prefix-removal-is-verified")
@pytest.mark.scenario("specloop-forge/verified-gitea-finish/existing-finish-gates-remain")
@pytest.mark.parametrize("mode", ["matching", "omitted", "act"])
def test_native_finish_ordering(native_service, monkeypatch, capfd, mode):
    s = native_service
    ctx = s.ctx
    head = ctx.head()
    path = f"specs/{CHANGE}/spec.md"
    original = git(ctx.repo, "show", f"{head}:{path}").decode()
    complete = Mock(wraps=speckit.finish)
    monkeypatch.setattr(speckit, "finish", complete)
    remote = cli._remote

    def validate(*args):
        assert complete.call_count == 0
        s.effects.append("origin")
        return remote(*args)

    monkeypatch.setattr(cli, "_remote", validate)
    if s.gitea:
        def read(call):
            assert s.effects[:2] == ["origin", "push"]
            assert Run(s.run.dir).state.pause.reason == "human_checks"
            assert git(s.remote, "rev-parse", ctx.ref).decode().strip() == ctx.head() != head
            return Reply({"title": "Draft: Keep Draft: inside" if len(s.fake.calls) == 1 else "Keep Draft: inside"})
        endpoint = "/git/api/v1/repos/example/repo/pulls/42"
        s.fake.route("GET", endpoint, read, read)
        s.fake.route("PATCH", endpoint, Reply({}))
    else:
        s.failure = "lost-ready"
    argv = ["act", CHANGE, "finish", "--json"] if mode == "act" else ["finish", CHANGE]
    if mode == "matching":
        argv += ["--expect-head", head.upper()]
    assert main(argv) == 0
    output = capfd.readouterr()
    assert f"{CHANGE}: completed and marked ready for review — {s.url}" in (output.err if mode == "act" else output.out)
    if mode == "act":
        assert json.loads(output.out)["outcome"] == "done"
    final = ctx.head()
    complete.assert_called_once_with(ctx.repo, CHANGE, True, head=head)
    assert git(ctx.repo, "diff", "--name-only", head, final).decode().splitlines() == [path]
    assert git(ctx.repo, "show", f"{final}:{path}").decode() == speckit.mark_complete(original)
    assert any(a[-4:] == ["update-ref", ctx.ref, final, head] for a in s.argv)
    s.run.reload()
    assert s.run.state.status == "done" and s.run.state.pause is None
    assert list(agents.read_jsonl(s.run.dir / "events.jsonl"))[-1]["event"] == "finished"
    if s.gitea:
        assert [(c.method, c.body) for c in s.fake.calls] == [
            ("GET", None), ("PATCH", {"title": "Keep Draft: inside"}), ("GET", None)]
    else:
        assert s.gh == [("pr", "ready", s.url, "-R", ctx.m.github),
                        ("pr", "view", s.url, "-R", ctx.m.github, "--json", "isDraft", "--jq", ".isDraft")]


@pytest.mark.scenario("specloop-interface/native-publication-lifecycle/finish-guards-and-ordering")
@pytest.mark.scenario("specloop-forge/verified-gitea-finish/existing-finish-gates-remain")
@pytest.mark.parametrize("gate", ["head", "unfinished", "empty", "ambiguous", "origin", "completion", "push", "race", "state"])
def test_native_finish_guards(native_service, monkeypatch, capfd, gate):
    s = native_service
    ctx = s.ctx
    if gate in {"unfinished", "empty", "ambiguous"}:
        text = ctx.tasks_text(ctx.head())
        text = (text.replace("[X]", "[ ]", 1) if gate == "unfinished" else
                "" if gate == "empty" else text + "\n## Phase 1: Duplicate\n")
        native_commit(ctx, {ctx.tasks_path: text})
    elif gate == "origin":
        git(ctx.m.repo, "remote", "set-url", "origin", "https://other.example/foreign/repo.git")
    elif gate == "state":
        s.run.pause("ready_to_publish")
    elif gate == "push":
        s.failure = "push"
    head = ctx.head()
    competing = seal.commit_files(ctx.repo, head, {"src/competing": "winner"}, "competing writer")
    finish = speckit.finish

    def complete(*args, **kwargs):
        if gate == "completion":
            raise spec_format.SpecError("completion failed")
        result = finish(*args, **kwargs)
        if gate == "race":
            git(ctx.repo, "update-ref", ctx.ref, competing, head)
        return result

    spy = Mock(side_effect=complete)
    monkeypatch.setattr(speckit, "finish", spy)
    state = (s.run.dir / "state.json").read_bytes()
    remote = git(s.remote, "rev-parse", ctx.ref)
    args = ["finish", CHANGE, "--expect-head", "0" * 40 if gate == "head" else head]
    if gate in {"ambiguous", "origin", "completion", "push"}:
        with pytest.raises((RuntimeError, ValueError)):
            main(args)
    else:
        assert main(args) == 1
    assert (s.run.dir / "state.json").read_bytes() == state
    assert s.fake.calls == s.gh == []
    assert s.effects == (["push"] if gate == "push" else [])
    assert git(s.remote, "rev-parse", ctx.ref) == remote
    if gate == "race":
        assert ctx.head() == competing
        assert "branch changed" in capfd.readouterr().err
    elif gate == "push":
        assert ctx.head() != head
    else:
        assert ctx.head() == head
    assert spy.call_count == int(gate in {"completion", "push", "race"})


@pytest.mark.scenario("specloop-interface/native-publication-lifecycle/publish-native-feature")
@pytest.mark.parametrize("mode", ["new", "adopted", "existing", ".github/workflows/test.yml",
                                 ".gitea/workflows/test.yml", ".forgejo/test.yml"])
def test_native_publication(native_service, monkeypatch, mode):
    s = native_service
    ctx = s.ctx
    protected = mode.startswith(".")
    if protected:
        native_commit(ctx, {mode: "workflow"})
    s.run.state.pr = s.url if mode == "existing" else ""
    s.run.pause("ready_to_publish")
    # Only committed plan checks belong in the draft.
    (ctx.m.repo / f"specs/{CHANGE}/plan.md").write_text("## Human checks\n- [ ] dirty source\n")
    checks = spec_format.SPECKIT.human_checks(ctx.repo, ctx.head(), CHANGE)
    assert checks
    endpoint = "/git/api/v1/repos/example/repo/pulls"
    if s.gitea:
        row = {"id": 42, "number": 42, "html_url": s.url, "state": "open",
               "head": {"ref": ctx.m.branch, "repo": {"full_name": "example/repo"}},
               "base": {"ref": ctx.m.pr_base}}
        s.fake.route("GET", endpoint, Reply([row] if mode == "adopted" else []), page=1)
        s.fake.route("GET", endpoint, Reply([]), page=2)
        s.fake.route("POST", endpoint, Reply({"html_url": s.url, "number": 42}))
    elif mode == "adopted":
        # Exercise the existing GitHub identity lookup with a matching response.
        original_gh = cli._gh

        def gh(*args):
            if args[0] == "api":
                s.gh.append(args)
                return json.dumps([{"html_url": s.url, "head": {"ref": ctx.m.branch,
                                    "repo": {"full_name": "example/repo"}}, "base": {"ref": ctx.m.pr_base}}])
            return original_gh(*args)

        monkeypatch.setattr(cli, "_gh", gh)
    state = (s.run.dir / "state.json").read_bytes()
    if protected:
        with pytest.raises(RuntimeError, match="Refusing to publish protected paths"):
            main(["publish", CHANGE])
        assert s.effects == ["fetch"]
        assert s.fake.calls == s.gh == []
        assert (s.run.dir / "state.json").read_bytes() == state
        return
    assert main(["publish", CHANGE]) == 0
    assert s.effects == ["fetch", "push"]
    s.run.reload()
    assert s.run.state.pause.reason == "human_checks" and s.run.state.pr == s.url
    if mode == "new":
        if s.gitea:
            request = next(c for c in s.fake.calls if c.method == "POST")
            title, body = request.body["title"], request.body["body"]
            assert request.body["head"] == ctx.m.branch and request.body["base"] == ctx.m.pr_base
        else:
            request = next(a for a in s.gh if a[:2] == ("pr", "create"))
            assert "--draft" in request
            title, body = request[request.index("--title") + 1], request[request.index("--body") + 1]
        assert title == ("Draft: " if s.gitea else "") + f"{CHANGE}: spec-kit feature via specloop"
        assert body.startswith(f"spec-kit feature `{CHANGE}`, driven by specloop: 7 tasks,")
        assert all(check in body for check in checks)
        assert "dirty source" not in body
    elif s.gitea:
        assert [c.method for c in s.fake.calls] == ([] if mode == "existing" else ["GET", "GET"])
    else:
        assert len(s.gh) == (0 if mode == "existing" else 1)
        assert not any(a[:2] == ("pr", "create") for a in s.gh)


@pytest.mark.scenario("specloop-forge/verified-gitea-finish/prefix-removal-is-verified")
@pytest.mark.parametrize("native_service", ["gitea"], indirect=True)
@pytest.mark.parametrize("title,intended", [
    ("[WIP.*]  Résumé [WIP.*]  ", " Résumé [WIP.*]  "),
    ("[wip.*] Ready", "[wip.*] Ready"),
    ("Ready [WIP.*] inside", "Ready [WIP.*] inside"),
])
def test_native_literal_prefix(native_service, configured, title, intended):
    s = native_service
    path, _ = configured
    path.write_text(path.read_text().replace('wip_prefix = "Draft: "', 'wip_prefix = "[WIP.*] "'))
    endpoint = "/git/api/v1/repos/example/repo/pulls/42"
    s.fake.route("GET", endpoint, Reply({"title": title}), Reply({"title": intended}))
    s.fake.route("PATCH", endpoint, Reply({}))
    assert main(["finish", CHANGE]) == 0
    s.run.reload()
    assert s.run.state.status == "done"
    assert [c.method for c in s.fake.calls] == (["GET"] if title == intended else ["GET", "PATCH", "GET"])
    if title != intended:
        assert s.fake.calls[1].body == {"title": intended}


@pytest.mark.scenario("specloop-forge/verified-gitea-finish/existing-finish-gates-remain")
@pytest.mark.parametrize("native_service", ["gitea"], indirect=True)
@pytest.mark.parametrize("gate", ["foreign-pr", "forge"])
def test_native_forge_guards(native_service, configured, monkeypatch, gate):
    s = native_service
    if gate == "foreign-pr":
        s.run.state.pr = s.url.replace("example/repo", "other/repo")
        s.run.save()
    else:
        path, _ = configured
        path.write_text("")
    head = s.ctx.head()
    state = (s.run.dir / "state.json").read_bytes()
    complete = Mock(wraps=speckit.finish)
    monkeypatch.setattr(speckit, "finish", complete)
    with pytest.raises((ValueError, RuntimeError)):
        main(["finish", CHANGE])
    assert s.ctx.head() == head
    assert (s.run.dir / "state.json").read_bytes() == state
    assert s.effects == s.fake.calls == s.gh == []
    complete.assert_not_called()



@pytest.mark.scenario("specloop-interface/native-publication-lifecycle/finish-guards-and-ordering")
@pytest.mark.scenario("specloop-forge/verified-gitea-finish/prefix-removal-is-verified")
def test_native_readiness_must_succeed_before_done(native_service):
    s = native_service
    head = s.ctx.head()
    state = (s.run.dir / "state.json").read_bytes()
    s.failure = "ready"
    if s.gitea:
        endpoint = "/git/api/v1/repos/example/repo/pulls/42"
        s.fake.route("GET", endpoint, Reply({"title": "Draft: Title"}), Reply({"title": "Other title"}))
        s.fake.route("PATCH", endpoint, Reply({"title": "Title"}))
    with pytest.raises(RuntimeError):
        main(["finish", CHANGE])
    assert s.ctx.head() != head
    assert git(s.remote, "rev-parse", s.ctx.ref).decode().strip() == s.ctx.head()
    assert (s.run.dir / "state.json").read_bytes() == state
    assert not any(e["event"] == "finished" for e in agents.read_jsonl(s.run.dir / "events.jsonl"))
    if s.gitea:
        assert s.effects == ["push"]
        assert [(c.method, c.body) for c in s.fake.calls] == [
            ("GET", None), ("PATCH", {"title": "Title"}), ("GET", None)]
    else:
        assert s.effects == ["push", "ready"]
        assert s.gh == [("pr", "ready", s.url, "-R", s.ctx.m.github),
                        ("pr", "view", s.url, "-R", s.ctx.m.github, "--json", "isDraft", "--jq", ".isDraft")]

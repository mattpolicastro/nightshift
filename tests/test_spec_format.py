"""The format boundary preserves OpenSpec's paths, calls and physical-line rules."""

import json
import subprocess
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from specloop import cli, loop, openspec, revise, spec_format, tasks
from specloop.manifest import Manifest
from specloop.seal import SAFE_FLAGS, git
from specloop.state import Run, TaskState


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, {"README.md": "fixture\n"})
    return root


def commit(repo, files):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", "-A")
    git(repo, *SAFE_FLAGS, "-c", "user.name=Test", "-c", "user.email=test@example.test",
        "commit", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD").decode().strip()


def run_context(repo, tmp_path):
    run = Run(tmp_path / "run")
    run.state.change = "sample-change"
    run.save()
    git(repo, "clone", "-q", "--bare", str(repo), str(run.dir / "repo.git"))
    m = Manifest(repo, "sample-change", "main", "main", "", {}, (), (), ())
    return loop.Ctx(run, m, {})


@pytest.mark.scenario("spec-format/format-seam/legacy-paths-and-publication-labels")
def test_openspec_adapter_matches_legacy_paths(tmp_path):
    adapter = spec_format.OPENSPEC
    for repo in (tmp_path, Path("relative/repo")):
        assert adapter.change_dir(repo, "sample-change") == repo / "openspec/changes/sample-change"
        assert adapter.change_dir(repo, "sample-change") == openspec.change_dir(repo, "sample-change")
    assert adapter.tasks_path("sample-change") == "openspec/changes/sample-change/tasks.md"
    assert adapter.drafter_root("sample-change") == "openspec/changes/sample-change/"
    assert adapter.pr_kind == "OpenSpec change"
    assert adapter.pr_title("sample-change") == "sample-change: OpenSpec change via specloop"


def test_manifest_construction_and_adapter_selection(tmp_path):
    m = Manifest(tmp_path, "sample-change", "spec/sample-change", "main", "", {}, (), (), ())
    assert fields(Manifest)[-1].name == "format"
    assert m.format == "openspec"
    assert spec_format.get(m) is spec_format.OPENSPEC
    assert spec_format.get(replace(m, format="openspec")) is spec_format.OPENSPEC
    with pytest.raises(openspec.SpecError, match="^spec-kit format is not available yet$"):
        spec_format.get(replace(m, format="speckit"))


@pytest.mark.scenario("spec-format/format-seam/delegation-stays-late-bound")
@pytest.mark.parametrize("method, backend, tail, result", [
    ("change_dir", "change_dir", (), Path("replacement")),
    ("validate", "validate", (), ["problem"]),
    ("scenarios", "scenarios", (), {"cap/req/example": "text"}),
    ("finish", "archive", (False,), {"archive": False}),
    ("finish", "archive", (True,), {"archive": True}),
])
def test_delegation_is_late_bound(tmp_path, monkeypatch, method, backend, tail, result):
    call = getattr(spec_format.OPENSPEC, method)
    assert spec_format.SpecError is openspec.SpecError
    assert spec_format.ValidatorUnavailable is openspec.ValidatorUnavailable
    replacement = Mock(return_value=result)
    monkeypatch.setattr(openspec, backend, replacement)
    args = (tmp_path, "sample-change", *tail)
    assert call(*args) is result
    replacement.assert_called_once_with(*args)
    for cls in (openspec.SpecError, openspec.ValidatorUnavailable, OSError):
        error = cls("same exception")
        replacement = Mock(side_effect=error)
        monkeypatch.setattr(openspec, backend, replacement)
        with pytest.raises(cls) as raised:
            call(*args)
        assert raised.value is error
        replacement.assert_called_once_with(*args)


@pytest.mark.scenario("spec-format/format-seam/delegation-stays-late-bound")
@pytest.mark.parametrize("failure", [None, "timeout", "ENOTFOUND", "domain"])
def test_validator_invocation_and_classification(tmp_path, monkeypatch, failure):
    process = Mock(return_value=subprocess.CompletedProcess([], 0, '{"items": []}', ""))
    if failure == "timeout":
        process.side_effect = subprocess.TimeoutExpired("npx", 120)
    elif failure:
        process.return_value = subprocess.CompletedProcess([], 1, "not JSON", failure)
    monkeypatch.setattr(openspec.subprocess, "run", process)
    monkeypatch.setenv("FORMAT_TEST_ENV", "inherited")
    if failure:
        expected = openspec.SpecError if failure == "domain" else openspec.ValidatorUnavailable
        with pytest.raises(expected) as raised:
            spec_format.OPENSPEC.validate(tmp_path, "sample-change")
        assert type(raised.value) is expected
        if failure == "timeout":
            assert raised.value.__cause__ is process.side_effect
    else:
        assert spec_format.OPENSPEC.validate(tmp_path, "sample-change") == []
    process.assert_called_once()
    assert process.call_args.args == ([
        "npx", "-y", "--prefer-offline", openspec.PACKAGE,
        "validate", "sample-change", "--strict", "--json",
    ],)
    kwargs = process.call_args.kwargs
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == 120
    assert kwargs["capture_output"] is True and kwargs["text"] is True
    assert kwargs["env"]["FORMAT_TEST_ENV"] == "inherited"
    assert all(kwargs["env"][key] == value for key, value in openspec.ENV.items())


@pytest.mark.scenario("spec-format/committed-task-discovery/live-and-archived-task-precedence")
def test_committed_task_candidates_preserve_precedence(repo, monkeypatch):
    change = "sample.change+extra"
    live = f"openspec/changes/{change}/tasks.md"
    archive = "openspec/changes/archive/"
    newest = f"{archive}2026-11-07-{change}/tasks.md"
    oldest = f"{archive}2025-12-31-{change}/tasks.md"
    head = commit(repo, dict.fromkeys([
        newest, oldest,
        f"{archive}2026-11-08-{change}-other/tasks.md",
        f"{archive}2026-11-08-other-{change}/tasks.md",
        f"{archive}2026-11-08-sampleXchangeextra/tasks.md",
        f"{archive}2026-1-08-{change}/tasks.md",
        f"{archive}2026-11-08-{change}/tasksXmd",
        f"{archive}2026-11-08-{change}/nested/tasks.md",
    ], "archived tasks\n"))
    spy = Mock(wraps=git)
    monkeypatch.setattr(spec_format, "git", spy)
    assert spec_format.OPENSPEC.tasks_paths_at(repo, head, change) == [newest, oldest]
    spy.assert_called_once_with(repo, "ls-tree", "-r", "--name-only", head, live, archive)
    assert cli._tasks_path(repo, head, change) == newest
    live_head = commit(repo, {live: "live tasks\n"})
    assert spec_format.OPENSPEC.tasks_paths_at(repo, live_head, change) == [live]
    assert cli._tasks_path(repo, live_head, change) == live
    # The requested commit, not the working tree, controls selection.
    assert spec_format.OPENSPEC.tasks_paths_at(repo, head, change) == [newest, oldest]


@pytest.mark.scenario("spec-format/committed-task-discovery/missing-tasks-retain-read-failure")
def test_missing_tasks_return_the_live_read_failure(repo):
    head = commit(repo, {"openspec/changes/archive/2026-11-07-other/tasks.md": "other"})
    live = "openspec/changes/sample-change/tasks.md"
    (repo / live).parent.mkdir(parents=True)
    (repo / live).write_text("uncommitted tasks must not be used")
    paths = spec_format.OPENSPEC.tasks_paths_at(repo, head, "sample-change")
    assert paths == [live]
    assert paths[0] == cli._tasks_path(repo, head, "sample-change")
    with pytest.raises(RuntimeError):
        git(repo, "show", f"{head}:{paths[0]}")


@pytest.mark.scenario("spec-format/context-and-human-checks/existing-context-files-are-copied")
@pytest.mark.parametrize("mask", range(8))
@pytest.mark.parametrize("relative", [False, True])
def test_context_entries(tmp_path, monkeypatch, mask, relative):
    monkeypatch.chdir(tmp_path)
    repo = Path("repo") if relative else tmp_path / "repo"
    src = repo / "openspec/changes/sample-change"
    src.mkdir(parents=True)
    expected = []
    for index, name in enumerate(("proposal.md", "design.md", "specs")):
        if mask & (1 << index):
            path = src / name
            if name == "specs":
                (path / "cap").mkdir(parents=True)
                (path / "cap/spec.md").write_text("scenario content")
            else:
                path.write_text(name)
            expected.append((path, name))
    (src / "tasks.md").write_text("not context")
    entries = spec_format.OPENSPEC.context_files(repo, "sample-change")
    assert entries == expected
    for path, name in entries:
        assert path.is_absolute() is not relative
        if name == "specs":
            assert path.is_dir()
            assert (path / "cap/spec.md").read_text() == "scenario content"
        else:
            assert path.read_text() == name

    # All three context consumers preserve optional entries and merge specs.
    m = Manifest(repo, "sample-change", "main", "main", "", {}, (), (), ())
    ctx = SimpleNamespace(repo=repo, m=m, run=Run(tmp_path / "run"))
    task = tasks.parse("## 1. Core\n- [ ] 1.1 Implement `cap/req/example`\n")[0]
    adapter = Mock(wraps=spec_format.OPENSPEC)
    adapter.scenarios.return_value = {"cap/req/example": "scenario from adapter"}
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    monkeypatch.setattr(loop, "git", Mock(return_value=b""))
    for kind in ("implementation", "gate", "review"):
        context = tmp_path / kind
        (context / "specs").mkdir(parents=True)
        (context / "specs/retained.md").write_text("retained")
        if kind == "implementation":
            loop._context(ctx, task, TaskState(), repo, context, False)
        elif kind == "gate":
            loop._gate_context(ctx, 1, "head", [task], TaskState(), repo, context)
        else:
            loop._review_context(ctx, task, TaskState(), "head", "candidate", repo, context)
        for name in ("proposal.md", "design.md", "specs/cap/spec.md"):
            source, copied = src / name, context / name
            assert copied.exists() == source.exists()
            if source.exists():
                assert copied.read_bytes() == source.read_bytes()
        assert (context / "specs/retained.md").read_text() == "retained"
        assert not (context / "tasks.md").exists()
        rendered = (context / ("section.md" if kind == "gate" else "task.md")).read_text()
        assert "### `cap/req/example`\nscenario from adapter" in rendered
    assert adapter.context_files.call_args_list == [call(repo, m.change)] * 3
    assert adapter.scenarios.call_args_list == [call(repo, m.change)] * 3
    assert selection.call_args_list == [call(m)] * 6


@pytest.mark.scenario("spec-format/context-and-human-checks/literal-committed-checks-remain-pending")
def test_committed_checklist_physical_lines(repo):
    path = "openspec/changes/sample-change/design.md"
    text = """- [ ] outside
## Human checks
 \t- [ ] first\t
### Verification and human rollout checklist
- [ ] nested
### Other subsection
- [ ] still pending\u2028###### Deepest
- [ ] deepest
- [x] checked
- [X] checked uppercase
* [ ] other bullet
- [] missing space
- [ ]missing separator
- [ ] \t
- [ ] \u00a0
- [ ]\u00a0wrong separator
## Other
- [ ] outside again
### Verification and human rollout checklist
- [ ] rollout
#### Deeper
- [ ] deeper rollout
### Other
- [ ] outside third-level
## human checks
- [ ] wrong case
## Human checks ##
- [ ] closing hashes
\u00a0## Human checks
- [ ] non-ASCII heading padding
### Verification and human rollout checklist ###
- [ ] decorated rollout
## Human checks
####### Not a boundary
#No heading separator
- [ ] retained
#\tEnd
- [ ] outside shallow
### Verification and human rollout checklist
\t- [ ]\tlast  `item`! \t
- [ ] \u00a0keep padding\u00a0
- [ ] first"""
    head = commit(repo, {path: text.replace("\n", "\r\n")})
    (repo / path).write_text("## Human checks\n- [ ] dirty source\n")
    expected = [f"- [ ] {item}" for item in (
        "first", "nested", "still pending", "deepest", "rollout", "deeper rollout", "retained")]
    expected += ["- [ ]\tlast  `item`!", "- [ ] \u00a0keep padding\u00a0", "- [ ] first"]
    assert spec_format.OPENSPEC.human_checks(repo, head, "sample-change") == expected
    ctx = SimpleNamespace(repo=repo, m=SimpleNamespace(change="sample-change", format="openspec"))
    assert loop.design_human_checks(ctx, head) == expected


@pytest.mark.scenario("spec-format/context-and-human-checks/literal-committed-checks-remain-pending")
@pytest.mark.parametrize("opening, closing, indent", [
    ("```md", "```", ""), ("<!--", "-->", ""), ("", "", "    "),
])
def test_literal_examples_stay_pending(repo, tmp_path, monkeypatch, opening, closing, indent):
    text = "\n".join(indent + line for line in [
        opening, "## Human checks", "- [ ] example", "# Boundary", "- [ ] excluded", closing])
    path = "openspec/changes/sample-change/design.md"
    head = commit(repo, {path: text, "openspec/changes/sample-change/tasks.md":
                         "## 1. Core\n- [x] 1.1 Implement\n"})
    assert spec_format.OPENSPEC.human_checks(repo, head, "sample-change") == ["- [ ] example"]
    (repo / path).write_text("## Human checks\n- [ ] uncommitted\n")
    ctx = run_context(repo, tmp_path)
    monkeypatch.setattr(loop, "prepare_publish", lambda ctx: True)
    assert loop.finals(ctx, head) is False
    assert ctx.run.state.pause.reason == "ready_to_publish"
    for rendered in (ctx.run.state.pause.detail, cli._pr_body(ctx)):
        assert [line for line in rendered.splitlines() if line.startswith("- [ ]")] == ["- [ ] example"]


@pytest.mark.scenario("spec-format/context-and-human-checks/unreadable-design-blocks-readiness")
@pytest.mark.parametrize("failure", ["lookup", "read", "decode", "os", "subprocess"])
def test_design_read_failures_keep_diagnostic(repo, tmp_path, monkeypatch, failure):
    path = "openspec/changes/sample-change/design.md"
    head = commit(repo, {path: "## Human checks\n- [ ] Pending\n"})
    ctx = run_context(repo, tmp_path)
    monkeypatch.setattr(loop, "prepare_publish", lambda ctx: True)
    error = {"lookup": RuntimeError("tree lookup failed"), "read": RuntimeError("blob read failed"),
             "os": OSError("read failed"), "subprocess": subprocess.SubprocessError("read failed")}.get(failure)

    def fail(root, *args):
        if args[0] == "ls-tree" and failure == "lookup":
            raise error
        if args[0] == "show":
            if failure == "decode":
                return b"\xff"
            raise error
        return git(root, *args)

    monkeypatch.setattr(spec_format, "git", fail)
    with pytest.raises(RuntimeError) as raised:
        spec_format.OPENSPEC.human_checks(repo, head, "sample-change")
    assert str(raised.value).startswith(f"could not collect human checks from {head}:{path}: ")
    if failure == "decode":
        assert isinstance(raised.value.__cause__, UnicodeDecodeError)
    else:
        assert raised.value.__cause__ is error
        assert str(raised.value) == f"could not collect human checks from {head}:{path}: {error}"
    for render in (lambda: loop.finals(ctx, head), lambda: cli._pr_body(ctx)):
        with pytest.raises(RuntimeError) as blocked:
            render()
        assert str(blocked.value) == str(raised.value)
        assert ctx.run.state.pause is None


@pytest.mark.scenario("spec-format/context-and-human-checks/unreadable-design-blocks-readiness")
@pytest.mark.parametrize("design", [None, "", "## Human checks\n- [x] done\n## Other\n- [ ] excluded"])
def test_absent_or_empty_committed_design(repo, design):
    path = "openspec/changes/sample-change/design.md"
    head = (git(repo, "rev-parse", "HEAD").decode().strip() if design is None
            else commit(repo, {path: design}))
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("## Human checks\n- [ ] uncommitted\n")
    assert spec_format.OPENSPEC.human_checks(repo, head, "sample-change") == []


@pytest.mark.scenario("spec-format/context-and-human-checks/literal-committed-checks-remain-pending")
def test_human_check_wrapper_routes_and_preserves_existing_rows(repo, tmp_path, monkeypatch):
    ctx = run_context(repo, tmp_path)
    adapter = Mock(spec=spec_format.SpecFormat)
    adapter.human_checks.return_value = ["- [ ] existing", "- [ ] new", "- [ ] new"]
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    existing = [" \t- [ ] existing\t", "- [ ] existing"]
    before = list(existing)
    assert loop.append_design_checks(ctx, "evaluated-head", existing) == existing + ["- [ ] new"]
    assert existing == before
    selection.assert_called_once_with(ctx.m)
    adapter.human_checks.assert_called_once_with(ctx.repo, "evaluated-head", ctx.m.change)


@pytest.mark.scenario("spec-format/engine-routing-compatibility/drafting-and-revision-retain-behavior")
@pytest.mark.parametrize("error", [None, spec_format.SpecError, spec_format.ValidatorUnavailable])
def test_committed_scenario_lookup_routes_and_cleans_up(repo, tmp_path, monkeypatch, error):
    head = commit(repo, {"scenario-source": "evaluated"})
    commit(repo, {"scenario-source": "newer"})
    ctx = run_context(repo, tmp_path)
    extracted = []
    result = {"cap/req/example": "scenario text"}

    def scenarios(source, change):
        assert change == ctx.m.change
        assert (source / "scenario-source").read_text() == "evaluated"
        extracted.append(source)
        if error:
            raise error("scenario lookup failed")
        return result

    adapter = Mock(spec=spec_format.SpecFormat)
    adapter.scenarios.side_effect = scenarios
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    assert loop.openspec is openspec
    if error:
        with pytest.raises(error, match="scenario lookup failed"):
            loop._scenarios_at(ctx, head)
    else:
        assert loop._scenarios_at(ctx, head) is result
    selection.assert_called_once_with(ctx.m)
    assert len(extracted) == 1 and not extracted[0].parent.exists()


@pytest.mark.scenario("spec-format/engine-routing-compatibility/drafting-and-revision-retain-behavior")
def test_task_path_routes_through_revision_and_replay(repo, tmp_path, monkeypatch):
    path = "format-fixture/work-items.md"
    original = "## 1. Core\n- [x] 1.1 Implement `cap/req/example`\n"
    parent = commit(repo, {path: original})
    adapter = Mock(spec=spec_format.SpecFormat)
    adapter.tasks_path.return_value = path
    selection = Mock(return_value=adapter)
    monkeypatch.setattr(spec_format, "get", selection)
    ctx = run_context(repo, tmp_path)
    assert ctx.tasks_path == path and ctx.tasks_text(parent) == original
    feedback = "Keep the scenario `cap/req/example`.\n- [ ] Verify it."
    result = revise.revise_result(ctx.run, ctx.m, feedback, round_id="format-round")
    assert not result.replayed and result.status == "running" and result.pause is None
    assert ctx.head() == result.commit
    text = ctx.tasks_text(result.commit)
    parsed = tasks.parse(text)
    assert parsed[:-1] == tasks.parse(original)
    assert parsed[-1].section_title == "Review round 1" and not parsed[-1].done
    assert parsed[-1].text == f"Address review feedback 1: {json.dumps(feedback)}"
    assert git(ctx.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", result.commit).decode().splitlines() == [path]
    assert git(ctx.repo, "show", "-s", "--format=%s", result.commit) == b"specloop: append review round 1\n"
    journal, = (ctx.run.dir / "revision-rounds").glob("*.json")
    record = json.loads(journal.read_text())
    assert record["status"] == "completed" and record["commit"] == result.commit
    assert record["parent"] == parent and record["tasks"] == 1 and record["round"] == 1
    saved = {p: p.read_bytes() for p in (journal, ctx.run.dir / "state.json", ctx.run.dir / "events.jsonl")}
    replay = revise.revise_result(ctx.run, ctx.m, feedback, round_id="format-round")
    assert replay == replace(result, replayed=True)
    assert ctx.head() == result.commit and ctx.tasks_text(result.commit) == text
    assert {p: p.read_bytes() for p in saved} == saved
    assert selection.call_args_list == [call(ctx.m)] * 3
    assert adapter.tasks_path.call_args_list == [call(ctx.m.change)] * 3

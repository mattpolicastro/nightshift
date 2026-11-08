"""Adapter-owned review rounds retain revision identity and replay semantics."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from specloop import openspec, revise, speckit, spec_format, tasks
from specloop.seal import git
from specloop.state import Run
from test_spec_format import commit, repo, run_context


@pytest.mark.scenario("spec-format/engine-routing-compatibility/drafting-and-revision-retain-behavior")
def test_task_path_routes_through_revision_and_replay(repo, tmp_path, monkeypatch):
    path = "format-fixture/work-items.md"
    original = "## 1. Core\n- [x] 1.1 Implement `cap/req/example`\n"
    parent = commit(repo, {path: original})
    adapter = Mock(spec=spec_format.SpecFormat)
    adapter.tasks_path.return_value = path
    adapter.append_round.side_effect = spec_format.OPENSPEC.append_round
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


BLOCKS = ['Keep the "café" label.\n- [ ] Regression test.', 'Handle empty input.']


@pytest.fixture
def revision_context(repo, tmp_path, request):
    format_name = getattr(request, "param", "speckit")
    native = format_name == "speckit"
    path = ("specs/sample-change/tasks.md" if native
            else "openspec/changes/sample-change/tasks.md")
    original = ("## Phase 3: Core\n- [X] T009.2 Implement\n\n## Phase 7: Empty\n"
                if native else "## 1. Core\n- [x] 1.1 Implement\n")
    parent = commit(repo, {path: original})
    ctx = run_context(repo, tmp_path)
    m = replace(ctx.m, format=format_name)
    ctx.run.state.gates_passed = [3]
    ctx.run.pause("human_checks", detail="Review the PR")
    return SimpleNamespace(run=ctx.run, m=m, repo=ctx.repo, ref=ctx.ref, path=path,
                           original=original, parent=parent)


def submit(ctx, blocks=BLOCKS, key="first"):
    return revise.revise_result(Run(ctx.run.dir, require_existing=True), ctx.m,
                                json.dumps(blocks), feedback_format="json", round_id=key)


def snapshot(ctx):
    return (git(ctx.repo, "show-ref"),
            {p.relative_to(ctx.run.dir): p.read_bytes() for p in ctx.run.dir.rglob("*")
             if p.is_file() and "repo.git" not in p.relative_to(ctx.run.dir).parts
             and p.name != "lock"})


@pytest.mark.scenario("specloop-interface/revision-round/native-rounds-advance-and-replay")
@pytest.mark.scenario("spec-format/manifest-selected-task-operations/consumers-use-selected-task-operations")
@pytest.mark.parametrize("equal_feedback", [False, True])
def test_native_rounds_advance_and_replay(revision_context, monkeypatch, equal_feedback):
    ctx = revision_context
    append = Mock(wraps=speckit.append_round)
    selection = Mock(wraps=spec_format.get)
    monkeypatch.setattr(speckit, "append_round", append)
    monkeypatch.setattr(spec_format, "get", selection)
    first = submit(ctx)
    second_blocks = BLOCKS if equal_feedback else ["A later correction."]
    second = submit(ctx, second_blocks, "second")
    assert not first.replayed and not second.replayed and first.commit != second.commit
    assert (first.status, first.pause, second.status, second.pause) == ("running", None, "running", None)
    first_text = git(ctx.repo, "show", f"{first.commit}:{ctx.path}").decode()
    second_text = git(ctx.repo, "show", f"{second.commit}:{ctx.path}").decode()
    first_tasks, second_tasks = speckit.parse_tasks(first_text), speckit.parse_tasks(second_text)
    assert second_tasks[:len(first_tasks)] == first_tasks
    added = second_tasks[1:]
    assert [(t.section, t.section_title, t.label) for t in added] == [
        (8, "Review round 1", "T010"), (8, "Review round 1", "T011"),
        *[(9, "Review round 2", f"T{12 + i:03d}") for i in range(len(second_blocks))],
    ]
    assert len({t.key for t in second_tasks}) == len(second_tasks)
    assert all(not t.done for t in added)
    assert [json.loads(t.text.split(": ", 1)[1]) for t in added] == BLOCKS + second_blocks
    assert git(ctx.repo, "show", "-s", "--format=%P", second.commit).decode().strip() == first.commit
    for number, result in enumerate((first, second), 1):
        assert git(ctx.repo, "show", "-s", "--format=%s", result.commit) == (
            f"specloop: append review round {number}\n".encode())
        assert git(ctx.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", result.commit).decode().splitlines() == [ctx.path]
    ctx.run.reload()
    assert ctx.run.state.gates_passed == [3]
    ctx.run.pause("stuck", detail="Later control decision")
    ctx.run.request_pause()
    before = snapshot(ctx)
    replay = submit(ctx)
    assert replay.commit == first.commit and replay.replayed
    assert replay.status == "paused" and replay.pause["reason"] == "stuck"
    assert snapshot(ctx) == before and ctx.run.pause_requested()
    assert git(ctx.repo, "rev-parse", ctx.ref).decode().strip() == second.commit
    assert selection.call_args_list == [call(ctx.m)] * 3
    assert append.call_args_list == [call(ctx.original, BLOCKS), call(first_text, second_blocks),
                                     call(ctx.original, BLOCKS)]
    with pytest.raises(ValueError, match="revision record conflict"):
        submit(ctx, ["Conflicting feedback"])
    assert snapshot(ctx) == before


@pytest.mark.scenario("specloop-interface/revision-round/feedback-becomes-tasks")
@pytest.mark.parametrize("revision_context", ["openspec"], indirect=True)
@pytest.mark.parametrize("key", [None, "first"])
def test_openspec_round_retains_exact_bytes(revision_context, key):
    ctx = revision_context
    result = submit(ctx, key=key)
    expected = ctx.original + "\n## 2. Review round 1\n\n" + "".join(
        f"- [ ] 2.{i} Address review feedback {i}: {json.dumps(block)}\n"
        for i, block in enumerate(BLOCKS, 1))
    assert git(ctx.repo, "show", f"{result.commit}:{ctx.path}") == expected.encode()
    assert result.status == "running" and result.pause is None
    assert all(not t.done for t in tasks.parse(expected)[1:])


@pytest.mark.scenario("specloop-interface/revision-round/empty-feedback-refused")
@pytest.mark.parametrize("revision_context", ["openspec", "speckit"], indirect=True)
@pytest.mark.parametrize("key", [None, "first"])
@pytest.mark.parametrize("feedback,format_name", [("\n---\n ", "text"), ("[]", "json"), ('["", "  "]', "json")])
def test_empty_feedback_preserves_revision(revision_context, key, feedback, format_name):
    ctx = revision_context
    submit(ctx)  # Empty feedback also refuses when the requested key already exists.
    before = snapshot(ctx)
    with pytest.raises(ValueError, match="feedback is empty"):
        revise.revise_result(ctx.run, ctx.m, feedback, feedback_format=format_name, round_id=key)
    assert snapshot(ctx) == before


@pytest.mark.scenario("specloop-interface/revision-round/native-rounds-advance-and-replay")
@pytest.mark.parametrize("wrapper", ["", "```markdown\n", "<!--\n"])
def test_native_round_discovery_is_physical_and_uses_maxima(wrapper):
    original = (wrapper + "## Phase 4: Review round 8\n- [X] T1001.7 Existing\n"
                "## Phase 20: Review round 2\n"
                "## Phase 10: Review round 99 extra\n"
                "    ## Phase 900: Review round 900\n"
                "## 999. Review round 999\n- [ ] T9999 Outside native phase\n")
    number, text = spec_format.SPECKIT.append_round(original, BLOCKS)
    assert number == 9 and text.startswith(original)
    added = speckit.parse_tasks(text)[-2:]
    assert [(t.section, t.section_title, t.label) for t in added] == [
        (21, "Review round 9", "T1002"), (21, "Review round 9", "T1003")]


@pytest.mark.scenario("specloop-interface/revision-round/native-rounds-advance-and-replay")
@pytest.mark.parametrize("original", [
    "## Phase 1: Empty\n## Phase 01: Other\n",
    "## Phase 1: Core\n- [ ] T001 First\n- [ ] T0001 Other\n",
    "## Phase 1: Core\n- [ ] T001.1 First\n- [ ] T001.01 Other\n",
    "## Phase 1: Core\n- [ ] T001 Same\n- [ ] T002 Same\n",
])
def test_native_round_refuses_ambiguous_identities(original):
    with pytest.raises(spec_format.SpecError, match="duplicate|ambiguous"):
        spec_format.SPECKIT.append_round(original + "## 9. Other style\n", BLOCKS)


@pytest.mark.scenario("spec-format/format-seam/delegation-stays-late-bound")
@pytest.mark.parametrize("operation,backend,kwargs", [
    ("validate", "validate", {}), ("scenarios", "scenarios", {}),
    ("finish", "archive", {"all_tasks_done": False}),
    ("finish", "archive", {"all_tasks_done": True}),
])
@pytest.mark.parametrize("error", [None, spec_format.SpecError, spec_format.ValidatorUnavailable])
def test_openspec_delegation_retains_results_and_errors(tmp_path, monkeypatch, operation, backend, kwargs, error):
    result = object()
    replacement = Mock(return_value=result, side_effect=error("failure") if error else None)
    monkeypatch.setattr(openspec, backend, replacement)
    if error:
        with pytest.raises(error) as raised:
            getattr(spec_format.OPENSPEC, operation)(tmp_path, "sample-change", **kwargs)
        assert raised.value is replacement.side_effect
    else:
        assert getattr(spec_format.OPENSPEC, operation)(tmp_path, "sample-change", **kwargs) is result
    replacement.assert_called_once_with(tmp_path, "sample-change", **kwargs)


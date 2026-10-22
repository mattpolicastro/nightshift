"""Section evidence attribution across an integrated upstream merge."""

import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from specloop import evidence, loop, revise, seal, state, tasks
from test_specloop_base_sync import base_env, sh


CODE = """def unchanged(value):
    return value


def earlier_section():
    return 1


def current_section(value):
    return value + 2
"""
UPSTREAM = """def upstream_only():
    return 100


"""


@pytest.fixture
def section_history(base_env):
    ctx = base_env.ctx
    common = seal.commit_files(ctx.repo, ctx.head(), {"calc.py": CODE}, "shared code")
    earlier_code = CODE.replace("return 1", "return 11")
    start = seal.commit_files(ctx.repo, common, {
        "calc.py": earlier_code, "prior.py": "prior = 10\n",
    }, "earlier section")
    owned_code = earlier_code.replace("value + 2", "value + 22")
    section_head = seal.commit_files(ctx.repo, start, {
        "calc.py": owned_code, "section.py": "owned = 20\n",
    }, "current section")
    base = seal.commit_files(ctx.repo, common, {
        "calc.py": UPSTREAM + CODE, "upstream.py": "upstream = 100\n",
    }, "upstream additions")
    merged = loop._merge_commits(ctx.repo, section_head, base)
    seal.git(ctx.repo, "update-ref", ctx.ref, merged, ctx.head())
    ctx.run.state.section_base["2"] = start
    ctx.run.state.base_sync = {"base": base, "previous_head": section_head,
                               "merged_head": merged, "verified_head": merged}
    ctx.run.save()
    ctx.m = replace(ctx.m, evidence=True, mutation_steps=("ok",),
                    mutation_paths=("calc.py", "prior.py", "section.py", "upstream.py"), mutation_count=100)
    return SimpleNamespace(ctx=ctx, start=start, base=base, head=merged,
                           section_head=section_head, owned_code=owned_code)


@pytest.mark.scenario("publish-base-sync/exclude-integrated-base-lines-from-evidence/base-lines-are-not-mutated")
def test_base_merge_lines_are_not_mutated(section_history, monkeypatch):
    h, ctx = section_history, section_history.ctx
    cited = "sample/behavior/section-change"
    section_tasks = tasks.parse(f"## 2. Current section\n\n- [x] 2.1 Cover `{cited}`\n")
    monkeypatch.setattr(loop, "_scenarios_at", lambda *args: {cited: "Section change"})
    executed = []

    def execute(repo, head, mutants, m, out):
        assert (repo, head, m) == (ctx.repo, h.head, ctx.m)
        for mutant in mutants:
            lines = seal.git(repo, "show", f"{head}:{mutant.path}").decode().splitlines()
            assert lines[mutant.line - 1] == mutant.before
            executed.append((mutant.path, mutant.line, mutant.before))
            mutant.result = "killed"
        return True

    monkeypatch.setattr(evidence, "mutate", execute)
    refs = sh(ctx.repo, "git", "show-ref")
    sync = dict(ctx.run.state.base_sync)
    old_line = h.owned_code.splitlines().index("    return value + 22") + 1
    shifted_line = old_line + len(UPSTREAM.splitlines())
    # The old two-tree baseline really includes upstream code in this history.
    original = evidence.added_lines(
        seal.git(ctx.repo, "diff", "--unified=0", h.start, h.head).decode(), ctx.m.mutation_paths)
    assert ("calc.py", 2, "    return 100") in original
    assert loop.section_evidence(ctx, 2, h.head, section_tasks)
    assert set(executed) == {
        ("calc.py", shifted_line, "    return value + 22"),
        ("section.py", 1, "owned = 20"),
    }
    assert len(executed) == 2
    assert shifted_line > old_line
    assert sh(ctx.repo, "git", "show-ref") == refs
    assert ctx.head() == h.head
    assert ctx.run.state.base_sync == sync
    assert ctx.run.state.section_base == {"2": h.start}
    assert ctx.run.state.evidenced == [2]
    out = ctx.run.dir / "gates" / "section-2-0"
    saved = json.loads((out / "evidence.json").read_text())
    assert saved["covered"] == [cited] and saved["uncovered"] == []
    assert saved["score"] == 1.0
    diagnostic = json.loads((out / "comparison-base.json").read_text())
    comparison = diagnostic.pop("comparison_base")
    assert diagnostic == {"section_base": h.start, "integrated_base": h.base, "head": h.head}
    assert seal.git(ctx.repo, "show", f"{comparison}:calc.py").decode() == (
        UPSTREAM + CODE.replace("return 1", "return 11"))
    assert sh(ctx.repo, "git", "rev-list", "--parents", "-n", "1", comparison).split()[1:] == [
        h.start, h.base]


@pytest.mark.parametrize("case", ["absent-section", "no-integration", "already-in-section", "pending"])
def test_comparison_keeps_existing_baseline_when_no_merge_crosses_section(section_history, monkeypatch, case):
    h, ctx = section_history, section_history.ctx
    head, expected = h.head, h.start
    if case == "absent-section":
        ctx.run.state.section_base.clear()
        expected = ""
    elif case == "no-integration":
        ctx.run.state.base_sync.clear()
    elif case == "already-in-section":
        ctx.run.state.section_base["2"] = h.head
        expected = h.head
    else:
        head = h.section_head

    def unexpected_merge(*args):
        pytest.fail("a comparison merge is unnecessary")

    monkeypatch.setattr(loop, "_merge_commits", unexpected_merge)
    assert loop._evidence_base(ctx, 2, head) == expected


@pytest.mark.scenario("publish-base-sync/exclude-integrated-base-lines-from-evidence/revision-after-integration")
@pytest.mark.parametrize("integration", ["before-revision", "during-revision"])
def test_reloaded_revision_measures_only_its_own_changes(section_history, monkeypatch, integration):
    h, ctx = section_history, section_history.ctx
    cited = "sample/behavior/revision-change"
    # Exercise both a revision starting on the merged head and a saved section
    # baseline predating integration (e.g. a revision resumed after base repair).
    parent = h.head if integration == "before-revision" else h.section_head
    text = ctx.tasks_text(parent) + "\n## 2. Current section\n\n- [x] 2.1 Done\n"
    parent = seal.commit_files(ctx.repo, parent, {ctx.tasks_path: text}, "completed sections")
    seal.git(ctx.repo, "update-ref", ctx.ref, parent, ctx.head())
    if integration == "during-revision":
        ctx.run.state.base_sync.clear()
    prior_sync = dict(ctx.run.state.base_sync)
    ctx.run.state.evidenced = [1, 2]
    ctx.run.pause("human_checks", detail="review requested")
    ctx = loop.Ctx(state.Run(ctx.run.dir, require_existing=True), ctx.m, {})
    assert ctx.run.state.base_sync == prior_sync
    start = revise.revise(ctx.run, ctx.m, f"Cover `{cited}` in the revision")
    assert ctx.run.state.base_sync == prior_sync
    ctx.run.state.section_base["3"] = start
    revision_code = h.owned_code.replace("value + 22", "value + 32")
    if integration == "before-revision":
        revision_code = UPSTREAM + revision_code
    text = ctx.tasks_text(start).replace("- [ ] 3.1", "- [x] 3.1")
    head = seal.commit_files(ctx.repo, start, {
        "calc.py": revision_code, "section.py": "owned = 30\n", ctx.tasks_path: text,
    }, "revision changes")
    if integration == "during-revision":
        merged = loop._merge_commits(ctx.repo, head, h.base)
        ctx.run.state.base_sync = {"base": h.base, "previous_head": head,
                                   "merged_head": merged, "verified_head": merged}
        head = merged
    seal.git(ctx.repo, "update-ref", ctx.ref, head, start)
    ctx.run.save()
    sync = dict(ctx.run.state.base_sync)
    # A new context must recover both the integration and section baselines.
    ctx = loop.Ctx(state.Run(ctx.run.dir, require_existing=True), ctx.m, {})
    assert ctx.run.state.base_sync == sync
    assert ctx.run.state.section_base == {"2": h.start, "3": start}
    monkeypatch.setattr(loop, "_scenarios_at", lambda *args: {cited: "Revision change"})
    executed = []

    def execute(repo, current, mutants, m, out):
        assert current == head
        for mutant in mutants:
            lines = seal.git(repo, "show", f"{current}:{mutant.path}").decode().splitlines()
            assert lines[mutant.line - 1] == mutant.before
            executed.append((mutant.path, mutant.line, mutant.before))
            mutant.result = "killed"
        return True

    monkeypatch.setattr(evidence, "mutate", execute)
    refs = sh(ctx.repo, "git", "show-ref")
    section_tasks = tasks.parse(ctx.tasks_text(head))
    assert [t.label for t in section_tasks if t.section == 3 and t.done] == ["3.1"]
    assert loop.section_evidence(ctx, 3, head, section_tasks)
    line = (UPSTREAM + h.owned_code).splitlines().index("    return value + 22") + 1
    assert sorted(executed) == [
        ("calc.py", line, "    return value + 32"), ("section.py", 1, "owned = 30"),
    ]
    saved = json.loads((ctx.run.dir / "gates/section-3-0/evidence.json").read_text())
    assert saved["covered"] == [cited] and saved["uncovered"] == []
    assert saved["score"] == 1.0
    ctx.run.reload()
    assert ctx.run.state.evidenced == [1, 2, 3]
    assert ctx.run.state.base_sync == sync
    assert ctx.run.state.section_base == {"2": h.start, "3": start}
    assert ctx.run.state.strengthen_rounds == {}
    assert sh(ctx.repo, "git", "show-ref") == refs
    assert ctx.head() == head and ctx.tasks_text(head) == text


@pytest.mark.scenario("publish-base-sync/exclude-integrated-base-lines-from-evidence/comparison-baseline-conflicts")
def test_comparison_conflict_pauses_before_generating_mutants(base_env, monkeypatch):
    ctx = base_env.ctx
    paths = ["a conflict.py", "z.py"]
    original = {path: "value = 1\n" for path in paths}
    common = seal.commit_files(ctx.repo, ctx.head(), original, "shared code")
    start = seal.commit_files(ctx.repo, common, {path: "value = 2\n" for path in paths},
                              "earlier section")
    # Later section work removed the overlap, so the actual integration is clean
    # even though merging the same base into the section start conflicts.
    section_head = seal.commit_files(ctx.repo, start, {**original, "owned.py": "owned = 20\n"},
                                     "remove overlap")
    base = seal.commit_files(ctx.repo, common, {path: "value = 3\n" for path in paths}, "upstream")
    head = loop._merge_commits(ctx.repo, section_head, base)
    seal.git(ctx.repo, "update-ref", ctx.ref, head, ctx.head())
    ctx.m = replace(ctx.m, evidence=True, mutation_steps=("ok",), mutation_paths=("*.py",))
    ctx.run.state.section_base["2"] = start
    ctx.run.state.base_sync = {"base": base, "previous_head": section_head,
                               "merged_head": head, "verified_head": head}
    ctx.run.state.evidenced = [1]
    ctx.run.state.evidence_summary = {"1": "prior section evidence"}
    ctx.run.save()
    ctx = loop.Ctx(state.Run(ctx.run.dir, require_existing=True), ctx.m, {})
    before = asdict(ctx.run.state)
    refs, text = sh(ctx.repo, "git", "show-ref"), ctx.tasks_text(head)

    def unexpected(*args, **kwargs):
        pytest.fail("a conflicting comparison must pause before mutation or task strengthening")

    monkeypatch.setattr(loop, "_scenarios_at", lambda *args: {})
    monkeypatch.setattr(evidence, "gather", unexpected)
    monkeypatch.setattr(evidence, "generate", unexpected)
    monkeypatch.setattr(loop, "_rewrite_tasks", unexpected)
    section_tasks = tasks.parse("## 2. Current section\n\n- [x] 2.1 Done\n")
    assert not loop.section_evidence(ctx, 2, head, section_tasks)
    ctx.run.reload()
    pause = ctx.run.state.pause
    assert pause.reason == "gate_evidence" and pause.task == "section:2"
    assert pause.resume_at == 0
    assert asdict(ctx.run.state) == {**before, "status": "paused", "pause": asdict(pause)}
    out = ctx.run.dir / "gates/section-2-0"
    diagnostic = json.loads((out / "comparison-base.json").read_text())
    assert diagnostic["paths"] == paths
    assert diagnostic["section_base"] == start
    assert diagnostic["integrated_base"] == base
    assert diagnostic["head"] == head
    assert diagnostic["error"] and "comparison_base" not in diagnostic
    for identifier in [start, base, head, *paths, str(out)]:
        assert identifier in pause.detail
    assert not (out / "evidence.json").exists()
    events = [json.loads(line) for line in (ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert not any(event["event"] == "evidence" for event in events)
    assert sh(ctx.repo, "git", "show-ref") == refs
    assert ctx.head() == head and ctx.tasks_text(head) == text
    assert not list(ctx.run.dir.glob("base-merge-*"))

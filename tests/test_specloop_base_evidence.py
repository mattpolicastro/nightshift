"""Section evidence attribution across an integrated upstream merge."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from specloop import evidence, loop, seal, tasks
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

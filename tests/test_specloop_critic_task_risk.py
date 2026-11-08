"""Prompt contracts and offline routing for proportionate critic findings."""

import json
import re
import subprocess
from collections import Counter
from dataclasses import replace

import pytest

from specloop import agents, critic, feasibility, manifest, openspec, propose, speckit, spec_format, state
from test_spec_format import repo
from test_speckit_drafting import CHANGE, M, ROOT, native_feature


def checklist_items(prompt):
    checklist = prompt.split("Work through all eight checklist items:\n", 1)[1]
    checklist = checklist.split("\n\nReturn contradictions", 1)[0]
    return {
        int(number): " ".join(body.split())
        for number, body in re.findall(r"^(\d+)\. (.*?)(?=^\d+\. |\Z)", checklist, re.M | re.S)
    }


@pytest.fixture
def items():
    return checklist_items(critic.PROMPT.format(
        change="effective-change", allowed="src/**, tests/**", frozen="protected.py",
        planned="{}", pr_files="{}", pr_state="Not configured", context_notes="[]",
    ))


@pytest.mark.scenario("spec-critic/advisory-task-count-findings/excessive-plans-receive-merge-advice")
@pytest.mark.scenario("spec-critic/advisory-task-count-findings/threshold-boundaries-are-advisory")
def test_critic_checklist_flags_excess_task_count(items):
    for clause in (
        "Count task entries across the whole plan, not headings or scenarios.",
        "More than eight tasks (nine or more), or more than two tasks (three or more) "
        "in a documentation-only plan, warrants a non-blocking risk.",
        "Documentation-only means changing documentation without changing executable behavior.",
        "Name concrete task labels that could merge and their shared concern; if necessary "
        "boundaries prevent a safe merge, explain why rather than inventing a merge.",
        "Exactly eight general tasks or exactly two documentation-only tasks do not trigger a count finding.",
        "The drafter's usual six-task target is not a critic rejection threshold.",
        "Task count alone never belongs in contradictions, requires correction or rejects a proposal.",
        "Independently evidenced unsafe designs still qualify as contradictions under the existing blocking criteria.",
        "A blocker must identify the mismatch and a sufficient simpler approach; preference alone is a risk.",
    ):
        assert clause in items[1]


@pytest.mark.scenario("spec-critic/proportionate-retry-remedies/safe-local-reruns-suffice")
@pytest.mark.scenario("spec-critic/proportionate-retry-remedies/unattended-and-github-effects-retain-scrutiny")
def test_critic_idempotency_item_scales_to_blast_radius(items):
    for clause in (
        "an external write the change adds or alters (a GitHub comment, label, push or PR edit) "
        "that a lost response or retried call could duplicate or drop. Cite the write and the retry path.",
        "Scale remedies to blast radius.",
        "For an operator-attended local command, safe re-runs that re-check actual current state satisfy this item.",
        "In that case, demanding durable journals, transaction records or interrupted-run reconciliation "
        "is over-engineering; their absence is not itself a gap.",
        "State checks must make the re-run safe: operator attendance alone does not excuse unsafe effects.",
        "Unattended daemon operations and GitHub writes (comments, labels, pushes and PR edits), "
        "even when invoked from a local CLI, retain strict scrutiny of lost responses and retry paths "
        "that can duplicate or drop effects. Cite the write and the retry path for those findings.",
    ):
        assert clause in items[7]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def draft_plan(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    (source / "calc.py").write_text("def sign(x): return 0\n")
    git(source, "add", "-A")
    git(source, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "base")
    m = manifest.parse({
        "run": {"repo": str(source), "change": "template", "branch": "spec/template"},
        "roles": {"implement": "p", "review": "p"},
        "profiles": {"p": {"adapter": "codex", "model": "test"}},
        "paths": {"allowed": ["calc.py", "tests/**", "docs/**"], "frozen": ["protected.py"]},
        "verify": [{"id": "tests", "run": ["true"]}],
    })
    monkeypatch.setattr(state, "ROOT", tmp_path / "state")
    monkeypatch.setattr(agents, "homes_dir", lambda: tmp_path / "homes")
    # Stub the external OpenSpec CLI; task parsing and draft lint run normally.
    monkeypatch.setattr(openspec, "validate", lambda repo, change: [])
    monkeypatch.setattr(openspec, "scenarios", lambda repo, change: {"calc/sign/zero": ""})
    out = tmp_path / "out"
    change = "effective-change"
    calls = []

    def run(response, count=9, docs_only=False):
        def agent(call):
            calls.append(call)
            call.transcript.write_text('{"event": "offline review"}\n')
            if call.access == "write":
                directory = call.cwd / f"openspec/changes/{change}"
                (directory / "specs/calc").mkdir(parents=True)
                concern = "Document sign behavior" if docs_only else "Implement sign behavior"
                (directory / "proposal.md").write_text(f"## Why\n{concern}.\n")
                (directory / "design.md").write_text(
                    "Change documentation without executable behavior changes.\n" if docs_only
                    else "Implement the sign helper.\n")
                (directory / "tasks.md").write_text("## 1. Sign\n\n" + "".join(
                    f"- [ ] 1.{i} {concern} part {i} covering `calc/sign/zero`\n"
                    for i in range(1, count + 1)
                ))
                (directory / "specs/calc/spec.md").write_text(
                    "## ADDED Requirements\n### Requirement: Sign\nThe helper SHALL return zero.\n"
                    "#### Scenario: Zero\n- **WHEN** input is zero\n- **THEN** return zero\n")
                return agents.Result("ok", {"status": "drafted", "summary": "", "questions": []})
            assert call.access == "read"
            assert call.schema is feasibility.SCHEMA
            if call.prompt.startswith("# Feasibility check"):
                return agents.Result("ok", {"contradictions": [], "risks": []})
            assert call.prompt.startswith("# Spec critic of an OpenSpec change")
            return response

        result = propose.draft(source, "main", change, "Define sign", [], m, out, agent=agent)
        return result, calls, out, m

    return run


@pytest.mark.scenario("spec-critic/advisory-task-count-findings/count-risk-preserves-successful-drafting")
@pytest.mark.parametrize("count, docs_only", [(9, False), (3, True)])
@pytest.mark.parametrize("prefix", ["", "Critic: "])
def test_task_count_finding_is_a_risk_not_a_contradiction(draft_plan, count, docs_only, prefix):
    risk = f"Plan has {count} tasks; merge 1.1 and 1.2 because both address sign behavior."
    verdict = {"contradictions": [], "risks": [prefix + risk]}
    draft, calls, out, _ = draft_plan(agents.Result("ok", verdict), count, docs_only)
    assert not (draft.problems or draft.questions or draft.contradictions)
    assert draft.commit
    assert draft.risks == ["Critic: " + risk]
    assert [call.access for call in calls] == ["write", "read", "read"]
    assert "## Spec critic feedback" not in calls[0].prompt
    assert not (out / "critic-2").exists()
    persisted = json.loads((out / "draft.json").read_text())
    assert persisted["contradictions"] == []
    assert persisted["problems"] == []
    assert persisted["risks"] == ["Critic: " + risk]
    assert json.loads((out / "critic-1/critic.json").read_text()) == verdict


@pytest.mark.scenario("spec-critic/independent-checklist-review/complete-checklist-and-read-contract")
def test_task_risk_review_preserves_complete_checklist_and_read_contract(draft_plan):
    verdict = {"contradictions": [], "risks": []}
    draft, calls, out, m = draft_plan(agents.Result("ok", verdict))
    assert not draft.problems
    drafting, feasible, review = calls
    assert review.access == "read"
    assert review.schema is feasible.schema is feasibility.SCHEMA
    assert review.profile == feasible.profile == m.roles["review"]
    assert review.wall_seconds == feasible.wall_seconds == 20 * 60
    assert review.idle_seconds == feasible.idle_seconds == m.idle_minutes * 60
    assert review.cwd == out / "critic-1/checkout"
    assert review.cwd != feasible.cwd != drafting.cwd
    assert not review.cwd.exists()
    prompt = " ".join(review.prompt.split())
    for clause in (
        "fresh extraction of the exact candidate commit", "Do not modify anything.",
        "openspec/changes/effective-change/", "read proposal.md, design.md, specs/ and tasks.md",
        "code, tests, existing requirements in openspec/specs/", "AGENTS.md when present",
        "Implementers may change only: calc.py, tests/**, docs/**",
        "They may never change: protected.py",
    ):
        assert clause in prompt
    assert "openspec/changes/template/" not in prompt
    items = checklist_items(review.prompt)
    assert list(items) == list(range(1, 9))
    assert [item.split(":", 1)[0] for item in items.values()] == [
        "Over-engineering", "File collisions and hidden instructions", "Invalidated protected assertions",
        "Conflicts with unowned behavior", "Tests broken by pending changes", "Unplanned migrations",
        "Retry and idempotency gaps", "Stale operator docs and manual steps",
    ]
    assert review.transcript == out / "critic-1/critic.jsonl"
    assert len({call.transcript for call in calls}) == 3
    assert all(call.transcript.exists() for call in calls)
    assert json.loads((out / "critic-1/critic.json").read_text()) == verdict
    assert (out / "feasibility-1/feasibility.json").exists()
    assert (out / "draft.json").exists()


@pytest.mark.scenario("spec-critic/independent-checklist-review/review-failure-is-not-a-pass")
@pytest.mark.parametrize("response, diagnostic", [
    (agents.Result("infra", None, detail="agent failed"), "infra: agent failed"),
    (agents.Result("ok", None), "expected object"),
    (agents.Result("ok", {"contradictions": [], "risks": "merge tasks"}), "expected array"),
])
def test_task_risk_review_failure_stops_drafting(draft_plan, response, diagnostic):
    draft, calls, out, _ = draft_plan(response)
    assert len(draft.problems) == 1
    assert draft.problems[0].startswith("critic review failed:")
    assert diagnostic in draft.problems[0]
    assert [call.access for call in calls] == ["write", "read", "read"]
    assert not (out / "draft.json").exists()
    assert not (out / "critic-2").exists()
    assert not (out / "critic-1/checkout").exists()
    result = json.loads((out / "critic-1/critic.json").read_text())
    assert diagnostic in result["error"]
    assert "contradictions" not in result
    assert (out / "critic-1/critic.jsonl").exists()


@pytest.mark.scenario("spec-critic/advisory-task-count-findings/excessive-plans-receive-merge-advice")
@pytest.mark.scenario("spec-critic/advisory-task-count-findings/threshold-boundaries-are-advisory")
@pytest.mark.scenario("spec-critic/advisory-task-count-findings/native-counts-per-story")
def test_speckit_count_prompt_contract(items):
    native = checklist_items(critic.SPECKIT_PROMPT.format(
        change=CHANGE, allowed="src/**", frozen="protected.py", planned="{}",
        pr_files="{}", pr_state="Not configured", context_notes="[]"))
    assert list(native) == list(range(1, 9))
    assert {n: text for n, text in native.items() if n != 1} == {
        n: text for n, text in items.items() if n != 1}
    for clause in (
        "speckit.PHASE, speckit.STORY_PHASE, tasks.TASK and speckit.TASK_LABEL",
        "column-zero `## Phase N: Title`", "title must start with `User Story K`",
        "same-or-higher heading (column-zero level one or two) closes the phase; deeper headings retain it",
        "Only native T-task checkboxes within that phase count", "`- [ ]`, `- [x]` or `- [X]`",
        "optional leading whitespace", "at least three digits and optional dotted numeric children",
        "optional positional [P] and [USk] flags",
        "Count checked and unchecked entries and each dotted child as entries, not headings or scenarios",
        "no semantic interpretation of fences, HTML or indentation",
        "matching lines in examples receive no exemption",
        "Ambiguous phase/task identity remains a validator/parser refusal, never a size pass",
        "More than eight tasks (nine or more) in one story phase warrants a non-blocking risk naming that story phase",
        "concrete task labels that could merge and their shared concern",
        "if necessary boundaries prevent a safe merge, explain why rather than inventing a merge",
        "Exactly eight tasks in a story phase do not trigger a count finding",
        "Fifteen tasks across three five-task story phases do not trigger a count finding",
        "Ignore whole-feature totals, including documentation-only totals",
        "Setup, Foundational and Polish phases are outside this story-count test",
        "subject to ordinary feasibility and over-engineering review",
        "same per-story threshold, not a two-task critic threshold",
        "six-task recommendation per story is not a critic rejection threshold",
        "about 300 changed lines per task, not per story or feature",
        "Task count alone never belongs in contradictions, requires correction or rejects a proposal",
        "Independently evidenced unsafe designs still qualify as contradictions under the existing blocking criteria",
    ):
        assert clause in native[1]
    assert "Count task entries across the whole plan" in items[1]
    assert "more than two tasks (three or more)" in items[1]
    assert "Count task entries across the whole plan" not in native[1]
    assert "more than two tasks (three or more)" not in native[1]


@pytest.mark.scenario("spec-critic/advisory-task-count-findings/native-counts-per-story")
@pytest.mark.scenario("spec-critic/advisory-task-count-findings/count-risk-preserves-successful-drafting")
@pytest.mark.scenario("spec-drafting/risk-based-task-sizing/native-feature-size-is-per-story")
@pytest.mark.scenario("spec-drafting/compact-documentation-plans/native-documentation-retains-story-phases")
@pytest.mark.parametrize("counts", [(5, 5, 5), (8,), (9,)], ids=["15/3", "8/1", "9/1"])
@pytest.mark.parametrize("docs_only", [False, True])
@pytest.mark.parametrize("prefix", ["", "Critic: "])
def test_speckit_counts_per_story(native_feature, tmp_path, counts, docs_only, prefix):
    # Fake verdicts establish routing, not whether a model follows its checklist.
    concern = "Document gate behavior" if docs_only else "Implement gate behavior"
    spec = "# Feature: Gate\n\n**Status**: Draft\n\n## User Scenarios & Testing\n\n"
    plan = "Documentation updates without changing executable behavior.\n" if docs_only else "Implement the gate.\n"
    tasks = "## Phase 1: Setup\n"
    for story, count in enumerate(counts, 1):
        spec += (f"### User Story {story} - Gate {story} (Priority: P{story})\n\n"
                 "**Acceptance Scenarios**:\n"
                 "1. **AS1** Given a check, when it runs, then report its result.\n\n")
        tasks += f"## Phase {story + 1}: User Story {story} - Gate {story}\n"
        for index in range(1, count + 1):
            # Checked entries, children, flags and deeper headings all contribute.
            label = f"T{story:03}.{index}"
            checked = (" ", "x", "X")[index % 3]
            tasks += (f"### Work {index}\n  - [{checked}] {label} [P] [US{story}] "
                      f"{concern} part {index} covering `{CHANGE}/us{story}/as1`\n")
    tasks += "## Phase 9: Polish\n"
    spec += "## Requirements\n- **FR-001**: Report results.\n\n## Success Criteria\n- **SC-001**: Results are readable.\n"
    files = {"spec.md": spec, "plan.md": plan, "tasks.md": tasks}
    parsed = spec_format.SPECKIT.parse_tasks(tasks)
    assert Counter(t.section for t in parsed) == {story + 1: count for story, count in enumerate(counts, 1)}
    assert any(t.done for t in parsed) and any(not t.done for t in parsed)
    risk = "Phase 2: User Story 1 has nine tasks; merge T001.1 and T001.2 because both address gate behavior."
    risks = [risk] if counts == (9,) else []
    verdict = {"contradictions": [], "risks": [prefix + r for r in risks]}
    calls = []

    def agent(call):
        calls.append(call)
        if call.access == "write":
            for name, body in files.items():
                (call.cwd / ROOT / name).write_text(body)
            return agents.Result("ok", {"status": "drafted", "summary": "", "questions": []})
        assert call.access == "read" and call.schema is feasibility.SCHEMA
        for name, body in files.items():
            assert (call.cwd / ROOT / name).read_text() == body
        if call.prompt.startswith("# Feasibility check"):
            return agents.Result("ok", {"contradictions": [], "risks": []})
        assert call.prompt.startswith("# Spec critic of a spec-kit feature")
        assert "Ignore whole-feature totals, including documentation-only totals" in " ".join(call.prompt.split())
        return agents.Result("ok", verdict)

    out = tmp_path / "draft"
    d = propose.draft(native_feature, "main", CHANGE, concern, [], replace(M, format="speckit"),
                      out, agent=agent, from_branch="main")
    assert not (d.problems or d.questions or d.contradictions), d
    assert d.commit and d.risks == ["Critic: " + r for r in risks]
    assert [call.access for call in calls] == ["write", "read", "read"]
    assert "## Spec critic feedback" not in calls[0].prompt
    assert not (out / "draft-2.jsonl").exists() and not (out / "critic-2").exists()
    persisted = json.loads((out / "draft.json").read_text())
    assert persisted["contradictions"] == persisted["problems"] == []
    assert persisted["risks"] == d.risks
    assert json.loads((out / "critic-1/critic.json").read_text()) == verdict


@pytest.mark.scenario("spec-critic/advisory-task-count-findings/native-counts-per-story")
@pytest.mark.parametrize("wrapper,ending", [("", ""), ("```markdown\n", "```"), ("<!--\n", "-->"), ("    <div>\n", "</div>")])
@pytest.mark.parametrize("boundary", ["# End", "## End", "##\tEnd"])
def test_speckit_count_entries_use_native_physical_lines(wrapper, ending, boundary):
    text = (wrapper + "## Phase 1: User Story 1 - Gate\n"
            "- [ ] T001 [US1] Parent\n### Details\n"
            "  - [x] T001.1 [P] [US1] Child\n    ## Indented heading\n"
            "- [X] T001.2 [US1] Another child\n"
            "- [ ] ordinary checkbox\n- [ ] T01 Invalid label\n" + boundary + "\n"
            "- [ ] T002 Outside phase\n## Phase 2: Setup\n- [ ] T003 Setup work\n"
            "## Phase 3: Foundational\n- [ ] T004 Foundation\n"
            "## Phase 4: Polish\n- [ ] T005 Polish work\n" + ending)
    parsed = spec_format.SPECKIT.parse_tasks(text)
    assert [t.label for t in parsed] == ["T001", "T001.1", "T001.2", "T003", "T004", "T005"]
    story = [t for t in parsed if speckit.STORY_PHASE.match(t.section_title)]
    assert [t.label for t in story] == ["T001", "T001.1", "T001.2"]
    assert [t.done for t in story] == [False, True, True]
    for ambiguous, diagnostic in (
        ("## Phase 1: Duplicate\n", "duplicate native phase"),
        ("## Phase 5: Extra\n- [ ] T0001 Duplicate\n", "duplicate native task label"),
    ):
        with pytest.raises(spec_format.SpecError, match=diagnostic):
            spec_format.SPECKIT.parse_tasks(text + "\n" + ambiguous)

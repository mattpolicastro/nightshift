"""Prompt contracts and offline routing for proportionate critic findings."""

import json
import re
import subprocess

import pytest

from specloop import agents, critic, feasibility, manifest, openspec, propose, state


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

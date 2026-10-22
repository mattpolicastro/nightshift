"""Runtime feedback references use the evaluated change's actual scenarios."""

import json
import subprocess
from dataclasses import asdict, replace

import pytest

from specloop import cli, evidence, loop, manifest, openspec, propose, revise, seal, tasks, verify
from specloop.state import Run, TaskState


KNOWN = "cap/req/known"
UNKNOWN = "cap/req/unknown"
FEEDBACK = f"Fix `a/b/c` and `{UNKNOWN}` while preserving `{KNOWN}`.\nKeep this feedback intact."
SCENARIOS_PATH = "openspec/changes/c/scenarios.json"


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    src = tmp_path / "source"
    change = openspec.change_dir(src, "c")
    change.mkdir(parents=True)
    (change / "tasks.md").write_text(f"## 1. Core\n\n- [x] 1.1 Implement `{KNOWN}`\n")
    # Control only the OpenSpec CLI boundary; scenario parsing and extraction stay real.
    scenario_data = {"deltas": [{"spec": "cap", "requirements": [{"name": "req", "scenarios": [
        {"name": "known", "rawText": "WHEN invoked THEN the known behavior holds"},
    ]}]}]}
    (src / SCENARIOS_PATH).write_text(json.dumps(scenario_data))

    def openspec_run(repo, *args):
        assert args == ("show", "c")
        return 0, json.loads((repo / SCENARIOS_PATH).read_text())

    monkeypatch.setattr(openspec, "_run", openspec_run)
    monkeypatch.setattr(openspec, "validate", lambda repo, change: [])
    sh(tmp_path, "git", "init", "-q", "-b", "spec/c", str(src))
    sh(src, "git", "add", "-A")
    sh(src, "git", "-c", "user.name=test", "-c", "user.email=test@test", "commit", "-qm", "fixture")
    run = Run(tmp_path / "run")
    run.save()
    sh(tmp_path, "git", "clone", "-q", "--bare", str(src), str(run.dir / "repo.git"))
    m = manifest.parse({
        "run": {"repo": str(src), "change": "c", "branch": "spec/c"},
        "roles": {"implement": "p", "review": "p"},
        "profiles": {"p": {"adapter": "codex", "model": "m"}},
        "paths": {"allowed": ["app/**"]},
        "verify": [{"id": "tests", "run": ["true"]}],
        "coverage": {"step": "tests"},
        "gates": {"evidence": True},
    })
    ctx = loop.Ctx(run, m, {})
    head = revise.revise(run, m, FEEDBACK)
    text = ctx.tasks_text(head)
    feedback_task = tasks.parse(text)[-1]
    loop._rewrite_tasks(ctx, head, tasks.tick(text, feedback_task), "complete fixture feedback", None)
    return ctx, src


def verification(monkeypatch, seen):
    def run_steps(repo, head, steps, m, out):
        out.mkdir(parents=True, exist_ok=True)
        (out / "tests.log").write_text("\n".join(sorted(seen)))
        return [verify.Receipt(s.id, list(s.run), s.expect, s.expect, True, 0, "") for s in steps]

    monkeypatch.setattr(verify, "run_steps", run_steps)


@pytest.mark.scenario("specloop-interface/known-runtime-scenario-references/paths-in-review-feedback")
@pytest.mark.scenario("specloop-interface/known-runtime-scenario-references/real-coverage-gaps-remain-actionable")
@pytest.mark.parametrize("covered", [False, True])
def test_review_feedback_only_accepts_existing_scenario_ids(env, tmp_path, monkeypatch, covered):
    ctx, src = env
    verification(monkeypatch, {KNOWN} if covered else set())
    head = ctx.head()
    original = ctx.tasks_text(head)
    parsed = tasks.parse(original)
    task = parsed[-1]
    assert task.text == f"Address review feedback 1: {json.dumps(FEEDBACK)}"
    assert task.scenarios == ["a/b/c", UNKNOWN, KNOWN]

    assert loop.section_evidence(ctx, 2, head, parsed)
    saved = json.loads((ctx.run.dir / "gates/section-2-0/evidence.json").read_text())
    assert saved["covered"] == ([KNOWN] if covered else [])
    assert saved["uncovered"] == ([] if covered else [KNOWN])
    after = tasks.parse(ctx.tasks_text(ctx.head()))
    assert after[:len(parsed)] == parsed  # Text, label, key inputs and completion survive.
    assert after[1].key == task.key
    if covered:
        assert ctx.head() == head
        assert ctx.tasks_text(head) == original
        assert ctx.run.state.evidenced == [2]
        assert ctx.run.state.strengthen_rounds == {}
    else:
        assert len(after) == len(parsed) + 1
        added = after[-1]
        assert added.section == 2 and added.label == "2.2" and not added.done
        assert added.text == (f"Add tagged tests covering `{KNOWN}`, fixing the code where a scenario does not hold")
        assert added.scenarios == [KNOWN]
        assert ctx.run.state.evidenced == []
        assert ctx.run.state.strengthen_rounds == {"2": 1}

    implementation = tmp_path / "implementation"
    loop._context(ctx, task, TaskState(), src, implementation, False)
    task_text, scenario_text = (implementation / "task.md").read_text().split("## Scenarios\n\n")
    assert task.text in task_text
    expected = f"### `{KNOWN}`\nreq — known\nWHEN invoked THEN the known behavior holds\n"
    assert scenario_text == expected
    gate = tmp_path / "gate"
    loop._gate_context(ctx, 2, head, [task], TaskState(), src, gate)
    section_text, scenario_text = (gate / "section.md").read_text().split("## Scenarios cited\n\n")
    assert task.text in section_text
    assert scenario_text == expected + "\n"


@pytest.mark.scenario("specloop-interface/known-runtime-scenario-references/paths-in-review-feedback")
def test_known_scenarios_preserve_candidate_order_and_exact_membership():
    task = tasks.parse("## 1. Feedback\n- [ ] 1.1 Fix `z/r/s`, `a/b/c`, `z/r/s`, `cap/req/unknown`\n")[0]
    before = asdict(task), task.key
    assert task.known_scenarios({"a/b/c", "z/r/s", "cap/req/unknown-extra"}) == ["z/r/s", "a/b/c", "z/r/s"]
    assert task.known_scenarios(set()) == []
    assert (asdict(task), task.key) == before


@pytest.mark.scenario("specloop-interface/known-runtime-scenario-references/paths-in-review-feedback")
def test_evidence_uses_scenarios_from_evaluated_revision(env, monkeypatch):
    ctx, src = env
    verification(monkeypatch, {KNOWN})
    evaluated = ctx.head()
    # The current branch now defines a/b/c, but the older evaluated revision does not.
    data = json.loads((src / SCENARIOS_PATH).read_text())
    data["deltas"].append({"spec": "a", "requirements": [{"name": "b", "scenarios": [{"name": "c"}]}]})
    newer = seal.commit_files(ctx.repo, evaluated, {SCENARIOS_PATH: json.dumps(data)}, "new scenario")
    seal.git(ctx.repo, "update-ref", ctx.ref, newer, evaluated)
    lookups = []
    real_lookup = openspec.scenarios

    def lookup(repo, change):
        result = real_lookup(repo, change)
        lookups.append(result)
        return result

    monkeypatch.setattr(openspec, "scenarios", lookup)
    assert loop.section_evidence(ctx, 2, evaluated, tasks.parse(ctx.tasks_text(evaluated)))
    assert len(lookups) == 1 and set(lookups[0]) == {KNOWN}
    assert ctx.run.state.evidenced == [2]
    assert ctx.run.state.strengthen_rounds == {}
    assert ctx.head() == newer
    # Once that path really is a scenario, its missing coverage is actionable.
    ctx.run.state.evidenced.clear()
    assert loop.section_evidence(ctx, 2, newer, tasks.parse(ctx.tasks_text(newer)))
    added = tasks.parse(ctx.tasks_text(ctx.head()))[-1]
    assert added.text == "Add tagged tests covering `a/b/c`, fixing the code where a scenario does not hold"


@pytest.mark.scenario("specloop-interface/known-runtime-scenario-references/strict-validation-and-failed-lookup")
def test_authored_unknown_ids_still_fail_drafting_and_admission(env):
    ctx, src = env
    authored = f"## 1. Core\n\n- [ ] 1.1 Implement `{KNOWN}`, `{UNKNOWN}` and `a/b/c`\n"
    (src / ctx.tasks_path).write_text(authored)
    rev = seal.commit_files(ctx.repo, ctx.head(), {ctx.tasks_path: authored}, "authored tasks")
    expected = [f"task 1.1: unknown scenario `{UNKNOWN}`", "task 1.1: unknown scenario `a/b/c`"]
    assert propose.lint(src, "c") == expected
    assert cli.problems_with(ctx.repo, rev, ctx.m) == expected


@pytest.mark.scenario("specloop-interface/known-runtime-scenario-references/strict-validation-and-failed-lookup")
@pytest.mark.parametrize("coverage_step", ["tests", ""])
def test_failed_lookup_cannot_pass_evidence_or_append_tasks(env, monkeypatch, coverage_step):
    ctx, _ = env
    ctx.m = replace(ctx.m, coverage_step=coverage_step)
    head = ctx.head()
    text = ctx.tasks_text(head)
    before = asdict(ctx.run.state)

    def failed_lookup(repo, change):
        raise openspec.SpecError("scenario service unavailable")

    def unexpected(*args, **kwargs):
        pytest.fail("a failed scenario lookup must stop before evidence or task writes")

    monkeypatch.setattr(openspec, "scenarios", failed_lookup)
    monkeypatch.setattr(evidence, "gather", unexpected)
    monkeypatch.setattr(loop, "_rewrite_tasks", unexpected)
    monkeypatch.setattr(verify, "run_steps", unexpected)
    with pytest.raises(openspec.SpecError, match="scenario service unavailable"):
        loop.section_evidence(ctx, 2, head, tasks.parse(text))
    assert ctx.head() == head and ctx.tasks_text(head) == text
    assert asdict(ctx.run.state) == before
    assert asdict(Run(ctx.run.dir).state) == before
    assert not list(ctx.run.dir.rglob("evidence.json"))


@pytest.mark.scenario("specloop-interface/known-runtime-scenario-references/strict-validation-and-failed-lookup")
def test_context_lookup_failures_keep_diagnostics(env, tmp_path, monkeypatch):
    ctx, src = env
    task = tasks.parse(ctx.tasks_text(ctx.head()))[-1]

    def failed_lookup(repo, change):
        raise openspec.SpecError("scenario service unavailable")

    monkeypatch.setattr(openspec, "scenarios", failed_lookup)
    implementation = tmp_path / "implementation"
    loop._context(ctx, task, TaskState(), src, implementation, False)
    assert "(scenario lookup failed: scenario service unavailable)" in (implementation / "task.md").read_text()
    gate = tmp_path / "gate"
    loop._gate_context(ctx, 2, ctx.head(), [task], TaskState(), src, gate)
    events = [json.loads(line) for line in (ctx.run.dir / "events.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "gate_scenarios_failed"
    assert events[-1]["error"] == "scenario service unavailable"
    assert task.text in (gate / "section.md").read_text()

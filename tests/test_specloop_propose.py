import subprocess

import pytest

from specloop import agents, manifest, openspec, propose


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(openspec, "validate", lambda repo, change: [])
    monkeypatch.setattr(openspec, "scenarios", lambda repo, change: {"calc/sign/zero": "", "calc/sign/negative": ""})
    src = tmp_path / "repo"
    src.mkdir()
    sh(tmp_path, "git", "init", "-q", "-b", "main", str(src))
    (src / "calc.py").write_text("")
    sh(src, "git", "add", "-A")
    sh(src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return src


M = manifest.parse({
    "run": {"repo": "/unused", "change": "c", "branch": "b"},
    "roles": {"implement": "p", "review": "p"},
    "profiles": {"p": {"adapter": "codex", "model": "m"}},
    "paths": {"allowed": ["calc.py"]},
    "verify": [{"id": "t", "run": ["true"]}],
})

GOOD = "## 1. Sign\n\n- [ ] 1.1 Add sign covering `calc/sign/zero` and `calc/sign/negative`\n"
PARTIAL = "## 1. Sign\n\n- [ ] 1.1 Add sign covering `calc/sign/zero` and `calc/sign/sideways`\n"


def drafter(*bodies, stray=False, feasible=True):
    calls = []

    def agent(call):
        calls.append(call)
        if call.access == "read":
            found = [] if feasible else [{"task": "1.1", "problem": "frozen", "evidence": "x:1"}]
            return agents.Result("ok", {"contradictions": found, "risks": []})
        body = bodies[len([c for c in calls if c.access == "write"]) - 1]
        d = call.cwd / "openspec" / "changes" / "sign-helpers"
        d.mkdir(parents=True, exist_ok=True)
        (d / "tasks.md").write_text(body)
        if stray:
            (call.cwd / "calc.py").write_text("oops")
        return agents.Result("ok", {"status": "drafted", "summary": "", "questions": []})
    agent.calls = calls
    return agent


def test_a_rejected_draft_is_fed_back_then_committed(source, tmp_path):
    agent = drafter(PARTIAL, GOOD)
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    writes = [c for c in agent.calls if c.access == "write"]
    assert len(writes) == 2 and "unknown scenario `calc/sign/sideways`" in writes[1].prompt
    assert "not covered by any task" in writes[1].prompt
    assert not d.problems and d.commit
    propose.publish_branch(d, source)
    assert "Add sign covering" in sh(source, "git", "show", "spec/sign-helpers:openspec/changes/sign-helpers/tasks.md")


def test_edits_outside_the_change_are_refused(source, tmp_path):
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out",
                      rounds=1, agent=drafter(GOOD, stray=True))
    assert any("calc.py: only files under" in p for p in d.problems) and not d.commit


def test_needs_input_returns_the_questions(source, tmp_path):
    def agent(call):
        return agents.Result("ok", {"status": "needs_input", "summary": "", "questions": ["stereo or mono?"]})
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    assert d.questions == ["stereo or mono?"] and not d.commit


def test_unchanged_revision_is_rejected_without_a_commit(source, tmp_path):
    initial = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "first",
                            agent=drafter(GOOD))
    propose.publish_branch(initial, source)
    revised = propose.draft(source, "main", "sign-helpers", "revise sign", [], M, tmp_path / "second",
                            from_branch="spec/sign-helpers", agent=drafter(GOOD))
    assert revised.problems == ["The drafting session made no changes."] and not revised.commit
    assert sh(source, "git", "rev-parse", "spec/sign-helpers") == initial.commit


def test_feasibility_contradictions_go_back_to_the_drafter(source, tmp_path):
    verdicts = [False, True]
    calls = []

    def agent(call):
        calls.append(call)
        if call.access == "read":
            ok = verdicts.pop(0)
            found = [] if ok else [{"task": "1.1", "problem": "tests are not importable", "evidence": "design.md:28"}]
            return agents.Result("ok", {"contradictions": found, "risks": []})
        d = call.cwd / "openspec" / "changes" / "sign-helpers"
        d.mkdir(parents=True, exist_ok=True)
        (d / "tasks.md").write_text(GOOD + ("\n" if len(calls) > 2 else ""))
        return agents.Result("ok", {"status": "drafted", "summary": "", "questions": []})

    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    writes = [c for c in calls if c.access == "write"]
    assert len(writes) == 2 and "tests are not importable" in writes[1].prompt
    assert not d.contradictions and d.commit


def test_contradictions_that_survive_every_round_are_reported(source, tmp_path):
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out",
                      rounds=2, agent=drafter(GOOD, GOOD, feasible=False))
    assert d.contradictions and d.commit


def test_drafting_prompt_keeps_human_checks_out_of_tasks(source, tmp_path):
    # A human-only task would be dispatched to an agent and stall the build.
    agent = drafter(GOOD)
    propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    prompt = [c for c in agent.calls if c.access == "write"][0].prompt
    assert "is never a task" in prompt and "human checklist" in prompt

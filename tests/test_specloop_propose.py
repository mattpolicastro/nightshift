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


from specloop import feasibility, state  # noqa: E402

HUMAN = GOOD + "- [ ] 1.2 Manually load the plugin and check `calc/sign/zero`\n"
SHARED = "## 1. Sign\n\n- [ ] 1.1 Add sign in `calc.py` with tests in `tests/test_calc.py` covering `calc/sign/zero` and `calc/sign/negative`\n"
SEPARATE = SHARED.replace("tests/test_calc.py", "tests/test_calc_sign.py")


def other_run(source, tmp_path, monkeypatch, status="running", repo=None):
    """An unfinished run on the same repository whose tasks plan to edit tests/test_calc.py."""
    monkeypatch.setattr(state, "ROOT", tmp_path / "home")
    sh(source, "git", "checkout", "-q", "-b", "spec/other-change")
    d = source / "openspec" / "changes" / "other-change"
    d.mkdir(parents=True)
    (d / "tasks.md").write_text("## 1. Other\n\n- [ ] 1.1 Extend `tests/test_calc.py` and `calc.py`\n")
    sh(source, "git", "add", "-A")
    sh(source, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "other")
    sh(source, "git", "checkout", "-q", "main")
    run = state.Run.for_change("other-change")
    run.state.status = status
    run.save()
    sh(tmp_path, "git", "clone", "-q", "--bare", str(source), str(run.dir / "repo.git"))
    (run.dir / "manifest.toml").write_text(f"""
[run]
repo = "{repo or source}"
change = "other-change"
branch = "spec/other-change"
[roles]
implement = "p"
review = "p"
[profiles.p]
adapter = "codex"
model = "m"
[paths]
allowed = ["calc.py"]
[[verify]]
id = "t"
run = ["true"]
""")
    return run


def test_human_task_is_fed_back_until_moved_to_the_design(source, tmp_path):
    agent = drafter(HUMAN, GOOD)
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    writes = [c for c in agent.calls if c.access == "write"]
    assert len(writes) == 2 and "task 1.2: a person does this" in writes[1].prompt
    assert "describe the check in design.md" in writes[1].prompt
    assert not d.problems and d.commit


def test_drafter_is_told_about_work_in_flight_and_moves_shared_tests(source, tmp_path, monkeypatch):
    other_run(source, tmp_path, monkeypatch)
    agent = drafter(SHARED, SEPARATE)
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    writes = [c for c in agent.calls if c.access == "write"]
    assert "## Work in flight" in writes[0].prompt
    assert "- `tests/test_calc.py` (other-change)" in writes[0].prompt
    assert "- `calc.py`" not in writes[0].prompt  # only paths with a directory are named
    assert "task 1.1: `tests/test_calc.py` is also being changed by other-change" in writes[1].prompt
    assert not d.problems and d.commit and not d.risks


def test_shared_test_file_gets_one_round_then_becomes_a_risk(source, tmp_path, monkeypatch):
    other_run(source, tmp_path, monkeypatch)
    agent = drafter(SHARED, SHARED.replace("Add sign", "Add the sign helper"))
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    assert not d.problems and d.commit
    assert d.risks == ["task 1.1: `tests/test_calc.py` is also being changed by other-change; put these "
                       "tests in a new file, so the two may conflict on merge"]


@pytest.mark.parametrize("status, repo", [("done", None), ("aborted", None), ("running", "/elsewhere")])
def test_finished_runs_and_other_repositories_are_not_in_flight(source, tmp_path, monkeypatch, status, repo):
    other_run(source, tmp_path, monkeypatch, status=status, repo=repo)
    assert propose.in_flight(source, "sign-helpers") == {}
    agent = drafter(SHARED)
    propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    assert "Work in flight" not in [c for c in agent.calls if c.access == "write"][0].prompt


def test_an_unreadable_run_does_not_stop_drafting(source, tmp_path, monkeypatch):
    run = other_run(source, tmp_path, monkeypatch)
    (run.dir / "manifest.toml").write_text("not toml [")
    assert propose.in_flight(source, "sign-helpers") == {}


def test_feasibility_review_checks_existing_specs_and_human_tasks():
    assert "requirement in\n  openspec/specs/ guarantees" in feasibility.PROMPT
    assert "a person, not an implementing agent" in feasibility.PROMPT
    assert "requirements in openspec/specs/ for each capability it touches" in propose.PROMPT


def test_a_test_file_the_task_says_to_leave_alone_is_not_an_overlap(source, tmp_path, monkeypatch):
    other_run(source, tmp_path, monkeypatch)
    hands_off = SEPARATE.replace(" covering", "; do not edit `tests/test_calc.py`, covering")
    agent = drafter(hands_off)
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    assert len([c for c in agent.calls if c.access == "write"]) == 1
    assert not d.problems and not d.risks
    assert propose.edited_paths("Extend `tests/a.py` and `c/e.py`. Never modify `tests/b.py`; "
                                "leave `c/d.py` as is.") == {"tests/a.py", "c/e.py"}


def test_the_drafter_gets_four_rounds_by_default(source, tmp_path):
    agent = drafter(PARTIAL, PARTIAL, PARTIAL, GOOD)
    d = propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    assert len([c for c in agent.calls if c.access == "write"]) == 4 and not d.problems and d.commit


def test_drafter_is_told_to_name_files_the_implementer_must_avoid(source, tmp_path, monkeypatch):
    # Spec PR #42 wrote "tests in the work-in-flight list must remain untouched",
    # an instruction its implementer could not follow.
    other_run(source, tmp_path, monkeypatch)
    agent = drafter(SEPARATE)
    propose.draft(source, "main", "sign-helpers", "add sign", [], M, tmp_path / "out", agent=agent)
    prompt = [c for c in agent.calls if c.access == "write"][0].prompt
    assert "The implementer never sees this list: if the change must avoid a file, name it." in prompt

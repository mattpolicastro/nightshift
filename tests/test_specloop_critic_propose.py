"""Proposal integration with real Git, review extraction, and offline agents."""

import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from specloop import agents, critic, manifest, openspec, propose, state


CLEAN = "spec-critic/ordered-proposal-gates/clean-draft-receives-the-extra-review"
CORRECTION = "spec-critic/single-critic-correction/blocking-finding-triggers-one-redraft"
BOTH = "spec-critic/concurrent-file-context/both-sources-reach-the-critic"
PARTIAL = "spec-critic/concurrent-file-context/partial-lookup-remains-visible"
NOTES = "spec-critic/notes-through-existing-risks/final-notes-replace-superseded-notes"
CHANGE = "sign-helpers"
DIRECTORY = f"openspec/changes/{CHANGE}"
TASKS = "## 1. Sign\n\n- [ ] 1.1 Implement sign covering `calc/sign/zero`\n"
INITIAL = "Use a package-wide assertion and forbid all existing test edits.\n"
CORRECTED = "Use a focused sign assertion; permit the necessary fixture update.\n"
FINDINGS = [
    {"task": "1.1", "problem": "Protected assertion becomes false", "evidence": "tests/test_calc.py:12"},
    {"task": "1.2", "problem": "Package-wide assumption conflicts with pending work", "evidence": "src/sign.py (other-change)"},
]
PASS = {"contradictions": [], "risks": []}
BLOCKED = {"contradictions": FINDINGS, "risks": ["Superseded critic note"]}
REAL_IN_FLIGHT = propose.in_flight


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def env(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    (source / "calc.py").write_text("def sign(x): return 0\n")
    commit(source, "base")
    m = manifest.parse({
        "run": {"repo": str(source), "change": "template", "branch": "spec/template", "github": "org/repo"},
        "roles": {"implement": "p", "review": "p"},
        "profiles": {"p": {"adapter": "codex", "model": "test"}},
        "paths": {"allowed": ["src/**", "tests/**"]},
        "verify": [{"id": "tests", "run": ["true"]}],
    })
    e = SimpleNamespace(source=source, m=m, out=tmp_path / "out", events=[], lookups=[],
                        planned={}, pr_files={}, notes=[])
    monkeypatch.setattr(state, "ROOT", tmp_path / "home")
    monkeypatch.setattr(openspec, "validate", lambda repo, change: [])
    monkeypatch.setattr(openspec, "scenarios", lambda repo, change: {"calc/sign/zero": ""})
    monkeypatch.setattr(propose, "in_flight", lambda source, change: e.planned)
    lint = propose.lint

    def checked_lint(*args):
        e.events.append("lint")
        return lint(*args)

    def lookup(github, branch):
        e.events.append("lookup")
        e.lookups.append((github, branch))
        return e.pr_files, e.notes

    monkeypatch.setattr(propose, "lint", checked_lint)
    monkeypatch.setattr(critic, "open_pr_files", lookup)
    return e


class Script:
    def __init__(self, env, bodies=(INITIAL,), feasibility=(PASS,), critics=(PASS,), tasks=TASKS):
        self.env, self.tasks = env, tasks
        self.bodies, self.feasibility, self.critics = map(iter, (bodies, feasibility, critics))
        self.calls, self.snapshots = [], []

    def __call__(self, call):
        self.calls.append(call)
        if call.access == "write":
            self.env.events.append("write")
            self.work = call.cwd
            body = next(self.bodies)
            if isinstance(body, agents.Result):
                return body
            if body is not None:
                directory = call.cwd / DIRECTORY
                (directory / "specs/calc").mkdir(parents=True, exist_ok=True)
                (directory / "proposal.md").write_text("## Why\nDefine sign at zero.\n")
                (directory / "design.md").write_text(body)
                (directory / "tasks.md").write_text(self.tasks)
                (directory / "specs/calc/spec.md").write_text(
                    "## ADDED Requirements\n### Requirement: Sign\nThe helper SHALL return zero.\n"
                    "#### Scenario: Zero\n- **WHEN** input is zero\n- **THEN** return zero\n")
            return agents.Result("ok", {"status": "drafted", "summary": "", "questions": []})
        kind = "critic" if call.prompt.startswith("# Spec critic of an OpenSpec change") else "feasibility"
        self.env.events.append(kind)
        self.snapshots.append((kind, git(self.work, "rev-parse", "HEAD"),
                               (call.cwd / DIRECTORY / "design.md").read_text()))
        result = next(self.critics if kind == "critic" else self.feasibility)
        return result if isinstance(result, agents.Result) else agents.Result("ok", result)

    def draft(self, **kwargs):
        e = self.env
        return propose.draft(e.source, "main", CHANGE, "Define sign", [], e.m, e.out, agent=self, **kwargs)

    def prompts(self, kind):
        prefix = "# Spec critic" if kind == "critic" else "# Draft an OpenSpec"
        return [c.prompt for c in self.calls if c.prompt.startswith(prefix)]


@pytest.mark.scenario(CLEAN)
@pytest.mark.parametrize("revision", [False, True])
def test_clean_candidate_receives_critic_after_feasibility(env, revision):
    branch = "draft/effective"
    start = git(env.source, "rev-parse", "main")
    if revision:
        git(env.source, "checkout", "-b", branch)
        directory = env.source / DIRECTORY
        directory.mkdir(parents=True)
        (directory / "design.md").write_text("Original reviewed design.\n")
        start = commit(env.source, "existing proposal")
        git(env.source, "checkout", "main")
    agent = Script(env, feasibility=({"contradictions": [], "risks": ["Feasibility risk"]},))
    d = agent.draft(branch=branch, from_branch=branch if revision else None)
    assert env.events == ["write", "lint", "feasibility", "lookup", "critic"]
    assert env.lookups == [("org/repo", branch)]
    assert not (d.problems or d.questions or d.contradictions)
    assert d.branch == branch and git(d.work, "branch", "--show-current") == branch
    assert git(d.work, "rev-parse", f"{d.commit}^") == start
    assert agent.snapshots == [(kind, d.commit, INITIAL) for kind in ("feasibility", "critic")]
    assert f"openspec/changes/{CHANGE}/" in agent.prompts("critic")[0]
    assert "Successful lookup: no open PR changed files." in agent.prompts("critic")[0]
    assert d.risks == ["Feasibility risk"]
    assert json.loads((env.out / "critic-1/critic.json").read_text()) == PASS


@pytest.mark.scenario(CORRECTION)
def test_critic_blocking_finding_triggers_one_redraft(env):
    agent = Script(env, bodies=(INITIAL, CORRECTED), feasibility=(PASS, PASS), critics=(BLOCKED, PASS))
    d = agent.draft()
    assert env.events == ["write", "lint", "feasibility", "lookup", "critic",
                          "write", "lint", "feasibility", "critic"]
    assert len(agent.prompts("write")) == len(agent.prompts("critic")) == 2
    assert "## Spec critic feedback" in agent.prompts("write")[1]
    for finding in FINDINGS:
        for field in finding.values():
            assert field in agent.prompts("write")[1]
    first = agent.snapshots[0][1]
    assert first != d.commit and git(d.work, "rev-parse", f"{d.commit}^") == first
    assert agent.snapshots == [("feasibility", first, INITIAL), ("critic", first, INITIAL),
                               ("feasibility", d.commit, CORRECTED), ("critic", d.commit, CORRECTED)]
    assert not (d.problems or d.questions or d.contradictions or d.risks)
    assert json.loads((env.out / "critic-2/critic.json").read_text()) == PASS


@pytest.mark.scenario(BOTH)
def test_critic_receives_in_flight_and_open_pr_files(env, monkeypatch):
    # Use the real reader so production paths survive its existing parser.
    run = state.Run.for_change("other-change")
    run.state.status = "running"
    run.save()
    git(env.source, "checkout", "-b", "spec/other-change")
    directory = env.source / "openspec/changes/other-change"
    directory.mkdir(parents=True)
    (directory / "tasks.md").write_text("## 1. Work\n- [ ] 1.1 Edit `src/sign.py` and `tests/test_pending.py`\n")
    commit(env.source, "pending work")
    git(env.source, "checkout", "main")
    git(env.source, "clone", "-q", "--bare", str(env.source), str(run.dir / "repo.git"))
    (run.dir / "manifest.toml").touch()
    monkeypatch.setattr(propose.manifest, "load", lambda path: replace(
        env.m, change="other-change", branch="spec/other-change"))
    monkeypatch.setattr(propose, "in_flight", REAL_IN_FLIGHT)
    env.pr_files = {"src/remote.py": ["#7 https://github.com/org/repo/pull/7"],
                    "tests/test_remote.py": ["#8 https://github.com/org/repo/pull/8"],
                    "README.md": ["#7 https://github.com/org/repo/pull/7"]}
    agent = Script(env, bodies=(INITIAL, CORRECTED), feasibility=(PASS, PASS), critics=(BLOCKED, PASS))
    d = agent.draft()
    prompts = agent.prompts("critic")
    assert len(prompts) == 2 and prompts[0] == prompts[1]
    for path in ("src/sign.py", "tests/test_pending.py"):
        assert f'"{path}": [\n    "other-change"' in prompts[0]
    for path, labels in env.pr_files.items():
        assert json.dumps(path) in prompts[0] and labels[0] in prompts[0]
    assert env.lookups == [("org/repo", f"spec/{CHANGE}")]
    assert not (d.problems or d.contradictions)


@pytest.mark.scenario(PARTIAL)
@pytest.mark.parametrize("echo", [False, True])
def test_partial_context_survives_even_when_critic_omits_it(env, echo):
    env.pr_files = {"src/available.py": ["#9 https://github.com/org/repo/pull/9"]}
    env.notes = ["Open PR #9 files incomplete: malformed GitHub page 2.",
                 "Open PR #10 files unavailable: GitHub request failed."]
    env.notes.append(env.notes[0])
    risks = env.notes if echo else []
    agent = Script(env, critics=({"contradictions": [], "risks": risks},))
    d = agent.draft()
    prompt, = agent.prompts("critic")
    assert "src/available.py" in prompt and "#9 https://github.com/org/repo/pull/9" in prompt
    assert "PR context is unavailable or partial; see limitations." in prompt
    assert all(note in prompt for note in env.notes)
    assert d.risks == [f"Critic: {note}" for note in env.notes[:2]]
    assert not (d.problems or d.contradictions)


@pytest.mark.scenario(NOTES)
def test_final_notes_replace_superseded_notes_and_preserve_overlap(env):
    env.planned = {"tests/test_calc.py": ["other-change"]}
    env.notes = ["PR files may be incomplete", "Another context limitation"]
    shared = TASKS.replace("Implement sign", "Implement sign in `tests/test_calc.py`")
    # The existing overlap gate spends one round before feasibility/critic.
    agent = Script(env, bodies=(INITIAL, INITIAL, CORRECTED), tasks=shared,
                   feasibility=({"contradictions": [], "risks": ["Superseded feasibility note"]},
                                {"contradictions": [], "risks": ["Final feasibility", "Final feasibility"]}),
                   critics=(BLOCKED, {"contradictions": [], "risks": [
                       "Final critic", "Critic: Final critic", env.notes[0], "Critic: " + env.notes[0]]}))
    d = agent.draft()
    expected = ["Final feasibility", "Final feasibility", "Critic: Final critic",
                "Critic: PR files may be incomplete", "Critic: Another context limitation",
                "task 1.1: `tests/test_calc.py` is also being changed by other-change; "
                "put these tests in a new file, so the two may conflict on merge"]
    assert not (d.problems or d.contradictions)
    assert d.risks == expected
    document = json.loads((env.out / "draft.json").read_text())
    assert document["risks"] == expected and document["commit"] == d.commit
    assert set(document) == {"change", "work", "problems", "questions", "contradictions", "risks", "commit", "branch"}
    assert env.events == ["write", "lint", "write", "lint", "feasibility", "lookup", "critic",
                          "write", "lint", "feasibility", "critic"]


@pytest.mark.parametrize("case,rounds,writes,reads", [
    ("persistent", 4, 2, 2), ("exhausted", 1, 1, 1), ("unchanged", 4, 2, 1),
])
def test_critic_correction_is_bounded_and_never_clears_unresolved_findings(env, case, rounds, writes, reads):
    agent = Script(env, bodies=(INITIAL, None if case == "unchanged" else CORRECTED),
                   feasibility=(PASS, PASS), critics=(BLOCKED, BLOCKED))
    d = agent.draft(rounds=rounds)
    assert len(agent.prompts("write")) == writes and len(agent.prompts("critic")) == reads
    assert d.contradictions == FINDINGS and not d.problems
    assert d.commit == agent.snapshots[-1][1]
    assert json.loads((env.out / "draft.json").read_text())["contradictions"] == FINDINGS


@pytest.mark.parametrize("gate", ["lint", "feasibility", "feasibility-error", "questions", "drafter-error"])
def test_failed_correction_stops_at_earlier_gate(env, gate):
    body = CORRECTED
    if gate == "questions":
        body = agents.Result("ok", {"status": "needs_input", "questions": ["Which sign convention?"]})
    elif gate == "drafter-error":
        body = agents.Result("infra", None, detail="drafter crashed")
    feasibility = agents.Result("infra", None, detail="review crashed") if gate == "feasibility-error" else BLOCKED
    agent = Script(env, bodies=(INITIAL, body), feasibility=(PASS, feasibility), critics=(BLOCKED,))
    if gate == "lint":
        original = agent.tasks

        def bodies():
            yield INITIAL
            agent.tasks = original.replace("calc/sign/zero", "calc/sign/unknown")
            yield CORRECTED

        agent.bodies = bodies()
    d = agent.draft()
    assert len(agent.prompts("write")) == 2 and len(agent.prompts("critic")) == 1
    if gate == "questions":
        assert d.questions == ["Which sign convention?"]
    elif gate == "feasibility":
        assert d.contradictions == FINDINGS
    else:
        assert d.problems
    assert env.events.count("feasibility") == (2 if gate.startswith("feasibility") else 1)


@pytest.mark.parametrize("failure", [agents.Result("infra", None, detail="critic crashed"), agents.Result("ok", {})])
def test_critic_failure_stops_without_redrafting(env, failure):
    agent = Script(env, critics=(failure,))
    d = agent.draft()
    assert len(agent.prompts("write")) == 1
    assert d.problems[0].startswith("critic review failed: ")
    assert "error" in json.loads((env.out / "critic-1/critic.json").read_text())


@pytest.mark.parametrize("gate", ["lint", "feasibility", "feasibility-error", "questions"])
def test_context_lookup_is_lazy_until_all_earlier_gates_pass(env, gate):
    body = agents.Result("ok", {"status": "needs_input", "questions": ["Which convention?"]}) if gate == "questions" else INITIAL
    review = agents.Result("infra", None, detail="review crashed") if gate == "feasibility-error" else BLOCKED
    agent = Script(env, bodies=(body,), feasibility=(review,), critics=(),
                   tasks=TASKS.replace("calc/sign/zero", "calc/sign/unknown") if gate == "lint" else TASKS)
    d = agent.draft(rounds=1)
    assert d.problems or d.questions or d.contradictions
    assert not env.lookups and not agent.prompts("critic")

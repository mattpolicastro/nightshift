"""Proposal integration with real Git, review extraction, and offline agents."""

import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from nightshift.cli import main

from specloop import agents, cli, critic, manifest, openspec, propose, state


CLEAN = "spec-critic/ordered-proposal-gates/clean-draft-receives-the-extra-review"
CORRECTION = "spec-critic/single-critic-correction/blocking-finding-triggers-one-redraft"
BOTH = "spec-critic/concurrent-file-context/both-sources-reach-the-critic"
PARTIAL = "spec-critic/concurrent-file-context/partial-lookup-remains-visible"
NOTES = "spec-critic/notes-through-existing-risks/final-notes-replace-superseded-notes"
EARLIER = "spec-critic/ordered-proposal-gates/earlier-gate-prevents-critic"
FAILURE = "spec-critic/independent-checklist-review/review-failure-is-not-a-pass"
PERSISTENT = "spec-critic/single-critic-correction/persistent-findings-reject"
EXHAUSTED = "spec-critic/single-critic-correction/no-remaining-attempt-rejects"
UNCHANGED = "spec-critic/single-critic-correction/unchanged-correction-keeps-findings"
FAILED_GATE = "spec-critic/single-critic-correction/correction-fails-an-earlier-gate"
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
INFEASIBLE = {"contradictions": [
    {"task": "1.1", "problem": "Required helper is frozen", "evidence": "manifest.toml:9"},
], "risks": []}
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


@pytest.fixture
def invoke_cli(env, monkeypatch, capfd):
    """Run real drafting and CLI rejection, capturing the result before serialization."""
    real_draft = propose.draft
    notifications = []
    monkeypatch.setattr(cli, "load_env", lambda path: {})
    monkeypatch.setattr(cli, "notifier", lambda secrets: notifications.append)
    monkeypatch.setattr(manifest, "template", lambda path, **kw: (
        replace(env.m, change=kw["change"], branch=kw["branch"]), ""))

    def invoke(agent, *, rounds=4, questions=False):
        drafts = []

        def draft(source, base, change, idea, sources, m, out, **kwargs):
            env.out = out
            result = real_draft(source, base, change, idea, sources, m, out,
                                rounds=rounds, **kwargs)
            drafts.append(result)
            return result

        monkeypatch.setattr(propose, "draft", draft)
        monkeypatch.setattr(agents, "run", agent)
        refs = git(env.source, "show-ref")
        code = main(["propose", "unused.toml", "--change", CHANGE,
                         "--branch", f"spec/{CHANGE}", "--idea", "Define sign", "--json"])
        captured = capfd.readouterr()
        d, = drafts  # Do not mistake a caught exception for a domain rejection.
        assert code == (2 if questions else 1), captured.err
        document = json.loads(captured.out)
        assert document == {
            "version": 1, "outcome": "needs_input" if questions else "rejected",
            "branch": d.branch, "commit": d.commit, "questions": d.questions,
            "contradictions": d.contradictions, "risks": d.risks, "problems": d.problems,
        }
        assert git(env.source, "show-ref") == refs  # No proposal branch was published.
        assert len(notifications) == (1 if questions else 0)
        return d

    return invoke


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
        assert kind == "critic" or call.prompt.startswith("# Feasibility check")
        self.env.events.append(kind)
        self.snapshots.append((kind, git(self.work, "rev-parse", "HEAD"),
                               (call.cwd / DIRECTORY / "design.md").read_text()))
        result = next(self.critics if kind == "critic" else self.feasibility)
        if isinstance(result, Exception):
            raise result
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
    pytest.param("persistent", 4, 2, 2, marks=pytest.mark.scenario(PERSISTENT)),
    pytest.param("exhausted", 1, 1, 1, marks=pytest.mark.scenario(EXHAUSTED)),
    pytest.param("unchanged", 4, 2, 1, marks=pytest.mark.scenario(UNCHANGED)),
])
def test_critic_correction_is_bounded_and_never_clears_unresolved_findings(
        env, invoke_cli, case, rounds, writes, reads):
    agent = Script(env, bodies=(INITIAL, None if case == "unchanged" else CORRECTED),
                   feasibility=(PASS, PASS), critics=(BLOCKED, BLOCKED))
    d = invoke_cli(agent, rounds=rounds)
    assert len(agent.prompts("write")) == writes and len(agent.prompts("critic")) == reads
    assert writes <= rounds
    assert d.contradictions == FINDINGS and not d.problems
    assert d.commit == agent.snapshots[-1][1]
    assert json.loads((env.out / "draft.json").read_text())["contradictions"] == FINDINGS
    expected = ["write", "lint", "feasibility", "lookup", "critic"]
    if writes == 2:
        expected += ["write", "lint"]
        for finding in FINDINGS:
            assert all(value in agent.prompts("write")[1] for value in finding.values())
    if reads == 2:
        expected += ["feasibility", "critic"]
        assert d.commit != agent.snapshots[0][1]
        assert git(d.work, "rev-parse", f"{d.commit}^") == agent.snapshots[0][1]
    else:
        assert d.commit == agent.snapshots[0][1]
        assert not (env.out / "feasibility-2").exists()
        assert not (env.out / "critic-2").exists()
    assert env.events == expected
    assert env.lookups == [("org/repo", f"spec/{CHANGE}")]
    assert git(d.work, "rev-parse", "HEAD") == d.commit
    assert git(d.work, "status", "--porcelain") == ""


@pytest.mark.scenario(FAILED_GATE)
@pytest.mark.parametrize("gate", ["lint", "feasibility", "feasibility-error", "questions", "drafter-error"])
def test_failed_correction_stops_at_earlier_gate(env, invoke_cli, gate):
    body = CORRECTED
    if gate == "questions":
        body = agents.Result("ok", {"status": "needs_input", "questions": ["Which sign convention?"]})
    elif gate == "drafter-error":
        body = agents.Result("infra", None, detail="drafter crashed")
    feasibility = agents.Result("infra", None, detail="review crashed") if gate == "feasibility-error" else INFEASIBLE
    agent = Script(env, bodies=(INITIAL, body), feasibility=(PASS, feasibility), critics=(BLOCKED,))
    if gate == "lint":
        original = agent.tasks

        def bodies():
            yield INITIAL
            agent.tasks = original.replace("calc/sign/zero", "calc/sign/unknown")
            yield CORRECTED

        agent.bodies = bodies()
    d = invoke_cli(agent, questions=gate == "questions")
    assert len(agent.prompts("write")) == 2 and len(agent.prompts("critic")) == 1
    if gate == "questions":
        assert d.questions == ["Which sign convention?"]
    elif gate == "feasibility":
        assert d.contradictions == INFEASIBLE["contradictions"]
        assert d.commit != agent.snapshots[0][1]
    else:
        assert d.problems
        if gate == "lint":
            assert "task 1.1: unknown scenario `calc/sign/unknown`" in d.problems
        elif gate == "feasibility-error":
            assert d.problems == ["feasibility review failed: infra: review crashed"]
        else:
            assert d.problems == ["drafting session failed: infra drafter crashed"]
    assert env.events.count("feasibility") == (2 if gate.startswith("feasibility") else 1)
    expected = ["write", "lint", "feasibility", "lookup", "critic", "write"]
    if gate not in ("questions", "drafter-error"):
        expected.append("lint")
    if gate.startswith("feasibility"):
        expected.append("feasibility")
    assert env.events == expected
    assert env.lookups == [("org/repo", f"spec/{CHANGE}")]
    assert not (env.out / "critic-2").exists()


@pytest.mark.scenario(FAILURE)
@pytest.mark.parametrize("failure,diagnostic", [
    (agents.Result("infra", None, detail="critic crashed"), "infra: critic crashed"),
    (agents.Result("usage_limit", PASS, detail="quota exhausted"), "usage_limit: quota exhausted"),
    (agents.Result("ok", None), "expected object"),
    (agents.Result("ok", {}), "missing contradictions"),
    (agents.Result("ok", {"contradictions": "none", "risks": []}), "expected array"),
    (agents.Result("ok", {"contradictions": [{"task": "1.1", "problem": "bad"}], "risks": []}), "missing evidence"),
    (agents.Result("ok", {**PASS, "verdict": "pass"}), "unexpected fields"),
    (RuntimeError("critic exploded"), "RuntimeError: critic exploded"),
])
@pytest.mark.parametrize("on_correction", [False, True])
def test_critic_failure_stops_without_redrafting(env, invoke_cli, failure, diagnostic, on_correction):
    agent = Script(env, bodies=(INITIAL, CORRECTED), feasibility=(PASS, PASS),
                   critics=(BLOCKED, failure) if on_correction else (failure,))
    d = invoke_cli(agent)
    attempts = 2 if on_correction else 1
    assert len(agent.prompts("write")) == attempts
    assert len(agent.prompts("critic")) == attempts
    assert d.problems[0].startswith("critic review failed: ")
    review_dir = env.out / f"critic-{attempts}"
    result = json.loads((review_dir / "critic.json").read_text())
    assert "error" in result and diagnostic in result["error"]
    assert d.problems == [f"critic review failed: {result['error']}"]
    assert not (review_dir / "checkout").exists()
    assert env.events == (["write", "lint", "feasibility", "lookup", "critic"]
                          + (["write", "lint", "feasibility", "critic"] if on_correction else []))
    assert env.lookups == [("org/repo", f"spec/{CHANGE}")]


@pytest.mark.scenario(EARLIER)
@pytest.mark.parametrize("gate", ["lint", "feasibility", "feasibility-error", "questions"])
def test_context_lookup_is_lazy_until_all_earlier_gates_pass(env, invoke_cli, gate):
    body = agents.Result("ok", {"status": "needs_input", "questions": ["Which convention?"]}) if gate == "questions" else INITIAL
    review = agents.Result("infra", None, detail="review crashed") if gate == "feasibility-error" else BLOCKED
    agent = Script(env, bodies=(body,), feasibility=(review,), critics=(),
                   tasks=TASKS.replace("calc/sign/zero", "calc/sign/unknown") if gate == "lint" else TASKS)
    d = invoke_cli(agent, rounds=4 if gate in ("questions", "feasibility-error") else 1,
                   questions=gate == "questions")
    assert d.problems or d.questions or d.contradictions
    assert not env.lookups and not agent.prompts("critic")
    expected = ["write"]
    if gate == "questions":
        assert d.questions == ["Which convention?"]
    else:
        expected.append("lint")
        if gate == "lint":
            assert "task 1.1: unknown scenario `calc/sign/unknown`" in d.problems
        else:
            expected.append("feasibility")
            if gate == "feasibility-error":
                assert d.problems == ["feasibility review failed: infra: review crashed"]
            else:
                assert d.contradictions == FINDINGS
    assert env.events == expected
    assert not list(env.out.glob("critic-*"))


@pytest.mark.scenario(EARLIER)
@pytest.mark.scenario(EXHAUSTED)
@pytest.mark.parametrize("gate", ["lint", "feasibility"])
def test_earlier_gate_retry_uses_last_attempt_before_critic_blocker(env, invoke_cli, gate):
    agent = Script(env, bodies=(INITIAL, CORRECTED), critics=(BLOCKED,),
                   feasibility=(INFEASIBLE, PASS) if gate == "feasibility" else (PASS,))
    if gate == "lint":
        def bodies():
            agent.tasks = TASKS.replace("calc/sign/zero", "calc/sign/unknown")
            yield INITIAL
            agent.tasks = TASKS
            yield CORRECTED

        agent.bodies = bodies()
    d = invoke_cli(agent, rounds=2)
    assert len(agent.prompts("write")) == 2
    feedback = agent.prompts("write")[1]
    if gate == "lint":
        assert "unknown scenario `calc/sign/unknown`" in feedback
        assert "scenario `calc/sign/zero` is not covered by any task" in feedback
    else:
        for finding in INFEASIBLE["contradictions"]:
            assert all(value in feedback for value in finding.values())
    assert "## Spec critic feedback" not in feedback
    assert env.events == (["write", "lint"] + (["feasibility"] if gate == "feasibility" else [])
                          + ["write", "lint", "feasibility", "lookup", "critic"])
    assert env.lookups == [("org/repo", f"spec/{CHANGE}")]
    assert d.contradictions == FINDINGS and not (d.problems or d.questions)
    assert d.commit == agent.snapshots[-1][1]
    assert agent.snapshots[-1][2] == CORRECTED
    assert not (env.out / "critic-1").exists()
    assert json.loads((env.out / "critic-2/critic.json").read_text()) == BLOCKED
    assert json.loads((env.out / "draft.json").read_text())["contradictions"] == FINDINGS

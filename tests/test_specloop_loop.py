"""The loop end to end against real git and sandbox-exec, with a scripted agent."""

import subprocess
from dataclasses import replace

import pytest

from nightshift.cli import main

from specloop import agents, cli, loop, manifest, openspec, quota, seal, state, verify
from specloop.state import Run

TASKS = """\
# Tasks

## 1. Core

- [ ] 1.1 Write hello covering `cap/req/hello`
- [ ] 1.2 Write world

## 2. Docs

- [ ] 2.1 Write docs
"""


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path / "specloop-home")
    monkeypatch.setattr(quota, "codex_limits", lambda home, timeout=20: None)
    monkeypatch.setattr(openspec, "scenarios", lambda repo, change: {"cap/req/hello": "Hello — it says hello"})
    src = tmp_path / "source"
    (src / "openspec" / "changes" / "c").mkdir(parents=True)
    (src / "openspec" / "changes" / "c" / "tasks.md").write_text(TASKS)
    (src / "openspec" / "changes" / "c" / "design.md").write_text("design\n")
    sh(tmp_path, "git", "init", "-q", "-b", "spec/c", str(src))
    sh(src, "git", "add", "-A")
    sh(src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    origin = tmp_path / "origin.git"
    sh(tmp_path, "git", "init", "-q", "--bare", str(origin))
    sh(src, "git", "remote", "add", "origin", str(origin))
    sh(src, "git", "push", "-q", "origin", "HEAD:refs/heads/main")
    data = {
        "run": {"repo": str(src), "change": "c", "branch": "spec/c"},
        "roles": {"implement": "p", "review": "p"},
        "profiles": {"p": {"adapter": "codex", "model": "m"}},
        "paths": {"allowed": ["out/**"], "frozen": ["openspec/**"]},
        "verify": [{"id": "has-content", "run": ["/bin/sh", "-c", "! grep -rq BAD {src}/out"], "expect": 0}],
        "limits": {"max_attempts": 2, "max_infra": 1},
        "gates": {"phases": [1]},
    }
    run = Run.for_change("c")
    run.dir.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(run.dir / "repo.git")], check=True)
    run.save()
    return run, manifest.parse(data), src


class Script:
    """Scripted agent: a queue of behaviours, one per session."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, call):
        self.calls.append(call)
        return self.steps.pop(0)(call)


def write(name, body="ok\n"):
    def act(call):
        (call.cwd / "out").mkdir(exist_ok=True)
        (call.cwd / "out" / name).write_text(body)
        return agents.Result("ok", {"status": "ready", "summary": "done", "subtasks": []})
    return act


def verdict(v, findings=(), needs_human=()):
    return lambda call: agents.Result("ok", {"verdict": v, "prior": [], "findings": list(findings),
                                             "needs_human": list(needs_human)})


def ctx_for(run, m, agent, notes=None):
    return loop.Ctx(run, m, {}, (notes.append if notes is not None else lambda t: None), agent)


def branch_tasks(run):
    return sh(run.dir / "repo.git", "git", "show", "spec/c:openspec/changes/c/tasks.md")


def test_happy_path_lands_each_task_and_stops_at_the_gate(env):
    run, m, _ = env
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"))
    assert loop.run(ctx_for(run, m, a)) == "paused"
    assert run.state.pause.reason == "phase_gate" and run.state.pause.task == "section:1"
    text = branch_tasks(run)
    assert "- [x] 1.1" in text and "- [x] 1.2" in text and "- [ ] 2.1" in text
    tree = sh(run.dir / "repo.git", "git", "ls-tree", "-r", "--name-only", "spec/c")
    assert "out/hello" in tree and "out/world" in tree
    implement, review_call = a.calls[0], a.calls[1]
    assert implement.access == "write" and review_call.access == "read"
    assert (review_call.cwd.parent / "context" / "diff.patch").read_text().count("out/hello")


@pytest.mark.scenario("verify-preparation/prepare-steps/dependencies-ready-for-sandboxed-tests")
def test_loop_counts_prepare_receipts_for_task_coverage_and_acceptance(env):
    from dataclasses import replace

    run, m, _ = env
    m = replace(m, prepare=(manifest.Step("dependencies", ("/usr/bin/touch", "dependency")),),
                verify=m.verify + (manifest.Step("dependency-ready", ("/bin/test", "-f", "dependency")),),
                acceptance=(manifest.Step("accept-dependency", ("/bin/test", "-f", "dependency")),),
                coverage_step="has-content", coverage_exempt={"cap/req/hello": "fixture"})
    run.state.gates_passed.append(1)
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), write("docs"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    assert run.state.pause.reason == "ready_to_publish"
    assert all(t.done for t in loop.tasks.parse(branch_tasks(run)))


@pytest.mark.scenario("verify-preparation/prepare-steps/failing-prepare-fails-verification")
def test_loop_prepare_failure_prevents_review_and_landing(env):
    from dataclasses import replace

    run, m, _ = env
    m = replace(m, prepare=(manifest.Step("dependencies", ("/bin/sh", "-c", "exit 3")),))
    a = Script(write("hello"), write("hello"))
    loop.run(ctx_for(run, m, a))
    assert run.state.pause.reason == "stuck"
    assert len(a.calls) == 2 and all(call.access == "write" for call in a.calls)
    assert "- [ ] 1.1" in branch_tasks(run)
    feedback = (a.calls[1].cwd.parent / "context" / "feedback.md").read_text()
    assert "dependencies" in feedback and "got 3" in feedback


def test_verify_failure_feeds_the_retry_and_seeds_its_diff(env):
    run, m, _ = env
    seen = {}

    def second(call):
        seen["feedback"] = (call.cwd.parent / "context" / "feedback.md").read_text()
        seen["seeded"] = (call.cwd / "out" / "hello").read_text()
        return write("hello", "fixed\n")(call)

    a = Script(write("hello", "BAD\n"), second, verdict("PASS"), write("world"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    assert "has-content" in seen["feedback"] and seen["seeded"] == "BAD\n"
    assert run.state.pause.reason == "phase_gate"


@pytest.fixture(params=[
    pytest.param("[showing the last 2000 of 4321 bytes; full log: task/a1/verify/check.log]\n"
                 + ("```\n<html>\n  diagnostic\n" + "x" * 2000)[:2000], id="marked",
                 marks=pytest.mark.scenario("verify-output/retry-feedback-preserves-receipt-output/retry-retains-marker-and-payload")),
    *[pytest.param(tail, id=name, marks=pytest.mark.scenario(
        "verify-output/retry-feedback-preserves-receipt-output/unmarked-tails-pass-through"))
      for name, tail in [("empty", ""), ("1501", "a" * 1501),
                         ("2000", " \n" + "b" * 1996 + "\t\n")]],
])
def receipt_tail(request):
    return request.param


def test_verification_summary_preserves_opaque_tail(receipt_tail):
    passed = verify.Receipt("ok", [], 0, 0, True, 0.1, "successful output")
    failed = verify.Receipt("check", [], 0, 1, False, 0.2, receipt_tail)
    later = verify.Receipt("later", [], 0, None, False, 0.3, "not the first failure")
    status = "- ok: expected exit 0, got 0 (pass, 0.1s)"
    assert verify.summary([passed]) == status
    assert verify.summary([passed, failed, later]) == (
        status + "\n- check: expected exit 0, got 1 (FAIL, 0.2s)"
        "\n- later: expected exit 0, got timeout (FAIL, 0.3s)"
        f"\n\nOutput tail of `check`:\n```\n{receipt_tail}\n```")


def test_retry_feedback_preserves_receipt_tail(env, monkeypatch, receipt_tail):
    run, m, _ = env
    receipt = verify.Receipt("check", [], 0, 1, False, 0.2, receipt_tail)

    def failing(repo, sha, steps, m, out, *, run_dir=None):
        assert run_dir == run.dir
        return [receipt]

    monkeypatch.setattr(verify, "run_steps", failing)
    agent = Script(write("hello"), write("hello"))
    loop.run(ctx_for(run, m, agent))
    assert run.state.pause.reason == "stuck" and len(agent.calls) == 2
    retry = agent.calls[1]
    feedback_path = retry.cwd.parent / "context" / "feedback.md"
    assert str(feedback_path) in retry.prompt
    assert feedback_path.read_text() == (
        "The previous attempt's diff is already applied to your workspace; correct it.\n\n"
        "The harness's verification failed:\n" + verify.summary([receipt]) + "\n")
    assert f"\n```\n{receipt_tail}\n```\n" in feedback_path.read_text()
    assert feedback_path.read_text().count("[showing the last") == receipt_tail.count("[showing the last")


def test_review_fail_then_pass_and_followups(env):
    run, m, _ = env
    block = {"blocking": True, "basis": "scenario:cap/req/hello", "text": "does not greet"}
    nit = {"blocking": True, "basis": "style", "text": "rename it"}
    a = Script(write("hello"), verdict("FAIL", [block, nit]), write("hello", "better\n"), verdict("PASS"),
               write("world"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    assert "does not greet" in (a.calls[2].cwd.parent / "context" / "feedback.md").read_text()
    assert "rename it" in (run.dir / "followups.md").read_text()
    assert "PRIOR" not in a.calls[3].prompt and "attempt 2" in a.calls[3].prompt


def test_stuck_pauses_and_resume_with_note_continues(env, monkeypatch):
    run, m, _ = env
    notes = []
    a = Script(write("hello", "BAD\n"), write("hello", "BAD\n"))
    loop.run(ctx_for(run, m, a, notes))
    assert run.state.pause.reason == "stuck" and "unstick" in notes[-1].text()
    key = run.state.pause.task
    b = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"))
    monkeypatch.setattr(cli, "_ctx", lambda r: ctx_for(r, m, b))
    assert main(["resume", "c", "--note", "write ok"]) == 0
    assert "write ok" in (b.calls[0].cwd.parent / "context" / "feedback.md").read_text()
    assert Run.for_change("c").state.tasks[key].status == "passed"


def test_resume_past_the_gate_finishes(env, monkeypatch):
    run, m, _ = env
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), write("docs"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    monkeypatch.setattr(cli, "_ctx", lambda r: ctx_for(r, m, a))
    main(["resume", "c"])
    st = Run.for_change("c").state
    assert st.pause.reason == "ready_to_publish" and "- [x] 2.1" in branch_tasks(run)


def test_too_big_splits_the_task(env):
    run, m, _ = env
    split = lambda call: agents.Result("ok", {"status": "too_big", "summary": "", "subtasks": ["part one", "part two"]})
    a = Script(split, write("one"), verdict("PASS"), write("two"), verdict("PASS"), write("world"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    text = branch_tasks(run)
    assert "- [x] 1.1.1 part one" in text and "- [x] 1.1.2 part two" in text and "Write hello" not in text


def test_infra_is_retried_uncounted_then_pauses(env):
    run, m, _ = env
    down = lambda call: agents.Result("infra", detail="idle timeout")
    a = Script(down, down)
    loop.run(ctx_for(run, m, a))
    ts = run.state.tasks[run.state.pause.task]
    assert run.state.pause.reason == "infra" and ts.attempts == 0 and ts.infra == 2


def test_usage_limit_pauses_immediately(env):
    run, m, _ = env
    loop.run(ctx_for(run, m, Script(lambda c: agents.Result("usage_limit", detail="429"))))
    assert run.state.pause.reason == "usage_limit"


def test_invalid_review_reruns_only_the_review(env):
    run, m, _ = env
    bad = lambda call: agents.Result("invalid_output", detail="$.verdict: missing")
    a = Script(write("hello"), bad, verdict("PASS"), write("world"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    assert [c.access for c in a.calls] == ["write", "read", "read", "write", "read"]


def test_repeated_violation_pauses_but_a_self_tick_does_not(env):
    run, m, _ = env

    def tick_and_stray(call):
        (call.cwd / "openspec" / "changes" / "c" / "tasks.md").write_text(TASKS.replace("[ ] 1.1", "[x] 1.1"))
        (call.cwd / "stray.txt").write_text("x")
        return write("hello", "BAD\n")(call)

    loop.run(ctx_for(run, m, Script(tick_and_stray, tick_and_stray)))
    assert run.state.pause.reason == "violation_repeat"
    assert "stray.txt" in run.state.pause.detail and "tasks.md" not in run.state.pause.detail


def test_a_seal_failure_pauses_instead_of_crash_looping(env, monkeypatch):
    run, m, _ = env
    monkeypatch.setattr(seal, "MAX_TOTAL", 4)
    a = Script(write("hello", "far too big\n"))
    assert loop.run(ctx_for(run, m, a)) == "paused"
    assert run.state.pause.reason == "seal" and "cap" in run.state.pause.detail and len(a.calls) == 1


def test_needs_human_pauses(env):
    run, m, _ = env
    block = {"blocking": True, "basis": "task", "text": "ambiguous"}
    loop.run(ctx_for(run, m, Script(write("hello"), verdict("FAIL", [block], ["spec contradicts itself"]))))
    assert run.state.pause.reason == "needs_human" and "contradicts" in run.state.pause.detail


def test_a_branch_that_moved_is_an_uncounted_reattempt(env):
    run, m, src = env

    def race(call):
        repo = run.dir / "repo.git"
        tree = sh(repo, "git", "rev-parse", "spec/c^{tree}")
        moved = sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", tree, "-p", "spec/c", "-m", "human")
        sh(repo, "git", "update-ref", "refs/heads/spec/c", moved)
        return verdict("PASS")(call)

    a = Script(write("hello"), race, write("hello"), verdict("PASS"), write("world"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    key = next(k for k, t in run.state.tasks.items() if t.status == "passed")
    assert run.state.tasks[key].attempts == 0 and run.state.pause.reason == "phase_gate"


def test_admission_refuses_unknown_scenarios(env, tmp_path, monkeypatch):
    run, m, src = env
    monkeypatch.setattr(openspec, "validate", lambda repo, change: [])
    monkeypatch.setattr(openspec, "scenarios", lambda repo, change: {})
    problems = cli.problems_with(run.dir / "repo.git", "spec/c", m)
    assert any("unknown scenario `cap/req/hello`" in p for p in problems)


def with_coverage(m, exempt=None, acceptance=None):
    from dataclasses import replace

    listing = manifest.Step("scenarios", ("/bin/sh", "-c", "cat {src}/out/* 2>/dev/null; true"), 0, 10)
    return replace(m, verify=m.verify + (listing,), coverage_step="scenarios",
                   coverage_exempt=exempt or {}, acceptance=acceptance or ())


def test_coverage_gap_appends_one_round_then_publishes(env, monkeypatch):
    run, m, _ = env
    monkeypatch.setattr(openspec, "scenarios", lambda repo, change: {"cap/req/hello": "", "cap/req/rack-only": ""})
    m = with_coverage(m, exempt={"cap/req/rack-only": "human: load it in Rack"})
    run.state.gates_passed.append(1)
    a = Script(write("hello", "no tag\n"), verdict("PASS"), write("world"), verdict("PASS"), write("docs"), verdict("PASS"),
               write("tagged", "cap/req/hello\n"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    text = branch_tasks(run)
    assert "## 3. Coverage" in text and "`cap/req/hello`" in text.split("## 3. Coverage")[1]
    assert run.state.pause.reason == "ready_to_publish" and "load it in Rack" in run.state.pause.detail


def test_coverage_gap_twice_pauses(env, monkeypatch):
    run, m, _ = env
    m = with_coverage(m)
    run.state.gates_passed.append(1)
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), write("docs"), verdict("PASS"),
               write("still-untagged"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    assert run.state.pause.reason == "coverage" and "cap/req/hello" in run.state.pause.detail


def test_acceptance_failure_appends_a_fix_task(env):
    run, m, _ = env
    accept = (manifest.Step("has-final", ("/bin/test", "-f", "{src}/out/final"), 0, 10),)
    m = with_coverage(m, acceptance=accept)
    from dataclasses import replace
    m = replace(m, coverage_step="")
    run.state.gates_passed.append(1)
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), write("docs"), verdict("PASS"),
               write("final"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    assert "## 3. Acceptance fixes" in branch_tasks(run) and "has-final" in branch_tasks(run)
    assert run.state.pause.reason == "ready_to_publish"


def test_publish_then_finish(env, monkeypatch, tmp_path):
    run, m, src = env
    m = replace(m, github="o/r")
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), write("docs"), verdict("PASS"))
    run.state.gates_passed.append(1)
    loop.run(ctx_for(run, m, a))
    assert run.state.pause.reason == "ready_to_publish"
    github = tmp_path / "github.git"
    subprocess.run(["git", "init", "-q", "--bare", str(github)], check=True)
    # Publish now fetches the PR base from this separate publication remote.
    sh(src, "git", "push", "-q", str(github), "HEAD:refs/heads/main")
    calls = []

    def gh(*args):
        calls.append(args)
        if args[:2] == ("api", "repos/o/r/pulls"):
            return "[]"
        return "https://github.com/o/r/pull/7" if args[:2] == ("pr", "create") else ""

    def archive(repo, change, all_tasks_done):
        assert all_tasks_done
        (repo / "openspec" / "specs").mkdir(parents=True, exist_ok=True)
        (repo / "openspec" / "specs" / "archived.md").write_text("x\n")
        return {}

    monkeypatch.setattr(cli, "_ctx", lambda r: ctx_for(r, m, a))
    monkeypatch.setattr(cli, "_remote", lambda ctx, target=None: str(github))
    monkeypatch.setattr(cli, "_gh", gh)
    monkeypatch.setattr(openspec, "archive", archive)
    assert main(["publish", "c"]) == 0
    st = Run.for_change("c").state
    assert st.pause.reason == "human_checks" and st.pr.endswith("/pull/7")
    assert sh(github, "git", "rev-parse", "spec/c") == sh(run.dir / "repo.git", "git", "rev-parse", "spec/c")
    assert calls[0][:2] == ("api", "repos/o/r/pulls")
    assert calls[1][:2] == ("pr", "create") and "--draft" in calls[1]
    assert main(["finish", "c"]) == 0
    assert Run.for_change("c").state.status == "done"
    assert "archived.md" in sh(github, "git", "ls-tree", "-r", "--name-only", "spec/c")
    assert calls[-1][:2] == ("pr", "ready")


def test_publish_refuses_before_ready(env):
    assert main(["publish", "c"]) == 1


def with_chain(m):
    from dataclasses import replace

    a, b = manifest.Profile("a", "codex", "m1"), manifest.Profile("b", "claude", "m2")
    return replace(m, roles={"implement": a, "review": a}, chains={"implement": (a, b), "review": (a, b)})


def by_profile(**behaviours):
    """Dispatch on the profile that was picked for the call."""
    calls = []

    def agent(call):
        calls.append(call.profile.name)
        return behaviours[call.profile.name].pop(0)(call)
    agent.calls = calls
    return agent


def test_usage_limit_falls_back_to_the_next_profile(env):
    run, m, _ = env
    limited = lambda call: agents.Result("usage_limit", detail="429")
    agent = by_profile(a=[limited], b=[write("hello"), verdict("PASS"), write("world"), verdict("PASS")])
    loop.run(ctx_for(run, with_chain(m), agent))
    assert agent.calls[:3] == ["a", "b", "b"] and run.state.pause.reason == "phase_gate"
    assert run.state.exhausted["a"] > __import__("time").time()


def test_every_profile_limited_pauses_with_a_resume_time(env):
    run, m, _ = env
    limited = lambda call: agents.Result("usage_limit", detail="429")
    loop.run(ctx_for(run, with_chain(m), by_profile(a=[limited], b=[limited])))
    p = run.state.pause
    assert p.reason == "usage_limit" and p.resume_at > __import__("time").time()


def test_a_profile_whose_window_reset_is_used_again(env):
    run, m, _ = env
    run.state.exhausted["a"] = 1.0  # long past
    agent = by_profile(a=[write("hello"), verdict("PASS"), write("world"), verdict("PASS")], b=[])
    loop.run(ctx_for(run, with_chain(m), agent))
    assert agent.calls[0] == "a" and "a" not in run.state.exhausted


def test_manifest_accepts_a_role_chain():
    data = {"run": {"repo": "/r", "change": "c", "branch": "b"},
            "roles": {"implement": ["x", "y"], "review": "y"},
            "profiles": {"x": {"adapter": "codex", "model": "m"}, "y": {"adapter": "claude", "model": "n"}},
            "paths": {"allowed": ["a/**"]}, "verify": [{"id": "t", "run": ["true"]}]}
    m = manifest.parse(data)
    assert [p.name for p in m.chains["implement"]] == ["x", "y"] and m.roles["implement"].name == "x"
    assert [p.name for p in m.chains["review"]] == ["y"]


def test_section_evidence_strengthens_in_place_then_gates_with_evidence(env):
    from dataclasses import replace

    run, m, _ = env
    m = replace(with_coverage(m), evidence=True)
    a = Script(write("hello", "untagged\n"), verdict("PASS"), write("world"), verdict("PASS"),
               write("tag", "cap/req/hello\n"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    text = branch_tasks(run)
    section1 = text.split("## 2.")[0]
    assert "- [x] 1.3 Add tagged tests covering `cap/req/hello`" in section1
    assert run.state.pause.reason == "phase_gate" and "Evidence:" in run.state.pause.detail
    assert run.state.evidenced == [1] and run.state.section_base["1"]


def test_section_evidence_failing_twice_pauses(env):
    from dataclasses import replace

    run, m, _ = env
    m = replace(with_coverage(m), evidence=True)
    a = Script(write("hello", "untagged\n"), verdict("PASS"), write("world"), verdict("PASS"),
               write("still", "untagged\n"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    assert run.state.pause.reason == "gate_evidence" and "cap/req/hello" in run.state.pause.detail



def admission_manifest(tmp_path, src):
    f = tmp_path / "m.toml"
    f.write_text(f"""
[run]
repo = "{src}"
change = "c2"
branch = "spec/c"
[roles]
implement = "p"
review = "p"
[profiles.p]
adapter = "codex"
model = "m"
[paths]
allowed = ["out/**"]
[[verify]]
id = "t"
run = ["true"]
""")
    return f


@pytest.mark.parametrize("contradictions,code", [
    ([{"task": "1.1", "problem": "a frozen checker rejects it", "evidence": "tools/check.py:63"}], 1),
    ([], 0),
])
def test_feasibility_review_gates_admission(env, monkeypatch, tmp_path, capsys, contradictions, code):
    run, m, src = env
    monkeypatch.setattr(cli, "problems_with", lambda repo, rev, m: [])
    seen = []

    def fake(call):
        seen.append(call)
        return agents.Result("ok", {"contradictions": contradictions, "risks": ["task 1.2 is vague"]})

    monkeypatch.setattr(agents, "run", fake)
    assert main(["admit", str(admission_manifest(tmp_path, src))]) == code
    out = capsys.readouterr()
    assert seen and seen[0].access == "read" and "impossible as written" in seen[0].prompt
    assert "risk: task 1.2 is vague" in out.out
    admitted = Run.for_change("c2")
    assert (admitted.dir / "state.json").exists() is (code == 0)
    if code != 0:
        assert list(admitted.dir.iterdir()) == [admitted.dir / "lock"]
    if code:
        assert "tools/check.py:63" in out.err


def test_feasibility_role_is_optional_and_falls_back_to_review():
    base = {"run": {"repo": "/r", "change": "c", "branch": "b"},
            "profiles": {"x": {"adapter": "codex", "model": "m"}, "y": {"adapter": "claude", "model": "n"}},
            "paths": {"allowed": ["a/**"]}, "verify": [{"id": "t", "run": ["true"]}]}
    m = manifest.parse({**base, "roles": {"implement": "x", "review": "y"}})
    assert m.roles["feasibility"].name == "y"
    m = manifest.parse({**base, "roles": {"implement": "x", "review": "y", "feasibility": "x"}})
    assert m.roles["feasibility"].name == "x" and m.roles["review"].name == "y"


def agent_gated(m):
    import dataclasses

    return dataclasses.replace(m, gate_review="agent")


def test_agent_gate_pass_carries_on_through_the_next_section(env):
    run, m, _ = env
    notes = []
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), verdict("PASS"),
               write("docs"), verdict("PASS"))
    assert loop.run(ctx_for(run, agent_gated(m), a, notes)) == "paused"
    assert run.state.pause.reason == "ready_to_publish" and run.state.gates_passed == [1]
    gate = a.calls[4]
    context = gate.cwd.parent / "context"
    assert gate.access == "read" and "Review of section 1: Core" in gate.prompt
    assert "- [x] 1.1" in (context / "section.md").read_text() and "out/world" in (context / "diff.patch").read_text()
    assert any("section 1 passed its gate review" in n.text() for n in notes)


def test_agent_gate_fail_adds_fix_tasks_then_rereviews_with_the_prior_findings(env):
    run, m, _ = env
    block = {"blocking": True, "basis": "integration:hello is written but world never reads it", "text": "connect them"}
    seen = {}

    def second_gate(call):
        seen["prior"] = (call.cwd.parent / "context" / "prior-review.md").read_text()
        seen["prompt"] = call.prompt
        return verdict("PASS")(call)

    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), verdict("FAIL", [block]),
               write("joined"), verdict("PASS"), second_gate, write("docs"), verdict("PASS"))
    assert loop.run(ctx_for(run, agent_gated(m), a)) == "paused"
    text = branch_tasks(run)
    fix = text.index("Fix this finding from section 1's review: connect them")
    assert fix < text.index("## 2. Docs") and "- [x] 1.3" in text
    assert "connect them" in seen["prior"] and "second review" in seen["prompt"]
    assert run.state.gates_passed == [1] and run.state.pause.reason == "ready_to_publish"


def test_agent_gate_failing_twice_pauses_for_a_human(env):
    run, m, _ = env
    block = {"blocking": True, "basis": "scenario:cap/req/hello", "text": "still does not greet"}
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), verdict("FAIL", [block]),
               write("again"), verdict("PASS"), verdict("FAIL", [block]))
    assert loop.run(ctx_for(run, agent_gated(m), a)) == "paused"
    p = run.state.pause
    assert p.reason == "phase_gate" and p.task == "section:1" and "again after a round of fixes" in p.detail
    assert run.state.gates_passed == []


@pytest.mark.parametrize("output, reason", [
    (verdict("FAIL", [{"blocking": True, "basis": "input:x", "text": "t"}], ["the spec contradicts itself"]), "phase_gate"),
    (verdict("FAIL", [{"blocking": True, "basis": "task", "text": "style"}]), "ready_to_publish"),
])
def test_agent_gate_needs_human_pauses_and_unsupported_bases_are_notes(env, output, reason):
    run, m, _ = env
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), output, write("docs"), verdict("PASS"))
    loop.run(ctx_for(run, agent_gated(m), a))
    assert run.state.pause.reason == reason


def test_gate_review_must_be_human_or_agent(env):
    _, m, src = env
    data = {"run": {"repo": str(src), "change": "c", "branch": "spec/c"}, "roles": {"implement": "p", "review": "p"},
            "profiles": {"p": {"adapter": "codex", "model": "m"}}, "paths": {"allowed": ["out/**"]},
            "verify": [{"id": "v", "run": ["/usr/bin/true"]}], "gates": {"phases": [1], "review": "robot"}}
    with pytest.raises(ValueError, match="gates.review"):
        manifest.parse(data)
    data["gates"]["review"] = "agent"
    assert manifest.parse(data).gate_review == "agent" and m.gate_review == "human"


def arbiter_says(decision, reason="r", note=""):
    return lambda call: agents.Result("ok", {"decision": decision, "reason": reason, "note": note})


def arbitrated(m):
    import dataclasses

    return dataclasses.replace(m, stuck="agent")


BLOCK = {"blocking": True, "basis": "input:old receipts from unmerged code", "text": "cannot read old receipts"}


def test_arbiter_accepts_a_verified_candidate_and_keeps_the_findings(env):
    run, m, _ = env
    notes, seen = [], {}

    def arbiter(call):
        seen["history"] = (call.cwd.parent / "context" / "history.md").read_text()
        seen["call"] = call
        return arbiter_says("accept", "the finding needs data only unmerged code wrote")(call)

    a = Script(write("hello"), verdict("FAIL", [BLOCK]), write("hello", "v2\n"), verdict("FAIL", [BLOCK]), arbiter,
               write("world"), verdict("PASS"))
    assert loop.run(ctx_for(run, arbitrated(m), a, notes)) == "paused"
    assert run.state.pause.reason == "phase_gate" and "- [x] 1.1" in branch_tasks(run)
    assert seen["call"].access == "read" and seen["history"].count("cannot read old receipts") >= 2
    assert "overruled by the arbiter" in (run.dir / "followups.md").read_text()
    key = next(k for k, ts in run.state.tasks.items() if ts.arbitrated)
    assert run.state.tasks[key].status == "accepted"
    assert any("arbiter accepted" in n.text() for n in notes)


def test_arbiter_retry_gives_one_more_attempt_with_its_note(env):
    run, m, _ = env
    seen = {}

    def third(call):
        seen["feedback"] = (call.cwd.parent / "context" / "feedback.md").read_text()
        return write("hello", "v3\n")(call)

    a = Script(write("hello"), verdict("FAIL", [BLOCK]), write("hello", "v2\n"), verdict("FAIL", [BLOCK]),
               arbiter_says("retry", "real gap", "handle old receipts by ignoring them"), third, verdict("PASS"),
               write("world"), verdict("PASS"))
    loop.run(ctx_for(run, arbitrated(m), a))
    assert "handle old receipts by ignoring them" in seen["feedback"]
    assert run.state.pause.reason == "phase_gate" and "- [x] 1.1" in branch_tasks(run)


@pytest.mark.parametrize("first, ruling, expect", [
    ("BAD\n", arbiter_says("accept", "looks fine"), "looks fine"),  # unverified: accept is not available
    ("ok\n", arbiter_says("human", "the spec is silent on old receipts"), "spec is silent"),
    ("ok\n", lambda call: agents.Result("invalid_output", {}, detail="garbled"), "could not rule"),
])
def test_arbiter_hands_to_a_human_when_it_cannot_settle_it(env, first, ruling, expect):
    run, m, _ = env
    rev = verdict("FAIL", [BLOCK]) if first == "ok\n" else None
    steps = [write("hello", first)] + ([rev] if rev else []) + [write("hello", first)] + ([rev] if rev else [])
    a = Script(*steps, ruling)
    loop.run(ctx_for(run, arbitrated(m), a))
    p = run.state.pause
    assert p.reason == "stuck" and expect in p.detail
    if first == "BAD\n":
        assert "not available" in a.calls[-1].prompt


def test_a_failed_retry_after_arbitration_goes_straight_to_a_human(env):
    run, m, _ = env
    a = Script(write("hello"), verdict("FAIL", [BLOCK]), write("hello", "v2\n"), verdict("FAIL", [BLOCK]),
               arbiter_says("retry", "real gap", "do it properly"), write("hello", "v3\n"), verdict("FAIL", [BLOCK]))
    loop.run(ctx_for(run, arbitrated(m), a))
    assert run.state.pause.reason == "stuck" and not a.steps


def test_pause_request_stops_at_the_next_task_boundary_and_resume_clears_it(env, monkeypatch):
    run, m, _ = env
    notes = []

    def first(call):
        run.request_pause()  # asked while task 1.1 is being implemented
        return write("hello")(call)

    a = Script(first, verdict("PASS"), write("world"), verdict("PASS"))
    assert loop.run(ctx_for(run, m, a, notes)) == "paused"
    assert run.state.pause.reason == "requested" and "- [x] 1.1" in branch_tasks(run) and "- [ ] 1.2" in branch_tasks(run)
    assert not run.pause_requested() and notes[-1].kind == "fyi"
    run.request_pause()
    monkeypatch.setattr(cli, "_ctx", lambda current: ctx_for(current, m, a, notes))
    cli.resume(type("A", (), {"change": "c", "note": "", "no_run": True})())
    assert not run.pause_requested() and Run.for_change("c").state.status == "running"


def test_pause_command_pauses_an_undriven_run_at_once(env):
    run, _, _ = env
    assert cli.pause_cmd(type("A", (), {"change": "c"})()) == 0
    assert Run.for_change("c").state.pause.reason == "requested"
    assert cli.pause_cmd(type("A", (), {"change": "c"})()) == 1


def test_a_driver_killed_during_verify_reverifies_instead_of_reimplementing(env, monkeypatch):
    from specloop import verify

    run, m, _ = env
    real = verify.run_steps

    def killed(*a, **k):
        raise KeyboardInterrupt  # the driver dies mid-verify

    monkeypatch.setattr(verify, "run_steps", killed)
    with pytest.raises(KeyboardInterrupt):
        loop.run(ctx_for(run, m, Script(write("hello"))))
    monkeypatch.setattr(verify, "run_steps", real)
    again = Run.for_change("c")
    a = Script(verdict("PASS"), write("world"), verdict("PASS"))
    loop.run(ctx_for(again, m, a))
    assert [c.access for c in a.calls] == ["read", "write", "read"]
    assert "- [x] 1.1" in branch_tasks(again) and again.state.pause.reason == "phase_gate"


def test_gate_role_defaults_to_the_review_chain_and_can_be_its_own(env):
    _, m, src = env
    assert m.roles["gate"] == m.roles["review"]
    data = {"run": {"repo": str(src), "change": "c", "branch": "spec/c"},
            "roles": {"implement": "p", "review": "p", "gate": "q"},
            "profiles": {"p": {"adapter": "codex", "model": "m"}, "q": {"adapter": "claude", "model": "opus"}},
            "paths": {"allowed": ["out/**"]}, "verify": [{"id": "v", "run": ["/usr/bin/true"]}]}
    assert manifest.parse(data).roles["gate"].name == "q"


@pytest.mark.parametrize("status, reason, allowed", [
    ("aborted", None, False), ("paused", "stuck", False), ("paused", "secret", False),
    ("paused", "phase_gate", False), ("paused", "ready_to_publish", True), ("paused", "human_checks", True),
    ("running", None, True),
])
def test_revise_never_clears_a_human_stop(env, status, reason, allowed):
    from specloop import revise
    from specloop.state import Pause

    run, m, _ = env
    run.state.status, run.state.pause = status, Pause(reason) if reason else None
    run.save()
    if allowed:
        revise.revise(run, m, "please rename the flag")
        assert Run.for_change("c").state.status == "running"
    else:
        with pytest.raises(ValueError, match="only added to a running run"):
            revise.revise(run, m, "please rename the flag")
        assert Run.for_change("c").state.status == status


@pytest.mark.parametrize("second, hinted", [(0.5, True), (0.6, False)])
def test_a_strengthening_round_that_cannot_move_the_score_says_why(env, monkeypatch, second, hinted):
    from dataclasses import replace

    from specloop import evidence

    run, m, _ = env
    m = replace(m, evidence=True, mutation_steps=("has-content",), mutation_min=0.8)
    scores = iter([0.5, second])

    def gather(*args, **kwargs):
        ev = evidence.Evidence(1)
        score = next(scores)
        killed = int(score * 10)
        ev.mutants = [evidence.Mutant("out/hello", 1, "<", "a", "b", "killed") for _ in range(killed)] + \
                     [evidence.Mutant("out/hello", 1, "<", "a", "b", "survived") for _ in range(10 - killed)]
        return ev

    monkeypatch.setattr(loop.evidence, "gather", gather)
    a = Script(write("hello"), verdict("PASS"), write("world"), verdict("PASS"), write("tests"), verdict("PASS"))
    loop.run(ctx_for(run, m, a))
    p = run.state.pause
    assert p.reason == "gate_evidence"
    assert ("did not improve after a round of strengthening" in p.detail) is hinted

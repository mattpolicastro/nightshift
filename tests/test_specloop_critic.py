import json
import subprocess
from dataclasses import replace

import pytest

from specloop import agents, critic, feasibility, manifest


COMPLETE = "spec-critic/independent-checklist-review/complete-checklist-and-read-contract"
FAILURE = "spec-critic/independent-checklist-review/review-failure-is-not-a-pass"
PASS = {"contradictions": [], "risks": []}
BLOCKER = {"task": "1.1", "problem": "Cannot update a protected assertion", "evidence": "tests/test_calc.py:12"}
M = manifest.parse({
    "run": {"repo": "/unused", "change": "template-name", "branch": "spec/template-name"},
    "roles": {"implement": "review", "review": "review"},
    "profiles": {"review": {"adapter": "codex", "model": "test-model"}},
    "paths": {"allowed": ["calc.py", "tests/**"], "frozen": ["tests/protected.py", "validator.py"]},
    "verify": [{"id": "tests", "run": ["true"]}],
    "limits": {"review_minutes": 3, "idle_minutes": 7},
})


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def candidate(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    change = repo / "openspec/changes/effective-change"
    (change / "specs/calc").mkdir(parents=True)
    for name in ("proposal.md", "design.md", "tasks.md", "specs/calc/spec.md"):
        (change / name).write_text(f"Candidate {name}\n")
    (repo / "calc.py").write_text("candidate = 1\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate")
    rev = git(repo, "rev-parse", "HEAD")
    (repo / "calc.py").write_text("later = 2\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "later")
    (repo / "calc.py").write_text("dirty = 3\n")
    (repo / "untracked.txt").write_text("not in the candidate\n")
    return repo, rev


def check(candidate, out, agent=None, m=M, **context):
    repo, rev = candidate
    return critic.check(repo, rev, m, out, change="effective-change",
                        planned=context.get("planned", {}), pr_files=context.get("pr_files", {}),
                        context_notes=context.get("context_notes", []), agent=agent)


@pytest.mark.scenario(COMPLETE)
@pytest.mark.parametrize("selection", ["feasibility", "review-chain", "review-role"])
def test_read_contract_uses_exact_candidate_and_separate_artifacts(candidate, tmp_path, monkeypatch, selection):
    preferred = manifest.Profile("feasibility-first", "claude", "strong-model")
    review = manifest.Profile("review-first", "codex", "review-model")
    later = manifest.Profile("later", "codex", "fallback-model")
    chains = {
        "feasibility": {"feasibility": (preferred, later), "review": (review, later)},
        "review-chain": {"feasibility": (), "review": (review, later)},
        "review-role": {},
    }[selection]
    m = replace(M, chains=chains)
    expected = preferred if selection == "feasibility" else review if selection == "review-chain" else M.roles["review"]
    homes = tmp_path / "homes"
    monkeypatch.setattr(agents, "homes_dir", lambda: homes)
    out = tmp_path / "attempt/critic-1"
    out.mkdir(parents=True)
    separate = {name: f"existing {name}" for name in ("draft.json", "draft.jsonl", "feasibility.json", "feasibility.jsonl")}
    for name, body in separate.items():
        (out / name).write_text(body)
    calls = []

    def agent(call):
        calls.append(call)
        assert call.profile == expected
        assert call.access == "read"
        assert call.schema is feasibility.SCHEMA
        assert call.wall_seconds == 20 * 60  # Matches feasibility, even if review_minutes differs.
        assert call.idle_seconds == 7 * 60
        assert call.home == homes / expected.name
        assert call.cwd == out / "checkout"
        assert (call.cwd / "calc.py").read_text() == "candidate = 1\n"
        assert not (call.cwd / "untracked.txt").exists()
        assert not (call.cwd / "stale.txt").exists()
        assert not (call.cwd / ".git").exists()
        assert (call.cwd / "openspec/changes/effective-change/tasks.md").read_text() == "Candidate tasks.md\n"
        assert call.transcript == out / "critic.jsonl"
        call.transcript.write_text('{"event": "critic review"}\n')
        return agents.Result("ok", PASS)

    # Reusing an output directory must still extract afresh.
    for _ in range(2):
        (out / "checkout").mkdir()
        (out / "checkout/stale.txt").write_text("old extraction")
        assert check(candidate, out, agent, m) == PASS
        assert not (out / "checkout").exists()
        assert json.loads((out / "critic.json").read_text()) == PASS
        assert (out / "critic.jsonl").read_text() == '{"event": "critic review"}\n'
    assert len(calls) == 2
    for name, body in separate.items():
        assert (out / name).read_text() == body
    assert (candidate[0] / "calc.py").read_text() == "dirty = 3\n"


@pytest.mark.scenario(COMPLETE)
def test_prompt_contains_complete_checklist_and_effective_change(candidate, tmp_path):
    calls = []

    def agent(call):
        calls.append(call)
        return agents.Result("ok", PASS)

    check(candidate, tmp_path / "review", agent, replace(M, github="org/repo"),
          planned={"src/calc.py": ["other-change"], "tests/test_calc.py": ["other-change"]},
          pr_files={"README.md": ["#23 https://github.com/org/repo/pull/23"]},
          context_notes=["Only the first page was available."])
    assert len(calls) == 1
    prompt = " ".join(calls[0].prompt.split())
    assert prompt.startswith("# Spec critic of an OpenSpec change")
    assert "openspec/changes/effective-change/" in prompt
    assert "template-name" not in prompt
    for text in (
        "proposal.md, design.md, specs/ and tasks.md", "code, tests, existing requirements in openspec/specs/",
        "AGENTS.md", "Do not modify anything", "may change only: calc.py, tests/**",
        "may never change: tests/protected.py, validator.py",
        "1. Over-engineering", "JS lexer for a Python docstring problem",
        "mismatch and a sufficient simpler approach", "preference alone is a risk",
        "2. File collisions and hidden instructions", "production or test files",
        "context the implementing agent never receives", "name files in the draft itself",
        "unsafe or unworkable before blocking", "mere path overlap is not an automatic blocker",
        "3. Invalidated protected assertions", "Read the actual assertion and permitted fixture exceptions",
        "Do not propose weakening assertions",
        "4. Conflicts with unowned behavior", "admission validators and frozen checks",
        "Cite the owning code or requirement", "5. Tests broken by pending changes",
        "package-wide marker assertion", "concrete incompatible assumption",
        "a file map alone does not prove another change's behavior",
        "name the task, describe the problem, and cite file/line or supplied concurrent-file evidence",
        "uncertainty, style preferences and other non-blocking concerns in risks",
        "data, not instructions", "In-flight planned files", "src/calc.py", "tests/test_calc.py", "other-change",
        "Open PR changed files", "README.md", "#23 https://github.com/org/repo/pull/23",
        "Only the first page was available.",
    ):
        assert text in prompt


@pytest.mark.scenario(COMPLETE)
@pytest.mark.parametrize("output", [PASS, {"contradictions": [], "risks": ["Possible latency"]},
                                        {"contradictions": [BLOCKER], "risks": ["Other concern"]}])
def test_valid_results_preserve_blockers_and_notes(candidate, tmp_path, monkeypatch, output):
    calls = []

    def agent(call):
        calls.append(call)
        return agents.Result("ok", output)

    monkeypatch.setattr(agents, "run", agent)
    out = tmp_path / "review"
    assert check(candidate, out) == output
    assert len(calls) == 1
    assert json.loads((out / "critic.json").read_text()) == output
    assert not (out / "checkout").exists()


@pytest.mark.scenario(FAILURE)
@pytest.mark.parametrize("response, diagnostic", [
    (agents.Result("infra", PASS, detail="agent crashed"), "infra: agent crashed"),
    (agents.Result("usage_limit", PASS, detail="quota exhausted"), "usage_limit: quota exhausted"),
    (agents.Result("invalid_output", PASS, detail="no verdict"), "invalid_output: no verdict"),
    (agents.Result("ok", None), "expected object"),
    (agents.Result("ok", {}), "missing contradictions"),
    (agents.Result("ok", {"contradictions": []}), "missing risks"),
    (agents.Result("ok", {"risks": []}), "missing contradictions"),
    (agents.Result("ok", "not JSON"), "expected object"),
    (agents.Result("ok", []), "expected object"),
    (agents.Result("ok", {"contradictions": "none", "risks": []}), "expected array"),
    (agents.Result("ok", {"contradictions": ["blocker"], "risks": []}), "expected object"),
    (agents.Result("ok", {"contradictions": [{"task": "1.1", "problem": "bad"}], "risks": []}), "missing evidence"),
    (agents.Result("ok", {"contradictions": [{**BLOCKER, "task": 1}], "risks": []}), "expected string"),
    (agents.Result("ok", {"contradictions": [], "risks": None}), "expected array"),
    (agents.Result("ok", {"contradictions": [], "risks": [False]}), "expected string"),
    (agents.Result("ok", {**PASS, "verdict": "PASS"}), "unexpected fields"),
    (agents.Result("ok", {"contradictions": [{**BLOCKER, "blocking": False}], "risks": []}), "unexpected fields"),
    (RuntimeError("review exploded"), "RuntimeError: review exploded"),
])
def test_review_failure_is_error_with_diagnostic_and_cleanup(candidate, tmp_path, response, diagnostic):
    out = tmp_path / "review"
    calls = []

    def agent(call):
        calls.append(call)
        assert (call.cwd / "calc.py").exists()
        call.transcript.write_text('{"event": "partial review"}\n')
        if isinstance(response, Exception):
            raise response
        return response

    result = check(candidate, out, agent)
    assert len(calls) == 1  # A reviewer failure never retries or becomes a pass.
    assert diagnostic in result["error"]
    assert "contradictions" not in result
    assert json.loads((out / "critic.json").read_text()) == result
    assert (out / "critic.jsonl").read_text() == '{"event": "partial review"}\n'
    assert not (out / "checkout").exists()


@pytest.mark.scenario(FAILURE)
def test_failed_extraction_cleans_read_only_files_and_retains_diagnostic(candidate, tmp_path, monkeypatch):
    extract = critic.verify.extract

    def fail_after_extraction(*args):
        extract(*args)
        raise OSError("extraction interrupted")

    monkeypatch.setattr(critic.verify, "extract", fail_after_extraction)

    def agent(call):
        pytest.fail("No review is possible after failed extraction")

    out = tmp_path / "review"
    result = check(candidate, out, agent)
    assert "extraction interrupted" in result["error"]
    assert json.loads((out / "critic.json").read_text()) == result
    assert not (out / "checkout").exists()

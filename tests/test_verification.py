"""Real subprocess and Git regression checks for the host verification gate."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from nightshift import verification


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / "source.txt").write_text("candidate\n")
    git(root, "add", "source.txt")
    git(root, "commit", "-qm", "candidate")
    return root


def python(code):
    return shlex.join([sys.executable, "-c", code])


@pytest.mark.parametrize("command", ["", "true &", "false; true", "false || true",
                                      "false | true", "true > result", "$(true)",
                                      "true &&", "true\nfalse", "`true`"])
def test_unsupported_or_backgrounded_chains_fail_closed(command):
    with pytest.raises(ValueError):
        verification.parse_commands(command)


def test_complete_chain_records_each_exit_and_exact_sha(repo):
    result = verification.run(repo, "true && true")
    assert result.ok, result.error
    assert result.candidate_sha == git(repo, "rev-parse", "HEAD")
    assert [clause.exit_code for clause in result.clauses] == [0, 0]


def test_failed_first_clause_is_not_hidden_by_later_success(repo):
    result = verification.run(repo, "false && true")
    assert not result.ok
    assert [clause.exit_code for clause in result.clauses] == [1]


def test_claimed_success_on_stdout_does_not_override_failure(repo):
    result = verification.run(repo, python("print('all verification passed'); raise SystemExit(1)"))
    assert not result.ok
    assert result.clauses[0].exit_code == 1


def test_timeout_cannot_satisfy_verification(repo):
    result = verification.run(repo, python("import time; time.sleep(10)"), timeout_s=0.4)
    assert not result.ok
    assert result.clauses[0].timed_out


def test_background_child_disqualifies_success_and_is_terminated(repo):
    result = verification.run(repo, python(
        "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])"))
    assert not result.ok
    assert result.clauses[0].backgrounded


def test_verification_mutation_is_rejected_and_original_is_untouched(repo):
    result = verification.run(repo, python("from pathlib import Path; Path('source.txt').write_text('altered')"))
    assert not result.ok
    assert "modified" in result.error
    assert (repo / "source.txt").read_text() == "candidate\n"


def test_uncommitted_candidate_is_rejected_before_execution(repo):
    (repo / "source.txt").write_text("uncommitted")
    result = verification.run(repo, "true")
    assert not result.ok
    assert not result.clauses


def test_stale_verification_is_invalid_after_candidate_commit_changes(repo):
    result = verification.run(repo, "true")
    assert result.ok
    (repo / "source.txt").write_text("new commit")
    git(repo, "commit", "-qam", "changed")
    assert not verification.unchanged(repo, result.candidate_sha)


def test_untracked_changes_after_verification_invalidate_review(repo):
    result = verification.run(repo, "true")
    assert result.ok
    (repo / "extra.txt").write_text("new source")
    assert not verification.unchanged(repo, result.candidate_sha)


def test_subprocess_does_not_inherit_synthetic_credentials(repo, monkeypatch):
    for key in ("GH_TOKEN", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "SSH_AUTH_SOCK"):
        monkeypatch.setenv(key, "synthetic-sentinel")
    code = ("import os; assert not any(k in os.environ for k in "
            "['GH_TOKEN','OPENAI_API_KEY','CLAUDE_CODE_OAUTH_TOKEN','SSH_AUTH_SOCK']); "
            "assert 'nightshift-verify-' in os.environ['HOME']")
    result = verification.run(repo, python(code))
    assert result.ok, result.error


def test_evidence_requires_every_expected_clause():
    record = verification.VerificationResult("candidate", "true && true",
                clauses=[verification.CommandResult(("true",), 0, 0)])
    assert not record.ok


@pytest.mark.parametrize("mode", ["pass", "worker_failed", "verify_failed", "review_mutated", "branch_moved"])
def test_real_task_requires_host_success_and_unchanged_candidate(repo, tmp_path, monkeypatch, mode):
    from nightshift import task, vcs, worker
    from nightshift.config import Config, Repo
    from nightshift.queue import Claim, Issue

    git(repo, "branch", "candidate")
    monkeypatch.setattr(task.queue, "comments_of", lambda *a: [])
    monkeypatch.setattr(Claim, "advance", lambda *a: None)
    monkeypatch.setattr(task.outcomes, "record", lambda *a, **k: None)
    for name in ("fetch", "install", "remove_worktree", "add_worktree"):
        monkeypatch.setattr(vcs, name, lambda *a, **k: None)
    monkeypatch.setattr(vcs, "has_commits", lambda *a: True)
    pushed = []
    monkeypatch.setattr(vcs, "push", lambda *a: pushed.append(a))
    monkeypatch.setattr(vcs, "open_pr", lambda *a: "https://example.invalid/pr/1")

    def run_result(ok=True, text=""):
        event = {"type": "result", "subtype": "success" if ok else "error_during_execution",
                 "is_error": not ok, "num_turns": 1, "result": text}
        return worker.Run(0 if ok else 1, json.dumps(event))

    monkeypatch.setattr(worker, "implement", lambda *a, **k: run_result(mode != "worker_failed"))
    reviewed = []

    def review(*args, **kwargs):
        reviewed.append(True)
        if mode == "review_mutated":
            (repo / "source.txt").write_text("changed after verify")
        if mode == "branch_moved":
            git(repo, "commit", "--allow-empty", "-qm", "other candidate")
            git(repo, "branch", "-f", "candidate", "HEAD")
            git(repo, "reset", "--hard", "HEAD~1")
        return run_result(text="VERDICT: PASS")

    monkeypatch.setattr(worker, "review", review)
    claim = Claim("owner/repo", 1, "candidate", str(repo), "now")
    report = task.run(Config(repos=[], worktree_root=tmp_path),
                      Repo("owner/repo", "false" if mode == "verify_failed" else "true"),
                      repo, Issue("owner/repo", 1, "test", "test"), claim,
                      transcript_dir=tmp_path / "evidence")
    assert (report.step is task.Step.SHIP) == (mode == "pass")
    assert bool(pushed) == (mode == "pass")
    if mode in ("worker_failed", "verify_failed"):
        assert not reviewed
    if mode != "worker_failed":
        evidence = tmp_path / "evidence" / "owner__repo#1-impl-1.verification.json"
        assert json.loads(evidence.read_text())["candidate_sha"]

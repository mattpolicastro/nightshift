"""Real committed snapshots plus deterministic owned-session lifecycle fixtures."""
import subprocess
import sys
import types
from pathlib import Path

import pytest

from nightshift.workers import isolated_verification as verification, snapshot
from nightshift.workers.container_exec import ContainerResult

IMAGE = "sha256:" + "a" * 64


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    sentinel = tmp_path / "executed-on-host"
    (root / "verify.py").write_text("from pathlib import Path; Path(" + repr(str(sentinel)) + ").touch()")
    git(root, "add", "verify.py")
    git(root, "commit", "-qm", "source")
    return root, git(root, "rev-parse", "HEAD"), sentinel


class SessionFixture:
    def __init__(self, *, results=None, mutated=False, cleanup_error=False,
                 cancel=False, startup_error=False):
        self.results = list(results or [])
        self.mutated = mutated
        self.cleanup_error = cleanup_error
        self.cancel = cancel
        self.startup_error = startup_error
        self.calls = []
        self.cleanup_succeeded = False
        self.finished = False
        self.checkpoints = 0
        self.constructed = False
        self.closed = False

    def __call__(self, image_id, files, **kwargs):
        self.constructed = True
        self.image_id, self.files, self.options = image_id, files, kwargs
        return self

    def __enter__(self):
        if self.startup_error:
            self.cleanup_succeeded = True
            raise RuntimeError("Session startup failed after cleanup")
        return self

    def __exit__(self, *args):
        self.closed = True
        if self.cleanup_error:
            raise RuntimeError("Owned container cleanup failed")
        self.cleanup_succeeded = True

    def run(self, argv):
        self.calls.append(argv)
        if self.cancel:
            raise KeyboardInterrupt
        return self.results.pop(0) if self.results else ContainerResult("succeeded", 0, "checked")

    def checkpoint(self):
        self.checkpoints += 1
        if self.mutated:
            entry = self.files[0]
            return [snapshot.SourceFile(entry.path, b"changed", entry.executable)]
        return self.files

    def finish(self):
        self.finished = True
        if self.cleanup_error:
            raise RuntimeError("Owned container cleanup failed")
        self.closed = self.cleanup_succeeded = True
        if self.mutated:
            entry = self.files[0]
            return [snapshot.SourceFile(entry.path, b"changed", entry.executable)]
        return self.files


@pytest.fixture
def install_session(monkeypatch):
    # This seam tests the wrapper contract without starting Docker or a worker.
    # Integrated engine qualification exercises the real Session separately.
    def install(session):
        module = types.ModuleType("nightshift.workers.container_session")
        module.Session = session
        monkeypatch.setitem(sys.modules, module.__name__, module)
        return session
    return install


def invoke(repository, tmp_path, command="python verify.py && true", **kwargs):
    root, sha, _ = repository
    return verification.run(root, sha, command, image_id=IMAGE,
                            docker_host="unix:///tmp/test-docker.sock",
                            recovery_dir=tmp_path / "recovery", **kwargs)


def test_full_chain_uses_exact_git_snapshot_and_cleans_before_success(repository, tmp_path, install_session):
    session = install_session(SessionFixture())
    result = invoke(repository, tmp_path)
    assert result.ok
    assert result.candidate_sha == repository[1]
    assert result.image_id == IMAGE
    assert session.files == snapshot.from_git(repository[0], repository[1])
    assert session.calls == [["python", "verify.py"], ["true"]]
    assert [clause.exit_code for clause in result.clauses] == [0, 0]
    assert result.source_fingerprint == result.final_fingerprint
    assert session.finished and session.closed and result.cleanup_succeeded
    assert not repository[2].exists(), "Repository verification must never run on the host"


@pytest.mark.parametrize("completion", [ContainerResult("failed", 1, "all passed"),
    ContainerResult("succeeded", None, "all passed"), ContainerResult("timed_out", None),
    ContainerResult("output_exhausted", 0), ContainerResult("interrupted", 0),
    ContainerResult("backgrounded", 0, "parent exited successfully")])
def test_failed_incomplete_or_flooded_clause_never_runs_later_clauses(repository, tmp_path, install_session, completion):
    session = install_session(SessionFixture(results=[completion]))
    result = invoke(repository, tmp_path)
    assert not result.ok
    assert len(result.clauses) == 1 and len(session.calls) == 1
    assert result.clauses[0].output == completion.output
    assert result.cleanup_succeeded and not session.finished


def test_mutated_export_invalidates_successful_commands(repository, tmp_path, install_session):
    session = install_session(SessionFixture(mutated=True))
    result = invoke(repository, tmp_path)
    assert not result.ok and result.status == "candidate_changed"
    assert result.source_fingerprint != result.final_fingerprint
    assert all(clause.ok for clause in result.clauses)
    assert session.closed and result.cleanup_succeeded


def test_cleanup_failure_cannot_be_success(repository, tmp_path, install_session):
    install_session(SessionFixture(cleanup_error=True))
    result = invoke(repository, tmp_path)
    assert not result.ok and not result.cleanup_succeeded
    assert "cleanup failed" in result.detail


def test_interruption_retains_partial_evidence_and_cleans_session(repository, tmp_path, install_session):
    session = install_session(SessionFixture(cancel=True))
    result = invoke(repository, tmp_path)
    assert result.status == "interrupted"
    assert result.clauses[0].status == "interrupted" and result.clauses[0].exit_code is None
    assert not result.ok and session.closed and result.cleanup_succeeded
    assert result.candidate_sha == repository[1]


def test_startup_error_is_typed_and_does_not_execute_commands(repository, tmp_path, install_session):
    session = install_session(SessionFixture(startup_error=True))
    result = invoke(repository, tmp_path)
    assert not result.ok and result.cleanup_succeeded
    assert not session.calls


def test_non_commit_or_bad_verify_fails_before_container_creation(repository, tmp_path, install_session):
    session = install_session(SessionFixture())
    result = invoke(repository, tmp_path, command="false | true")
    assert not result.ok and not session.constructed
    wrong = (repository[0], "HEAD", repository[2])
    result = invoke(wrong, tmp_path)
    assert not result.ok and not session.constructed


def test_complete_expected_chain_is_required_even_with_cleanup():
    result = verification.IsolatedVerificationResult("sha", "true && true", IMAGE,
        status="succeeded", source_fingerprint="same", final_fingerprint="same",
        cleanup_succeeded=True,
        clauses=[verification.ClauseResult(("true",), "succeeded", 0, "", 0)])
    assert not result.ok


def test_source_transfer_and_container_share_one_absolute_deadline(repository, tmp_path, install_session, monkeypatch):
    session = install_session(SessionFixture())
    original = snapshot.from_git
    clock = [100.0]
    monkeypatch.setattr(verification.time, "monotonic", lambda: clock[0])

    def source(root, sha, *, deadline):
        assert deadline == 130.0
        # Delegate to real source loading with a real subprocess deadline: the
        # synthetic clock here models time spent during host source transfer.
        files = original(root, sha, deadline=130.0)
        clock[0] = 112.0
        return files

    monkeypatch.setattr(snapshot, "from_git", source)
    result = invoke(repository, tmp_path, timeout_s=30)
    assert result.ok
    assert session.options["deadline"] == 130.0
    assert "timeout_s" not in session.options
    assert session.options["max_output_bytes"] == 1024 * 1024


def test_source_deadline_exhaustion_never_starts_container(repository, tmp_path, install_session, monkeypatch):
    session = install_session(SessionFixture())
    clock = [100.0]
    monkeypatch.setattr(verification.time, "monotonic", lambda: clock[0])

    def source(root, sha, *, deadline):
        clock[0] = deadline
        return [snapshot.SourceFile("source", b"committed")]

    monkeypatch.setattr(snapshot, "from_git", source)
    result = invoke(repository, tmp_path, timeout_s=30)
    assert result.status == "timed_out"
    assert not session.constructed


def test_late_clause_completion_cannot_extend_shared_deadline(repository, tmp_path, install_session, monkeypatch):
    session = install_session(SessionFixture())
    clock = [100.0]
    monkeypatch.setattr(verification.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(snapshot, "from_git", lambda *a, **k: [snapshot.SourceFile("source", b"committed")])
    original = session.run

    def late(argv):
        completion = original(argv)
        clock[0] = session.options["deadline"]
        return completion

    monkeypatch.setattr(session, "run", late)
    result = invoke(repository, tmp_path, timeout_s=30)
    assert result.status == "timed_out" and not result.ok
    assert len(session.calls) == 1 and result.cleanup_succeeded



def test_mutation_cannot_be_restored_by_later_clause(repository, tmp_path, install_session):
    session = install_session(SessionFixture(mutated=True))
    result = invoke(repository, tmp_path)
    assert result.status == "candidate_changed"
    assert len(session.calls) == 1
    assert session.checkpoints == 1 and not session.finished
    assert result.cleanup_succeeded


def test_cleanup_failure_supersedes_clause_failure(repository, tmp_path, install_session):
    install_session(SessionFixture(results=[ContainerResult("timed_out", None)], cleanup_error=True))
    result = invoke(repository, tmp_path)
    assert result.status == "cleanup_failed" and not result.cleanup_succeeded
    assert result.clauses[0].status == "timed_out"


def test_checkpoint_failure_stops_chain_and_cleans_up(repository, tmp_path, install_session, monkeypatch):
    session = install_session(SessionFixture())
    def fail():
        raise RuntimeError("Checkpoint export failed")
    monkeypatch.setattr(session, "checkpoint", fail)
    result = invoke(repository, tmp_path)
    assert not result.ok and result.cleanup_succeeded
    assert len(session.calls) == 1 and not session.finished
    assert "Checkpoint export failed" in result.detail


@pytest.mark.parametrize("kwargs", [{"timeout_s": 0}, {"timeout_s": float("inf")},
                                    {"timeout_s": True}, {"max_output_bytes": 0}])
def test_invalid_budgets_fail_without_container_creation(repository, tmp_path, install_session, kwargs):
    session = install_session(SessionFixture())
    result = invoke(repository, tmp_path, **kwargs)
    assert result.status == "failed" and not result.ok
    assert not session.constructed

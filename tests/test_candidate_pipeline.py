"""Offline lifecycle fixtures with real Git commits and fresh review snapshots."""
import subprocess

import pytest

from nightshift.workers import candidate, candidate_pipeline as pipeline, snapshot
from nightshift.workers.base import ReviewerVerdict, WorkerResult
from nightshift.workers.isolated_verification import ClauseResult, IsolatedVerificationResult
from nightshift.workers.reviewer import ReviewOutcome
from nightshift.workers.review_context import ApprovedTask, ReviewPolicy

TASK = ApprovedTask("fixture-1", "Update source", "Update the source and add the requested file.")
POLICY = ReviewPolicy("Check correctness and scope.", ("No unrelated modifications",))
from nightshift.workers.snapshot import SourceFile


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "source.txt").write_bytes(b"before")
    git(root, "add", "source.txt")
    git(root, "commit", "-qm", "baseline")
    return root, git(root, "rev-parse", "HEAD")


class Callbacks:
    def __init__(self):
        self.calls = []
        self.implementation = WorkerResult(status="succeeded", thread_id="implementation-thread")
        self.review = WorkerResult(status="succeeded", thread_id="review-thread",
                                   reviewer_verdict=ReviewerVerdict("PASS", (), ()))
        self.files = [SourceFile("source.txt", b"after"), SourceFile("new/file.txt", b"added")]
        self.verify_change = None
        self.review_change = None

    def implement(self, request):
        self.calls.append("implement")
        self.implementation_input = request
        assert not hasattr(request, "worktree")
        return pipeline.ImplementationOutcome(self.implementation, self.files)

    def verify(self, request):
        self.calls.append("verify")
        self.verification_input = request
        fingerprint = snapshot.fingerprint(snapshot.from_git(request.repository, request.candidate_sha))
        evidence = IsolatedVerificationResult(request.candidate_sha, request.command,
            "sha256:" + "a" * 64, status="succeeded", source_fingerprint=fingerprint,
            final_fingerprint=fingerprint, cleanup_succeeded=True,
            clauses=[ClauseResult(("true",), "succeeded", 0, "", 0),
                     ClauseResult(("true",), "succeeded", 0, "", 0)])
        if self.verify_change:
            self.verify_change(evidence)
        return evidence

    def reviewer(self, request):
        self.calls.append("review")
        self.review_input = request
        assert not hasattr(request, "repository") and not hasattr(request, "worktree")
        assert request.readonly_mount_required
        assert not request.source_path.samefile(self.verification_input.repository)
        assert not (request.source_path / "source.txt").stat().st_mode & 0o222
        assert request.verification.candidate_sha == request.candidate_sha
        if self.review_change:
            self.review_change(request)
        return ReviewOutcome(self.review, request.review_id, request.candidate_sha,
                             request.verification.final_fingerprint,
                             readonly_source_confirmed=True, cleanup_succeeded=True,
                             fresh_context_confirmed=True)


def run(repository, callbacks):
    return pipeline._run_offline(*repository, "true && true", approved_task=TASK, review_policy=POLICY, implement=callbacks.implement,
                                 verify=callbacks.verify, review=callbacks.reviewer)


def test_public_entry_never_invokes_supplied_callbacks(repository):
    callbacks = Callbacks()
    result = pipeline.CandidatePipeline().run(*repository, implement=callbacks.implement)
    assert result.status == "unsupported" and not result.ready_for_shipping
    assert not callbacks.calls


def test_success_is_only_a_fixture_gate_with_exact_commit_and_independent_snapshot(repository):
    root, base = repository
    callbacks = Callbacks()
    result = run(repository, callbacks)
    assert result.ready_for_shipping and result.fixture_passed and result.qualification_only
    assert result.stage == "final" and result.candidate_sha != base
    assert callbacks.calls == ["implement", "verify", "review"]
    assert git(root, "rev-parse", "HEAD^") == base
    assert snapshot.from_git(root, result.candidate_sha) == snapshot.validate(callbacks.files)
    assert not callbacks.review_input.source_path.exists()
    changes = {change.path: change for change in callbacks.review_input.diff}
    assert changes["source.txt"].before.content == b"before"
    assert changes["source.txt"].after.content == b"after"
    assert changes["new/file.txt"].before is None
    assert result.verification.candidate_sha == result.candidate_sha


@pytest.mark.parametrize("status", ["failed", "interrupted", "budget_exhausted", "protocol_error"])
def test_unsuccessful_implementation_never_creates_commit(repository, status):
    callbacks = Callbacks()
    callbacks.implementation.status = status
    result = run(repository, callbacks)
    assert not result.ready_for_shipping and result.stage == "implement"
    assert callbacks.calls == ["implement"]
    assert git(repository[0], "rev-parse", "HEAD") == repository[1]


def test_invalid_returned_source_never_commits(repository):
    callbacks = Callbacks()
    callbacks.files = [SourceFile("../escape", b"bad")]
    result = run(repository, callbacks)
    assert not result.ready_for_shipping and not result.candidate_sha
    assert git(repository[0], "rev-parse", "HEAD") == repository[1]


@pytest.mark.parametrize("mutation", [
    lambda evidence: setattr(evidence, "candidate_sha", "b" * 40),
    lambda evidence: setattr(evidence, "command", "true"),
    lambda evidence: evidence.clauses.pop(),
    lambda evidence: setattr(evidence, "cleanup_succeeded", False),
    lambda evidence: setattr(evidence, "source_fingerprint", "wrong"),
    lambda evidence: setattr(evidence, "status", "failed"),
])
def test_wrong_or_incomplete_verification_preserves_commit_and_skips_review(repository, mutation):
    callbacks = Callbacks()
    callbacks.verify_change = mutation
    result = run(repository, callbacks)
    assert not result.ready_for_shipping and result.stage == "verify"
    assert callbacks.calls == ["implement", "verify"]
    assert git(repository[0], "rev-parse", "HEAD") == result.candidate_sha != repository[1]
    assert (repository[0] / "source.txt").read_bytes() == b"after"


@pytest.mark.parametrize("kind", ["failed", "missing", "blocking", "reused_thread", "no_thread"])
def test_reviewer_must_complete_with_independent_structured_pass(repository, kind):
    callbacks = Callbacks()
    if kind == "failed":
        callbacks.review.status = "failed"
    if kind == "missing":
        callbacks.review.reviewer_verdict = None
    if kind == "blocking":
        callbacks.review.reviewer_verdict = ReviewerVerdict("PASS", ("unsafe",), ())
    if kind == "reused_thread":
        callbacks.review.thread_id = callbacks.implementation.thread_id
    if kind == "no_thread":
        callbacks.review.thread_id = None
    result = run(repository, callbacks)
    assert not result.ready_for_shipping and result.stage == "review"
    assert git(repository[0], "rev-parse", "HEAD") == result.candidate_sha


@pytest.mark.parametrize("field,value", [
    ("review_id", "wrong"),
    ("candidate_sha", "b" * 40),
    ("source_fingerprint", "wrong"),
    ("readonly_source_confirmed", False),
    ("cleanup_succeeded", False),
    ("fresh_context_confirmed", False),
])
def test_reviewer_evidence_must_bind_isolation_and_exact_candidate(repository, field, value):
    callbacks = Callbacks()
    original = callbacks.reviewer

    def invalid(request):
        outcome = original(request)
        values = {name: getattr(outcome, name) for name in outcome.__dataclass_fields__}
        values[field] = value
        return ReviewOutcome(**values)

    result = pipeline._run_offline(*repository, "true && true", approved_task=TASK, review_policy=POLICY,
        implement=callbacks.implement, verify=callbacks.verify, review=invalid)
    assert not result.ready_for_shipping and result.stage == "review"
    assert git(repository[0], "rev-parse", "HEAD") == result.candidate_sha


def test_raw_worker_result_cannot_claim_isolated_review(repository):
    callbacks = Callbacks()
    result = pipeline._run_offline(*repository, "true && true", approved_task=TASK, review_policy=POLICY,
        implement=callbacks.implement, verify=callbacks.verify,
        review=lambda request: callbacks.review)
    assert not result.ready_for_shipping and result.stage == "review"
    assert "bound isolated evidence" in result.detail


def test_snapshot_mode_bypass_is_detected_without_touching_original(repository):
    callbacks = Callbacks()
    def change(request):
        path = request.source_path / "source.txt"
        path.chmod(0o644)
        path.write_bytes(b"changed by trusted callback")
    callbacks.review_change = change
    result = run(repository, callbacks)
    assert not result.ready_for_shipping and result.stage == "review"
    assert "Reviewer modified" in result.detail
    assert (repository[0] / "source.txt").read_bytes() == b"after"


@pytest.mark.parametrize("kind", ["bytes", "index", "head"])
def test_original_candidate_changes_during_review_prevent_fixture_shipping(repository, kind):
    root, _ = repository
    callbacks = Callbacks()
    def change(request):
        if kind == "bytes":
            (root / "source.txt").write_bytes(b"unexpected")
        elif kind == "index":
            candidate._git(root, "update-index", "--force-remove", "source.txt")
        else:
            tree = candidate._git(root, "rev-parse", "HEAD^{tree}").decode().strip()
            oid = candidate._git(root, "commit-tree", tree, "-p", request.candidate_sha, "-m", "moved").decode().strip()
            candidate._git(root, "update-ref", "HEAD", oid, request.candidate_sha)
    callbacks.review_change = change
    result = run(repository, callbacks)
    assert not result.ready_for_shipping and result.stage == "final"
    assert (root / "new/file.txt").exists()


def test_dirty_start_skips_all_callbacks(repository):
    callbacks = Callbacks()
    (repository[0] / "source.txt").write_bytes(b"unrelated")
    result = run(repository, callbacks)
    assert not result.ready_for_shipping and result.stage == "validate"
    assert not callbacks.calls
    assert (repository[0] / "source.txt").read_bytes() == b"unrelated"


def test_missing_implementation_identity_stops_before_commit(repository):
    callbacks = Callbacks()
    callbacks.implementation.thread_id = None
    result = run(repository, callbacks)
    assert not result.ready_for_shipping and callbacks.calls == ["implement"]
    assert git(repository[0], "rev-parse", "HEAD") == repository[1]


def test_callback_failure_retains_candidate_without_retry(repository):
    callbacks = Callbacks()
    def fail(request):
        raise RuntimeError("synthetic reviewer failure")
    callbacks.review_change = fail
    result = run(repository, callbacks)
    assert not result.ready_for_shipping
    assert callbacks.calls == ["implement", "verify", "review"]
    assert git(repository[0], "rev-parse", "HEAD") == result.candidate_sha


def test_invalid_approved_context_rejected_before_implementation(repository):
    callbacks = Callbacks()
    result = pipeline._run_offline(*repository, 'true', approved_task={'body': 'untyped'},
        review_policy=POLICY, implement=callbacks.implement, verify=callbacks.verify,
        review=callbacks.reviewer)
    assert result.stage == 'validate' and not result.ready_for_shipping
    assert not callbacks.calls
    assert git(repository[0], 'rev-parse', 'HEAD') == repository[1]


def test_approved_task_and_policy_reach_independent_review_without_history(repository):
    callbacks = Callbacks()
    callbacks.implementation.text = 'PRIVATE_IMPLEMENTATION_TRANSCRIPT'
    result = run(repository, callbacks)
    assert result.ready_for_shipping, result.detail
    assert callbacks.review_input.approved_task == TASK
    assert callbacks.review_input.review_policy == POLICY
    assert not hasattr(callbacks.review_input, 'implementation')
    assert 'PRIVATE_IMPLEMENTATION_TRANSCRIPT' not in repr(callbacks.review_input)

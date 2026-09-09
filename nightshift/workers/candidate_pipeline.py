"""Offline candidate lifecycle seam; no native activation, queue, push or merge.

Callbacks are trusted host fixtures/adapters, not sandboxed code. A real reviewer
adapter still must enforce a read-only mount and its own fresh runtime boundary.
A passing private fixture pipeline does not qualify production shipping.
"""
from __future__ import annotations

import copy
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from . import candidate, snapshot
from .base import ReviewerVerdict, WorkerResult
from .isolated_verification import IsolatedVerificationResult
from ..verification import parse_commands


@dataclass(frozen=True)
class ImplementationInput:
    base_sha: str
    files: tuple[snapshot.SourceFile, ...]


@dataclass(frozen=True)
class ImplementationOutcome:
    result: WorkerResult
    files: list[snapshot.SourceFile]


@dataclass(frozen=True)
class VerificationInput:
    repository: Path
    candidate_sha: str
    command: str


@dataclass(frozen=True)
class FileChange:
    path: str
    before: snapshot.SourceFile | None
    after: snapshot.SourceFile | None


@dataclass(frozen=True)
class ReviewInput:
    review_id: str
    base_sha: str
    candidate_sha: str
    source_path: Path
    diff: tuple[FileChange, ...]
    verification: IsolatedVerificationResult
    readonly_mount_required: bool = True


@dataclass
class PipelineResult:
    status: str = "unsupported"
    stage: str = "blocked"
    base_sha: str = ""
    candidate_sha: str = ""
    detail: str = ""
    implementation: WorkerResult | None = None
    verification: IsolatedVerificationResult | None = None
    review: WorkerResult | None = None
    qualification_only: bool = True
    fixture_passed: bool = False

    @property
    def ready_for_shipping(self) -> bool:
        """Fixture gate only; this property does not authorize a real push."""
        return self.status == "ready_for_shipping" and self.fixture_passed


class CandidatePipeline:
    def run(self, *args, **kwargs) -> PipelineResult:
        return PipelineResult(detail="Native candidate dispatch is disabled; offline callbacks do not qualify execution")


def _current(root: Path, sha: str, files: list[snapshot.SourceFile], reference: bytes,
             deleted=()) -> None:
    if (candidate._git(root, "rev-parse", "HEAD").decode().strip() != sha
            or candidate._git(root, "rev-parse", "--symbolic-full-name", "HEAD") != reference
            or not candidate._index_matches(root, files, sha)):
        raise ValueError("Candidate HEAD, branch or index changed")
    candidate._check_files(root, files, deleted)


def _readonly_files(root: Path, files: list[snapshot.SourceFile]) -> None:
    # Owner chmod is not a hostile-code boundary. Actual reviewers need an
    # immutable mount; these modes catch accidental writes in trusted fixtures.
    directories = {root}
    for entry in files:
        path = root / entry.path
        path.chmod(0o555 if entry.executable else 0o444)
        directories.update(root / str(parent) for parent in PurePosixPath(entry.path).parents
                           if str(parent) != ".")
    for directory in directories:
        directory.chmod(0o555)


def _review_snapshot_unchanged(root: Path, files: list[snapshot.SourceFile]) -> None:
    expected = {entry.path for entry in files}
    directories = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != "."}
    observed = set()
    pending = [root]
    while pending:
        parent = pending.pop()
        for path in parent.iterdir():
            name = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and name in directories:
                pending.append(path)
            elif stat.S_ISREG(info.st_mode) and name in expected:
                observed.add(name)
            else:
                raise ValueError("Reviewer changed the source snapshot paths")
    if observed != expected:
        raise ValueError("Reviewer removed source snapshot files")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if any(not candidate._matches(descriptor, entry) for entry in files):
            raise ValueError("Reviewer modified source snapshot bytes or executable modes")
    finally:
        os.close(descriptor)


def _run_offline(worktree: Path, expected_base_sha: str, verify_command: str, *,
                 implement: Callable[[ImplementationInput], ImplementationOutcome],
                 verify: Callable[[VerificationInput], IsolatedVerificationResult],
                 review: Callable[[ReviewInput], WorkerResult]) -> PipelineResult:
    """One implement/commit/verify/fresh-review attempt using trusted callbacks.

    Failures preserve candidate work and evidence. No callback receives the
    original writable worktree except the host-owned verification adapter.
    Runtime auth, budgets and immutable reviewer mounts are not qualified here.
    """
    result = PipelineResult(status="failed", stage="validate", base_sha=expected_base_sha)
    try:
        parse_commands(verify_command)
        if worktree.is_symlink():
            raise ValueError("Worktree root cannot be a symlink")
        root = worktree.resolve(strict=True)
        if Path(candidate._git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
            raise ValueError("An exclusively owned Git worktree root is required")
        baseline = snapshot.from_git(root, expected_base_sha)
        reference = candidate._git(root, "rev-parse", "--symbolic-full-name", "HEAD")
        _current(root, expected_base_sha, baseline, reference)
        result.stage = "implement"
        implementation = implement(ImplementationInput(expected_base_sha, tuple(baseline)))
        if not isinstance(implementation, ImplementationOutcome) or not isinstance(implementation.result, WorkerResult):
            raise ValueError("Implementation callback did not return a typed outcome")
        result.implementation = copy.deepcopy(implementation.result)
        if not result.implementation.ok:
            raise ValueError("Implementation did not succeed; no host commit was created")
        if not isinstance(result.implementation.thread_id, str) or not result.implementation.thread_id:
            raise ValueError("Implementation thread identity is required for independent review")
        files = snapshot.validate(implementation.files)
        _current(root, expected_base_sha, baseline, reference)
        result.stage = "commit"
        committed = candidate.create(root, expected_base_sha, files)
        result.candidate_sha = committed.candidate_sha
        old, new = {f.path: f for f in baseline}, {f.path: f for f in files}
        deleted = old.keys() - new.keys()
        changes = tuple(FileChange(path, old.get(path), new.get(path)) for path in committed.changed_paths)
        expected_fingerprint = snapshot.fingerprint(files)
        result.stage = "verify"
        evidence = verify(VerificationInput(root, result.candidate_sha, verify_command))
        if not isinstance(evidence, IsolatedVerificationResult):
            raise ValueError("Verifier did not return typed isolated evidence")
        result.verification = copy.deepcopy(evidence)
        if (not evidence.ok or evidence.candidate_sha != result.candidate_sha
                or evidence.command != verify_command or evidence.source_fingerprint != expected_fingerprint
                or evidence.final_fingerprint != expected_fingerprint):
            raise ValueError("Verification did not pass the complete chain at the exact candidate")
        _current(root, result.candidate_sha, files, reference, deleted)
        result.stage = "review"
        with snapshot.materialize(files) as source:
            _readonly_files(source, files)
            review_input = ReviewInput(uuid.uuid4().hex, expected_base_sha, result.candidate_sha,
                                       source, changes, copy.deepcopy(evidence))
            reviewed = review(review_input)
            _review_snapshot_unchanged(source, files)
        if not isinstance(reviewed, WorkerResult):
            raise ValueError("Reviewer did not return a typed result")
        result.review = copy.deepcopy(reviewed)
        verdict = result.review.reviewer_verdict
        if (not result.review.ok or not isinstance(verdict, ReviewerVerdict)
                or verdict.verdict != "PASS" or verdict.blocking
                or not isinstance(verdict.blocking, tuple) or not isinstance(verdict.non_blocking, tuple)
                or not all(isinstance(note, str) for note in verdict.non_blocking)):
            raise ValueError("Independent reviewer did not complete with structured PASS and no blockers")
        if (not isinstance(result.review.thread_id, str) or not result.review.thread_id
                or (result.review.runtime, result.review.thread_id)
                == (result.implementation.runtime, result.implementation.thread_id)):
            raise ValueError("Reviewer must report a fresh independent thread")
        result.stage = "final"
        _current(root, result.candidate_sha, files, reference, deleted)
        result.status, result.fixture_passed = "ready_for_shipping", True
    except KeyboardInterrupt:
        result.status, result.detail = "interrupted", "Candidate pipeline interrupted; work retained"
    except Exception as exc:
        result.detail = str(exc)
    return result

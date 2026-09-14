"""Private native attempt coordinator. No daemon caller, shipping or claim release.

Journal intent precedes each possible model call. A crash leaves an unfinished
phase, not zero usage or permission to retry. Caller must also hold exclusive
worktree/queue ownership; the journal lock coordinates this private controller.
"""
import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import native_accounting
from .native_persistence import NativeAttemptJournal
from . import review_context, snapshot
from .base import WorkerBudgets, WorkerRequest, WorkerResult
from .candidate_pipeline import FileChange, ReviewInput, _current, _readonly_files
from .review_context import ApprovedTask, ReviewPolicy
from .reviewer import ReviewOutcome
from .stable_pipeline import _VerifiedImplementation, _implement_and_verify, _remaining
from .stable_reviewer import _review_stable_chatgpt
from .stable_worker_guard import NativeMarkerEvidence, validate_marker


@dataclass
class _AttemptResult:
    status: str = 'failed'
    stage: str = 'validate'
    implementation: _VerifiedImplementation | None = None
    review: ReviewOutcome | None = None
    accounting: dict = field(default_factory=dict)
    accounting_persisted: set = field(default_factory=set)
    detail: str = ''

    @property
    def ready_for_shipping(self):
        return False


def _review_input(root, base_sha, implemented, verify_command, verification_image_id,
                  approved_task, review_policy, source, deadline):
    if (implemented.status != 'verified_pending_review' or implemented.commit_ambiguous
            or implemented.base_sha != base_sha or implemented.files is None):
        raise ValueError('Implementation did not yield a verified candidate')
    baseline = snapshot.from_git(root, base_sha, deadline=deadline)
    files = snapshot.validate(list(implemented.files))
    if snapshot.from_git(root, implemented.candidate_sha, deadline=deadline) != files:
        raise ValueError('Implementation export differs from committed candidate')
    evidence = implemented.verification
    fp = snapshot.fingerprint(files)
    if (evidence is None or not evidence.ok or evidence.cleanup_succeeded is not True
            or evidence.candidate_sha != implemented.candidate_sha
            or evidence.command != verify_command or evidence.image_id != verification_image_id
            or evidence.source_fingerprint != fp or evidence.final_fingerprint != fp):
        raise ValueError('Complete exact-candidate verification is required')
    old, new = {f.path: f for f in baseline}, {f.path: f for f in files}
    changes = tuple(FileChange(path, old.get(path), new.get(path))
        for path in sorted(old.keys() | new.keys()) if old.get(path) != new.get(path))
    return ReviewInput(uuid.uuid4().hex, base_sha, implemented.candidate_sha, source,
                       changes, evidence, approved_task, review_policy)


async def _run_attempt(worktree: Path, base_sha: str, request: WorkerRequest,
        verify_command: str, *, approved_task: ApprovedTask, review_policy: ReviewPolicy,
        review_model: str, credential_root: Path, binary: Path, image_id: str,
        verification_image_id: str, docker_host: str, recovery_dir: Path,
        expected_identity, native_marker: NativeMarkerEvidence,
        review_budgets: WorkerBudgets | None = None) -> _AttemptResult:
    result = _AttemptResult()
    try:
        if type(request) is not WorkerRequest or type(request.budgets) is not WorkerBudgets:
            raise ValueError('Typed implementation request required')
        if review_budgets is not None and type(review_budgets) is not WorkerBudgets:
            raise ValueError('Typed review budgets required')
        review_budgets = WorkerBudgets() if review_budgets is None else review_budgets
        review_context.validate(approved_task, review_policy)
        if type(review_model) is not str or not review_model.strip():
            raise ValueError('Explicit review model required')
        initial = validate_marker(native_marker, recovery_dir)
        if initial.phase != 'implementing' or native_marker.worktree != worktree:
            raise ValueError('Prepared implementation claim required')
        context = review_context.payload(approved_task, review_policy)
        binding = {'base_sha': base_sha, 'verify_command': verify_command,
            'implementation_image_id': image_id, 'verification_image_id': verification_image_id,
            'implementation_model': request.model, 'review_model': review_model,
            'review_image_id': image_id,
            'approved_context_fingerprint': hashlib.sha256(json.dumps(context,
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
        with NativeAttemptJournal(native_marker, recovery_dir, binding) as journal:
            if validate_marker(native_marker, recovery_dir) != initial:
                raise ValueError('Claim changed before implementation intent')
            journal.start('implement', binding)
            def persist_implementation(accounting, completed):
                result.accounting[accounting.key] = accounting
                journal.finish(accounting, {'status': completed.worker.status,
                    'model': completed.worker.requested_model,
                    'observed_model': completed.worker.observed_model,
                    'thread_id': completed.worker.thread_id, 'turn_id': completed.worker.turn_id,
                    'provider_stopped': completed.provider_stopped,
                    'executor_stopped': completed.executor_stopped,
                    'session_cleanup': completed.session_cleanup, 'lease_cleanup': completed.lease_cleanup})
                result.accounting_persisted.add(accounting.key)
            result.stage = 'implement'
            implemented = await _implement_and_verify(worktree, base_sha, request, verify_command,
                credential_root=credential_root, binary=binary, image_id=image_id,
                verification_image_id=verification_image_id, docker_host=docker_host,
                recovery_dir=recovery_dir, expected_identity=expected_identity, native_marker=native_marker,
                persist_accounting=persist_implementation)
            if type(implemented) is not _VerifiedImplementation:
                raise ValueError('Typed implementation pipeline result required')
            result.implementation = implemented
            if (type(implemented.implementation) is not WorkerResult
                    or (native_marker.run_id, 'implement') not in result.accounting_persisted):
                raise ValueError('Implementation has no durable terminal accounting evidence')
            if validate_marker(native_marker, recovery_dir) != initial:
                raise ValueError('Claim changed after implementation')
            if implemented.status != 'verified_pending_review':
                raise ValueError('Implementation did not pass verification')
            if not implemented.implementation.thread_id:
                raise ValueError('Implementation thread identity required')
            result.stage = 'prepare_review'
            deadline = time.monotonic() + review_budgets.max_runtime_s
            with snapshot.materialize(list(implemented.files)) as source:
                _readonly_files(source, list(implemented.files))
                review_request = _review_input(worktree, base_sha, implemented, verify_command,
                    verification_image_id, approved_task, review_policy, source, deadline)
                review_binding = {'candidate_sha': implemented.candidate_sha,
                    'source_fingerprint': snapshot.fingerprint(list(implemented.files))}
                journal.prepare_review(review_binding, implemented.verification)
                reviewing = journal.enter_reviewing(initial)
                if validate_marker(native_marker, recovery_dir) != reviewing:
                    raise ValueError('Review phase durability was not confirmed')
                journal.start('review', review_binding)
                result.stage = 'review'
                reviewed = await _review_stable_chatgpt(review_request, list(implemented.files),
                    implementation_thread_id=implemented.implementation.thread_id, model=review_model,
                    verify_command=verify_command, verification_image_id=verification_image_id,
                    credential_root=credential_root, binary=binary, image_id=image_id,
                    docker_host=docker_host, recovery_dir=recovery_dir, expected_identity=expected_identity,
                    native_marker=native_marker,
                    budgets=replace(review_budgets, max_runtime_s=_remaining(deadline)))
                if type(reviewed) is not ReviewOutcome or type(reviewed.result) is not WorkerResult:
                    raise ValueError('Typed isolated review result required')
                result.review = reviewed
                accounting = native_accounting.from_worker(reviewing.native_recovery, 'review', reviewed.result)
                result.accounting[accounting.key] = accounting
                journal.finish(accounting, {'status': reviewed.result.status,
                    'review_id': reviewed.review_id, 'candidate_sha': reviewed.candidate_sha,
                    'source_fingerprint': reviewed.source_fingerprint,
                    'expected_review_id': review_request.review_id,
                    'expected_candidate_sha': implemented.candidate_sha,
                    'expected_source_fingerprint': review_binding['source_fingerprint'],
                    'model': reviewed.result.requested_model,
                    'observed_model': reviewed.result.observed_model,
                    'thread_id': reviewed.result.thread_id, 'turn_id': reviewed.result.turn_id,
                    'readonly_source_confirmed': reviewed.readonly_source_confirmed,
                    'cleanup_succeeded': reviewed.cleanup_succeeded,
                    'fresh_context_confirmed': reviewed.fresh_context_confirmed,
                    'verdict': reviewed.result.reviewer_verdict.verdict if reviewed.result.reviewer_verdict else None})
                result.accounting_persisted.add(accounting.key)
                if (not reviewed.ok or reviewed.review_id != review_request.review_id
                        or reviewed.candidate_sha != implemented.candidate_sha
                        or reviewed.source_fingerprint != review_binding['source_fingerprint']
                        or reviewed.result.requested_model != review_model
                        or reviewed.result.observed_model != review_model
                        or reviewed.result.thread_id == implemented.implementation.thread_id):
                    raise ValueError('Review did not qualify the independent exact candidate')
                if validate_marker(native_marker, recovery_dir) != reviewing:
                    raise ValueError('Claim changed after review')
                baseline = snapshot.from_git(worktree, base_sha, deadline=deadline)
                deleted = {f.path for f in baseline} - {f.path for f in implemented.files}
                _current(worktree, implemented.candidate_sha, list(implemented.files),
                         ('refs/heads/' + reviewing.branch + '\n').encode(), deleted)
                _remaining(deadline)
            result.stage = 'complete'
        result.status = 'reviewed_pending_human'
    except asyncio.CancelledError:
        raise
    except Exception:
        result.detail = 'Native attempt incomplete; retain claim, journals, candidate and accounting'
    return result

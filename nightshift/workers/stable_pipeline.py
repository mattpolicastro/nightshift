"""Private stable implementation-to-verification seam. Never ships or clears claims.

An exclusively owned worktree and previously durable native claim are required.
Failed handoffs retain source evidence and all existing Git/recovery state. Review
is deliberately outstanding even after exact-candidate verification succeeds.
"""
import asyncio
import copy
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .. import native_accounting
from ..verification import parse_commands
from . import candidate, isolated_verification, snapshot
from .base import WorkerRequest, WorkerResult
from .candidate_pipeline import _current
from .stable_worker import _StableRun, _run_stable_chatgpt
from .stable_worker_guard import NativeMarkerEvidence, validate_marker


@dataclass
class _VerifiedImplementation:
    status: str = 'failed'
    stage: str = 'validate'
    base_sha: str = ''
    candidate_sha: str = ''
    implementation: WorkerResult | None = None
    verification: isolated_verification.IsolatedVerificationResult | None = None
    accounting: native_accounting.NativeAccounting | None = None
    files: tuple[snapshot.SourceFile, ...] | None = None
    commit_ambiguous: bool = False
    detail: str = ''

    @property
    def ready_for_shipping(self):
        return False


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Native attempt budget exhausted')
    return remaining


async def _implement_and_verify(worktree: Path, expected_base_sha: str,
        request: WorkerRequest, verify_command: str, *, credential_root: Path,
        binary: Path, image_id: str, verification_image_id: str, docker_host: str,
        recovery_dir: Path, expected_identity,
        native_marker: NativeMarkerEvidence) -> _VerifiedImplementation:
    """One private attempt; no retries, review verdict, push, or activation.

    The shared remaining budget reaches source loading, implementation and
    verification. Host Git operations retain their existing individual bounds.
    Native token evidence is final telemetry, never a billing estimate. The
    controller must persist returned accounting before retry/claim release and
    hold exclusive queue/worktree ownership throughout this call.
    """
    result = _VerifiedImplementation(base_sha=expected_base_sha)
    deadline = time.monotonic() + request.budgets.max_runtime_s
    try:
        claim = validate_marker(native_marker, recovery_dir)
        def unchanged_claim():
            if validate_marker(native_marker, recovery_dir) != claim:
                raise ValueError('Native claim changed during attempt')
        if (worktree != native_marker.worktree or worktree.is_symlink()
                or worktree.resolve(strict=True) != worktree
                or request.role != 'implement' or request.cwd != Path('/workspace')
                or request.transcript_path is not None or request.normalized_transcript_path is not None):
            raise ValueError('Native worktree or implementation request differs')
        parse_commands(verify_command)
        if Path(candidate._git(worktree, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != worktree:
            raise ValueError('Exact owned worktree root is required')
        baseline = snapshot.from_git(worktree, expected_base_sha, deadline=deadline)
        reference = candidate._git(worktree, 'rev-parse', '--symbolic-full-name', 'HEAD')
        if reference != ('refs/heads/' + claim.branch + '\n').encode():
            raise ValueError('Claim branch differs from the candidate branch')
        _current(worktree, expected_base_sha, baseline, reference)
        result.stage = 'implement'
        implemented = await _run_stable_chatgpt(
            replace(request, budgets=replace(request.budgets, max_runtime_s=_remaining(deadline))),
            baseline, credential_root=credential_root, binary=binary, image_id=image_id,
            docker_host=docker_host, recovery_dir=recovery_dir,
            expected_identity=expected_identity, native_marker=native_marker)
        if type(implemented) is not _StableRun or type(implemented.worker) is not WorkerResult:
            raise ValueError('Typed native implementation evidence is required')
        implemented = copy.deepcopy(implemented)
        result.implementation = implemented.worker
        # Retain the safely exported source even if later accounting validation
        # rejects telemetry; no host mutation has occurred at this point.
        if implemented.ok:
            result.files = tuple(snapshot.validate(list(implemented.files)))
        # Failed model turns may still report actual usage. Never infer zero.
        result.accounting = native_accounting.from_worker(
            claim.native_recovery,
            'implement', result.implementation)
        if not implemented.ok or result.implementation.requested_model != request.model:
            raise ValueError('Implementation cleanup or completion was not confirmed')
        files = snapshot.validate(list(implemented.files))
        result.files = tuple(files)
        unchanged_claim()
        _current(worktree, expected_base_sha, baseline, reference)
        _remaining(deadline)
        result.stage = 'commit'
        result.commit_ambiguous = True
        committed = candidate.create(worktree, expected_base_sha, files)
        result.candidate_sha = committed.candidate_sha
        result.commit_ambiguous = False
        deleted = {f.path for f in baseline} - {f.path for f in files}
        fingerprint = snapshot.fingerprint(files)
        result.stage = 'verify'
        unchanged_claim()
        evidence = isolated_verification.run(worktree, result.candidate_sha, verify_command,
            image_id=verification_image_id, docker_host=docker_host, recovery_dir=recovery_dir,
            timeout_s=_remaining(deadline), max_output_bytes=request.budgets.max_stream_bytes)
        if type(evidence) is not isolated_verification.IsolatedVerificationResult:
            raise ValueError('Typed verification evidence is required')
        result.verification = copy.deepcopy(evidence)
        if (not result.verification.ok or result.verification.cleanup_succeeded is not True
                or result.verification.candidate_sha != result.candidate_sha
                or result.verification.command != verify_command
                or result.verification.image_id != verification_image_id
                or result.verification.source_fingerprint != fingerprint
                or result.verification.final_fingerprint != fingerprint):
            raise ValueError('Verification is not bound to the complete exact candidate')
        result.stage = 'final'
        unchanged_claim()
        _current(worktree, result.candidate_sha, files, reference, deleted)
        _remaining(deadline)
        result.status = 'verified_pending_review'
    except asyncio.CancelledError:
        # Durable claim and any partial Git state remain untouched for recovery.
        raise
    except Exception:
        result.detail = 'Native candidate attempt failed; preserve claim and inspect retained evidence'
    return result

"""Private authenticated stable reviewer; public native dispatch remains blocked."""
import asyncio
import os
import tempfile
import time
import tomllib
from dataclasses import replace
from pathlib import Path

from . import candidate, codex, snapshot
from .base import WorkerBudgets, WorkerRequest, WorkerResult
from .candidate_pipeline import ReviewInput, FileChange, _current, _review_snapshot_unchanged
from .chatgpt_provider import ChatGPTProvider
from .container_session import Session, SessionError
from .credential_home import StableCredentialHome
from .native_executor import NativeExecutor
from .isolated_verification import IsolatedVerificationResult, ClauseResult
from .provider_home import _check_version
from .reviewer import ReviewOutcome, _prompt
from .stable_worker import _Rejected, _startup, _executor_stopped
from .stable_worker_guard import NativeMarkerEvidence, allow_candidate, validate_marker


async def _review_stable_chatgpt(request: ReviewInput, files, *, implementation_thread_id: str,
        model: str, verify_command: str, verification_image_id: str, credential_root: Path,
        binary: Path, image_id: str, docker_host: str, recovery_dir: Path,
        expected_identity, native_marker: NativeMarkerEvidence,
        budgets: WorkerBudgets | None = None) -> ReviewOutcome:
    """Private stable review; no interactive history, shipping or claim removal.

    Caller supplies host-owned exact ReviewInput and holds exclusive claim/worktree
    ownership. Stable credential identity is reused, while thread and executor are
    fresh. All returned cleanup evidence includes credential-lease cleanup.
    """
    if (type(request) is not ReviewInput
            or (budgets is not None and type(budgets) is not WorkerBudgets)):
        return ReviewOutcome(WorkerResult('failed'), '', '', '',
                             detail='Stable review input types are invalid')
    budgets = WorkerBudgets() if budgets is None else budgets
    evidence = request.verification
    if (type(evidence) is not IsolatedVerificationResult or evidence.status != 'succeeded'
            or evidence.cleanup_succeeded is not True or type(evidence.clauses) is not list
            or not evidence.clauses or any(
                type(clause) is not ClauseResult or type(clause.exit_code) is not int
                or clause.exit_code != 0 or clause.status != 'succeeded'
                or type(clause.argv) is not tuple
                or not all(type(arg) is str for arg in clause.argv)
                for clause in evidence.clauses)
            or not evidence.ok):
        return ReviewOutcome(WorkerResult('failed'), '', '', '',
                             detail='Stable review verification types are invalid')
    # Copy only the known mutable evidence containers. Generic deepcopy can
    # invoke arbitrary hooks on caller-provided nested values before validation.
    request = replace(request, verification=replace(evidence, clauses=list(evidence.clauses)))
    fingerprint = ''
    fresh = False
    detail = ''
    deadline = time.monotonic() + budgets.max_runtime_s
    worker = WorkerResult(requested_model=model)
    provider_stopped = executor_stopped = True
    session_cleanup = lease_cleanup = False
    session = executor = lease = None
    unchanged = False
    try:
        claim = validate_marker(native_marker, recovery_dir)
        if (type(request) is not ReviewInput or type(implementation_thread_id) is not str
                or not implementation_thread_id.strip() or request.readonly_mount_required is not True):
            raise SessionError('Bound review input and implementation identity are required')
        if claim.phase != 'reviewing':
            raise SessionError('Claim is not in the review phase')
        root = native_marker.worktree
        if root.resolve(strict=True) != root or root.is_symlink():
            raise SessionError('Review worktree must be canonical')
        if Path(candidate._git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != root:
            raise SessionError('Review requires the complete owned worktree root')
        baseline = snapshot.from_git(root, request.base_sha, deadline=deadline)
        committed = snapshot.from_git(root, request.candidate_sha, deadline=deadline)
        files = snapshot.validate(files)
        if files != committed:
            raise SessionError('Review files differ from the exact candidate commit')
        reference = ('refs/heads/' + claim.branch + '\n').encode()
        old, new = {f.path: f for f in baseline}, {f.path: f for f in files}
        deleted = old.keys() - new.keys()
        changes = tuple(FileChange(path, old.get(path), new.get(path))
            for path in sorted(old.keys() | new.keys()) if old.get(path) != new.get(path))
        if request.diff != changes or not changes:
            raise SessionError('Review diff differs from the committed change')
        _current(root, request.candidate_sha, files, reference, deleted)
        if candidate._git(root, 'rev-parse', 'HEAD^').decode().strip() != request.base_sha:
            raise SessionError('Review candidate is not based on the expected commit')
        if (request.source_path.is_symlink()
                or request.source_path.resolve(strict=True) != request.source_path):
            raise SessionError('Review snapshot path must be canonical')
        _review_snapshot_unchanged(request.source_path, files)
        if (request.verification.command != verify_command
                or request.verification.image_id != verification_image_id
                or request.verification.cleanup_succeeded is not True):
            raise SessionError('Review verification policy differs')
        fingerprint = snapshot.fingerprint(files)
        prompt = _prompt(request, files)
        with StableCredentialHome(credential_root) as lease:
            try:
                policy = ChatGPTProvider._private_policy(binary, model, expected_identity)
                policy.home = lease.home
                policy._config = policy._configuration()
                policy._expected = tomllib.loads(policy._config)
                lease.write_config(policy._config)
                # Version probing creates runtime files and never needs credentials.
                with tempfile.TemporaryDirectory(prefix='nightshift-version-') as temporary:
                    home = Path(temporary)
                    home.chmod(0o700)
                    await _check_version(binary, home,
                        {'PATH': os.defpath, 'HOME': str(home), 'CODEX_HOME': str(home)}, deadline)
                session = Session(image_id, files, docker_host=docker_host,
                    recovery_dir=recovery_dir, deadline=deadline,
                    max_output_bytes=budgets.max_stream_bytes, readonly_source=True)
                try:
                    with session:
                        if session.readonly_source_confirmed is not True:
                            raise SessionError('Immutable review source was not confirmed before launch')
                        executor = NativeExecutor(session, lease.home)
                        try:
                            with executor:
                                lease.validate_startup(_startup(policy, executor))
                                remaining = deadline - time.monotonic()
                                if remaining <= 0:
                                    worker.status = 'budget_exhausted'
                                    raise _Rejected()
                                if validate_marker(native_marker, recovery_dir) != claim:
                                    raise SessionError('Review claim changed before launch')
                                provider_stopped = False
                                worker = await codex._run_stdio(
                                    WorkerRequest("review", Path("/workspace"), prompt, model,
                                        budgets=replace(budgets, max_runtime_s=remaining)),
                                    [str(binary), 'app-server', '--stdio', '--strict-config'],
                                    env=policy._environment(), external_executor=True,
                                    provider_cwd=lease.home, config_validator=policy._validate_configuration,
                                    admission=policy._admission())
                                # The transport returns only after its process group is reaped.
                                provider_stopped = True
                                if not worker.ok:
                                    raise _Rejected()
                            executor_stopped = _executor_stopped(executor)
                            unchanged = snapshot.fingerprint(session.finish()) == fingerprint
                            if not unchanged:
                                raise SessionError('Immutable review source changed')
                        finally:
                            executor_stopped = _executor_stopped(executor)
                finally:
                    session_cleanup = session.cleanup_succeeded
            finally:
                if provider_stopped and executor_stopped and (session is None or session_cleanup):
                    lease.confirm_stopped()
        lease_cleanup = lease.cleanup_succeeded
        if time.monotonic() >= deadline or validate_marker(native_marker, recovery_dir) != claim:
            raise SessionError('Review budget or claim changed')
        _current(root, request.candidate_sha, files, reference, deleted)
        _review_snapshot_unchanged(request.source_path, files)
        if time.monotonic() >= deadline:
            raise SessionError('Review exceeded its shared deadline')
        fresh = (type(worker.thread_id) is str and bool(worker.thread_id.strip())
                 and worker.thread_id != implementation_thread_id)
    except asyncio.CancelledError:
        # Transport cancellation normally reaps, but without a returned result its
        # shutdown cannot be attested here. Preserve recovery state conservatively.
        raise
    except Exception:
        unchanged = False
        detail = 'Stable review failed; retain recovery evidence'
    finally:
        if lease is not None:
            lease_cleanup = lease.cleanup_succeeded
    complete = worker.requested_model == model and allow_candidate(worker, provider_stopped=provider_stopped,
        executor_stopped=executor_stopped, session_cleanup=session_cleanup,
        lease_cleanup=lease_cleanup)
    return ReviewOutcome(worker, request.review_id, request.candidate_sha, fingerprint,
        bool(session and session.readonly_source_confirmed is True),
        complete and unchanged, fresh and complete and unchanged, detail)

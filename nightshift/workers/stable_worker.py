"""Private, unwired stable ChatGPT implementation attempt. Never clears claims.

Only generated policy is reused: no inherited provider runner, interactive profile,
API key, or custom-provider fallback. Public activation remains blocked.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from . import codex, snapshot
from .base import WorkerRequest, WorkerResult
from .chatgpt_provider import ChatGPTProvider
from .container_session import Session, SessionError
from .credential_home import StableCredentialHome
from .native_executor import NativeExecutor, _LAUNCHER
from .provider_home import _check_version
from .stable_worker_guard import NativeMarkerEvidence, allow_candidate, validate_marker


@dataclass(frozen=True)
class _StableRun:
    worker: WorkerResult
    files: tuple[snapshot.SourceFile, ...] | None = None
    provider_stopped: bool = True
    executor_stopped: bool = True
    session_cleanup: bool = False
    lease_cleanup: bool = False

    @property
    def ok(self):
        return self.files is not None and allow_candidate(self.worker,
            provider_stopped=self.provider_stopped, executor_stopped=self.executor_stopped,
            session_cleanup=self.session_cleanup, lease_cleanup=self.lease_cleanup)


class _Rejected(Exception):
    pass


def _startup(policy, executor):
    launcher = policy.home / 'nightshift-executor-launcher.py'
    environments = ('default="remote"\ninclude_local=false\n[[environments]]\n'
        'id="remote"\nprogram=' + json.dumps(sys.executable) + '\nargs=' +
        json.dumps([str(launcher), str(executor.socket_path)]) + '\ninitialize_timeout_sec=5\n')
    return {'config.toml': policy._config, 'environments.toml': environments,
            'nightshift-executor-launcher.py': _LAUNCHER}


def _executor_stopped(executor):
    # Both the actor and its child must be gone: an unjoined actor can launch late.
    return ((executor._thread is None or not executor._thread.is_alive())
            and (executor._process is None or executor._process.poll() is not None))


async def _run_stable_chatgpt(request: WorkerRequest, files, *, credential_root: Path,
        binary: Path, image_id: str, docker_host: str, recovery_dir: Path,
        expected_identity, native_marker: NativeMarkerEvidence) -> _StableRun:
    """One private implementation attempt; caller owns the durable native claim.

    Returned files are provisional candidate data, not verification or review.
    An exception with uncertain provider shutdown retains the credential journal.
    The separate exact-SHA reviewer adapter remains responsible for review.
    """
    deadline = time.monotonic() + request.budgets.max_runtime_s
    worker = WorkerResult(requested_model=request.model)
    provider_stopped = executor_stopped = True
    session_cleanup = lease_cleanup = False
    session = executor = lease = None
    candidate = None
    try:
        validate_marker(native_marker, recovery_dir)
        if (request.role != 'implement' or request.cwd != Path('/workspace')
                or request.transcript_path is not None or request.normalized_transcript_path is not None):
            raise SessionError('Stable implementation requires the isolated workspace')
        files = snapshot.validate(files)
        with StableCredentialHome(credential_root) as lease:
            try:
                policy = ChatGPTProvider._private_policy(binary, request.model, expected_identity)
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
                    max_output_bytes=request.budgets.max_stream_bytes)
                try:
                    with session:
                        executor = NativeExecutor(session, lease.home)
                        try:
                            with executor:
                                lease.validate_startup(_startup(policy, executor))
                                remaining = deadline - time.monotonic()
                                if remaining <= 0:
                                    worker.status = 'budget_exhausted'
                                    raise _Rejected()
                                provider_stopped = False
                                worker = await codex._run_stdio(
                                    replace(request, budgets=replace(request.budgets, max_runtime_s=remaining)),
                                    [str(binary), 'app-server', '--stdio', '--strict-config'],
                                    env=policy._environment(), external_executor=True,
                                    provider_cwd=lease.home, config_validator=policy._validate_configuration,
                                    admission=policy._admission())
                                # The transport returns only after its process group is reaped.
                                provider_stopped = True
                                if not worker.ok:
                                    raise _Rejected()
                            executor_stopped = _executor_stopped(executor)
                            candidate = tuple(session.finish())
                        finally:
                            executor_stopped = _executor_stopped(executor)
                finally:
                    session_cleanup = session.cleanup_succeeded
            finally:
                if provider_stopped and executor_stopped and (session is None or session_cleanup):
                    lease.confirm_stopped()
        lease_cleanup = lease.cleanup_succeeded
    except asyncio.CancelledError:
        # Transport cancellation normally reaps, but without a returned result its
        # shutdown cannot be attested here. Preserve recovery state conservatively.
        raise
    except Exception:
        candidate = None
    finally:
        if lease is not None:
            lease_cleanup = lease.cleanup_succeeded
    if not allow_candidate(worker, provider_stopped=provider_stopped,
            executor_stopped=executor_stopped, session_cleanup=session_cleanup,
            lease_cleanup=lease_cleanup):
        candidate = None
    return _StableRun(worker, candidate, provider_stopped, executor_stopped,
                      session_cleanup, lease_cleanup)

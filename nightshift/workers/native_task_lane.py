"""Dormant retained native task lane. No daemon/public routing or shipping."""
import asyncio
import os
import re
import selectors
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .. import outcomes, queue
from ..config import Repo
from ..verification import parse_commands
from .chatgpt_admission import ChatGPTIdentity
from .review_context import ApprovedTask, validate as validate_context
from . import candidate, snapshot
from .managed_task import _ValidatedProfile, _build_inputs, _path
from .native_persistence import NativePreparationLock, NativeClaimLease
from .stable_controller import _AttemptResult, _controller_binding, _run_attempt


def _git(root, *args, deadline, data=b'', https=False):
    """Bounded host Git with no inherited credentials, hooks or lazy fetch."""
    env = {'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1'}
    argv = ['/usr/bin/git', '--literal-pathspecs', '-c', 'core.hooksPath=/dev/null',
        '-c', 'core.fsmonitor=false', '-c', 'credential.helper=', '-c', 'http.proxy=',
        '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
        '-c', 'protocol.https.allow=' + ('always' if https else 'never'), *args]
    limit = min(deadline, time.monotonic() + 30)
    if time.monotonic() >= limit:
        raise ValueError('Native preparation deadline exhausted')
    # File stdin cannot deadlock against output from a large raw index update.
    with tempfile.TemporaryFile() as source:
        source.write(data)
        source.seek(0)
        process = subprocess.Popen(argv, cwd=root, env=env, stdin=source,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        output = bytearray()
        reaped = False
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = limit - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError('Native Git preparation timed out')
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > 1024 * 1024:
                        raise ValueError('Native Git output limit exceeded')
            code = process.wait(timeout=max(.001, limit - time.monotonic()))
            reaped = True
            if code != 0:
                raise ValueError('Native Git preparation failed')
            return bytes(output)
        finally:
            # Never signal a group ID after reaping its leader. On interrupted
            # reads the unreaped leader reserves the ID while we terminate it.
            if not reaped:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            process.stdout.close()


def _fetch_base(repository, repo_name, base, *, deadline):
    """Fetch a public GitHub branch without credentials into a fresh Git config.

    No local origin URL, credential helper, URL rewrite, or fetch hook config can
    choose the remote. Private-repository authentication is not supported here.
    """
    if type(repo_name) is not str or re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo_name) is None:
        raise ValueError('Explicit GitHub repository identity required')
    _git(repository, 'check-ref-format', 'refs/heads/' + base, deadline=deadline)
    with tempfile.TemporaryDirectory(prefix='native-fetch-') as temporary:
        root = Path(temporary)
        _git(root, 'init', '--bare', '--template=', '.', deadline=deadline)
        _git(root, 'fetch', '--no-tags', '--no-recurse-submodules', '--no-auto-maintenance',
            'https://github.com/' + repo_name + '.git',
            'refs/heads/' + base + ':refs/heads/native-base', deadline=deadline, https=True)
        sha = _git(root, 'rev-parse', '--verify', 'refs/heads/native-base^{commit}', deadline=deadline).decode().strip()
        _git(repository, 'fetch', '--no-tags', '--no-recurse-submodules', '--no-auto-maintenance',
            '--no-write-fetch-head', str(root), sha, deadline=deadline)
    return sha


def _fresh_worktree(repository, worktree, branch, base_sha, *, deadline):
    if os.path.lexists(worktree):
        raise ValueError('Native worktree already exists')
    _git(repository, 'check-ref-format', 'refs/heads/' + branch, deadline=deadline)
    if _git(repository, 'for-each-ref', '--format=%(refname)', 'refs/heads/' + branch, deadline=deadline):
        raise ValueError('Native branch already exists')
    files = snapshot.from_git(repository, base_sha, deadline=deadline)
    _git(repository, 'worktree', 'add', '--no-checkout', '-b', branch, str(worktree), base_sha, deadline=deadline)
    descriptor = os.open(worktree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for entry in files:
            candidate._write(descriptor, entry)
    finally:
        os.close(descriptor)
    # Populate the index directly: no checkout/smudge/clean filters or hooks.
    records = bytearray()
    for entry in files:
        oid = _git(worktree, 'hash-object', '-w', '--stdin', '--no-filters',
                   data=entry.content, deadline=deadline).strip()
        records.extend((b'100755' if entry.executable else b'100644') + b' ' + oid + b'\t' + entry.path.encode() + b'\0')
    _git(worktree, 'update-index', '-z', '--index-info', data=bytes(records), deadline=deadline)
    if not candidate._index_matches(worktree, files, base_sha):
        raise ValueError('Native initial index differs')
    candidate._check_files(worktree, files, ())
    return files


@dataclass(frozen=True)
class _RetainedTask:
    status: str = 'blocked'
    stage: str = 'validate'
    result: _AttemptResult | None = None

    @property
    def ready_for_shipping(self):
        return False


class NativeTaskLane:
    def run(self, *args, **kwargs) -> _RetainedTask:
        """Public activation is unconditionally blocked; no operator bypass."""
        return _RetainedTask()


async def _run_claimed_native(profile, *, identity_reference, identity, issue, repo,
        claim, repository: Path, worktree_root: Path, review_policy) -> _RetainedTask:
    """One private lane; every path retains ownership and reports local attention."""
    stage, result = 'validate', None
    owned = False
    ownership = None
    try:
        if (type(profile) is not _ValidatedProfile or type(issue) is not queue.Issue
                or type(repo) is not Repo or type(claim) is not queue.Claim
                or issue.repo != repo.name or claim.repo != repo.name or claim.number != issue.number
                or type(issue.number) is not int or type(claim.number) is not int or issue.number <= 0
                or type(identity) is not ChatGPTIdentity or type(identity_reference) is not str
                or identity_reference != profile.identity_reference
                or issue.revise is not False or claim.revise is not False):
            raise ValueError('Exact new native claimed task required')
        validate_context(ApprovedTask(repo.name + '#' + str(issue.number), issue.title, issue.body), review_policy)
        parse_commands(repo.verify)
        _path(repository, 'worktree')
        worktree = Path(claim.worktree)
        ownership = NativePreparationLock(claim, worktree, worktree_root, profile.recovery_root)
        with ownership:
            owned = True
            stage = 'prepare'
            deadline = time.monotonic() + min(120, profile.implement.budgets.max_runtime_s)
            if Path(_git(repository, 'rev-parse', '--show-toplevel', deadline=deadline).decode().strip()).resolve() != repository:
                raise ValueError('Explicit repository root required')
            base = queue.base_branch(issue.body) or repo.base
            base_sha = _fetch_base(repository, repo.name, base, deadline=deadline)
            _fresh_worktree(repository, worktree, claim.branch, base_sha, deadline=deadline)
            inputs = _build_inputs(profile, identity_reference=identity_reference, identity=identity,
                issue=issue, repo=repo, claim=claim, review_policy=review_policy)
            binding = _controller_binding(base_sha, inputs.request, inputs.verify_command,
                inputs.approved_task, inputs.review_policy, profile.review.model,
                profile.implementation_image_id, profile.verification_image_id, profile.review_image_id,
                profile.review.reasoning_effort)
            with NativeClaimLease(claim, worktree, profile.recovery_root, binding, ownership=ownership) as lease:
                with lease.open_journal() as journal:
                    stage = 'controller'
                    result = await _run_attempt(worktree, base_sha, inputs.request, inputs.verify_command,
                        approved_task=inputs.approved_task, review_policy=inputs.review_policy,
                        review_model=profile.review.model, credential_root=profile.credential_root,
                        binary=profile.binary, image_id=profile.implementation_image_id,
                        verification_image_id=profile.verification_image_id, review_image_id=profile.review_image_id,
                        docker_host=profile.docker_host, recovery_dir=lease.recovery_dir,
                        expected_identity=inputs.expected_identity, native_marker=lease.marker,
                        review_budgets=profile.review.budgets, review_reasoning_effort=profile.review.reasoning_effort,
                        prepared_journal=journal)
                    if type(result) is not _AttemptResult:
                        raise ValueError('Native controller returned untyped evidence')
                    stage = 'complete'
    except asyncio.CancelledError:
        raise
    except Exception:
        pass
    finally:
        # Creation of the durable tombstone starts ownership even if context
        # entry later fails (including ambiguous fsync); never erase that fact.
        owned = owned or bool(ownership is not None and ownership.ownership_started)
        # Reporting is best effort and never grants cleanup or retry authority.
        if owned and type(issue) is queue.Issue:
            try:
                outcomes.record(issue.repo, issue.number, state='needs_decision', native_retained=True,
                    reason='Native task retained for explicit local inspection', escalated=False)
            except Exception:
                pass
    return _RetainedTask(status='retained' if owned else 'blocked', stage=stage, result=result)

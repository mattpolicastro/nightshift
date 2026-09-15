"""Dormant typed native dispatch boundary. No daemon caller or activation flag.

Runtime values are explicit operator-owned inputs, not a claim that native
execution is publicly supported. Public dispatch remains unconditionally blocked.
"""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import queue
from .config import Repo
from .workers.chatgpt_admission import ChatGPTIdentity
from .workers.container_session import SessionError
from .workers.managed_task import _ValidatedProfile, _Phase, _validated_profile, _path
from .workers.native_task_lane import _RetainedTask, _run_claimed_native
from .workers.review_context import ReviewPolicy, ApprovedTask, validate


@dataclass(frozen=True, init=False, repr=False)
class _NativeRuntime:
    profile: _ValidatedProfile
    identity_reference: str
    identity: ChatGPTIdentity
    worktree_root: Path

    def __init__(self, *args, **kwargs):
        raise SessionError('Public native runtime construction is disabled')

    @property
    def execution_enabled(self):
        return False


def _validated_runtime(profile, *, identity_reference, identity, worktree_root):
    """Private data validation only: no credential lookup, CLI or provider call."""
    if (type(profile) is not _ValidatedProfile or type(identity) is not ChatGPTIdentity
            or type(identity_reference) is not str or identity_reference != profile.identity_reference
            or type(profile.implement) is not _Phase or type(profile.review) is not _Phase):
        raise ValueError('An exact managed profile and private identity binding are required')
    def phase(value):
        return {'driver': 'codex-app-server', 'provider': 'openai', 'auth': 'chatgpt',
                'model': value.model, 'reasoning_effort': value.reasoning_effort, 'budgets': value.budgets}
    checked = _validated_profile(credential_root=profile.credential_root,
        identity_reference=profile.identity_reference, binary=profile.binary,
        binary_version=profile.binary_version, implementation_image_id=profile.implementation_image_id,
        review_image_id=profile.review_image_id, verification_image_id=profile.verification_image_id,
        docker_host=profile.docker_host, recovery_root=profile.recovery_root,
        phases={'implement': phase(profile.implement), 'review': phase(profile.review)})
    worktree_root = _path(worktree_root, 'worktree')
    for protected in (checked.credential_root, checked.recovery_root):
        if (worktree_root == protected or worktree_root in protected.parents
                or protected in worktree_root.parents):
            raise ValueError('Native worktree and protected namespaces must be disjoint')
    runtime = object.__new__(_NativeRuntime)
    for key, value in {'profile': checked, 'identity_reference': identity_reference,
            'identity': ChatGPTIdentity(identity.email, identity.account_id),
            'worktree_root': worktree_root}.items():
        object.__setattr__(runtime, key, value)
    return runtime


class _Route(Enum):
    LEGACY = 'legacy'
    MANAGED_NATIVE = 'managed-native'
    BLOCKED = 'blocked'


def _dispatch_route(driver, runtime=None):
    """Structural routing only; native still requires local evidence validation.

    Intended-native input never becomes legacy when configuration is missing.
    This function does not call or enable either execution backend.
    """
    if type(driver) is not str:
        return _Route.BLOCKED
    if driver == 'claude-code' and runtime is None:
        return _Route.LEGACY
    if driver == 'codex-app-server' and type(runtime) is _NativeRuntime:
        return _Route.MANAGED_NATIVE
    return _Route.BLOCKED


class NativeDispatch:
    def run(self, *args, **kwargs) -> _RetainedTask:
        """Always blocked, including when typed input or operator flags are given."""
        return _RetainedTask(status='blocked', stage='dispatch_disabled')


async def _dispatch_claimed(runtime, *, issue, repo, claim, repository, review_policy,
                            driver='codex-app-server'):
    """Private testable handoff; no daemon/config path calls this function.

    Local ownership is checked before profile filesystem inspection or any lane
    Git/provider action. Native tombstones cannot fall into legacy dispatch.
    """
    delegated = False
    try:
        if (_dispatch_route(driver, runtime) is not _Route.MANAGED_NATIVE or type(issue) is not queue.Issue
                or type(repo) is not Repo or type(claim) is not queue.Claim
                or type(issue.number) is not int or type(claim.number) is not int or issue.number <= 0
                or issue.repo != repo.name or claim.repo != repo.name or claim.number != issue.number
                or claim.phase != 'claimed' or claim.revise is not False or issue.revise is not False):
            return _RetainedTask(status='blocked', stage='invalid_native_input')
        records, repairs = queue._load_claims(repo.name)
        retained = {entry.number for entry in repairs if entry.repair is queue.Repair.RETAINED}
        if 0 in retained or issue.number in retained or claim.native_recovery is not None:
            return _RetainedTask(status='retained', stage='recovery_required')
        current = records.get(issue.number)
        if current is None or current != claim:
            return _RetainedTask(status='blocked', stage='claim_changed')
        if current.native_recovery is not None:
            return _RetainedTask(status='retained', stage='recovery_required')
        validate(ApprovedTask(repo.name + '#' + str(issue.number), issue.title, issue.body), review_policy)
        # Revalidate even an old exact runtime: files, socket and permissions
        # may have changed since its configuration-only construction.
        checked = _validated_runtime(runtime.profile, identity_reference=runtime.identity_reference,
            identity=runtime.identity, worktree_root=runtime.worktree_root)
        delegated = True
        result = await _run_claimed_native(checked.profile, identity_reference=checked.identity_reference,
            identity=checked.identity, issue=issue, repo=repo, claim=current,
            repository=repository, worktree_root=checked.worktree_root, review_policy=review_policy)
        if type(result) is not _RetainedTask:
            raise ValueError('Native lane returned untyped outcome')
        return result
    except Exception:
        if delegated:
            return _RetainedTask(status='retained', stage='native_handoff_ambiguous')
        return _RetainedTask(status='blocked', stage='native_validation_failed')

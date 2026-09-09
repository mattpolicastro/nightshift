"""Private read-only managed-account metadata qualification.

No model thread/turn, credential provisioning, login, or daemon activation.
Passes only point-in-time admission: it does not establish keyring isolation or
reserve usage. Caller owns the generated provider policy and external executor
binding. This module writes no transcripts and returns no account data/stderr.
"""
import asyncio
from dataclasses import dataclass
from pathlib import Path

from . import codex
from .base import WorkerBudgets, WorkerRequest


@dataclass(frozen=True)
class _QualificationResult:
    status: str
    requested_model: str
    duration_s: float = 0

    @property
    def passed(self):
        return self.status == 'passed'

    @property
    def execution_enabled(self):
        return False


async def _qualify_managed_account(model: str, argv: list[str], *, env: dict[str, str],
                                  expected_identity, external_executor: bool,
                                  provider_cwd: Path, config_validator,
                                  budgets: WorkerBudgets | None = None,
                                  reasoning_effort: str | None = None) -> _QualificationResult:
    """Run guarded config/account/usage/catalog RPCs, never thread/start or turn/start.

    `config_validator` must be the caller's bound generated-provider validator.
    Identity is mandatory; it is neither returned nor written to a transcript.
    Inputs are explicit private harness capabilities, not operator activation flags.
    """
    from .chatgpt_admission import ChatGPTAdmission, ChatGPTIdentity
    if (not isinstance(expected_identity, ChatGPTIdentity) or external_executor is not True
            or not isinstance(provider_cwd, Path) or not provider_cwd.is_absolute()
            or not callable(config_validator)):
        return _QualificationResult('protocol_error', model)
    try:
        request = WorkerRequest('review', Path('/workspace'), '', model,
                                budgets=budgets or WorkerBudgets(), reasoning_effort=reasoning_effort)
    except (TypeError, ValueError, AttributeError):
        return _QualificationResult('protocol_error', model)
    try:
        result = await codex._run_stdio(request, argv, env=env, external_executor=True,
            provider_cwd=provider_cwd, config_validator=config_validator,
            admission=ChatGPTAdmission(expected_identity=expected_identity), preflight_only=True)
    except asyncio.CancelledError:
        return _QualificationResult('interrupted', model)
    except Exception:
        # A trusted callback failure may still contain private provider metadata.
        # The owned transport performs cleanup; never propagate its raw exception.
        return _QualificationResult('protocol_error', model)
    # Internal success has no model output or task meaning. Never expose raw
    # diagnostics, stderr, text, identity, or token/credit data from this seam.
    if result.ok and result.thread_id is None and result.turn_id is None and not result.commands:
        return _QualificationResult('passed', model, result.duration_s)
    return _QualificationResult(result.status if not result.ok else 'protocol_error', model, result.duration_s)

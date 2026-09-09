"""Private composition of a stable credential-home lease and metadata admission.

Never inherits a provider run() method, starts NativeExecutor, creates a model
thread/turn, or enables dispatch. An inert remote-only descriptor prevents local
fallback. The lease remains held until provider shutdown is confirmed; uncertain
shutdown retains its recovery state. Authentication is managed externally.
"""
import time
import tomllib
from dataclasses import replace
from pathlib import Path

from .base import WorkerBudgets
from .chatgpt_provider import ChatGPTProvider
from .managed_qualification import _QualificationResult, _qualify_managed_account
from .provider_home import _check_version

_REMOTE_ONLY = ('default="remote"\ninclude_local=false\n[[environments]]\nid="remote"\n'
                'program="/usr/bin/false"\nargs=[]\ninitialize_timeout_sec=1\n')


async def _qualify_stable_home(root: Path, binary: Path, model: str, *, expected_identity,
                              budgets: WorkerBudgets | None = None,
                              reasoning_effort: str | None = None) -> _QualificationResult:
    """Private metadata-only attempt, not a stable-home native worker adapter."""
    from .credential_home import StableCredentialHome

    budgets = budgets or WorkerBudgets()
    deadline = time.monotonic() + budgets.max_runtime_s
    result = _QualificationResult('protocol_error', model)
    process_may_exist = False
    try:
        with StableCredentialHome(root) as lease:
            try:
                # Reuse the reviewed generated policy/validator without entering
                # its temporary-home context or exposing its inherited runner.
                policy = ChatGPTProvider._private_policy(binary, model, expected_identity)
                policy.home = lease.home
                policy._config = policy._configuration()
                policy._expected = tomllib.loads(policy._config)
                lease.write_config(policy._config)
                lease.write_asset('environments.toml', _REMOTE_ONLY)
                startup = {'config.toml': policy._config, 'environments.toml': _REMOTE_ONLY}
                lease.validate_startup(startup)
                env = policy._environment()
                process_may_exist = True
                try:
                    await _check_version(binary, lease.home, env, deadline)
                finally:
                    # _check_version owns and always reaps its process group.
                    process_may_exist = False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return _QualificationResult('budget_exhausted', model, provider_stopped=True)
                lease.validate_startup(startup)
                process_may_exist = True
                result = await _qualify_managed_account(model,
                    [str(binary), 'app-server', '--stdio', '--strict-config'], env=env,
                    expected_identity=expected_identity, external_executor=True,
                    provider_cwd=lease.home, config_validator=policy._validate_configuration,
                    budgets=replace(budgets, max_runtime_s=remaining), reasoning_effort=reasoning_effort)
                process_may_exist = not result.provider_stopped
            finally:
                if not process_may_exist:
                    lease.confirm_stopped()
        return result
    except Exception:
        # No raw policy, account identity, credential path, or child diagnostics.
        return _QualificationResult('protocol_error', model, provider_stopped=not process_may_exist)

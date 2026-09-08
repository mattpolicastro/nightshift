"""Transitional Claude Code adapter, not yet used by production dispatch.

The legacy runtime remains responsible for its role permissions, transcript and
max-turn limit. This adapter deliberately accepts legacy-shaped arguments: it
cannot promise the Codex transport's elapsed/action/token/byte budgets.

Legacy parsed results remain available for existing quota and verdict consumers.
Their inferred billing and default-zero telemetry are NOT normalized as facts.
"""
from __future__ import annotations

import copy
import json
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .. import config, trace, worker
from .base import WorkerResult


@dataclass(frozen=True)
class ClaudeRunResult:
    result: WorkerResult
    legacy: trace.Result | None
    # Raw reported accounting, including unknown additive fields. None means
    # absent; an explicitly reported zero remains distinguishable from unknown.
    native_usage: dict | None = None
    model_usage: dict | None = None
    requested_commands: tuple[str, ...] = ()
    observed_models: tuple[str, ...] = ()
    session_id: str | None = None
    legacy_verdict: bool | None = None


def _string(value):
    return value if isinstance(value, str) and value else None


def normalize(run: worker.Run, *, requested_model: str | None = None,
              role: Literal["implement", "review"] | None = None) -> ClaudeRunResult:
    """Normalize only evidence actually present in the terminal stream.

    A Bash tool-use request does not prove execution or an exit code, so command
    completions stay empty. Plain-text legacy PASS/FAIL is retained separately;
    it does not become a fabricated structured review with empty findings.
    """
    result = WorkerResult(runtime="claude-code", requested_model=requested_model)
    terminal = None
    observed_models: list[str] = []
    try:
        for line in run.events.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue  # Legacy CLI diagnostics share its stdout stream.
            if not isinstance(event, dict):
                continue
            if event.get("type") == "result":
                terminal = event
            elif event.get("type") == "system" and event.get("subtype") == "init":
                result.runtime_version = _string(event.get("claude_code_version"))
            elif event.get("type") == "assistant":
                message = event.get("message")
                model = _string(message.get("model")) if isinstance(message, dict) else None
                if model and model not in observed_models:
                    observed_models.append(model)
        legacy = worker.parse_result(run)
    except (AttributeError, TypeError, ValueError):
        result.diagnostics.append("Malformed legacy worker telemetry")
        return ClaudeRunResult(result, None)

    if len(observed_models) == 1:
        result.observed_model = observed_models[0]
    native_usage = None
    model_usage = None
    session_id = None
    if terminal is not None:
        result.text = terminal.get("result") if isinstance(terminal.get("result"), str) else ""
        session_id = _string(terminal.get("session_id"))
        duration = terminal.get("duration_ms")
        if type(duration) in (int, float) and duration >= 0:
            import math
            if math.isfinite(duration):
                result.duration_s = duration / 1000
        if isinstance(terminal.get("usage"), dict):
            native_usage = copy.deepcopy(terminal["usage"])
            mapping = {
                "input_tokens": "inputTokens", "output_tokens": "outputTokens",
                "cache_read_input_tokens": "cachedInputTokens",
                "cache_creation_input_tokens": "cacheWriteInputTokens",
            }
            for native, normalized in mapping.items():
                value = native_usage.get(native)
                if type(value) is int and value >= 0:
                    result.usage[normalized] = value
        if isinstance(terminal.get("modelUsage"), dict):
            model_usage = copy.deepcopy(terminal["modelUsage"])
        if legacy is not None:
            result.denied_actions = [denial.tool for denial in legacy.denials]

    if run.returncode in {-signal.SIGINT, -signal.SIGTERM, -signal.SIGKILL}:
        result.status = "interrupted"
        result.diagnostics.append("Claude Code worker terminated by signal")
    elif legacy is not None and legacy.truncated:
        result.status = "budget_exhausted"
    elif terminal is not None and terminal.get("api_error_status") in (401, 403):
        result.status = "auth_failed"
    elif terminal is not None and terminal.get("api_error_status") == 429:
        result.status = "rate_limited"
    elif run.returncode != 0:
        result.status = "failed"
        result.diagnostics.append("Claude Code process exited unsuccessfully")
    elif legacy is None or terminal is None:
        result.status = "protocol_error"
        result.diagnostics.append("No terminal Claude Code result")
    elif terminal.get("is_error") is True:
        result.status = "failed"
    elif terminal.get("subtype") != "success" or terminal.get("is_error") is not False:
        result.status = "protocol_error"
        result.diagnostics.append("Unrecognized or incomplete terminal Claude Code status")
    elif not isinstance(terminal.get("result"), str):
        result.status = "protocol_error"
        result.diagnostics.append("Terminal Claude Code result lacks final text")
    else:
        result.status = "succeeded"

    return ClaudeRunResult(
        result=result, legacy=legacy, native_usage=native_usage, model_usage=model_usage,
        requested_commands=tuple(legacy.commands) if legacy is not None else (),
        observed_models=tuple(observed_models), session_id=session_id,
        legacy_verdict=trace.verdict(result.text) if role == "review" else None,
    )


class ClaudeWorker:
    """Legacy invocation + normalized result; only max_turns is a runtime limit.

    No generic WorkerRequest is accepted, so native Codex budgets or structured
    output requirements cannot be silently ignored. This does not harden the
    legacy runner's filesystem, credential, cancellation or logging boundaries.
    """
    def run(self, worktree: Path, prompt: str, *, role: Literal["implement", "review"],
            model: str, max_turns: int, endpoint: config.Endpoint | None = None,
            context_tokens: int = 0, foreign_auth_envs: tuple[str, ...] = (),
            transcript: Path | None = None) -> ClaudeRunResult:
        if role not in {"implement", "review"}:
            raise ValueError("Unknown Claude Code role")
        if type(max_turns) is not int or max_turns <= 0:
            raise ValueError("Claude Code requires a positive max_turns limit")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("An explicit model is required")
        invoke = worker.implement if role == "implement" else worker.review
        try:
            raw = invoke(worktree, prompt, model=model, max_turns=max_turns,
                         endpoint=endpoint, context_tokens=context_tokens,
                         foreign_auth_envs=foreign_auth_envs, transcript=transcript)
        except OSError:
            return ClaudeRunResult(WorkerResult(
                runtime="claude-code", requested_model=model, status="failed",
                diagnostics=["Claude Code worker invocation failed"]), None)
        return normalize(raw, requested_model=model, role=role)

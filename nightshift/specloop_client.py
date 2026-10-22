"""The daemon's subprocess boundary to specloop's versioned JSON interface.

No specloop internals are imported. The runner has subprocess.run's signature;
tests can supply completed processes without launching a CLI or an agent.
"""

from __future__ import annotations

import json
import subprocess


class SpecloopError(RuntimeError):
    """An invocation failed or did not speak the version-1 protocol."""


def call(args: list[str], *, runner=None) -> dict:
    """Run a command, preserving structured refusals even on nonzero exits.

`needs_input`, `rejected` and `refused` are domain outcomes the caller must
present to a human. A process/transport failure is never an empty result.
"""
    command = ["specloop", *args, "--json"]
    try:
        proc = (runner or subprocess.run)(
            command, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise SpecloopError(f"could not run specloop: {exc}") from exc
    try:
        result = json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise SpecloopError(
            f"specloop returned invalid JSON (exit {proc.returncode}): {proc.stderr.strip()}"
        ) from exc
    if not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1:
        raise SpecloopError("specloop returned an unsupported JSON document; expected version 1")
    if "error" in result:
        raise SpecloopError(f"specloop: {result['error']}")
    if proc.returncode and result.get("outcome") not in ("needs_input", "rejected", "refused"):
        raise SpecloopError(f"specloop failed (exit {proc.returncode}): {proc.stderr.strip()}")
    return result

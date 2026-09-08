"""Show a finished phase in Paseo, without letting Paseo run anything.

Paseo is the surface Matt already reaches from a phone; Nightshift is where the
work actually runs. Keeping those separate is deliberate. Dispatching THROUGH
Paseo was measured on 2026-09-05 and rejected: `paseo run` cannot pass
`--allowedTools`/`--disallowedTools`, so the reviewer's read-only contract
would be gone; its agents park on permission prompts rather than hard-denying,
so an unattended queue would hang; and its persisted transcript has no `result`
event, so `trace.parse` returns None and truncation, denials and `costBasis`
all disappear.

Reporting INTO it costs none of that. Nightshift keeps `claude -p`, its flags,
and its telemetry; Paseo gets a read-only copy to display.

**Why a symlink.** Workers run under an isolated `CLAUDE_CONFIG_DIR` so they
cannot reach the interactive login's credentials, and Paseo's daemon only reads
`~/.claude/projects`. Setting `CLAUDE_CONFIG_DIR` for the `paseo` CLI does not
help — the DAEMON performs the import, so the CLI's environment is irrelevant,
and it fails by silently producing an empty agent rather than by erroring.
Linking the session file into the default projects directory leaves the
isolation intact and gives the daemon something to find.

**Why at the END of a phase.** `paseo import` snapshots: re-importing is
refused with "Provider session is already imported", and `attach` replays the
cache rather than tailing, so an agent imported at the start stays frozen at
its first few turns. Importing once the phase is done — but before the daemon
tears the worktree down, which import requires to exist — puts the COMPLETE
transcript on the phone a minute after it happened. Live progress is
`nightshift status`, which reads the streamed transcript directly.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from . import worker

log = logging.getLogger("nightshift")

DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"


def _session_of(transcript: Path) -> tuple[str, str]:
    """The `(session_id, cwd)` the CLI reported in its init event.

    Scans rather than reading the first line: `worker._run` merges stderr into
    the stream, so line one is often the `[claude-code:unrecognized_model]`
    warning rather than JSON.
    """
    try:
        text = transcript.read_text()
    except OSError:
        return "", ""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("session_id") and event.get("cwd"):
            return str(event["session_id"]), str(event["cwd"])
    return "", ""


def _link(session_id: str, config_root: Path) -> Path | None:
    """Link a worker's session into the projects dir Paseo's daemon reads."""
    found = list(config_root.glob(f"**/projects/*/{session_id}.jsonl"))
    if not found:
        return None
    source = found[0]
    destination = DEFAULT_PROJECTS / source.parent.name / source.name
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.symlink_to(source)
    except OSError as exc:  # noqa: BLE001 — a display feature, never a failure
        log.warning("mirror: could not link %s: %s", session_id, exc)
        return None
    return destination


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc.stdout


def mirror(
    transcript: Path | None,
    *,
    repo: str,
    issue: int,
    phase: str,
    config_root: Path | None = None,
    runner=None,
) -> str:
    """Best effort. Returns what happened, for the log; never raises.

    A phase that ran is worth strictly more than a phase that is visible, so
    nothing here is allowed to affect the outcome of a task.
    """
    if transcript is None:
        return ""
    session_id, cwd = _session_of(transcript)
    if not session_id:
        return "no session id in the transcript"
    if not _link(session_id, config_root or worker.CONFIG_DIR):
        return f"session {session_id[:8]} not on disk"
    if not Path(cwd).exists():
        # Import requires it, and the daemon removes worktrees on teardown —
        # which is why this runs before that rather than after.
        return f"worktree {cwd} is gone; too late to import"
    try:
        out = (runner or _default_runner)(
            [
                "paseo", "import", session_id,
                "--provider", "claude",
                "--cwd", cwd,
                "--label", f"nightshift_repo={repo}",
                "--label", f"nightshift_issue={issue}",
                "--label", f"nightshift_phase={phase}",
                "--json",
            ]
        )
    except (RuntimeError, OSError, FileNotFoundError) as exc:
        # `paseo` absent is the common case on a machine that does not run it.
        return f"paseo import failed: {exc}"
    # `--json` is NOT pure JSON: `paseo run` prints "Created workspace …" and a
    # "Tip:" line first, so scan for the object rather than parsing the stream.
    brace = out.find("{")
    if brace < 0:
        return "paseo import returned no JSON"
    try:
        agent = json.loads(out[brace:]).get("agentId")
    except json.JSONDecodeError:
        return "paseo import returned unparseable JSON"
    return f"mirrored {phase} to paseo agent {str(agent)[:8]}"

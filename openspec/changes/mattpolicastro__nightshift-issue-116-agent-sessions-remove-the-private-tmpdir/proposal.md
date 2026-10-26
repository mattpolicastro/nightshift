## Why

Each Codex session creates `<transcript-stem>.tmp` beside its transcript and
passes it as `$TMPDIR`, but never removes it. A host cleanup on 2026-10-26
removed 546 directories from finished runs, approximately 44 GB. Session
scratch should end with the session instead of accumulating with retained
attempt evidence.

## What Changes

- Keep the existing per-session directory, creation behavior and environment.
- Remove that directory after the session's process group stops executing,
  including success, failure, wall/idle timeout and agent termination.
- Repair owner-write permissions on read-only directories as needed, without
  following symlinks or changing targets outside the private directory.
- Record cleanup failures in a separate per-session JSONL event file; preserve
  the original result or exception even when cleanup or event writing fails.
- Add local subprocess and filesystem regression tests, and document scratch
  lifetime and the pending first-build host check.

Implementation scope is `specloop/agents/codex.py`,
`specloop/agents/__init__.py`, new files under `tests/`,
`docs/specloop/README.md` and `KNOWN-LIMITATIONS.md`.

Explicitly out of scope: sandbox flags, existing attempt evidence (including
transcripts, `*.out.json`, `seal.json`, receipts and `review*`), verify build
directories, Claude temporary-directory policy, historical-directory sweeping,
daemon job control, crash recovery and new retention settings. Killing the
harness itself with SIGKILL or losing the host cannot execute finalizers and
is not covered by the agent-kill guarantee.

## Capabilities

### New Capabilities

- `agent-session-tmpdir`: session-scoped scratch removal, process-group ordering,
  confined deletion and non-fatal cleanup events.

### Modified Capabilities

- `specloop-documentation`: add scratch-lifetime and validation-limit guidance.
  This is an ADDED requirement; existing documentation requirements remain.

Existing provider classification, host environment, evidence retention and
background-job control requirements remain unchanged. No existing requirement
needs a MODIFIED or REMOVED delta.

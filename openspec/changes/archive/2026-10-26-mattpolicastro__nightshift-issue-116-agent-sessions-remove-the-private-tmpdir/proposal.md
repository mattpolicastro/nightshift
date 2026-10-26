## Why

Each Codex session creates `<transcript-stem>.tmp` beside its transcript and
passes it as `$TMPDIR`, but never removes it. A host cleanup on 2026-10-26
removed 546 directories from finished runs, approximately 44 GB. Session
scratch should be disposed of when the adapter finishes the session.

## What Changes

- Wrap the Codex session lifecycle in `try`/`finally`, covering temp directory
  creation, setup, spawning and result interpretation. Remove scratch with
  `shutil.rmtree` during finalization.
- Preserve per-session creation and writable `TMPDIR` behavior. Retry a
  permission failure once after adding owner-write permission to the containing
  directory, without following symlinks outside scratch.
- Append a cleanup-failure event to existing session stderr evidence.
  Cleanup and diagnostic failures never change a `Result` or mask an exception.
- Add regression tests, including the four tests named in issue #116, and
  update the run-state passage and one known-limitations entry.

Implementation scope is `specloop/agents/codex.py`, new tests under `tests/`,
`docs/specloop/README.md` and `KNOWN-LIMITATIONS.md`. Existing assertions remain
intact. The only evidence change authorized is appending the cleanup diagnostic
to session stderr; transcripts, outputs, seals, receipts and reviews remain.

Explicitly out of scope: changes to `specloop/agents/__init__.py` or `spawn()`'s
existing timeout/idle `killpg` handling; process-group inspection, descendant
termination or PGID ownership tracking; `tests/test_host_paths.py` migration;
sandbox flags; Claude behavior; evidence deletion; verify build directories;
historical sweeps, new cleanup sidecars and crash recovery. A descendant left
running after its leader exits may race removal; any resulting removal failure
is recorded and non-fatal.

## Capabilities

### New Capabilities

- `agent-session-tmpdir`: Codex scratch finalization, permission-tolerant
  symlink-safe removal and non-fatal cleanup diagnostics.

### Modified Capabilities

- `specloop-documentation`: an ADDED requirement explaining session scratch
  lifetime and the pending first-build host check.

Existing provider classification, host environment, protected-test and
documentation requirements remain intact. No existing requirement requires
a MODIFIED or REMOVED delta.

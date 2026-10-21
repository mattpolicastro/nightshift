## Why

`nightshift attention` filters specloop pauses through `HUMAN_REASONS`. That
allowlist hides infrastructure pauses after their automatic resumes run out
and omits the newer `requested` pause. Both can wait indefinitely for someone
who never sees them in the attention list.

## What Changes

- Include the approved minimal addition to `specloop/cli.py`: expose remaining
  automatic resumes on each non-null pause in version-1 status JSON.
- Replace `HUMAN_REASONS` in `nightshift/specloop_attention.py` with classification
  based on the pause's automatic-resume allowance, with a defined fallback for
  older status responses.
- List exhausted infrastructure pauses, requested pauses, and other pauses
  without automatic recovery; continue excluding pauses that can resume
  automatically and runs that are not paused.
- Preserve attention entry fields, cached/offline behavior, stale handling,
  worker tasks, and existing exit codes.
- Add producer and consumer regressions, including
  `test_exhausted_infra_pause_is_listed` and `test_requested_pause_is_listed`.

Explicitly out of scope: supervisor retry limits or scheduling changes,
persisted state migrations, pause/resume commands, action messages or verbs,
Paseo plugin changes, and unrelated documentation cleanup. Implementation is
limited to `specloop/cli.py`, `nightshift/specloop_attention.py`, and their tests.
Paseo presentation remains a human verification item.

## Capabilities

- `specloop-interface`: add the remaining-automatic-resumes status contract.
- `attention`: add automatic-resume-aware pause selection and compatibility
  behavior to the existing attention capability.

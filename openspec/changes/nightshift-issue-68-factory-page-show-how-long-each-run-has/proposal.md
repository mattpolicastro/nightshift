## Why

Factory identifies paused runs but does not show how long they have waited for
a person. Showing elapsed time makes neglected decisions visible, with warning
color after the three-hour stall-alert threshold.

## What Changes

- Add `pause.since` to every non-null pause in named and all-run
  `specloop status --json` responses, using the latest pause event's epoch
  seconds or null when unavailable. Keep document version 1 and read-only status.
- Add optional pause display fields to the Factory schema and make the approved
  minimal server projection edit: forward only `since` and
  `auto_resumes_remaining`, retaining existing validation and protections.
- Show `waiting Nm` below one hour and `waiting Nh` thereafter beside the status
  chip/progress block only for paused runs with zero automatic resumes remaining
  and a known timestamp. Use `foregroundMuted` through three hours and
  `statusWarning` after three hours.
- Add Python, server, model, and rendered-view tests in new files. Preserve
  existing assertions; only necessary fixture/expected-data additions for the
  additive contract are permitted, explained in the implementation PR.
- Implementation production scope is `specloop/cli.py` (JSON status only),
  `plugins/nightshift/shared/factory.ts`, `plugins/nightshift/server/factory.ts`
  (projection only), `plugins/nightshift/client/factory-model.ts`, and
  `plugins/nightshift/client/factory.tsx`.
- Out of scope: daemon, intake, stall watcher, supervisor policy, persisted
  state/event formats, CLI text output, notifications, new polling mechanisms,
  action behavior, deployment, and unrelated UI changes.

## Capabilities

- `specloop-interface`: add a pause timestamp requirement to the existing
  version-1 status contract.
- `paseo-runs-page`: add validated pause display metadata and elapsed human-wait
  presentation requirements.

These are additive requirements in existing capabilities. No existing
requirement is modified or removed, including the exact status/progress text
and all behaviors in `paseo-runs-page`, `factory-finished-runs`, `run-context`,
and `run-actions`.

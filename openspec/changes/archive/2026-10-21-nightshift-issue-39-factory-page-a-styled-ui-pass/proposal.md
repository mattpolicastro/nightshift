## Why

Factory works, but its actions, links, and toggles look like an undecorated
cluster of clickable text. Matt needs a clear visual hierarchy on desktop and
phone without changing how runs are operated.

## What Changes

- Give run actions padded, rounded buttons with at least 44-point targets:
  the first listed verb is primary, Abort is always destructive, and other
  verbs are outlined. Apply the same roles to confirmation controls.
- Group the currently available navigation links into one secondary row,
  preserving their existing visibility conditions and destinations.
- Show a status chip beside muted task progress using the host theme.
- Style Details and Finished as secondary disclosure rows with chevrons,
  and Refresh Factory as a compact secondary button.
- Add rendered-style and structure tests without modifying existing tests.
  Preserve every existing accessibility label, role, state, and tested text.

Implementation scope is `plugins/nightshift/client/factory.tsx`, optionally
`plugins/nightshift/client/factory-model.ts` for a presentation helper, appended
new tests in `plugins/nightshift/tests/factory-view.test.ts`, and new files under
`plugins/nightshift/tests/`. The default plan uses new test files only.

Explicitly out of scope: `plugins/nightshift/server/*`, all `shared/*`, the
attention section, specloop, the daemon, dependencies, deployment automation,
and changes to behavior. No files listed as work in flight need edits, including
`plugins/nightshift/tests/factory.test.ts`.

## Capabilities

- `paseo-runs-page`: add presentation requirements for action roles, link
  grouping, status chips, secondary disclosures, and refresh styling.
  Existing requirements remain in force; there are no MODIFIED or REMOVED
  requirements.
- Preserve all requirements of `factory-finished-runs`, `run-context`, and
  `run-actions` unchanged. This adds no RPC, model contract, or engine capability.

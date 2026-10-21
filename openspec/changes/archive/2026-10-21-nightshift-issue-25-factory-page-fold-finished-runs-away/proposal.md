## Why

`specloop status` continues returning `done` and `aborted` runs until their
directories move. Factory currently displays them alongside running work,
so finished runs accumulate after merges and obscure runs that need attention.

## What Changes

- Display active runs (`paused`, `running`) first, with paused runs first and
  source order preserved within each active status.
- Put finished runs (`done`, `aborted`) below active runs inside an initially
  collapsed `Finished (N)` toggle exposing `accessibilityState.expanded`.
  Expanded finished runs use the existing rows, in source order.
- Count active runs in the summary, appending ` · M finished` when applicable;
  preserve existing singular grammar and stale indication.
- Add `groupFactoryRuns(runs)` returning `{ active, finished }`, leaving
  `orderFactoryRuns` and its existing tests untouched.
- Keep action results visible exactly once: in their row while rendered, or
  in the existing above-list slot identifying the change while the row is
  folded or absent. Expanding moves the notice into its row without a duplicate.
- Add model and view tests, including the two requested test names. Preserve
  action results, Details, confirmations, refresh, and stale handling.

Implementation is limited to `plugins/nightshift/client/factory.tsx`,
`plugins/nightshift/client/factory-model.ts`, and new tests or compatible test
harness extensions under `plugins/nightshift/tests/`. Existing test cases are
not edited. No existing test asserts the count text, so no count-test exception
is needed.

Explicitly out of scope: specloop, the daemon, `plugins/nightshift/server/*`,
`shared/*` (including the plugin's shared contracts), run-directory cleanup,
archiving, deployment, persistent fold preferences, and unrelated page changes.

## Capabilities

- **New: `factory-finished-runs`** — client-side run grouping, a finished-run
  disclosure, and active/finished counts. This adds presentation rules to the
  existing `paseo-runs-page` and `run-context` behavior without changing their
  action, Details, or RPC contracts. All runs remain available on the page.

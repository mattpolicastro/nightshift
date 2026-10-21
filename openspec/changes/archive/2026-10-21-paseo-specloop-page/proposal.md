## Why

Paseo is where Matt works, on desktop and phone. specloop's pauses currently
reach him as Slack messages he can only read, and acting on one means a
terminal. Once `nightshift-on-specloop` lands, the Nightshift Paseo page will
show specloop pauses read-only (through `nightshift attention`). This change
lets him act on them there.

## What Changes

- specloop's `status --json` carries, for each paused run, the same action
  (headline, why, command) its Slack message gives, so Paseo and Slack say the
  same thing.
- `specloop act <change> <action>` is one narrow entry point for interfaces:
  resume, resume with a note, accept, publish, finish or abort. It refuses an
  action that does not fit the run's current pause, and prints a version-1
  JSON outcome.
- The Nightshift Paseo plugin gains a Factory section:
  - each specloop run with its progress and, when it is waiting on Matt, the
    action headline and the buttons that fit;
  - a note field for resuming;
  - confirmation before publishing or finishing (both push to GitHub);
  - refresh while visible, keeping the last good list when a read fails.

Out of scope: changing the plugin's Paseo version range (SDK 0.12.0, accepts
0.10.1–0.12.x since `47c244c`); acting on the
Nightshift daemon's own (worker) tasks; Slack interactivity.

## Capabilities

### New Capabilities

- `run-actions`: action descriptions in status JSON, and `specloop act`.
- `paseo-runs-page`: the Factory section of the Nightshift Paseo page.

## Impact

- `specloop/` (status JSON, a new `act` command) and its tests.
- `plugins/nightshift/` (shared RPC types, server RPCs, client section) and
  its tests.
- Depends on `nightshift-on-specloop`, for `specloop status --json` and
  `prepare` steps, being merged first.

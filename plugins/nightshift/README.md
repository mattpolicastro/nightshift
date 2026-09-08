# Nightshift for Paseo

One host-level sidebar page for outstanding Nightshift tasks. Requires Paseo
0.7.2 and the Nightshift checkout at `~/Projects/nightshift` on the daemon host.
The existing outcome database must be present. No agents or Hub runs are created.

Select the daemon host and open **Nightshift** in the sidebar, or search
**Nightshift attention** in the Command Center. Rows link to GitHub. Reading a
row does not resolve it; Nightshift reconciles resolution from GitHub.

## Development

```sh
npm ci --ignore-scripts
npm run typecheck
npm test
```

Paseo provides the SDK, React, React Native and Zod at runtime. Dependencies are
for development; this plugin has no install/build hooks.

## Installation

```sh
paseo plugin install /absolute/path/to/nightshift/plugins/nightshift
paseo plugin ls --json
```

Paseo's global plugins setting must be enabled with the user's authorization.
A local
installation reads this source directory; after editing it, run:

```sh
paseo plugin reload nightshift
paseo plugin logs nightshift --json
```

## Refresh behavior

The page first reads cached outcomes, then refreshes against GitHub. It refreshes
every minute while mounted and the app is active, and when the app returns to
the foreground. Refreshes share a server-side promise and are throttled to one
per 30 seconds across clients. Manual Refresh obeys the same throttle.

Each CLI refresh checks at most 32 tasks with up to four concurrent GitHub
requests and a 40-second network budget (eight seconds per request). Outstanding
tasks take priority; up to eight slots are reserved for resolved history, and
either group can borrow unused slots. Attempts rotate oldest first, including
failed attempts, so a broken record cannot monopolize the next refresh.
Resolved records normally cool down for an hour; failed historical checks retry
after a minute. Reopened historical items therefore appear on a later historical
check, not necessarily the next minute. Large backlogs need multiple refreshes.
Checks deferred by the work/time limit retain their prior state and show stale.

The server calls a fixed `/opt/homebrew/bin/uv run --no-sync nightshift attention
--json` command in the fixed checkout. RPC input is only a refresh boolean.
It accepts CLI exit 1 as a valid stale-data response. Timeouts, malformed output
and transport errors retain the last list with an error. Limits: 15 seconds for
a cache read, 60 seconds for GitHub refresh, 2 MiB for command output. A missing
installation produces an error, never a healthy empty list.

Only display fields cross the RPC boundary. Transcripts, run internals, local
paths and raw command errors are excluded. GitHub links are checked against the
task's repository. The plugin exposes no merge, issue-edit or arbitrary-command
operation. Its backend runs with Paseo's ordinary trusted-plugin host access.

This is a Paseo app surface, not a Hub dashboard extension or a multi-host
ledger aggregator. Native phone testing requires a connected phone; compact
browser testing does not substitute for native device testing.

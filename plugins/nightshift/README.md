# Nightshift for Paseo

One host-level sidebar page for outstanding Nightshift tasks and specloop runs.
Requires Paseo 0.10.1–0.12.x, daemon and app alike (`paseo-plugin.json` pins
`>=0.10.1 <0.13.0`; built against SDK 0.12.0), and the Nightshift
checkout on the Paseo host (`NIGHTSHIFT_CHECKOUT`, default `~/Projects/nightshift`;
see [platform settings and limits](../../KNOWN-LIMITATIONS.md#platform-assumptions)).
The existing outcome database must be present. The page creates no agents.

Select the host and open **Nightshift** in the sidebar, or search
**Nightshift attention** in the Command Center. Nightshift task rows link to
GitHub. Reading a task row does not resolve it; Nightshift reconciles resolution
from GitHub. The **Factory** section lets you act on paused specloop runs.

## Factory

Factory lists each specloop run with its change, status, tasks done/total and
PR link when available. Paused runs appear first, with the same action headline
and explanation as Slack. Each row offers only the actions allowed by its
current pause:

| Action | When and what it does |
| --- | --- |
| Resume | Continue from a phase gate or another resumable pause, optionally with a note of up to 2,000 characters for the next attempt. |
| Accept | Accept the paused task's verified candidate at a `stuck` or `needs_human` pause. |
| Publish | At `ready_to_publish`, push the branch to GitHub and update the recorded PR, or open a draft PR. |
| Finish | At `human_checks`, after completing the PR's human checks, archive the change, push to GitHub and mark the PR ready for review. |
| Abort | Stop a paused run. Available at every pause; a `secret` pause otherwise needs inspection by hand. |

Each row also has **Details**, collapsed initially. Open it to see the change
summary, repository and branch, current or paused task, full pause detail and
recent events, when available. Home directories appear as `~`. A published
change can include an **Open change on GitHub** link to its change folder.
Details always includes `nightshift status <change> --events 20` to run on the Paseo host.

**Open in Paseo** appears in Details when the host supports workspace navigation
and has a matching workspace for the run's repository. The page loads workspaces
from the selected host when Details is first opened, and reuses that
list while the page is open. Workspaces being archived are excluded; if
several match, the most recently active one opens. If workspace lookup fails,
the host lacks the required methods, or no workspace matches, the button is
absent and the other details remain available.

**Publish and Finish both push to GitHub, so the page asks for confirmation
before sending either action.** Cancel sends nothing. Finish does not merge
the PR. After an action, the page shows the result and refreshes the list.
Resume and Accept leave further execution to the `nightshift serve` supervisor.
These controls apply to specloop runs; Nightshift worker tasks remain read-only.

Factory reads `nightshift status --json` on opening, every 60 seconds while the
page is visible and active, and on return to the foreground. **Refresh Factory**
requests a read too. The server shares reads and caches them for 15 seconds;
an action invalidates that cache. A failed read keeps the last good list marked
stale with an error. If no list has loaded yet, it shows an error rather than
claiming there are no runs.

### `nightshift act`

The page uses the same narrow command available to other interfaces:

```sh
uv run nightshift status --json
uv run nightshift act <change> <verb> [--note TEXT] --json
uv run nightshift act example-change resume --note "Use the revised acceptance criteria" --json
```

The verbs are `resume`, `accept`, `publish`, `finish` and `abort`. Each paused
run's status has an `action` containing `kind`, `headline`, `why`, `command` and
the allowed `actions`; running runs have no action. Use that list to select a
verb. The optional note supplies guidance when resuming.

`act` reloads the run under its lock and checks the current pause before
applying the matching command. It refuses an unknown change, an unavailable
verb, a note over 2,000 characters or a busy lock. A stale page or duplicate
request therefore cannot repeat an action after its pause has gone. Resume
and Accept behave as with `--no-run`: `act` never drives the build itself, so
`nightshift serve` must be running to continue automatically.

With `--json`, the outcome has `version: 1`, `outcome` (`done` or `refused`),
`verb` and a human-readable `detail`. Exit status is 0 for done and 1 for
refused. For example:

```json
{"version":1,"outcome":"done","verb":"resume","detail":"resume completed for example-change"}
```

Confirmation belongs to the Paseo page: direct `nightshift act` calls do not
prompt before publishing or finishing. Callers must obtain confirmation
before invoking either GitHub-pushing action.

The Factory server runs the fixed host checkout's `uv run --no-sync nightshift`
with an argument array, never a shell. It accepts only a change in the last
good status and a verb in that run's action list; specloop rechecks the live
state. Notes are passed as literal arguments. Only validated display fields
cross the RPC boundary, with the home directory replaced by `~` in strings
and raw command errors excluded.

## Layout (Paseo 0.10+ plugin contract)

`index.server.ts` registers the attention RPC (`server/attention.ts`, reading
through `server/reader.ts`) and the Factory RPCs (`server/factory.ts`);
`shared/attention.ts` and `shared/factory.ts` define their typed contracts.
`index.client.tsx` registers the surface, sidebar item and Command Center entry
(`client/main.tsx`), with Factory rendered by `client/factory.tsx`.
This directory is the plugin's source of truth; an installation runs a copy
exported from one of its commits. To ship a change without restarting the daemon (a changed plugin path needs a
`paseo daemon restart`), keep the configured path and swap its contents: move the
live copy aside as `nightshift-<old sha>`, export the committed tree in its place
(`git archive <sha> plugins/nightshift | tar -x -C <dir> --strip-components=2`),
then `paseo plugin reload nightshift`. Roll back by swapping the directories
back and reloading again.

## Development

Use a separate build copy from the repository root so installation leaves source untouched:

```sh
mkdir -p build/plugin
cp -R plugins/nightshift/. build/plugin/
cd build/plugin
npm ci --ignore-scripts
npm run typecheck
npm test
```

Paseo provides the SDK, React, React Native and Zod at runtime. Dependencies are
for development; this plugin has no install/build hooks.

## Installation

Both server adapters use `NIGHTSHIFT_CHECKOUT` (default `~/Projects/nightshift`)
as their working directory and `NIGHTSHIFT_UV` as the exact executable. An
unset `NIGHTSHIFT_UV` follows `NIGHTSHIFT_BIN_DIR` (default `/opt/homebrew/bin`)
plus `/uv`. Attention uses that bin directory followed by
`/usr/bin:/bin:/usr/sbin:/sbin`; Factory prepends `NIGHTSHIFT_NODE_BIN` (default
`~/n/bin`) for Node/`npx`, including finish. Neither appends arbitrary inherited
PATH entries. Attention's installation check uses the same configured checkout.

For an alternate layout, supply these assignments in the **Paseo host process
environment** before starting it (substitute actual installed paths):

```sh
export NIGHTSHIFT_BIN_DIR="/alternate/tools"
export NIGHTSHIFT_NODE_BIN="/alternate/node/bin"
export NIGHTSHIFT_UV="/alternate/tools/uv"
export NIGHTSHIFT_CHECKOUT="/alternate/nightshift"
```

Restart the Paseo host after changing its environment. Exporting in a terminal
or reloading the plugin cannot update an already-running host's environment;
there is no hot reload of host settings. These are server-local operator
settings, never RPC inputs. Paths accept absolute or leading `~/` forms;
spaces stay literal and the resolver does not expand `$VAR`, globs or shell
substitutions. Blank/relative paths, unsupported tilde forms, NUL/line breaks
and colons in PATH directories are rejected. Tools must already be installed.

The installation pathname below selects plugin source, not its CLI checkout:

```sh
paseo plugin install /absolute/path/to/nightshift/plugins/nightshift
paseo plugin ls --json
```

Paseo's global plugins setting must be enabled. A local installation reads
this source directory; after editing it, run:

```sh
paseo plugin reload nightshift
paseo plugin logs nightshift --json
```

Python services need their own applicable environment and restart; see
[host setup](../../README.md#host-path-setup). The unchanged
`bin/nightshift-daemon` and `bin/specloop-serve` retain fixed bootstrap checkout,
uv and PATH locations. These settings do not relocate their commands. Wrapper
portability is deferred because `bin/**` is frozen; direct CLI examples do not
configure bundled service startup. Full daemon rollout requires separate
wrapper work or an independently provisioned operator service preserving
equivalent credential and preflight guards. Second-install and actual Paseo
host integration validation for alternate paths remain pending; see the
[manual checks](../../KNOWN-LIMITATIONS.md#platform-assumptions).

## Nightshift task refresh behavior

The task list first reads cached outcomes, then refreshes against GitHub. It
refreshes every minute while mounted and the app is active, and when the app
returns to the foreground. Refreshes share a server-side promise and are throttled to one
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

The server invokes `uv run --no-sync nightshift attention --json` through the
configured uv executable in the configured checkout, using literal argv without
a shell. RPC input is only a refresh boolean.
It accepts CLI exit 1 as a valid stale-data response. Timeouts, malformed output
and transport errors retain the last list with an error. Limits: 15 seconds for
a cache read, 60 seconds for GitHub refresh, 2 MiB for command output. A missing
installation produces an error, never a healthy empty list.

Only display fields cross the RPC boundary. Transcripts, run internals, local
paths and raw command errors are excluded. GitHub links are checked against the
task's repository. The plugin exposes no merge, issue-edit or arbitrary-command
operation. Its backend runs with Paseo's ordinary trusted-plugin host access.

This is a Paseo app surface, not a multi-host ledger aggregator. Native phone testing requires a connected phone; compact
browser testing does not substitute for native device testing.

The installed page was verified in the Paseo desktop app and on a
phone, including working GitHub links, on 2026-09-05.

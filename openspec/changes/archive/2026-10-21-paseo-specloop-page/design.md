## Context

The plugin (`plugins/nightshift`, built against SDK 0.12.0) is built in three parts:
- `server/attention.ts` runs `uv run --no-sync nightshift attention --json`
  with `execFile` and caches the last good result;
- `server/reader.ts` validates it with zod and passes only display fields to
  the client;
- `client/main.tsx` renders one surface.

The Factory section follows the same pattern. specloop's messages live in
`specloop/messages.py`; its status JSON comes from `nightshift-on-specloop`
task 1.1.

## Decisions

### specloop owns the wording and the rules

`status --json` gains `"action": {"kind","headline","why","command","actions"}`
for every paused run. The first four fields come from `messages.for_pause`, so
a Slack message and a Paseo row never disagree. `actions` lists which `act`
verbs fit the pause:

| Pause | Verbs |
| --- | --- |
| `phase_gate`, `gate_evidence`, `coverage`, `infra`, `violation_repeat`, `review_invalid`, `requested` | `resume` |
| `stuck`, `needs_human`, `blocked` | `resume`, `accept` (stuck and needs_human only) |
| `ready_to_publish` | `publish` |
| `human_checks` | `finish` |
| `secret` | none (inspect by hand) |
| any pause | `abort` |

### One narrow entry point

`specloop act <change> <verb> [--note TEXT] --json` holds the run's lock while
it reloads the run, checks that the verb is listed for its current pause, and
applies the matching command's state change (`resume --note`, `accept`,
`publish`, `finish`, `abort`). It prints
`{"version":1,"outcome":"done"|"refused","verb","detail"}`. A verb that does
not fit, an unknown change, a note over 2,000 characters, or a run whose lock
is held is refused. A stale page or a double tap therefore cannot do the wrong
thing: the second tap finds the pause already gone.

`act` never drives the run. `resume` and `accept` behave as with `--no-run`,
and `specloop serve` picks the run up, so an `act` call returns in seconds
instead of lasting as long as a build.

### Plugin server

`server/factory.ts` exposes two RPCs in `shared/factory.ts`:
- `factoryRuns`: the cached, zod-validated `specloop status --json`, refreshed
  at most every 15 s, keeping the last good result with a `stale` flag when a
  read fails.
- `factoryAct`: runs `specloop act` with an argv array, never a shell. The
  change must appear in the last good status, and the verb must be in its
  `actions`. A completed action invalidates the status cache, so the next
  `factoryRuns` reads fresh state.

Only display fields cross to the client: no raw stderr, and no host paths.
`why` can carry diagnostic text, so the server replaces the home directory
with `~` in every string it passes on.

### Plugin client

A Factory section in the existing Nightshift surface lists runs, those
waiting on Matt first. For each, it shows:
- the change, status, tasks done/total and PR link;
- when paused, the action headline and why;
- a button per allowed verb, with an optional note field shown for `resume`.

Publish and finish ask for confirmation, because they push to GitHub; abort asks
too, because it cannot be undone. After an
action, the result is shown and the list refreshes. While visible, the section
refreshes every 60 s.

### Verification

The verify recipe runs Nightshift's Python tests and, after a `prepare` step
that copies `plugins/nightshift` into the build directory and runs `npm ci`
there, the plugin's typecheck and node tests in that copy. Pure functions
carry the testable logic: verb tables, row ordering and argv building.

## Risks / Trade-offs

- **Version range:** the plugin keeps `requirements.paseo` at `>=0.10.1 <0.13.0`
  (`47c244c`). Daemon and app both enforce it, and a desktop client in use is on 0.11,
  so raise the floor only if the page needs an SDK feature newer than 0.10.1. The
  Studio daemon has run 0.12.0 since 2026-10-20; a later minor needs a typecheck
  and test run against its SDK before the ceiling moves.
- **Phone input:** the note field is plain text, so very long notes are better
  written at a keyboard.

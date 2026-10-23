## Context

`nightshift/touchpoints.py` establishes a standalone, standard-library report
with injectable roots, deterministic output, diagnostics and no state writes.
Its receipt joins and approximate draft-date selection are unnecessary here.
Read event logs directly; do not import its private reader or construct a
`specloop.state.Run`, load manifests, read Git refs or inspect credentials.

`Run.event` writes JSON objects with numeric epoch-seconds `at` and string
`event`. `specloop/loop.py` records task keys on phase events, but completion
events do not repeat the starts' numeric session counters. `implemented` and
`reviewed` carry the profile actually used. `landed` has a task key and label;
`evidence` has a section number and no explicit start. `attempt_failed` is the
harness's failed-attempt marker. `specloop/cli.py` records `admitted`, manual
`resumed`, `published` and `finished`; `specloop/serve.py` separately records
`auto_resumed`. Finishing archives and marks a PR ready; it is not a merge.

The current review harness computes decisions from findings and waivers,
rather than trusting `output.verdict`. This report must identify its failure
statistic as the reviewer's reported FAIL rate, not a reconstructed harness
decision or a measure of reviewer quality.

## Goals / Non-Goals

Goals: reproduce the requested observable intervals, make retry and missing
history semantics explicit, compare profiles, and run offline against small
fixtures without the daemon, GitHub, launchd, a sandbox or real agent sessions.

Non-goals: instrumentation changes, CPU-time profiling, scheduling changes,
cost accounting, model inference from manifests, worker-engine history,
automatic attribution of every wall-clock second, merge-time measurement or
backfilling missing events. Do not require the six phases to sum to build time.

## Decisions

### Files, interfaces and concurrent work

Implement only `nightshift/timings.py`, new `tests/test_timings.py`, and a short
`### Build timing report` subsection after the README paragraph beginning
"The [operations guide]" that describes touchpoints, before dependency preparation.
Preserve the existing paragraph, links, commands and surrounding content.
Issue #76 also edits README: apply this small insertion to the current file and
preserve its arming/enrollment additions when reconciling concurrent changes.

Explicitly avoid `KNOWN-LIMITATIONS.md`, `docs/arming.md`, `docs/landing.md`,
`docs/specloop/README.md`, `nightshift/arm.py`, `nightshift/cli.py`,
`nightshift/land.py`, `pyproject.toml`, `specloop/cli.py`, `specloop/critic.py`,
`tests/test_arm.py` and `tests/test_land.py`. Also leave `nightshift/touchpoints.py`,
`tests/test_touchpoints.py`, existing assertions and all event writers untouched.

Expose `collect_report(runs_root: Path, *, since: date | None = None,
change: str | None = None) -> dict`, `render_table(report: dict) -> str`, and
`main(argv: list[str] | None = None, *, runs_root: Path | None = None) -> int`.
The default root is `Path.home() / ".specloop" / "runs"`. The keyword override
is the testing seam; no extra CLI flag, dependency or entry-point registration.
Use an ordinary module `__main__` guard. Private parsing/replay helpers are fine.

### Discovery and damaged history

Enumerate immediate run directories in name order and use the directory name
as the change identity. Read only their `events.jsonl`, once per run, in append
order. Non-directory entries are ignored. An absent root is empty history;
missing or unreadable event files produce an error and omit that run. On a
mid-read I/O failure discard that run's partial data. Discovery errors retain
already discovered readable runs and appear in the report.

Decode JSONL per line. A malformed/undecodable line, non-object record, missing
event kind, invalid timestamp or invalid field needed for a known event is
diagnosed by path and one-based line. Clear pending pair state at that gap,
then continue with valid lines. Do not repair a partial final line. `at` must
be a finite int or float, excluding bool; allow zero and fractional seconds.
Unknown fields and well-formed unknown event kinds are ignored. Validate task
keys for task phases, `landed` and `attempt_failed` as nonempty strings.
Do not require unused fields such as usage, label, SHA or output findings.

Malformed lines do not discard already completed samples or observed counters.
The lifecycle timestamps remain observed endpoints despite gaps; both output
formats retain the errors. Do not catch `KeyboardInterrupt` or `SystemExit`.

### Phase pairing

Replay independently per run; never sort events by timestamp. Each completion
consumes one pending start. A newer start for the same key replaces an
unfinished start, with a warning. Missing endpoints are omitted with warnings,
not zero-filled or extended to the current time. Reject negative or non-finite
duration arithmetic with a diagnostic, consume the pair, and retain valid
samples. Equal timestamps form a valid zero-second sample.

| JSON phase key | Start / end | Matching rule |
| --- | --- | --- |
| `implement` | `implement` / `implemented` | Same task key, latest pending start. |
| `verify` | `sealed` / `verified` | Same task key, latest pending start. |
| `review` | `review` / `reviewed` | Same task key, latest pending start. |
| `evidence_gate` | `landed` / `evidence` | Latest preceding unconsumed landing in this run; consume only that landing. |
| `slot_wait` | `admitted` or `resumed` / `implement` | Run-wide latest pending eligibility marker, consumed by the first implementation start. |
| `paused_wait` | `pause` / `resumed` | Run-wide latest unresolved pause, regardless of reason. |

New `implement` clears stale task-phase starts for that task before opening its
own pair; `attempt_failed` and `landed` clear that task's pending starts.
`pause` cancels a pending slot wait. `resumed` closes the pause before opening
slot wait. `auto_resumed`, `human_accept` and `revised` clear pause and slot
markers without forming samples; they are not aliases for manual resume.
`revised`, `finished` and `aborted` clear all pending phase markers. Unexpected
discarded starts and unmatched ends warn; superseded landings do not warn,
because only the last task before an evidence check supplies its start.

Other events, including `reverify`, do not introduce extra starts. A verify
pair after `reverify` still uses its recorded seal time. Task and evidence
pairs can span a pause; they measure endpoint wall time and can overlap a
paused-wait sample. No synthetic automatic-resume, gate-review or inter-task
slot intervals. Evidence after several landed tasks contributes once, from the
last landing, even though earlier tasks also belonged to that section. An
evidence retry with no intervening landing has no new measurable pair.

For each phase, expose `count`, `total_seconds`, `median_seconds`. Compute
medians over all individual included samples, including failed phase outcomes;
do not average per-change medians. Empty phases have count 0, total 0 and
median null. Preserve numeric precision in JSON; text rounds seconds to three
decimal places.

### Changes and lifecycle

Each readable selected run has a row, including active, aborted and empty logs.
`tasks_landed` counts distinct task keys in valid `landed` events, so a repeated
landing cannot inflate it. `failed_attempts` counts `attempt_failed` records;
do not deduplicate using the numeric attempt field, which manual resume resets.

Select the first `admitted`, the first subsequent `published`, and the first
`finished` following that publication in append order. Later publications do
not reset either clock: initial build ends at first publication, and later
revision work is included in publication-to-finish elapsed time. Expose their
epoch timestamps as `admitted_at`, `published_at`, `finished_at`; when admission
is missing, retain the first observed publication and following finish so
publication-to-finish can still be measured. Ignore earlier out-of-order
lifecycle markers with a warning. A missing endpoint gives null; an inverted
timestamp gives null and an error. Divide seconds by 3600 for
`build_hours` and `publication_wait_hours`; never substitute file mtimes or now.
Counts and phase samples cover all selected history, including later revisions,
so they need not lie entirely within initial build hours.

### Profile accounting

Use a list keyed by the literal completion-event profile, shared across roles,
with `implementation_attempts`, `review_attempts`, `review_failures`, and
`review_failure_rate`. Every valid `implemented` increments implementation
attempts; every valid `reviewed` increments review attempts, including infra
and invalid-output completions and completions without a matching timed start.
Starts without a completion cannot identify an actually used profile.

A review failure is a `reviewed` event with `disposition == "ok"` and object
`output` whose `verdict` equals `"FAIL"`. Count its reported verdict even if
the harness later lands it; a raw PASS followed by `attempt_failed` does not
become a reported FAIL. Do not count verification failures, `gate_reviewed` or
arbiter decisions. Rate is `review_failures / review_attempts` in [0, 1], null
when there are no review attempts; text displays a percentage to one decimal.
Thus infra/invalid completions remain in the denominator. Explain that choice
in the report limitations so comparisons do not imply adjudicated-review rates.

A missing, empty or non-string profile goes in the JSON `profile: null` bucket,
rendered `(unknown)` with a warning, never inferred from admission or a manifest.
Missing/non-object review output or an unrecognized verdict contributes no
reported FAIL and produces a warning, while retaining the attempt and timing.

### Selection, output and exit status

`--since` accepts only a valid `YYYY-MM-DD`, inclusive UTC midnight, and selects
whole runs by their first observed admission time. It does not cut event pairs
or use draft/mtime dates. Without a usable admission a run stays visible
unfiltered, but is excluded with a diagnostic when `--since` is supplied.
`--change` is exact equality against discovered directory names, never a glob
or a path to join. Apply it before opening files; no match is an empty report.
Both filters compose, and all aggregates use only included runs. Retain
diagnostics encountered while reading date-filter candidates; do not open
unselected names to collect their diagnostics.

JSON version 1 has these fields:

```text
version: 1
since: YYYY-MM-DD | null
change: string | null
coverage: "observed_local_history"
limitations: [string, ...]
phases: {<all six phase keys>: {count, total_seconds, median_seconds}}
changes: [{change, tasks_landed, failed_attempts, admitted_at, published_at,
           finished_at, build_hours, publication_wait_hours, warnings: [string]}]
profiles: [{profile: string | null, implementation_attempts, review_attempts,
            review_failures, review_failure_rate}]
errors: [{path: string, line: integer | null, message: string}]
```

Sort changes by name, named profiles lexically with null last, warnings
lexically, errors by path/line/message, and phases in the table's order. Use
finite JSON numbers and null, never NaN/Infinity. Text shows the same three
tables, selection, coverage, limitations, warnings and errors; show unknown
values as `-`, hours to three decimals, and do not hide zero-count phases.
JSON stdout is exactly one document, without log chatter.

Like touchpoints, completed reports exit 1 when errors exist and 0 otherwise,
including empty selection. Warnings alone do not fail the command. Invalid
CLI syntax/date exits 2 with ordinary argparse stderr before reading history,
even with `--json`; help exits 0. This standalone CLI does not alter the
separate `specloop` JSON-argument-error contract.

### Validation and human checklist

Use temporary event logs, injected read/enumeration failures and forbidden
external-call stubs. Snapshot fixture contents and directory entries around
reporting. Add scenario markers in `tests/test_timings.py`, including the six
test names required by the issue. Keep existing tests/assertions unchanged;
implementation acceptance requires `uv run pytest` green and
`uv run python scripts/check_types.py` without a baseline edit.

Human checks for the implementation PR, never agent tasks:

- On the host containing the original local history, run
  `uv run python -m nightshift.timings --json` once and compare it with the
  2026-10-23 analysis: evidence about 32%, implement 21%, slot wait 18%, verify
  11%, review 4%. Record the selected runs, cutoff/snapshot, denominator,
  absolute totals and any discrepancy in the PR. Use the same cohort and
  denominator as the original analysis; `--since 2026-10-23` is not an
  end-of-day snapshot and must not be used as one. Explain changes caused by
  retained history, later revisions, pairing rules or overlapping intervals;
  do not tune the report to force the historical percentages. If the original
  cohort/denominator cannot be recovered, mark the comparison unresolved.
- Confirm the README insertion reads naturally beside touchpoints and retains
  any concurrently landed arming guidance. CI fixtures cannot establish that
  the real host's retained logs reproduce a historical analysis.

## Risks / Trade-offs

- Evidence and slot timings are observed endpoint estimates, not exact gate
  execution or scheduler-only delays. Uninstrumented work remains unassigned.
- Incomplete or corrupt history undercounts pairs; explicit gaps and nulls
  prevent apparent precision from fabricated durations. An active append can
  produce a partial-line diagnostic; a later invocation can read the completion.
- Rates measure recorded reviewer verdicts over observed completed calls.
  Profile fallback, infra failures and missing historical profile fields limit
  reviewer comparisons; profiles do not imply model identities or causality.
- Later revision phases and overlapping pauses mean phase totals are not a
  partition of first-admission-to-first-publication time. No percentage column
  is added without a defined denominator.
- README is the sole intentional shared implementation file with in-flight work.
  The narrow insertion and named exclusions avoid taking ownership of arming
  or landing changes. All other implementation files are new.

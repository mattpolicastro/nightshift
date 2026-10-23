## Why

Build-speed work and the reviewer trial need repeatable measurements. A
2026-10-23 analysis of local `events.jsonl` files attributed about 32% of build
time to evidence gates, 21% to implementation, 18% to waiting for a build slot,
11% to verification and 4% to review. Turn that one-off analysis into a local
report, alongside the touchpoint report, without changing the build engine.

blocked-by: #67

Issue #67's documentation change is archived in this checkout. Preserve its
operator guidance; this proposal does not claim to establish the live issue's
closure state.

## What Changes

- Add `nightshift/timings.py`, invoked with `uv run python -m nightshift.timings`,
  to read `~/.specloop/runs/*/events.jsonl` with an injectable runs root.
- Report totals, completed-pair counts and medians for implementation,
  verification, review, evidence gates, slot waits and manually resumed pauses.
- Report each change's landed tasks, failed attempts, admission-to-publication
  hours and publication-to-finish hours, with unknown durations left null.
- Group observed implementation and review attempts by the profile recorded on
  their completion events; show reported review failures and failure rates.
- Support `--since YYYY-MM-DD`, exact `--change NAME`, and versioned `--json`.
  Keep healthy history visible when another run is unreadable, with diagnostics.
- Add all tests in new `tests/test_timings.py`, preserving existing assertions.
  Add one short README subsection immediately beside the touchpoint-report
  paragraph; this is the only necessary overlap with issue #76.

Implementation edits are limited to `nightshift/timings.py`,
`tests/test_timings.py`, and that README subsection. Everything else is out of
scope: event producers, persisted state, scheduling, reviewer policy, existing
touchpoint code/tests, CLI registration, dependencies, dashboards and other
documentation. No network calls, subprocesses, repairs or writes to run state.
This drafting step writes only this OpenSpec change directory.

## Capabilities

### New Capabilities

- `build-timing-report`: read-only event-pair timing, change lifecycle summaries,
  profile comparisons, selection and deterministic text/JSON reporting.

### Modified Capabilities

None. Preserve all existing requirements in `touchpoint-report`,
`specloop-interface` and `specloop-documentation`. The new report neither alters
those interfaces nor replaces their guidance; no MODIFIED or REMOVED delta is
needed.

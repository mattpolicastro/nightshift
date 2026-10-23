## Why

Build-speed work and the reviewer trial need repeatable numbers. The
2026-10-23 one-off analysis found evidence gates at about 32% of build time,
implementation 21%, slot waits 18%, verification 11% and review 4%. Productize
that small analysis to compare build speed before and after #73 and #75.

blocked-by: #67

## What Changes

- Add `nightshift/timings.py`, run with `uv run python -m nightshift.timings`,
  reading local run events without writing state.
- Summarize six phase pairings, per-change hours/tasks/failed attempts, and
  implementation/review attempts and review-failure rates by recorded profile.
- Support `--since YYYY-MM-DD`, `--change NAME`, and `--json`; produce one text
  table or JSON from the same data. Skip malformed lines and emit one
  diagnostic line per unreadable run before continuing.
- Add the six requested tests in new `tests/test_timings.py` and a short README
  subsection beside touchpoints. Target about 400 implementation lines total,
  including tests and documentation, across three tasks.

Only those three implementation files are in scope. Everything else is out:
event producers, run-state changes, scheduler/reviewer changes, recovery
machinery, dependencies, CLI registration, touchpoint refactoring, dashboards
and other documentation. This draft edits only its own change directory.

## Capabilities

### New Capabilities

- `build-timing-report`: a read-only local timing and outcome report.

### Modified Capabilities

None. Preserve existing `touchpoint-report`, `specloop-interface` and
`specloop-documentation` requirements and assertions. The README addition
retains existing operations guidance; no MODIFIED or REMOVED delta is needed.

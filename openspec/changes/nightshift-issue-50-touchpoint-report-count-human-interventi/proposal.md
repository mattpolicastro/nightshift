## Why

Nightshift needs a local measure of how often a front-door change needs human
help between drafting and merge. Current receipts and run events contain that
evidence, but inspecting only the latest receipt loses earlier questions and
refusals, and adding receipts to events can count the same revision twice.

## What Changes

- Add `nightshift/touchpoints.py`, runnable as `python -m nightshift.touchpoints`,
  to report each repository, issue number and change name from local intake
  receipts, retained intake job records and run event logs.
- Count drafter question rounds, spec and build revision rounds, human pauses
  and manual resumes by reason, human accepts, and admission refusals. Produce
  a table with totals and a version-1 `--json` document.
- Add `--since YYYY-MM-DD` to select changes by their earliest locally recorded
  drafting attempt, retaining their subsequent history. Expose timestamp
  approximations and incomplete history rather than implying exact accounting.
- Isolate unreadable records and list errors alongside usable results.
- Add only `tests/test_touchpoints.py` for implementation tests, including the
  three requested named tests; require `uv run pytest` to remain green.

Out of scope: edits to `nightshift/cli.py`, `nightshift/daemon.py`,
`nightshift/specloop_intake.py`, `nightshift/specloop_job.py`, any `specloop/`
file, plugin files, `docs/validation.md`, existing tests, or packaging.
No GitHub calls, subprocess-based status queries, new telemetry writers,
daemon integration, merge action, or reconstruction from prompts/transcripts.
Local records do not establish the actual merge time; the report explicitly
describes observed history, and the human validation checks that boundary.

## Capabilities

### New Capabilities

- `touchpoint-report`: read-only local intervention accounting, deterministic
  text and JSON output, drafting-date filtering, and partial-result diagnostics.

### Modified Capabilities

None. Existing `issue-intake`, `pr-revision`, `specloop-interface`, `attention`,
`run-actions`, and `run-context` requirements remain unchanged. This report
reads their persisted evidence without changing their behavior or interfaces.

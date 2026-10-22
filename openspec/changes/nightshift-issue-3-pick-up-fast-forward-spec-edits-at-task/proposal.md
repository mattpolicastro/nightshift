## Why

Human corrections to an admitted change's `tasks.md` currently reach the run only through `resume`, requiring a pause even when the source branch safely extends the run head. The driver should take those commits at its next task boundary and continue without intervention.

blocked-by: #14

Issue #14's CLI polish is the prerequisite; its archived change and requirements are already present in this checkout.

## What Changes

- Share the existing source synchronization logic between `specloop/cli.py::_sync` and `specloop/loop.py`, and invoke it before each boundary's task and gate decisions.
- Advance only when the fetched source commit strictly descends from the current run head, using an expected-old-head ref update. Record `synced` only after success.
- Record diverged source history and lost ref-update races, leave the run branch intact, and continue. Equal or older source tips are quiet no-ops; fetch failures remain nonfatal.
- Preserve explicit pause requests, resume behavior, task verification, review, and landing safeguards. Add tests in new files, including `test_boundary_sync_takes_fast_forward_edits` and `test_boundary_sync_ignores_diverged_source`.

Implementation scope is `specloop/loop.py`, `specloop/cli.py` (the `_sync` delegation), and new tests. The daemon, supervisor, plugin, publication, remote polling, merge/rebase/force movement of the run branch, and changes to admission or gate policy are explicitly out of scope. Sync imports the fetched commit as a whole, as resume does today; it does not filter commits to spec-only paths.

## Capabilities

### New Capabilities

- `boundary-sync`: Safe import of source commits at driver boundaries, with observable skip outcomes and shared resume semantics.

### Modified Capabilities

None. Existing `specloop-interface`, `run-actions`, and `run-context` requirements remain intact, including issue #14's known runtime scenario references and CLI JSON behavior. This adds driver behavior without modifying or removing their requirements.

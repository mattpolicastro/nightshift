## Why

One run that cannot be read currently makes `specloop status --json` fail for every repository. The daemon and Paseo Factory page then lose healthy runs along with the damaged one. A partially completed `finish` is an important regression case: the archive commit can exist while a failed push leaves durable state paused at `human_checks`.

## What Changes

- Isolate exceptions while reading each run, return an explicit per-run `error`, and continue producing the version-1 status document with exit code 0.
- Give error entries a shape accepted by existing consumers, without advertising actions or changing persisted run state.
- Distinguish strict reads of existing state from admission's default new state, retaining forward-compatible loading of unknown fields.
- Preserve archive-aware task lookup and add the required `test_status_reports_broken_run_and_keeps_others` and `test_status_after_partial_finish` regressions.
- Keep unknown named changes and document-wide failures as top-level errors with exit code 1.

Implementation is limited to `specloop/cli.py`, `specloop/state.py`, and tests. Out of scope: repairing damaged runs, changing `finish` ordering or retry behavior, text status changes, daemon scheduling changes, new Paseo UI behavior, and exhaustive validation of all possible disk corruption.

## Capabilities

- Extend `specloop-interface` with per-run status failure isolation, strict existing-state reads, and an explicit partial-finish status guarantee. Existing healthy status, context, and action contracts remain intact.

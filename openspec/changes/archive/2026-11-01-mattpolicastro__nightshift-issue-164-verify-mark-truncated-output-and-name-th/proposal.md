## Why

Verification receipts silently retain only the last 2,000 bytes of each step's
output. Retry feedback slices that tail again, hiding both earlier diagnostics
and the fact that output was omitted. Readers need an accurate truncation marker
and a path to the retained full log.

## What Changes

- Prefix truncated receipt tails with `[showing the last N of M bytes; full log: <path>]`
  and a newline, counting raw bytes and naming the step log relative to the
  owning run directory. Leave output of at most 2,000 bytes unchanged.
- Pass an optional keyword-only `run_dir` through the verifier and supply it
  at the four verification call sites in `specloop/loop.py`. Direct verifier
  calls without a run directory use their output directory as the path base.
- Preserve the complete receipt tail in verification summaries and retry
  feedback, removing the summary's independent 1,500-character slice.
- Update existing verifier doubles and forwarding wrappers in
  `tests/test_specloop_base_sync.py`, `tests/test_specloop_base_sync_recovery.py`,
  `tests/test_specloop_base_publication.py`,
  `tests/test_specloop_feedback_scenarios.py`, and
  `tests/test_snapshot_warming.py` to accept keyword-only `run_dir` and forward
  it when delegating, preserving every existing assertion.
- Add tests for the marker, unchanged short output, byte boundaries, path
  fallback, and retry feedback. Require a green `uv run pytest`.

Out of scope: changing the 2,000-byte payload limit, receipt fields, execution
or sandbox policy, pass/fail decisions, log contents or retention, mutation
or snapshot-warming orchestration, and unrelated prompt or Markdown parsing.
The only loop behavior change is supplying the owning run directory.

## Capabilities

### New Capabilities

- `verify-output`: Traceable truncated verification tails and lossless forwarding
  of those tails into retry feedback.

### Modified Capabilities

None. Existing `verify-preparation` and `publish-base-sync` requirements remain
in force; this adds a presentation contract without changing their receipt
schema, execution, recovery, or publication behavior.

## Context

`specloop/verify.py::_run_commands()` writes combined stdout/stderr to
`out / f"{step.id}.log"`, reads its final 2,000 bytes, and decodes with UTF-8
replacement. It stores that string in `Receipt.tail` and `receipts.json`.
`cleanup()` retains both files. `summary()` then slices the first failing
receipt's tail to 1,500 characters. The loop stores this summary in task
feedback and copies it into `context/feedback.md`, which the next implementer
prompt references.

`run_steps()` and `run_extracted()` know the verification output directory but
not the owning run directory. The loop has that information at task, base-sync,
coverage, and acceptance verification call sites. Existing tests replace these
interfaces with strict doubles, including a forwarding wrapper that accepts
only positional arguments; adding a keyword without migrating those doubles
would fail before their existing assertions run.

The existing `verify-preparation` requirements preserve preparation ordering,
credential isolation, environments, snapshots, verdicts and receipt schema.
`publish-base-sync` preserves integrated-head verification, retained evidence,
recovery and publication gates. All remain in force. There is no existing
`verify-output` capability in the main specs.

## Goals / Non-Goals

Goals: make omitted output explicit; locate the full retained log; preserve the
receipt's entire bounded payload in retry feedback; retain existing assertions
and verification behavior. Keep this one concern in one implementation task,
targeting fewer than 300 changed lines including tests and fixture migration.

Non-goals: changing log capture or retention, adding receipt fields, changing
command execution or sandbox permissions, increasing the payload limit, changing
mutation/snapshot orchestration, or introducing Markdown/marker parsing.

## Decisions

### Format tails once from raw bytes

In `_run_commands()`, read the captured log once. Let `M` be its raw byte count
and `N = min(2000, M)`. Decode the last `N` bytes with UTF-8 `errors="replace"`,
as today. Only when `M > 2000`, prefix exactly
`[showing the last 2000 of M bytes; full log: <path>]\n`, substituting the decimal
count and relative path. The marker is additional metadata outside the payload
limit; it does not consume any of the 2,000 retained bytes.

At 0, 1,999 and exactly 2,000 bytes, return the existing decoded output with no
marker, trimming, or extra newline. At 2,001 bytes, mark truncation. Counts refer
to bytes before decoding, not characters or the re-encoded replacement string.
A cut through a multibyte character keeps the existing replacement behavior.
Apply the shared formatter to prepare and requested check receipts regardless
of their verdict. The full log remains byte-for-byte unchanged.

### Pass the path base explicitly

Add `run_dir: Path | None = None` as a keyword-only argument to `run_steps()`,
`run_extracted()`, and `_run_commands()` in `specloop/verify.py`. Forward it
through `run_steps -> run_extracted -> _run_commands`, including both existing
snapshot/no-snapshot branches. Keep positional interfaces and `snapshots`
behavior intact. Use `out` when `run_dir` is omitted; do not infer a run root
from directory depth or snapshot settings.

Render the log path relative to the resolved explicit root (or resolved `out`
fallback), using `os.path.relpath` and POSIX separators. This also defines a
relative path for a direct caller supplying an output directory outside the
root: `..` components are permitted; no absolute-path fallback is used.
Normal loop output directories are descendants of `ctx.run.dir`.

Pass `run_dir=ctx.run.dir` explicitly at all four `verify.run_steps()` calls in
`specloop/loop.py`: `prepare_publish()`, `finals()` acceptance,
`_seen_scenarios()`, and `_verify_then_review()`. Preserve `**ctx.snapshot_kwargs`.
For example, a task log is named `<key>/a<attempt>/verify/<step>.log`
according to its actual output directory, not by constructing that layout in
the formatter. Standalone calls without a root name `<step>.log` relative to
their own output directory. Existing evidence and warm callers may continue
omitting the new argument and use this fallback; their orchestration is outside
this change.

### Migrate strict test doubles with the interface

In `tests/test_specloop_base_sync.py`, update `controlled_verify()`'s `check`
and the local `verify` doubles for driver readiness and pending obligations.
In `tests/test_specloop_base_sync_recovery.py`, update `verification()`'s `check`
and `change_during_verify`; the latter must accept keyword-only `run_dir` and
forward it to `real_verify`. Update the publication fixture's `verify` in
`tests/test_specloop_base_publication.py`, the `verification()` helper's
`run_steps` in `tests/test_specloop_feedback_scenarios.py`, and the snapshot
warming `check` wrapper in `tests/test_snapshot_warming.py`. The snapshot
wrapper must retain `snapshots` and forward both keywords to the real runner.

Use explicit keyword-only `run_dir=None` parameters, preserve every existing
assertion, and add owning-root assertions where the fixture has a `ctx`.
Inspect other replacements of these interfaces for the same incompatibility;
update any additional strict forwarding wrapper without weakening its checks.
Pure doubles accept the parameter; wrappers that delegate forward it unchanged.

### Forward receipt tails without a second cutoff

Replace `bad.tail[-1500:]` in `verify.summary()` with `bad.tail`. Preserve the
per-step status lines, first-failure selection, output heading and surrounding
fence. Treat tails as opaque strings: do not parse markers, fences, indentation
or HTML. This keeps the marker and all 2,000 payload bytes together, and also
passes through unmarked legacy/manual receipts verbatim, including tails longer
than 1,500 characters. Successful-only summaries still omit an output block.

The existing `_failed()` and `_context()` path already preserves the feedback
string; no prompt-template rewrite is needed. Test that the next implementer
prompt references the feedback file and that the file contains the complete
marked tail exactly once. A path in the marker is relative to the owning run,
not the agent checkout or feedback directory.

### Test the contract without a live host application

Add `test_truncated_verify_tail_names_full_log` and
`test_short_verify_output_has_no_marker` in `tests/test_specloop_seal_verify.py`.
Use deterministic command output, temporary directories, and a locally stubbed
`verify.wrap` where necessary for portability; keep all existing sandbox tests
and assertions unchanged. Check the in-memory receipt and serialized tail,
the exact payload and counts, and full-log existence/content after cleanup.
Parameterize focused cases to cover 0/1,999/2,000/2,001 bytes, larger output,
multibyte boundaries, invalid UTF-8, explicit nested run paths, and the omitted
root fallback through both public verifier entry points. Cover prepare and
check receipts and passing/failing output without duplicating whole fixtures.

Add summary and retry-context regressions in `tests/test_specloop_loop.py`,
using its scripted agent or a focused temporary context fixture. Assert the
marker and payload survive into `feedback.md` without a second cutoff, and
that unmarked tails (including 1,501–2,000 characters and empty output) pass
through unchanged. Tag tests with all six scenario IDs in the task's Covers
list. Run the affected verifier, loop, and five migrated test modules first,
then `uv run pytest`; preserve every existing assertion.

No live service, UI, or host-application check is required for this change.
The full suite is the implementation acceptance gate, not a claim made by this
spec-only draft.

## Risks / Trade-offs

- Missing one strict verifier double can produce `TypeError` and prevent an
  unrelated regression from running. Explicit fixture migration and the full
  suite are part of the same task as keyword propagation.
- Removing the second cutoff increases retry text by up to 500 ASCII payload
  characters plus the marker for newly generated receipts. It avoids losing
  diagnostics or a truthful truncation notice; no new prompt budget is added.
- Replacement decoding may display fewer characters than the stated byte
  count. The raw-byte count and boundary tests make this deliberate.
- Direct callers without a run directory receive an output-directory-relative
  filename. Owning-run paths are guaranteed for the four loop verification
  paths that explicitly supply the root.
- Full-log memory reads and existing log retention remain as they are; this
  change adds no streaming optimization or log migration.

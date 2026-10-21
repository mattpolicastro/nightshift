## Context

`specloop/evidence.py:gather` deterministically selects mutants from the section
diff and writes `evidence.json`. `mutate` first calls `verify.run_steps` once
for the unmutated head, then changes and restores files in one shared `out/src`.
Without prepare steps it reuses the baseline build and `_run_steps`; with
prepare steps it calls `verify.run_extracted` in `mutant-N` build directories,
but still supplies the shared source. Neither source arrangement is safe for
overlapping mutants.

The existing budget is per step, not per gate: a nonzero baseline receipt time
produces `max(30.0, 3 * seconds)`, capped by the declared step timeout. Missing
or zero durations fall back to the declared timeout. The prepare branch
currently bypasses this budget and uses full manifest ceilings. Consistent
time-boxing requires fixing that branch as part of the executor change.

`specloop/manifest.py` owns `[gates]` parsing and the frozen `Manifest` dataclass.
`specloop/verify.py` already provides extraction, preparation, sandbox profiles,
receipt logging, process-group termination and cleanup. Its public interfaces
can be reused without changing that module. Relevant tests are
`tests/test_specloop_evidence.py` and `tests/test_specloop_units.py`.

## Goals / Non-Goals

Goals are bounded overlap (default two), private mutable state for every mutant,
one baseline before any mutant starts, stable evidence independent of scheduling,
and unchanged baseline-derived timeout arithmetic. A single-worker run uses the
same executor as a multi-worker run so comparisons exercise identical rules.

Non-goals are parallel baseline or within-mutant steps, changed mutant selection
or score thresholds, new cancellation APIs, crash recovery, global scheduling,
resource autodetection, and a performance promise. Host-wide writable locations
explicitly granted by a trusted manifest are not virtualized by this change.

## Decisions

1. **Configuration.** Append `mutation_parallel: int = 2` to `Manifest` to avoid
   shifting existing positional fields. Parse it in `_gates`; accept only a
   positive integer (reject booleans, floats, strings, zero and negatives) and
   raise `ValueError` naming `gates.mutation_parallel` for invalid values. Do
   not silently clamp it or derive it from CPU count. An omitted field in old
   saved manifests loads as `2`; explicit values survive `manifest.template`
   and `load`. No new persisted evidence field is needed.

2. **Baseline and execution boundary.** Keep signatures and return contracts
   of `gather` and `mutate`. Run `verify.run_steps(repo, head, steps, m, out)`
   exactly once before creating mutant work, using original prepare steps and
   selected verify steps in manifest order. If `verify.ok` fails, mark all
   mutants invalid, clean the baseline directory and return `False`, with no
   mutant workers started. Preserve existing gather skip cases. Hold the
   baseline source read-only until every worker has finished.

3. **Private worker.** Introduce an evidence-local helper such as
   `_mutate_one(number, mut, baseline_src, head, steps, m, out)` returning a
   result string. Use the original one-based list index for `out/mutant-N`.
   Check the original line and equivalent annotation against baseline source;
   a mismatch is invalid and an equivalent annotation avoids execution. For
   executable mutants, copy the baseline source to `mutant-N/src` with separate
   file contents (no hard links), apply only that mutant, and restore read-only
   permissions before commands run. Start a fresh build for every mutant,
   including when `m.prepare` is empty. Do not copy baseline build artifacts.
   `verify.run_extracted` supplies private `build`, `HOME`, `TMPDIR`, profiles,
   logs and receipts, preserving prepare and verify order and existing sandbox
   restrictions. Workers never change process-wide environment or working
   directory. Use it for both paths and retire the redundant `_run_steps`
   implementation once unused.

4. **One immutable budget, local timeout overrides.** Derive budgets once from
   baseline receipts. For every prepare and selected verify step with a
   positive receipt duration use
   `min(step.timeout, max(MIN_MUTANT_SECONDS, MUTANT_TIME_FACTOR * seconds))`,
   retaining constants `30.0` and `3`. With a missing or zero duration use
   `step.timeout`. Pass copied `Step` objects with these timeouts and a copied
   manifest with the bounded prepare tuple to `verify.run_extracted`; preserve
   fractional seconds and never mutate the shared manifest. This uses the
   existing runtime timeout interface without editing `verify.py`. Queue wait,
   extraction and sibling execution do not consume a step's budget. There is
   no total gate deadline and no concurrency multiplier. Baseline commands keep
   their original timeouts. Applying the budget to prepared mutants is an
   intentional correction: a prepared mutant exceeding the budget now times
   out even if it previously finished under its full manifest ceiling.

5. **Bounded scheduling and deterministic collection.** Use a scoped
   `concurrent.futures.ThreadPoolExecutor` with at most
   `min(m.mutation_parallel, len(mutants))` workers; handle an empty list without
   creating an executor. External verify processes do the heavy work, so a
   process pool adds no useful isolation. Assign stable indices before
   submission. Workers return classifications; the coordinator assigns them
   to original list entries and does not reorder by completion. `1` uses the
   same private worker sequentially. Concurrent capacity is local to this
   `mutate` call, not a shared limit across runs.

6. **Results and lifecycle.** Preserve the first-failing-step classification:
   all expected exits means survived; failure or timeout in `configure`,
   `build`, or any prepare step means invalid; other selected step failures,
   signals or timeouts mean killed. Preserve equivalent and source-mismatch
   handling, score denominator, all-invalid failure, coverage, summary and
   evidence JSON schema/order. Serial/parallel equality means identical
   evidence for the same selected mutants and deterministic commands under
   equivalent budgets; elapsed receipt times, expanded absolute paths and log
   interleaving are not byte-identical outputs. Prepared-run timeout correction
   and removing stale build reuse are explicit compatibility boundaries.
   Each worker calls `verify.cleanup(mutant_out)` in `finally`, retaining logs,
   receipts and artifacts. The executor must finish active workers before
   baseline cleanup in an outer `finally`. Unexpected Python/I/O errors are
   propagated after workers are drained and cleaned, never converted into a
   passing gate or a partial successful evidence file. Existing
   `verify.run_extracted` process-group termination handles child processes
   on command exit/timeout; no new signal-handling subsystem is introduced.

7. **Verification.** Add marked tests alongside each implementation task.
   `test_parallel_mutants_match_serial_results` compares fresh runs at `1`,
   `2` and a value larger than the mutant count, including prepared/unprepared
   fixtures, mixed classifications, weak/strong tests, score, summary and
   parsed `evidence.json`. Use events/barriers and a locked active-worker
   counter to force out-of-order completion and prove actual overlap and the
   cap without a speed assertion. Never wait for all queued jobs at one barrier
   when there are fewer worker slots. Check same-file mutants for contamination.
   `test_parallel_mutants_respect_time_box` uses controlled baseline receipts
   and queued work to assert the exact per-step formula, zero/missing fallback,
   manifest ceilings, prepared execution and no queue-time deduction. Small
   real subprocess timeout tests verify process-group cleanup and classification
   with the floor temporarily reduced; avoid tight timing ratios.

   These tests need no daemon, GitHub, Paseo or target host application. Unit
   tests can stub the command boundary; local Git/Python fixtures exercise the
   real executor. Actual sandbox enforcement remains a macOS check, as in the
   existing suite. Implementation completion requires `uv run pytest` green,
   including both named regression tests. This draft does not implement or
   claim to have run those future tests.

## Risks / Trade-offs

- Two builds consume more memory, disk and CPU than one. A default of two is
  deliberately modest, and `mutation_parallel = 1` is the operational fallback.
  Isolation adds source-copy and fresh-build overhead and may reduce the gain
  for small or cache-dependent checks.
- CPU contention, external caches or shared locations granted by `m.writable`
  can change command behavior or cause a new timeout. Bit-for-bit evidence
  equivalence cannot be promised for nondeterministic commands or a saturated
  host. The time budget is not loosened to hide contention.
- Prepared runs gain the intended time-box, which can classify previously
  long-running prepared mutants differently. Tests explicitly cover this
  boundary instead of treating the old bypass as part of serial equivalence.
- Before rollout, a human runs the same real section/head with concurrency
  `1` and `2` using comparable cache conditions, records total gate wall time
  and compares evidence, then repeats at `2` while an independent build runs.
  Record memory/CPU pressure, timeouts and the independent build's duration;
  choose `1` if contention makes `2` unsuitable. CI cannot establish this
  wall-clock gain or acceptable contention on the user's machine. Record these
  findings with the implementation review; no fixed speedup target is claimed.

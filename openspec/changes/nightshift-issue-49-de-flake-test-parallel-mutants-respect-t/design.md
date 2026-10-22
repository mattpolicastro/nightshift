## Context

Implementation follows #21. Its mutation-selection and measured-evidence rules
are already represented in the current specs and must be retained. No AGENTS.md
was present in the checkout or its ancestor directories when drafting; README.md
points to CLAUDE.md, which was read along with the engine documentation.

The target test calls `evidence.mutate` with five explicit mutants and
`mutation_parallel=2`, parameterized with and without preparation. A local
wrapper around `verify.run_extracted` supplies controlled baseline durations,
asserts exact derived timeouts, and then runs real commands. A barrier overlaps
the first two workers; the third must wait longer than its own test-step budget.
The test also checks receipts, artifacts, process death, and cleanup order.

The current shared `PROCESS_TREE_STEP` already sleeps for 60 seconds on timeout
markers. Therefore the reported failure does not establish that a short hanging
sleep caused it. Ordinary steps have ceilings as short as 0.5 seconds and the
final test receives one second; under contention an otherwise passing or failing
command can itself time out. Preserve that distinction when inspecting failures.
Do not claim the exact flipped receipt is known without captured evidence.

## Goals / Non-Goals

Make intended hangs independent of scheduler timing, leave ordinary commands
comfortable startup margin, and preserve the test's existing behavioral strength.
Both parameter values must retain identical ordered classifications and every
budget-formula branch. Use real executor timeout and cleanup paths.

Only the named test body and its decorators may change in the implementation.
Nested helpers, imports, and command strings belong inside that function. Keep
`PROCESS_TREE_STEP`, `PREPARE_PROCESS_TREE_STEP`,
`assert_worker_resources_released`, module imports, and every other test intact.
Do not introduce test files, shared fixtures, CI configuration, or dependencies.

Production behavior is out of scope, including `specloop/evidence.py` and
`specloop/verify.py`. Avoid all concurrently edited files: `README.md`,
`nightshift/queue.py`, `nightshift/specloop_client.py`,
`nightshift/specloop_intake.py`, `specloop/loop.py`, `specloop/state.py`,
`tests/test_comment_trust.py`, `tests/test_pr_feedback_ids.py`,
`tests/test_revise.py`, `tests/test_revision_comment_retry.py`,
`tests/test_revision_feedback_consumption.py`,
`tests/test_revision_feedback_recovery.py`,
`tests/test_revision_feedback_state.py`, `tests/test_specloop_base_evidence.py`,
`tests/test_specloop_base_publication.py`, `tests/test_specloop_base_sync.py`,
`tests/test_specloop_base_sync_recovery.py`,
`tests/test_specloop_build_revision.py`,
`tests/test_specloop_label_authorization.py`, and
`tests/test_specloop_spec_revision.py`.

## Decisions

1. **Local blocking subprocess.** Define a command string inside the target test
   with the same source-marker, diagnostic, artifact, parent/child PID, and
   exit-code contract as `PROCESS_TREE_STEP`. Default to an unset
   `threading.Event().wait()` in the subprocess's timeout branch and in its
   background child. The parent writes `artifacts/pids.json` and
   `artifacts/source.txt` and flushes `diagnostic` before blocking. Failure and
   success branches exit immediately with 3 and 0. The baseline has no timeout
   marker and exits 0. The verifier, not a test-thread timer, kills the process
   group. Do not block an executor worker thread on an event it cannot release.
   Continue using system `/bin/sh` commands for preparation; the Python fixture
   is a verify step, respecting preparation's host-HOME read restrictions.

2. **Scale fixture values, keep exact assertions.** Use a fixed factor of ten
   over the current fixture's timings, exclusively inside this test. Set the
   monkeypatched `evidence.MIN_MUTANT_SECONDS` to `10.0`; use baseline durations
   `(0.1, 3.75, 0, None, 20, 0.1)`, manifest ceilings
   `(100, 100, 40, 50, 20, 5)`, and assert the literal expected mutant budgets
   `(10.0, 11.25, 40, 50, 20, 5)` for preparation and verification. The final
   test step has manifest ceiling 100, baseline duration 0.1, and an asserted
   derived budget of 10.0. This retains floor, fractional scaling, zero/missing
   fallback, cap below scaled duration, and cap below floor cases. It removes
   subsecond deadlines from ordinary subprocess work. Do not calculate expected
   values by calling the production budget implementation or replace equality
   with tolerance/range checks. Baseline steps still receive original manifest
   ceilings, and the shared manifest remains unchanged. The production floor
   stays 30 seconds; this is only fixture configuration.

3. **Retain real queue and resource evidence.** Keep two worker slots, the
   first-pair barrier, active/peak accounting, all five mutants, and exactly one
   baseline. Use a 60-second barrier watchdog solely to report a broken fixture.
   Assert that the third worker starts at least 10.0 seconds after the earliest
   first-pair start, and that each hanging final-step receipt records at least
   10.0 seconds of its own execution. No upper runtime ratio is needed. Retain
   exact per-worker timeout assertions so time in the queue cannot silently
   reduce the third worker's budget. Keep all receipt-order, preceding-step
   success, exit-code, log, artifact, source isolation, process-death, and
   baseline-last cleanup assertions. The exact result list remains three
   timeouts followed by one kill and one survivor. Never manufacture timeout
   receipts, mock process termination, or accept multiple classifications.

4. **Validate in an external harness.** First run
   `uv run pytest tests/test_specloop_evidence.py::test_parallel_mutants_respect_time_box`.
   Then start `stress -c 10` once, keep it running throughout 20 sequential
   invocations of that same command, and require all 20 to pass both parameter
   values (40 test cases, including 20 prepared cases). Use no automatic retries
   or discarded failures. Verify the load process remains live throughout.
   If `stress` is unavailable, a temporary Python harness may launch ten
   independent CPU-bound processes with readiness signals, run the same pytest
   loop, and check worker liveness. No package installation is needed. Use
   `try/finally` or shell traps to terminate and reap all load workers on success,
   failure, or interruption. Keep the harness and logs in external temporary
   storage, never in production code or a committed extra test. Record commands,
   platform, worker count, and individual run outcomes in implementation evidence.
   After stopping the load, require `uv run pytest` green. Inspect the diff to
   confirm that only the authorized test/decorators changed outside this change's
   OpenSpec documents. Failed validation must be reported, not fixed elsewhere.

5. **Preserve existing contracts and scenario coverage.** Add the delta scenario
   markers to this test while keeping its current budget and cleanup markers.
   The loaded repetition scenario is established by external execution of the
   marked test, not by spawning CPU load during every normal pytest run. All
   `parallel-evidence-mutants` requirements remain in force, including baseline
   ordering, bounded concurrency, isolation, immutable per-step budgets,
   classification, scoring, reporting, and cleanup. No changes to
   `mutation-selection` or `verify-preparation` are needed.

## Risks / Trade-offs

- Roomier real timeouts cost approximately 20 seconds per parameter value just
  for two waves of hanging work; the full stress acceptance loop can take around
  14 minutes plus setup overhead. This is a deliberate test-only trade-off.
  A permanently blocking fixture also relies on the existing verifier timeout;
  run it under the normal verification watchdog to bound a broken executor.
- Finite margins cannot guarantee success under unlimited scheduler starvation.
  Blocking makes intended hangs deterministic; the 20-run experiment checks
  ordinary subprocess startup under the specified load. Retained diagnostics
  identify accidental prepare or verify timeouts if the experiment fails.
- Tests require local subprocess and filesystem support and the repository's
  supported macOS sandbox environment. They need no running Nightshift daemon,
  Paseo, GitHub, or credentials. Receipt-only mocks would miss the regression.
- **Human checklist for the implementation PR:** watch the next evidence gates
  on the loaded Mac Studio, especially gates with three concurrent builds. Note
  the gate/section, load, and any failure of this test; inspect its worker receipts
  to distinguish the intended hangs from accidental startup timeouts. CI and
  synthetic CPU load cannot prove behavior on that specific host. This is an
  operational follow-up, not an implementing-agent task.

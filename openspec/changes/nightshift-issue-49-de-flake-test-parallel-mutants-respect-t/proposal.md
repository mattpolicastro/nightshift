## Why

`test_parallel_mutants_respect_time_box[True]` failed once in #8's evidence-gate
baseline while three builds ran concurrently at roughly load 7 on 10 cores.
A timing-dependent classification differed; isolated reruns passed 3/3. The
test introduced by #4 and updated by #31 needs reliable timeout fixtures without
losing its exact-budget, queue-wait, concurrency, or cleanup coverage.

blocked-by: #21

## What Changes

- Change only `test_parallel_mutants_respect_time_box` and its decorators in
  `tests/test_specloop_evidence.py`, retaining both `prepared` parameter values.
- Use a local subprocess fixture whose hanging branch blocks until the real
  verifier terminates it. Give ordinary subprocess work sufficient scheduling
  margin using explicitly scaled test-only timings; retain exact budget values
  and every branch of the original budget matrix.
- Retain the ordered results `timeout, timeout, timeout, killed, survived`,
  real process termination, diagnostics, cleanup, and queue-wait assertions.
- Require one ordinary focused pytest invocation covering both parameter
  values and a green `uv run pytest`, in the same task as the test patch.
  Leave loaded repetition to the human reviewer after merge, as documented
  in the design's human checklist; it is not an implementation task or scenario.

Out of scope: production code, other tests, shared test helpers/constants,
new test files, dependency or CI changes, permanent load generators, retries,
xfails, changed classification rules, and performance claims about the Mac
Studio. Observe subsequent real evidence gates separately.

## Capabilities

- **Extended: `parallel-evidence-mutants`** — add a regression-fixture
  requirement for deterministic hanging work and retained budget/queue coverage.
  All existing requirements remain unchanged; no MODIFIED or REMOVED delta is
  needed. `mutation-selection` and `verify-preparation` remain unchanged.

## Purpose

Stabilize the parallel mutant time-box regression test under CPU contention
while retaining all existing execution, budget, classification, and cleanup
requirements for `parallel-evidence-mutants`.

## ADDED Requirements

### Requirement: Deterministic time box regression

`test_parallel_mutants_respect_time_box` SHALL use test-local subprocess blocking
for intended hangs, with no natural completion near the enforced time limit.
It SHALL preserve both preparation modes, exact budget-formula assertions,
queue-wait independence, ordered classifications, and real process cleanup.
Only this test and its decorators in `tests/test_specloop_evidence.py` SHALL
change; production code, shared helpers, and other tests SHALL remain unchanged.

#### Scenario: Hanging work has fixed outcomes
- **WHEN** the test runs with `prepared=False` or `prepared=True` and two mutant worker slots
- **THEN** the baseline and ordinary preparation/verification steps succeed, the first three mutants block in their selected test step until the real verifier times out, the fourth exits 3, and the fifth exits 0
- **THEN** results are exactly `timeout, timeout, timeout, killed, survived` in selection order, with the existing receipt, diagnostic, artifact, process-death, source-isolation, and cleanup-order assertions retained

#### Scenario: Exact budgets survive queue wait
- **WHEN** controlled baseline durations exercise the floor, fractional scaling, missing/zero fallback, and both manifest-cap cases while a third hanging mutant queues behind the first two
- **THEN** the test asserts exact budgets `(10.0, 11.25, 40, 50, 20, 5)` for the corresponding steps and `10.0` for the final test step, retains baseline manifest ceilings, and verifies manifest immutability
- **THEN** two workers overlap without exceeding the limit, the third waits at least 10.0 seconds before execution, and each hanging final step still receives and consumes its full 10.0-second execution budget

#### Scenario: Repeated execution under CPU load
- **WHEN** an external validation harness runs both parameter values in 20 consecutive pytest invocations while `stress -c 10` or ten equivalent CPU-bound workers remain active
- **THEN** all 20 invocations pass both parameter values without retries, skips, xfails, or relaxed assertions, and the harness terminates and reaps its load workers on completion, failure, or interruption
- **THEN** a subsequent `uv run pytest` is green, the implementation changes no other test, and load generation remains confined to the temporary validation harness

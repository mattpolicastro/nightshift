## Why

A selected mutant test that exceeds its time limit currently counts as killed.
With three builds on the host and two mutants per evidence gate, contention can
therefore make a surviving mutant look caught and inflate mutation evidence.
Neither the evidence event nor the reviewer summary identifies that uncertainty.

This change depends on #4, whose parallel executor is already present in this
checkout. It deliberately reverses #4's test-timeout classification while
preserving its execution, isolation, timeout budgets and cleanup guarantees.

## What Changes

- Classify a mutant as `timeout` when its first non-passing selected test step
  has a timeout receipt. Preparation, configure and build timeouts stay `invalid`.
- Exclude `timeout` from both mutation-score numerator and denominator. When
  generated mutants are only timeouts or invalid, evidence remains unmeasured
  and reaches the existing gate pause. Preserve equivalence and skip semantics.
- Persist `timeout` in each affected mutant's `evidence.json` result. Add an
  integer `timeouts` count, including zero, to the existing evidence event.
- Keep the summary's caught fraction aligned with the score. When timeouts
  occur, include exactly `N of M mutants timed out and were not counted`, where
  N counts timeout results and M counts all selected mutants.
- Implement in `specloop/evidence.py` and `tests/test_specloop_evidence.py`.
  The approved scope exception permits adding the count only at the existing
  `evidence` event call in `specloop/loop.py`. The approved test exceptions are
  the four #4 timeout-as-kill tests and the earlier hanging-mutant test, listed
  individually in the design with their PR explanations.

Out of scope: mutant generation or selection; `specloop/verify.py`; gate policy,
thresholds or strengthening/retry behavior; timeout budgets; concurrency or
host scheduling; other production modules; and changes to any other existing
test. New tests stay in `tests/test_specloop_evidence.py`. No files involved in
the other changes in flight need edits.

## Capabilities

- Modified: `parallel-evidence-mutants`. Add timeout classification, scoring and
  reporting requirements; modify the existing Stable evidence results and
  Baseline step time boxes requirements to explicitly replace timeout-as-kill.
  All other requirements of this capability remain in force.
- No new capability. The `verify-preparation` sandbox, receipt and failure
  requirements and the `specloop-interface` CLI requirements remain unchanged.

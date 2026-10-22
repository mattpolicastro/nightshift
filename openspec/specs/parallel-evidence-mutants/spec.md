# parallel-evidence-mutants Specification

## Purpose
Reduce section evidence-gate waiting time by checking independent mutants
concurrently while preserving deterministic evidence and baseline-derived
per-step limits.

## Requirements

### Requirement: Configurable concurrency

The trusted manifest SHALL accept `[gates] mutation_parallel` as a positive
integer, defaulting to `2`. A value of `1` SHALL select serial mutant execution.
Invalid values SHALL be rejected with an error naming the setting.

#### Scenario: Default and explicit limits
- **WHEN** a manifest omits `mutation_parallel` or explicitly sets it to `1` or `4`
- **THEN** its effective limit is respectively `2`, `1` or `4`, and template serialization and loading preserve explicit values

#### Scenario: Invalid limit rejected
- **WHEN** `mutation_parallel` is zero, negative, a boolean, a float or a string
- **THEN** parsing raises `ValueError` identifying `gates.mutation_parallel`

### Requirement: Baseline before bounded work

The evidence gate SHALL finish one unmutated baseline before starting any
mutant verification, SHALL execute at most the configured number of mutants
at once, and SHALL allow independent mutants to overlap when capacity exists.

#### Scenario: Baseline precedes overlap
- **WHEN** a successful baseline is followed by at least three executable mutants with a limit of `2`
- **THEN** exactly one baseline finishes before mutant work starts, two mutants can overlap, and no more than two mutant executions are active at once

#### Scenario: Serial and oversized limits
- **WHEN** the same mutants run with limit `1` and with a limit larger than their count
- **THEN** the first run has no overlapping mutants and the second runs each mutant once without exceeding the number available

#### Scenario: Failed baseline stops mutants
- **WHEN** an unmutated prepare or selected verify step fails or times out
- **THEN** no mutant verification starts, all selected mutants are invalid, baseline failure fails the gate, and baseline source and build directories are cleaned

#### Scenario: Skipped mutation stays skipped
- **WHEN** gather has no mutation steps, no section base, or no generated mutants
- **THEN** it retains the existing skip reason and coverage result and starts neither a mutation baseline nor mutant workers

### Requirement: Independent mutant workspaces

Each executable mutant SHALL use a private source and fresh build tree under
its stable one-based `mutant-N` directory, including private HOME, TMPDIR,
sandbox profiles and command outputs. Preparation SHALL precede verification
against that mutant alone. Commands SHALL see read-only source, and execution
SHALL retain the existing prepare and verify sandbox restrictions.

#### Scenario: Same file mutations remain separate
- **WHEN** mutants touching the same source file execute concurrently without prepare steps
- **THEN** each command sees only its assigned mutation and private build state, and the baseline source and repository checkout remain unchanged

#### Scenario: Prepared copies remain separate
- **WHEN** each concurrent mutant has prepare steps that copy source into its build tree
- **THEN** each prepare receives only its own mutant's read-only source, each verify uses its own prepared build, and no baseline or sibling build artifacts are reused

### Requirement: Stable evidence results

For identical selected mutants and deterministic command outcomes under the
same budgets, serial and parallel execution SHALL produce identical ordered
classifications, score, pass decision, summary and evidence JSON. Selection
SHALL apply the `mutation-selection` exclusions before sampling while retaining
seeded order and at most one mutant per line. The gate SHALL retain existing
equivalence classification and coverage rules. Selected test timeouts SHALL
be classified as `timeout` and excluded from scoring; generated evidence SHALL
require a killed or survived result to be measured, as specified by Timeout
scoring.

#### Scenario: Parallel results match serial
- **WHEN** the same section is checked with concurrency `1` and greater than `1`, with prepared and unprepared commands and deliberately different completion order
- **THEN** mutant identities and results remain in selection order, evidence JSON and summaries match, and each mutant's output directory retains its original index

#### Scenario: Classification rules retained
- **WHEN** selected mutants include successful checks, non-timeout failing tests, failed prepare or configure/build steps, source-line mismatches and equivalent annotations
- **THEN** they are respectively survived, killed, invalid, invalid and equivalent; mismatches and equivalent mutants execute no commands, and scoring counts only killed and survived mutants

#### Scenario: All invalid remains unmeasured
- **WHEN** mutants were generated but every mutant is invalid
- **THEN** the score is absent, evidence is unmeasured and the gate fails at every concurrency setting

### Requirement: Baseline step time boxes

Every mutant prepare and selected verify step SHALL use the lesser of its
manifest timeout and `max(30.0 seconds, 3 * baseline step seconds)` when that
baseline duration is positive. A missing or zero baseline duration SHALL use
the manifest timeout. The baseline SHALL retain its manifest timeouts. Queue
waiting SHALL NOT consume a step's budget or change it based on concurrency.

#### Scenario: Parallel mutants respect time box
- **WHEN** several mutants including queued and hanging work run with prepared or unprepared verification
- **THEN** each step receives the baseline-derived limit capped by its own manifest timeout, independent of queue wait and sibling durations, and timed-out test steps classify their mutants as timeout

#### Scenario: Missing duration uses manifest ceiling
- **WHEN** a mutant step has a missing or zero baseline duration, or its manifest timeout is below the baseline-derived limit
- **THEN** it uses its manifest timeout without exceeding it or changing the baseline or shared manifest

#### Scenario: Preparation timeout is invalid
- **WHEN** a mutant prepare step or configure/build step exceeds its baseline-derived limit
- **THEN** its process group is terminated, the mutant is invalid, later steps for that mutant do not run, and other mutants can finish

### Requirement: Worker cleanup

The gate SHALL clean each mutant's source and build trees after execution,
retain its available logs, receipts and artifacts, and wait for active workers
before cleaning baseline resources. Unexpected worker errors SHALL propagate
without being reported as successful evidence.

#### Scenario: Completed workers release resources
- **WHEN** concurrent workers finish normally or encounter command failure or timeout
- **THEN** command process groups are terminated, private source and build trees are removed, available diagnostics and artifacts remain under their original mutant indices, and baseline cleanup follows worker completion

#### Scenario: Worker exception cannot pass
- **WHEN** a worker raises an unexpected exception while another worker is active
- **THEN** active workers finish and their workspaces are cleaned before baseline cleanup, the error propagates, and gather writes no successful partial evidence result

### Requirement: Test timeout classification

After a passing baseline, the executor SHALL classify a mutant as `timeout`
when its first non-passing selected verify receipt has `exit: null`, except
that configure, build and manifest prepare steps SHALL remain `invalid`.
This SHALL apply with and without preparation. Passing steps, actual test
failures and baseline timeouts SHALL retain their existing classifications.

#### Scenario: Unprepared test times out
- **WHEN** the baseline passes and a mutant without prepare steps exceeds a selected test step's time limit
- **THEN** its result is `timeout`, its failing receipt retains `exit: null`, and no later step for that mutant runs

#### Scenario: Prepared test times out
- **WHEN** the baseline and a mutant's preparation pass but its selected test step exceeds its time limit
- **THEN** its result is `timeout`, its failing receipt retains `exit: null`, and no later step for that mutant runs

#### Scenario: Other outcomes retain classification
- **WHEN** mutants have passing steps, unexpected integer test exits including signals, or timeouts in prepare, configure or build, or the unmutated baseline times out
- **THEN** passing mutants survive, actual test failures are killed, preparation/configure/build timeouts are invalid, and a baseline timeout marks selected mutants invalid without dispatching them

### Requirement: Timeout scoring

Only killed and survived mutants SHALL enter the score and summary's caught
fraction; timeouts SHALL enter neither numerator nor denominator. Evidence with
generated mutants SHALL be unmeasured unless at least one mutant was killed or
survived. Equivalent, timeout, invalid and pending results SHALL provide no
measurement. Unmeasured evidence SHALL have a null score, fail at every
threshold, and reach the existing `gate_evidence` pause without strengthening
tasks. Coverage, baseline-failure and no-mutant skip rules SHALL remain unchanged.

#### Scenario: Mixed results exclude timeouts
- **WHEN** evidence contains one killed mutant, one survivor, two timeouts, one invalid and one equivalent
- **THEN** its score is `0.5`, its caught fraction is `1/2`, and the timeouts contribute no credit; evidence containing a survivor and only timeouts otherwise scores zero

#### Scenario: All timeouts are unmeasured
- **WHEN** a passing baseline is followed by generated mutants that all time out in selected tests
- **THEN** the score is null, evidence is unmeasured, the pass check fails even at threshold zero, and the existing gate pauses with the summary and adds no strengthening tasks

#### Scenario: Timeouts mixed with invalid are unmeasured
- **WHEN** generated mutants include timeouts and invalids but no killed, survived or equivalent results
- **THEN** the score is null, evidence is unmeasured, and the pass check fails at every threshold

#### Scenario: Equivalent and skipped behavior retained
- **WHEN** otherwise covered evidence with a passing baseline contains equivalent and timeout mutants, or mutation was skipped with no mutants
- **THEN** the score stays null, the equivalent/timeout case is unmeasured and fails at every threshold, while the skipped case remains not unmeasured and passes subject to existing coverage and baseline-failure checks; existing skip reasons are preserved

#### Scenario: Equivalent and timeout only evidence is unmeasured
- **WHEN** generated mutants consist only of equivalents, equivalents with timeouts, or equivalents with timeouts and invalids, without any killed or survived result
- **THEN** evidence is unmeasured, its score is null and the gate fails even at threshold zero, equally at serial and parallel concurrency

#### Scenario: A behavioral outcome establishes measurement
- **WHEN** otherwise covered evidence with a passing baseline adds one killed or survived result to equivalent, timeout, invalid or pending results
- **THEN** evidence is measured, a lone killed result scores `1.0`, a lone survivor scores `0.0`, and passing depends on the configured threshold; uncovered scenarios or baseline failure still prevent a pass

### Requirement: Timeout reporting

`evidence.json` SHALL record each mutant's result in selection order. Evidence
events SHALL add integer `timeouts`, counting timeout results including zero.
For positive N, summaries SHALL include exactly one line `N of M mutants timed
out and were not counted`, where M counts all selected mutants, including
invalid and equivalent results. Reviewer summaries and unmeasured pause
details SHALL retain this line and accurately describe timeout results.

#### Scenario: Persist timeout results
- **WHEN** gather finishes a mixture of timeout and other mutant results, with or without prepare steps and at concurrency 1 or 2
- **THEN** evidence JSON records every timeout result under its original mutant identity and selection order with the correctly computed score

#### Scenario: Event counts timeout mutants
- **WHEN** a section emits evidence containing two test-timeout mutants and other results, including invalid preparation/build timeouts
- **THEN** the event includes `timeouts: 2` and preserves its section, passed, score, uncovered and survived fields

#### Scenario: Zero timeouts are explicit
- **WHEN** a section emits evidence with no mutant timeout results, including skipped or baseline-failed evidence
- **THEN** its event includes `timeouts: 0` and its summary contains no timeout-count line

#### Scenario: Reviewer receives timeout count
- **WHEN** a section with one killed mutant, one survivor, two timeouts, one invalid and one equivalent passes at threshold `0.5` and its gate review context is produced
- **THEN** the stored summary and reviewer evidence text contain `1/2 caught` and exactly one line `2 of 6 mutants timed out and were not counted`

#### Scenario: Unmeasured summary explains timeouts
- **WHEN** evidence consists of three timeout mutants, or timeouts mixed only with invalids
- **THEN** its summary says nothing was measured, describes timeout results accurately, and includes the exact timeout-count line even without a numeric score; three timeouts produce `3 of 3 mutants timed out and were not counted`

### Requirement: Unmeasured equivalence reporting

Evidence with generated mutants but no killed or survived result SHALL explain
that nothing was measured. When equivalent annotations contribute to this result,
the summary SHALL identify them accurately and direct review of the annotations,
rather than describing them as invalid mutants. JSON result order and shape,
null score representation, and existing timeout reporting SHALL be preserved.

#### Scenario: Equivalent outcomes explained accurately
- **WHEN** evidence contains only equivalents or a mixture of equivalents, timeouts and invalid results with no killed or survived mutant
- **THEN** its summary explains the lack of measurement and the equivalent annotations, includes the existing timeout-count line exactly once when applicable, and persisted JSON retains the original classifications in selection order with a null score

#### Scenario: Unmeasured equivalents pause the section
- **WHEN** an otherwise covered section with a passing baseline and no previous strengthening round has only equivalent results or equivalents mixed with timeouts or invalids
- **THEN** it pauses at `gate_evidence` with the accurate summary, does not record section completion or add strengthening tasks, and its evidence event reports failure, a null score and the actual timeout count

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

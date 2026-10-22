## Purpose

Distinguish selected mutant test timeouts from caught mutations, exclude those
unjudged results from scoring, and expose them to evidence consumers and gate
reviewers while preserving #4's execution guarantees.

## ADDED Requirements

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
fraction; timeouts SHALL enter neither numerator nor denominator. Generated
mutants with only timeout or invalid results SHALL be unmeasured with a null
score, fail at every threshold, and reach the existing `gate_evidence` pause
without strengthening tasks. Equivalent, coverage, baseline-failure and skip
rules SHALL remain unchanged.

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
- **THEN** the score stays null and those cases do not become unmeasured solely from lacking killed or survived mutants; existing skip reasons and coverage failures are preserved

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

## MODIFIED Requirements

### Requirement: Stable evidence results

For identical selected mutants and deterministic command outcomes under the
same budgets, serial and parallel execution SHALL produce identical ordered
classifications, score, pass decision, summary and evidence JSON. The gate
SHALL retain existing selection, equivalence, coverage and scoring rules,
except that selected test timeouts SHALL be classified as `timeout` and
excluded from scoring as specified above.

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

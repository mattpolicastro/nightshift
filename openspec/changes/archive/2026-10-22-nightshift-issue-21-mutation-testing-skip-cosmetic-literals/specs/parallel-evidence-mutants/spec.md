## Purpose

Preserve deterministic parallel evidence while requiring an observed killed or
survived result and accurately reporting annotation-only measurement gaps.

## ADDED Requirements

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

## MODIFIED Requirements

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

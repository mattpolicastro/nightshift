## Purpose

Distinguish non-compiling mutants from test kills using trusted manifest IDs,
retaining baseline, preparation, concurrency, timeout and measurement contracts.

## ADDED Requirements

### Requirement: Configurable invalid steps

The manifest SHALL expose `gates.mutation_invalid_steps` as
`Manifest.mutation_invalid_steps`, an immutable tuple defaulting to
`("configure", "build")`. An explicit array SHALL replace the default, including
an empty array. Entries SHALL be nonempty strings; malformed values SHALL raise
`ValueError` naming the setting. This setting SHALL classify, not select, steps.

#### Scenario: Defaults match build steps
- **WHEN** the setting is omitted in parsed or directly constructed manifests
- **THEN** its tuple equals `evidence.BUILD_STEPS`, exactly `("configure", "build")`, and existing default classification remains unchanged

#### Scenario: Explicit settings round trip
- **WHEN** an empty or custom array is parsed, templated with run overrides and loaded again
- **THEN** entries and ordering are preserved verbatim; duplicates and unused IDs are accepted and no commands are added or reordered
- **THEN** `[]` removes the configure/build exception and a custom array replaces rather than extends it

#### Scenario: Invalid settings are rejected
- **WHEN** the setting is a scalar, a table, or an array containing non-string or empty-string entries
- **THEN** parsing raises `ValueError` naming `gates.mutation_invalid_steps` before execution

### Requirement: Configured failure classification

After a passing baseline, the first failing mutant receipt SHALL yield invalid
if its ID is configured in `mutation_invalid_steps` or belongs to preparation.
Otherwise it SHALL yield timeout for a null exit and killed for an unexpected
integer exit. The same rule SHALL apply to every route and execution phase;
passing required checks SHALL yield survived.

#### Scenario: Non compiling mutant is invalid
- **WHEN** a compiler fixture under configured ID `dart-analyze` accepts baseline source and rejects mutated source before the test step
- **THEN** the mutant is invalid, the test step does not run, its failing receipt is retained and it contributes neither a kill nor a scored result
- **THEN** all-invalid evidence remains unmeasured with null score and a failed gate

#### Scenario: Replacement and preparation precedence
- **WHEN** custom or empty invalid-step lists are used and the first failure is a configured ID, an omitted default ID, a test ID or a prepare ID, including timeouts
- **THEN** configured and prepare failures remain invalid; all other null exits are timeout and unexpected integer exits are killed, including omitted configure/build IDs
- **THEN** expected exits still pass, baseline failure still dispatches no mutants, and source mismatch and equivalent annotations retain their existing prechecks

#### Scenario: Routes and phases agree
- **WHEN** the same configured failure occurs under fallback or routed commands during full, narrowed or confirmation execution, at concurrency one or two
- **THEN** classification is identical, later commands stop, a narrowed invalid result triggers no confirmation and diagnostics retain the deciding phase
- **THEN** timeout budgets, cleanup, result ordering and evidence schemas remain unchanged

## MODIFIED Requirements

### Requirement: Stable evidence results

Serial and parallel execution SHALL produce identical ordered results, scores,
pass decisions, summaries and JSON for identical mutants, commands and budgets. Selection SHALL retain
pre-sampling exclusions, seeded order and one mutant per line. Equivalence and
coverage SHALL remain unchanged. Configured invalid IDs SHALL govern failure
classification; other selected-test timeouts SHALL be timeout. Timeout scoring
SHALL require at least one killed or survived result for measurement.

#### Scenario: Parallel results match serial
- **WHEN** the same section is checked with concurrency `1` and greater than `1`, with prepared and unprepared commands and deliberately different completion order
- **THEN** mutant identities and results remain in selection order, evidence JSON and summaries match, and each mutant's output directory retains its original index

#### Scenario: Classification rules retained
- **WHEN** selected mutants include successful checks, non-timeout failing tests whose IDs are not configured as invalid, failed prepare or configured invalid steps (configure/build by default), source-line mismatches and equivalent annotations
- **THEN** they are respectively survived, killed, invalid, invalid and equivalent; mismatches and equivalent mutants execute no commands, and scoring counts only killed and survived mutants

#### Scenario: All invalid remains unmeasured
- **WHEN** mutants were generated but every mutant is invalid
- **THEN** the score is absent, evidence is unmeasured and the gate fails at every concurrency setting

### Requirement: Baseline step time boxes

Each mutant phase SHALL cap each prepare/selected step at the lesser of its
manifest timeout and max(30 seconds, 3 times its full-baseline duration).
Missing/zero durations SHALL retain manifest ceilings. Budgets SHALL use the
mutant's resolved sequence, not narrowed or sibling timings. Baselines SHALL
retain manifest timeouts; queueing/concurrency SHALL NOT change budgets or the
shared manifest.

#### Scenario: Parallel mutants respect time box
- **WHEN** several mutants including queued and hanging work run with prepared or unprepared verification
- **THEN** each step receives the baseline-derived limit capped by its own manifest timeout, independent of queue wait and sibling durations, and timed-out test steps not configured as invalid classify their mutants as timeout

#### Scenario: Missing duration uses manifest ceiling
- **WHEN** a mutant step has a missing or zero baseline duration, or its manifest timeout is below the baseline-derived limit
- **THEN** it uses its manifest timeout without exceeding it or changing the baseline or shared manifest

#### Scenario: Preparation timeout is invalid
- **WHEN** a mutant prepare step or configured invalid step (configure/build by default) exceeds its baseline-derived limit
- **THEN** its process group is terminated, the mutant is invalid, later steps for that mutant do not run, and other mutants can finish

#### Scenario: Both phases use their full baseline
- **WHEN** step sequences share a step ID but have different full-baseline timings, and a mutant's narrowed execution is faster than its assigned full baseline
- **THEN** its narrowed and confirmation steps and preparation use the same limits derived from its assigned full-baseline receipts, without cross-sequence timing reuse or narrowed-time recalculation

### Requirement: Test timeout classification

After a passing baseline, the executor SHALL classify a mutant as `timeout`
when its first non-passing selected verify receipt has `exit: null`, except
that configured invalid IDs (configure and build by default) and manifest
prepare steps SHALL remain `invalid`.
This SHALL apply with and without preparation. Passing steps, failures of tests
not configured as invalid and baseline timeouts SHALL retain their classifications.

#### Scenario: Unprepared test times out
- **WHEN** the baseline passes and a mutant without prepare steps exceeds a selected test step's time limit, with its ID not configured as invalid
- **THEN** its result is `timeout`, its failing receipt retains `exit: null`, and no later step for that mutant runs

#### Scenario: Prepared test times out
- **WHEN** the baseline and a mutant's preparation pass but its selected test step, not configured as invalid, exceeds its time limit
- **THEN** its result is `timeout`, its failing receipt retains `exit: null`, and no later step for that mutant runs

#### Scenario: Other outcomes retain classification
- **WHEN** mutants have passing steps, unexpected integer exits including signals from tests not configured as invalid, or timeouts in prepare or configured invalid steps, or the unmutated baseline times out
- **THEN** passing mutants survive, actual test failures are killed, preparation and configured-step timeouts are invalid, and a baseline timeout marks selected mutants invalid without dispatching them

### Requirement: Narrow then confirm

The worker SHALL expand `{mutant_path}` to the relative path for narrowing. A passing narrowed sequence SHALL trigger the same full sequence with the placeholder empty, against the same mutation and a fresh prepared build. Only a full pass SHALL establish survival. Existing expected-exit and result classifications SHALL apply in both phases. Each worker SHALL occupy one concurrency slot through both phases.

#### Scenario: Narrowed failure kills
- **WHEN** a narrowed selected-test command whose ID is not configured as invalid exits with an unexpected non-timeout status after passing preparation
- **THEN** the mutant is killed, no confirmation or later command executes, and it enters the score exactly once as killed

#### Scenario: Narrowed survivor receives full confirmation
- **WHEN** every narrowed step passes, including a fixture where the selected tests miss a killing test
- **THEN** the worker reruns every selected step with `{mutant_path}` empty against the same mutated source, including steps without the placeholder, and a full test failure whose step ID is not configured as invalid kills the mutant while a fully passing confirmation counts it once as survived
- **THEN** preparation runs again in a fresh build, so narrowed artifacts cannot supply cached full-suite results

#### Scenario: Timeout and invalid results remain distinct
- **WHEN** either phase encounters a selected-test timeout or unexpected integer test exit (including a signal) whose ID is not configured as invalid, or a failed/timed-out prepare or configured invalid step
- **THEN** the result is respectively timeout, killed or invalid under existing rules, later steps do not run, and only killed or survived results enter the score
- **THEN** a narrowed timeout or invalid result triggers no confirmation; source mismatches and equivalent annotations still run no mutant commands

#### Scenario: Commands without narrowing run once
- **WHEN** no command in the selected sequence contains `{mutant_path}`
- **THEN** it runs once in full mode and its existing classification is final without a duplicate confirmation

#### Scenario: Two phases preserve isolation and cleanup
- **WHEN** prepared or unprepared mutants sharing a file run at concurrency one or two, or a worker raises during confirmation while another worker is active
- **THEN** each phase sees only its assigned mutation, private build/HOME/TMPDIR and existing sandbox restrictions; a worker retains one pool slot through confirmation and source and baseline content remain isolated
- **THEN** phase process groups terminate, diagnostics and artifacts remain, confirmation and mutant workspaces are cleaned before baseline cleanup, and unexpected errors propagate without successful partial evidence

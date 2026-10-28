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

The evidence gate SHALL finish a full unmutated baseline for every distinct resolved ordered step sequence used by its selected mutants before starting any mutant verification. It SHALL baseline each such sequence once per gate, in first-use order, with `{mutant_path}` empty, ordinary preparation and manifest timeouts. Unused routes SHALL NOT run baselines. It SHALL execute at most the configured number of mutants at once and allow independent mutants to overlap when capacity exists. A single selected sequence SHALL retain the existing one-baseline behavior.

#### Scenario: Baseline precedes overlap
- **WHEN** a successful single-sequence baseline is followed by at least three executable mutants with a limit of `2`
- **THEN** exactly one baseline finishes before mutant work starts, two mutants can overlap, and no more than two mutant executions are active at once

#### Scenario: Serial and oversized limits
- **WHEN** the same mutants run with limit `1` and with a limit larger than their count
- **THEN** the first run has no overlapping mutants and the second executes each mutant's required phases once without exceeding the number of mutants available

#### Scenario: Failed baseline stops mutants
- **WHEN** any required unmutated prepare or selected step fails or times out, including a later route's baseline after an earlier baseline passed
- **THEN** no mutant verification starts, all selected mutants are invalid, baseline failure fails the gate, no later baseline is started, and all created baseline source and build directories are cleaned with diagnostics retained

#### Scenario: Skipped mutation stays skipped
- **WHEN** gather has no mutation steps, no section base, or no generated mutants
- **THEN** it retains the existing skip reason and coverage result and starts neither a mutation baseline nor mutant workers

#### Scenario: Used step sequences are baselined once
- **WHEN** multiple mutants use overlapping routes, two routes resolve to the same ordered step sequence, a mutant uses fallback, and an unused route exists
- **THEN** each distinct used sequence including fallback gets one full baseline, identical sequences share it, differently ordered sequences remain distinct, and unused routes get none
- **THEN** every required baseline passes before any worker starts and all selected mutants retain their original identities and order

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

`test_parallel_mutants_respect_time_box` SHALL use test-local subprocess blocking for intended hangs, with no natural completion near the enforced time limit. It SHALL preserve both preparation modes, exact budget-formula assertions, queue-wait independence, ordered classifications and real process cleanup. The restriction to editing only this test and its decorators applied to the #49 regression repair; subsequent execution features SHALL preserve the test and its assertions and add their coverage in new test files.

#### Scenario: Hanging work has fixed outcomes
- **WHEN** the test runs with `prepared=False` or `prepared=True` and two mutant worker slots
- **THEN** the baseline and ordinary preparation/verification steps succeed, the first three mutants block in their selected test step until the real verifier times out, the fourth exits 3, and the fifth exits 0
- **THEN** results are exactly `timeout, timeout, timeout, killed, survived` in selection order, with the existing receipt, diagnostic, artifact, process-death, source-isolation, and cleanup-order assertions retained

#### Scenario: Exact budgets survive queue wait
- **WHEN** controlled baseline durations exercise the floor, fractional scaling, missing/zero fallback, and both manifest-cap cases while a third hanging mutant queues behind the first two
- **THEN** the test asserts exact budgets `(10.0, 11.25, 40, 50, 20, 5)` for the corresponding steps and `10.0` for the final test step, retains baseline manifest ceilings, and verifies manifest immutability
- **THEN** two workers overlap without exceeding the limit, the third waits at least 10.0 seconds before execution, and each hanging final step still receives and consumes its full 10.0-second execution budget

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

### Requirement: Mutation decision receipts

The gate SHALL retain the initial and confirmation step receipts separately and write `mutation-runs.json` linking selected mutants, routes, step sequences and baselines to their decisive phase. Entries SHALL retain selection order independently of completion order. Existing evidence JSON, step receipt arrays, summaries, score calculation and loop events SHALL retain their shape and meaning.

#### Scenario: Deciding runs are recorded
- **WHEN** a gate finishes full-only results, narrowed kills, confirmed survivors or confirmation failures
- **THEN** each sidecar entry records the stable mutant identity, one-based index, route index or fallback, ordered step IDs, assigned baseline, final classification, `decided_by` phase and relative paths to all runs' retained receipts
- **THEN** confirmation receipts under `mutant-N/confirmation/` do not overwrite the initial `mutant-N/receipts.json`, and baseline receipts remain attributable to their full step sequence

#### Scenario: Nonexecuted results and ordering are explicit
- **WHEN** results include equivalents, source mismatches or a gate-wide baseline failure, or workers finish out of order
- **THEN** nonexecuted mutants have no fabricated run receipts and identify `precheck` or `baseline` as the decision source, including the failed baseline identity for an abort
- **THEN** equivalent deterministic serial and parallel runs produce identical decision indexes and existing evidence JSON in selection order, without durations or absolute run-directory paths in the index

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

### Requirement: Snapshot isolation across evidence phases

The evidence harness SHALL pass the run snapshot context to every used-sequence baseline and executable mutant phase, compute keys from each actual source extraction, and supply a fresh private clone per build. It SHALL never warm or publish dependencies from evidence execution. Existing selection, baseline ordering, concurrency, expected exits, classifications, scores, timeout budgets, receipt schemas and cleanup order SHALL remain unchanged.

#### Scenario: Baselines mutants and confirmations stay independent
- **WHEN** routed mutants run concurrently and a passing narrowed phase requires full confirmation
- **THEN** all required baselines finish before workers, each baseline and phase receives a distinct writable snapshot clone, candidate mutations remain visible, and a narrowed phase's cache writes cannot affect confirmation or siblings
- **THEN** the store remains unchanged, workers retain their existing slot and timeout rules, and existing deterministic results and receipt schemas are preserved

#### Scenario: Mutated lockfile falls back without changing classifications
- **WHEN** a mutant changes a declared lockfile while retaining the candidate SHA argument, or any evidence build cannot use its snapshot
- **THEN** lookup uses that build's actual lockfile bytes and records fallback per affected execution, with thread-safe run-event forwarding and no warm invocation
- **THEN** original prepare/test outcomes govern baseline failure, invalid, timeout, killed or survived classification, and setup misses fabricate no test receipts or mutation kills

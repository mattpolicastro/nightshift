## Purpose

Run routed mutants with bounded concurrency and full-baseline time limits, allowing narrowed kills while requiring full confirmation of survivors and retaining auditable decisions.

## ADDED Requirements

### Requirement: Narrow then confirm

The worker SHALL expand `{mutant_path}` to the relative path for narrowing. A passing narrowed sequence SHALL trigger the same full sequence with the placeholder empty, against the same mutation and a fresh prepared build. Only a full pass SHALL establish survival. Existing expected-exit and result classifications SHALL apply in both phases. Each worker SHALL occupy one concurrency slot through both phases.

#### Scenario: Narrowed failure kills
- **WHEN** a narrowed selected-test command exits with an unexpected non-timeout status after passing preparation
- **THEN** the mutant is killed, no confirmation or later command executes, and it enters the score exactly once as killed

#### Scenario: Narrowed survivor receives full confirmation
- **WHEN** every narrowed step passes, including a fixture where the selected tests miss a killing test
- **THEN** the worker reruns every selected step with `{mutant_path}` empty against the same mutated source, including steps without the placeholder, and a full test failure kills the mutant while a fully passing confirmation counts it once as survived
- **THEN** preparation runs again in a fresh build, so narrowed artifacts cannot supply cached full-suite results

#### Scenario: Timeout and invalid results remain distinct
- **WHEN** either phase encounters a selected-test timeout, an unexpected integer test exit including a signal, or a failed/timed-out prepare, configure or build step
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

## MODIFIED Requirements

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

### Requirement: Baseline step time boxes

Every mutant prepare and selected step in either phase SHALL use the lesser of its manifest timeout and `max(30.0 seconds, 3 * baseline step seconds)` when the corresponding full baseline duration is positive. A missing or zero duration SHALL use the manifest timeout. Durations SHALL come from the full baseline for that mutant's resolved step sequence, never its narrowed run or another sequence. Baselines SHALL retain manifest timeouts. Queue waiting and concurrency SHALL NOT consume or change a step's budget, and the shared manifest SHALL remain immutable.

#### Scenario: Parallel mutants respect time box
- **WHEN** several mutants including queued and hanging work run with prepared or unprepared verification
- **THEN** each step receives the baseline-derived limit capped by its own manifest timeout, independent of queue wait and sibling durations, and timed-out test steps classify their mutants as timeout

#### Scenario: Missing duration uses manifest ceiling
- **WHEN** a mutant step has a missing or zero baseline duration, or its manifest timeout is below the baseline-derived limit
- **THEN** it uses its manifest timeout without exceeding it or changing the baseline or shared manifest

#### Scenario: Preparation timeout is invalid
- **WHEN** a mutant prepare step or configure/build step exceeds its baseline-derived limit
- **THEN** its process group is terminated, the mutant is invalid, later steps for that mutant do not run, and other mutants can finish

#### Scenario: Both phases use their full baseline
- **WHEN** step sequences share a step ID but have different full-baseline timings, and a mutant's narrowed execution is faster than its assigned full baseline
- **THEN** its narrowed and confirmation steps and preparation use the same limits derived from its assigned full-baseline receipts, without cross-sequence timing reuse or narrowed-time recalculation

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

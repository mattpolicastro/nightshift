## Purpose

Reduce section evidence-gate waiting time by checking independent mutants
concurrently while preserving deterministic evidence and baseline-derived
per-step limits.

## ADDED Requirements

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
classifications, score, pass decision, summary and evidence JSON. The gate
SHALL retain existing selection, equivalence, coverage and scoring rules.

#### Scenario: Parallel results match serial
- **WHEN** the same section is checked with concurrency `1` and greater than `1`, with prepared and unprepared commands and deliberately different completion order
- **THEN** mutant identities and results remain in selection order, evidence JSON and summaries match, and each mutant's output directory retains its original index

#### Scenario: Classification rules retained
- **WHEN** selected mutants include successful checks, failing tests, failed prepare or configure/build steps, source-line mismatches and equivalent annotations
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
- **THEN** each step receives the baseline-derived limit capped by its own manifest timeout, independent of queue wait and sibling durations, and timed-out test steps classify their mutants as killed

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

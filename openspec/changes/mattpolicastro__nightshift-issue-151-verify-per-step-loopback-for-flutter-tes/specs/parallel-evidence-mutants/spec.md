## Purpose

Preserve independent evidence executions while applying the configured loopback policy separately at each command launch.

## ADDED Requirements

### Requirement: Per execution loopback policy

Evidence steps SHALL retain loopback through timeout copies and use fresh launch-time policies in baselines, mutants and confirmations. Executions SHALL NOT share mutable profiles or cached listener snapshots. Setup errors SHALL propagate through existing worker-error handling without fabricated test results or successful partial evidence. Routing, concurrency, preparation, budgets, executed-command classifications, receipts and cleanup SHALL otherwise remain unchanged.

#### Scenario: Evidence phases use fresh policies
- **WHEN** opted-in steps are copied with evidence timeouts and executed in distinct baseline, mutant or confirmation build directories, including concurrent runs using the same step IDs
- **THEN** the flag is retained, every opted-in command takes its own snapshot and uses its own profile path, and unflagged and prepare commands retain their original profiles
- **THEN** setup failure produces no fabricated test receipt or mutant kill and propagates under existing worker-error rules, preserving cleanup and all unchanged evidence assertions

## MODIFIED Requirements

### Requirement: Independent mutant workspaces

Each executable mutant SHALL use a private source and fresh build tree under
its stable one-based `mutant-N` directory, including private HOME, TMPDIR,
sandbox profiles and command outputs. Preparation SHALL precede verification
against that mutant alone. Commands SHALL see read-only source, and execution
SHALL retain the existing prepare and verify sandbox restrictions, except that
verification-mode steps explicitly declaring `loopback = true` SHALL use the
localhost TCP allowances and per-launch listener exclusions defined by
verify-preparation. This exception SHALL NOT alter preparation or any
non-network restriction.

#### Scenario: Same file mutations remain separate
- **WHEN** mutants touching the same source file execute concurrently without prepare steps
- **THEN** each command sees only its assigned mutation and private build state, and the baseline source and repository checkout remain unchanged

#### Scenario: Prepared copies remain separate
- **WHEN** each concurrent mutant has prepare steps that copy source into its build tree
- **THEN** each prepare receives only its own mutant's read-only source, each verify uses its own prepared build, and no baseline or sibling build artifacts are reused

## Purpose

Preserve independent evidence executions while applying the configured loopback policy separately at each command launch.

## ADDED Requirements

### Requirement: Per execution loopback policy

Evidence steps SHALL retain loopback through timeout copies and use fresh, private launch-time policies for every baseline, mutant and confirmation. Setup and result-channel errors SHALL propagate without fabricated receipts, mutant judgments or successful partial evidence. Only confirmed command results SHALL enter classification. Routing, concurrency, preparation, budgets, receipt schemas, scoring and cleanup SHALL otherwise remain unchanged.

#### Scenario: Evidence phases use fresh policies
- **WHEN** opted-in steps are copied with evidence timeouts and executed in distinct baseline, mutant or confirmation build directories, including concurrent runs using the same step IDs
- **THEN** the flag is retained, every opted-in command takes its own snapshot and uses its own profile path, and unflagged and prepare commands retain their original profiles
- **THEN** setup failure produces no fabricated test receipt or mutant kill and propagates under existing worker-error rules, preserving cleanup and all unchanged evidence assertions

#### Scenario: Sandbox failure after baseline cannot kill a mutant
- **WHEN** a baseline passes and sandbox startup fails with wrapper exit 65 in a mutant or its confirmation, at concurrency one or two
- **THEN** `test_loopback_sandbox_startup_failure_is_not_mutant_kill` verifies that RuntimeError propagates, the affected mutant remains unjudged instead of killed, survived, invalid or timed out, and no receipt is invented for its unexecuted command regardless of expect
- **THEN** gather produces no successful partial evidence, earlier diagnostics remain, active workers finish under existing rules and worker cleanup precedes baseline cleanup
- **THEN** a genuine unexpected command exit 65 under a valid profile still kills a mutant whose step is not configured as invalid, preserving existing scoring rules

#### Scenario: Baseline sandbox failure dispatches no mutants
- **WHEN** an opted-in baseline cannot start its sandboxed command, including when its expected exit matches the wrapper failure
- **THEN** the setup error propagates without an affected-step receipt or mutant dispatch, and baseline cleanup retains completed receipts and diagnostics

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

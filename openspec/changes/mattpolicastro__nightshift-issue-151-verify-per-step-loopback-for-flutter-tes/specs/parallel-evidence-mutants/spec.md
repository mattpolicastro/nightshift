## Purpose

Preserve independent evidence executions while applying the configured loopback policy separately at each command launch.

## ADDED Requirements

### Requirement: Per execution loopback policy

Every opted-in baseline, mutant and confirmation step SHALL use a fresh private loopback policy and direct profile probe. Setup errors SHALL propagate without fabricated receipts, mutant judgments or successful partial evidence. Successful probes SHALL preserve ordinary command receipt and classification semantics.

#### Scenario: Evidence phases use fresh policies
- **WHEN** opted-in steps are copied with evidence timeouts and executed in distinct baseline, mutant or confirmation build directories, including concurrent runs using the same step IDs
- **THEN** the flag is retained, every opted-in command takes its own snapshot and uses its own profile path, and unflagged and prepare commands retain their original profiles
- **THEN** setup failure produces no fabricated test receipt or mutant kill and propagates under existing worker-error rules, preserving cleanup and all unchanged evidence assertions
- **THEN** routing, concurrency, preparation, timeout-budget calculation, receipt schemas, scoring and cleanup order remain unchanged

#### Scenario: Sandbox failure after baseline cannot kill a mutant
- **WHEN** a baseline passes and the direct profile probe rejects a malformed mutant or confirmation profile with exit 65, at concurrency one or two
- **THEN** `test_loopback_sandbox_startup_failure_is_not_mutant_kill` verifies that RuntimeError propagates, the affected mutant remains unjudged instead of killed, survived, invalid or timed out, and no receipt is invented for its unexecuted command regardless of expect
- **THEN** gather produces no successful partial evidence, earlier diagnostics remain, active workers finish under existing rules and worker cleanup precedes baseline cleanup
- **THEN** a genuine unexpected command exit 65 under a valid profile still kills a mutant whose step is not configured as invalid, preserving existing scoring rules

#### Scenario: Baseline sandbox failure dispatches no mutants
- **WHEN** an opted-in baseline's direct profile probe fails, including when its expected exit matches the probe failure
- **THEN** the setup error propagates without an affected-step receipt or mutant dispatch, and baseline cleanup retains completed receipts and diagnostics

## MODIFIED Requirements

### Requirement: Independent mutant workspaces

Each executable mutant SHALL use private source and fresh build trees under its stable one-based `mutant-N` directory, with private HOME, TMPDIR, profiles and outputs. Preparation SHALL precede verification against that mutant alone; commands SHALL see read-only source. Existing sandbox restrictions SHALL remain except `loopback = true` verification SHALL use verify-preparation's localhost TCP policy. Preparation and non-network restrictions SHALL remain unchanged.

#### Scenario: Same file mutations remain separate
- **WHEN** mutants touching the same source file execute concurrently without prepare steps
- **THEN** each command sees only its assigned mutation and private build state, and the baseline source and repository checkout remain unchanged

#### Scenario: Prepared copies remain separate
- **WHEN** each concurrent mutant has prepare steps that copy source into its build tree
- **THEN** each prepare receives only its own mutant's read-only source, each verify uses its own prepared build, and no baseline or sibling build artifacts are reused

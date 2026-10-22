## Purpose

Integrate the current PR base and verify the resulting run head before readiness and publication, preserving recoverable failures and evidence attributable to the run's own section changes.

## ADDED Requirements

### Requirement: Fetch current base

The harness SHALL fetch the manifest's `pr_base` from the source clone's `origin` into the run repository before declaring `ready_to_publish` and on every eligible `publish` attempt, including retries. It SHALL use the freshly fetched commit, leave the source checkout unchanged, and prevent readiness/publication when fetching fails.

#### Scenario: Readiness uses origin
- **WHEN** all tasks are complete and the source clone's local base is stale relative to its origin
- **THEN** the readiness gate fetches the configured base from that origin and evaluates the fetched commit before final checks and the readiness pause, without changing the source checkout

#### Scenario: Publish fetches again
- **WHEN** the base advances after readiness or after a failed publication attempt
- **THEN** the next eligible publish attempt fetches once and checks the newly fetched base before any push or PR operation

#### Scenario: Fetch cannot establish base
- **WHEN** fetching fails, times out, or the configured base is missing
- **THEN** the run pauses unscheduled as `base_fetch` with a ref-specific diagnostic and resume instructions, the branch remains unchanged, and no stale fetched value permits readiness or publication

### Requirement: Preserve run history

When the fetched base is not an ancestor of the run head, the harness SHALL create a merge with the previous run head as first parent and fetched base as second parent, using its own commit identity. It SHALL never fast-forward away the run history. A conflicting merge SHALL pause as `base_conflict`, list the conflicting paths, leave the run branch unchanged and perform no publication.

#### Scenario: Clean moved base
- **WHEN** the fetched base has commits absent from the run and the merge is clean
- **THEN** a harness-authored two-parent merge advances the run branch with the previous head as first parent and fetched base as second parent

#### Scenario: Fast forward shaped base
- **WHEN** the old run head is an ancestor of the fetched base
- **THEN** integration still creates the two-parent merge instead of replacing the run ref with the base commit

#### Scenario: Conflicting files
- **WHEN** integrating the fetched base conflicts in one or more files
- **THEN** `base_conflict` names all conflicting paths, the run head and tree remain unchanged, and verification, push and PR operations do not run

### Requirement: Verify integrated head

After a base integration, the harness SHALL run the trusted manifest's prepare and verify pipeline on the resulting head before readiness or publication. Failed checks SHALL pause as `base_drift` with retained receipts and logs, keep the merged head available for repair, and prevent publication. A base already incorporated with no unresolved verification obligation SHALL add only one fetch and no merge or extra verification.

#### Scenario: Merged head passes
- **WHEN** integration succeeds and all trusted prepare and verify receipts pass
- **THEN** readiness may proceed and publication pushes that verified merged head, preserving adopted PR reuse or existing draft PR reconciliation and the human-checks pause

#### Scenario: Drift breaks verify
- **WHEN** a clean base merge causes a trusted verify step to fail or time out
- **THEN** the run retains the merged branch and pauses as `base_drift` naming the base, head, failing checks and saved receipt/log paths, without push, PR operations or automatic acceptance-fix tasks

#### Scenario: Preparation fails
- **WHEN** preparation for the merged head fails
- **THEN** the existing preparation receipts are retained, later verify steps do not run, and the same `base_drift` publication block applies without changing sandbox rules

#### Scenario: Unmoved base
- **WHEN** fetching returns an already incorporated base and no new or pending integration verification is required
- **THEN** that gate performs one fetch, creates no merge and runs no additional verification, and normal readiness or publication can proceed

### Requirement: Recover pending verification

The harness SHALL persist integration intent before advancing the branch and bind successful verification to the exact integrated head. Reloads, retries and repair commits SHALL NOT allow ancestry alone to discharge unfinished verification. Missing new state fields SHALL retain compatibility with existing runs, and unknown fields SHALL remain ignorable.

#### Scenario: Interrupted integration
- **WHEN** execution stops after saving integration intent, after advancing the branch, or after verification before saving success
- **THEN** a later check fetches again, reconciles the recorded intent with the actual branch, and completes required verification before readiness/publication without duplicating an already applied merge

#### Scenario: Repair against unchanged base
- **WHEN** a human resumes a drift-paused run with an unchanged failing head or a descendant repair, or resumes a conflict-paused run after incorporating the base manually, and the fetched base has not moved
- **THEN** verification runs again, the failing head stays blocked, and only a passing current head may become ready and publish

#### Scenario: Legacy state
- **WHEN** a run state without base-sync fields or with additional unknown fields is loaded
- **THEN** it remains readable, a fresh fetch and actual ancestry determine required integration, and existing pauses, task state and PR identity are preserved

### Requirement: Exclude integrated base lines from evidence

Section mutation evidence SHALL exclude lines introduced solely through integrated base commits while preserving the section's own eligible changes at their current head locations. It SHALL preserve earlier-section exclusion, known-scenario coverage, mutation filtering, sampling, classifications, scoring and timeout behavior. It SHALL NOT mark evidence successful when a trustworthy comparison baseline cannot be constructed.

#### Scenario: Base lines are not mutated
- **WHEN** an upstream merge falls between a section's recorded start and evaluated head, including upstream additions in the same file that shift the section's line numbers
- **THEN** generated mutants include eligible section-owned changes at their head locations and exclude upstream-only additions and earlier-section changes

#### Scenario: Revision after integration
- **WHEN** a later revision section runs after reloading a run that integrated a newer base
- **THEN** evidence retains the integrated-base context and measures only that section's eligible changes, excluding base-only and prior-section lines

#### Scenario: Comparison baseline conflicts
- **WHEN** incorporating the recorded integrated base into the section's comparison baseline conflicts
- **THEN** the harness pauses as `gate_evidence` with paths and baseline identifiers before generating mutants, without marking evidence passed or adding strengthening tasks

### Requirement: Preserve publication contracts

The new gate SHALL retain existing target-repository validation, locking, version-1 command JSON, publication retry reconciliation, and review rules. A gate refusal SHALL expose its diagnostic through the existing publication refusal contract without claiming successful publication.

#### Scenario: Wrong publication target
- **WHEN** publish has no manifest GitHub repository or a hosted source origin mismatches it
- **THEN** the existing target refusal occurs before the new publication fetch, merge, verification, push or PR operations

#### Scenario: Drift refusal as JSON
- **WHEN** `publish --json` or `act publish --json` encounters a base conflict or verification drift
- **THEN** it exits nonzero with exactly one existing-format version-1 refusal document conveying the pause diagnostic, retains the PR identity, and performs no publication

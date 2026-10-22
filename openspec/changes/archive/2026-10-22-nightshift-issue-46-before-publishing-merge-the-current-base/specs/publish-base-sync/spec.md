## Purpose

Integrate the current PR base and verify the resulting run head before readiness and publication, preserving recoverable failures and evidence attributable to the run's own section changes.

## ADDED Requirements

### Requirement: Fetch current base

The harness SHALL fetch the manifest's `pr_base` from the source clone's `origin` into the run repository before declaring `ready_to_publish` and on every eligible `publish` attempt, including retries. It SHALL use the freshly fetched commit, leave the source checkout unchanged, and prevent readiness/publication when fetching fails. Publish SHALL bound its single fetch to 20 seconds and SHALL NOT retry fetching inline.

#### Scenario: Readiness uses origin
- **WHEN** all tasks are complete and the source clone's local base is stale relative to its origin
- **THEN** the readiness gate fetches the configured base from that origin and evaluates the fetched commit before final checks and the readiness pause, without changing the source checkout

#### Scenario: Publish fetches again
- **WHEN** the base advances after readiness or after a failed publication attempt
- **THEN** the next eligible publish attempt fetches once and checks the newly fetched base before any push or PR operation

#### Scenario: Fetch cannot establish base
- **WHEN** fetching in driver preparation fails, times out, or the configured base is missing
- **THEN** the run pauses unscheduled as `base_fetch` with a ref-specific diagnostic and resume instructions, the branch remains unchanged, and no stale fetched value permits readiness or publication

#### Scenario: Publish fetch fails retryably
- **WHEN** a ready run's publish fetch times out at its 20-second limit, fails, or cannot find the configured base
- **THEN** direct `publish --json` exits 1 with exactly one version-1 top-level `error` document naming the ref and explaining retry, with no outcome or reasons and no stale-base fallback
- **THEN** the ready pause, checks, branch, PR and pending verification remain unchanged, and no merge, verify, push, PR operation or `base_fetch` pause occurs

### Requirement: Preserve run history

When the fetched base is not an ancestor of the run head, the driver in `finals` via `prepare_publish` SHALL create a merge with the previous run head as first parent and fetched base as second parent, using its own commit identity. It SHALL never fast-forward away the run history. A conflicting merge SHALL pause as `base_conflict`, list the conflicting paths, leave the run branch unchanged and perform no publication. Publish SHALL NOT construct or install merges.

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

After integration, only the driver in `finals` via `prepare_publish` SHALL run trusted prepare/verify steps before readiness. Publish SHALL NOT prepare or verify inline. Failed checks SHALL pause as `base_drift`, retaining the merged head, receipts and logs for repair and blocking publication. An incorporated base with no verification obligation SHALL add one fetch and no merge or extra verification.

#### Scenario: Merged head passes
- **WHEN** integration succeeds and all trusted prepare and verify receipts pass
- **THEN** driver final checks may proceed and restore readiness at the verified merged head; a later publish fetches once and pushes that head if still eligible, preserving adopted PR reuse or existing draft PR reconciliation and the human-checks pause without merging or verifying inline

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
- **THEN** later driver preparation fetches again, reconciles the recorded intent with the actual branch, and completes required verification before readiness/publication without duplicating an already applied merge
- **THEN** any eligible publish with this outstanding obligation only fetches and schedules the driver, leaving the head and obligation intact even if the fetched base is already incorporated

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

The gate SHALL preserve target validation, locking, version-1 JSON, publication/comment recovery and review rules. Wrong-target and not-ready refusals SHALL retain their contracts. Driver pauses SHALL expose diagnostics through status. Publish scheduling and fetch failures SHALL return retryable direct-command errors without claiming publication or producing daemon-cached refusals.

#### Scenario: Wrong publication target
- **WHEN** publish has no manifest GitHub repository or a hosted source origin mismatches it
- **THEN** the existing target refusal occurs before the new publication fetch, merge, verification, push or PR operations

#### Scenario: Driver pause blocks publication
- **WHEN** driver preparation pauses as `base_conflict` or `base_drift` and publication is then requested
- **THEN** status retains the files or receipt diagnostics, direct publish and act publish reject the non-ready state in their existing version-1 refusal formats, retain the PR identity, and perform no fetch, merge, verify or publication

### Requirement: Driver preparation handoff

If the fetched base is not incorporated or verification remains due, publish SHALL atomically save `running` with a null pause, preserving the branch, tasks, gates, PR and verification state, then return a retryable error before publication effects. The unchanged supervisor SHALL be able to schedule driver preparation. Publish SHALL NOT merge, verify or drive the run inline, including retries and recovery.

#### Scenario: Moved base schedules preparation
- **WHEN** publish begins at H and fetches a base not incorporated into H
- **THEN** it leaves H unchanged, saves running status with a null pause, and performs no merge, verify, push, PR lookup/creation, published event or transition to `human_checks`
- **THEN** direct `publish --json` exits 1 with exactly one version-1 top-level `error` document and no outcome or reasons explaining scheduled preparation and retry after readiness; plain publish exits 1 with the explanation, and `act publish` keeps its refused/detail format
- **THEN** the supervisor can schedule the run, the driver refetches and merges/verifies in `finals`, successful final checks restore ready state at M with every human check, and a later eligible publish fetches once and publishes M without merging or verifying

#### Scenario: Pending verification schedules preparation
- **WHEN** publish fetches an already incorporated base but a pending integration intent, retry flag or changed head leaves verification outstanding
- **THEN** it saves running status with a null pause and returns the retryable error without reconciling intent, moving the branch, clearing the obligation, verifying or publishing
- **THEN** only driver preparation completes recovery and verification before the run can regain readiness

#### Scenario: Handoff survives interruption
- **WHEN** intake saved an H-keyed receipt and publication is interrupted before or after saving running status, loses its handoff response, or subsequent driver preparation crashes after ref advancement before verification success is saved
- **THEN** a pre-save interruption leaves ready state for another handoff; a post-save interruption leaves schedulable running state, and driver restart recovers any verification obligation before readiness
- **THEN** intake skips running runs, later status reports actual ready head M with all checks for a new M-keyed receipt, and handoff causes no publication-refusal comment, needs-human label or delivery of the old H-keyed receipt
- **THEN** every publish invocation performs neither merge nor verify

#### Scenario: Base moves on successive attempts
- **WHEN** the base advances again after driver preparation restored readiness, or after a later push fails before publication completes
- **THEN** each eligible publish fetches once and schedules the driver if needed without merging or verifying inline, and each driver preparation refetches the latest base before integration and verification
- **THEN** a subsequent stable attempt publishes only the latest verified head with a matching newly captured receipt, without duplicate merge commits or a stale human-checks receipt

### Requirement: Recover publication comment after integration

Publication after integration SHALL preserve human-checks comment recovery using unchanged `(change, head, pr)` matching and round markers. Intake SHALL be able to capture the merged head and checks between integration and external publication. Lost responses or interrupted delivery SHALL NOT lose or duplicate the comment or require another publication solely to deliver it. Status SHALL report the actual branch head.

#### Scenario: Published response is lost
- **WHEN** intake has refreshed its receipt to M after driver preparation restored readiness and the stable publish invocation pushes M and enters `human_checks` but its response is lost
- **THEN** after restart the receipt still matches actual head M, existing intake recovers the published result and posts the complete human-checks comment exactly once without another publish invocation

#### Scenario: Comment delivery is interrupted
- **WHEN** publication of the stable merged head succeeds and its human-checks comment fails before delivery or succeeds remotely but its acknowledgement is lost
- **THEN** later polls reuse the matching merged-head receipt and round marker, deliver or reconcile exactly one comment with all checks and the hosted CI reminder, and mark delivery complete without another publish invocation

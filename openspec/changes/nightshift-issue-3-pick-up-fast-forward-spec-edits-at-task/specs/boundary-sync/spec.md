## Purpose

Import eligible human commits from the configured local source branch at specloop task boundaries without requiring a pause or losing run history.

## ADDED Requirements

### Requirement: Safe source import

Source synchronization SHALL fetch the configured local source branch into a tracking ref and advance the run branch only to a fetched commit that strictly descends from its current head. It SHALL preserve the source checkout, use the same policy for resume and driver boundaries, and record `synced` with the imported `head` only after a successful run-ref update.

#### Scenario: Resume imports a strict descendant
- **WHEN** resume synchronizes a source commit that strictly descends from the run head
- **THEN** the run branch advances to that exact commit, one `synced` event records its head and previous head, and the source branch and worktree remain unchanged

#### Scenario: Equal or older source is quiet
- **WHEN** the fetched source equals the run head or is an ancestor of it
- **THEN** synchronization does not update the run branch or emit `synced`, `sync_skipped`, or `sync_failed`, and normal execution can continue

#### Scenario: Diverged source is recorded
- **WHEN** neither fetched source nor run head is an ancestor of the other
- **THEN** synchronization records `sync_skipped` with reason `diverged` and both heads, preserves the run branch without merging, rebasing, or force-moving it, and continues without a sync-induced pause

#### Scenario: Failed source read is nonfatal
- **WHEN** source fetch fails or an ancestry query returns an error distinct from a negative ancestry result
- **THEN** synchronization records `sync_failed` with the failing stage and a nonempty diagnostic of at most 500 characters, leaves the run branch and task attempt counters unchanged, and permits normal execution without using stale tracking data or emitting `synced`

### Requirement: Boundary scheduling

The running driver SHALL synchronize after checking explicit pause requests and before reading the head and tasks used for each iteration's evidence, gate, task, or final decision. It SHALL use the resulting current head and SHALL NOT synchronize during an in-flight task's implementation, sealing, verification, review, or landing.

#### Scenario: Edits between tasks take effect
- **WHEN** a task lands and the local source gains a task edit based on that landed run head before the next boundary
- **THEN** the same driver invocation imports the edit, preserves the landed task tick, records `synced`, and selects the next task from the edited tasks without requiring pause or resume

#### Scenario: First boundary imports available edits
- **WHEN** a running driver starts with a source tip strictly descending from the run head
- **THEN** its first scheduling decision uses the imported head and tasks

#### Scenario: Completion decisions see new tasks
- **WHEN** the pre-sync task list would trigger section evidence, a phase gate, or final checks, but an eligible source edit adds unchecked work in the relevant section
- **THEN** the driver evaluates the updated task list and schedules that work before treating the section or run as complete

#### Scenario: Requested pause takes precedence
- **WHEN** a pause request is present at a task boundary with eligible source edits available
- **THEN** the driver takes the existing requested pause without fetching or synchronizing at that boundary

#### Scenario: In flight work keeps its snapshot
- **WHEN** the source branch changes while a task step is implementing, verifying, reviewing, or landing
- **THEN** boundary synchronization does not run until that step returns, and existing candidate-base checks and guarded landing behavior remain in effect

### Requirement: Atomic run advancement

Synchronization SHALL use an expected-old-head run-ref update and a captured fetched source SHA. A lost update race SHALL leave the winning head intact, record the skip, and allow scheduling from the reread head without retrying the update inside that boundary.

#### Scenario: Run head moves before update
- **WHEN** another writer advances the run ref after its head was read but before synchronization updates it
- **THEN** the guarded update cannot overwrite that writer, no `synced` event is emitted, and `sync_skipped` records reason `run_moved`, the expected head, source head, and actual head
- **THEN** the driver continues using the actual head and tasks without a sync-induced pause

#### Scenario: Source moves after fetch
- **WHEN** the source branch advances after synchronization captures its fetched tip
- **THEN** synchronization evaluates and can import only that captured commit during the current attempt, and a later boundary evaluates the newer source tip under the same ancestry rule

### Requirement: Resume compatibility

Resume SHALL retain source synchronization under the existing run lock before changing paused state to running, including deferred resume. It SHALL retain pause-specific resets, notes, and clearing of pause requests, and SHALL leave paused state intact when an unexpected run-ref write failure prevents synchronization.

#### Scenario: Deferred resume preserves existing behavior
- **WHEN** a paused run resumes with a note and deferred execution while an eligible source correction exists
- **THEN** synchronization occurs under the run lock while state is still paused, the correction is imported, existing task resets and note handling apply, the pause request is cleared, and execution is left to the driver without running inline

#### Scenario: Unexpected write failure preserves pause
- **WHEN** resume cannot update the run ref for a reason other than a moved run head
- **THEN** the error propagates through existing CLI handling without emitting `synced`, changing saved paused state, or clearing the pause request

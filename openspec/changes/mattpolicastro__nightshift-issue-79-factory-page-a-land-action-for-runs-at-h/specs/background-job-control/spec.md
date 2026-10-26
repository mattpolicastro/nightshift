## Purpose

Extend local job discovery and verified control to explicitly confirmed landing attempts while preserving every existing intake behavior.

## MODIFIED Requirements

### Requirement: Explicit stopped job release

`nightshift jobs release <id>` SHALL resolve an exact full local job ID and
atomically remove only its matching intake receipt's `job` field, or a landing attempt's own hold receipt `job` field, after
confirming the attempt is stopped. It SHALL work offline without daemon,
configuration or credentials, preserve historical evidence, and exit 0.

#### Scenario: Release each tracked stage
- **WHEN** release selects a confirmed stopped proposal, admission or revision whose valid receipt still references it
- **THEN** only that receipt's job reference is removed and the command prints `<id>: released` with exit 0 without network access, process signalling, job launch or configuration loading
- **THEN** all other receipt fields, input files, request, logs, result and stop evidence remain intact and inventory still reports the attempt as stopped

#### Scenario: Release while paused
- **WHEN** a confirmed stopped job is released while the pause file exists
- **THEN** the local receipt is detached successfully, pause remains unchanged, and no launch or delivery occurs

### Requirement: Durable command timing

New intake background requests SHALL persist stage, exact args, started_at, timeout_seconds, deadline_at and round_id before launch. round_id SHALL be the saved key for keyed revise commands and null otherwise; older five-field requests SHALL remain readable as unkeyed. Keyed request identity SHALL agree with argv and the parent snapshot. Budgets SHALL be 3600 seconds for propose, 300 for admit and 1800 for revise. UTC epoch timing SHALL survive restart without extension and SHALL remain readable without subprocess or network access. Landing requests SHALL instead retain their confirmed identity and a 2400-second reporting budget under the Landing inventory requirement. Existing intake request fields and budgets SHALL remain unchanged.

#### Scenario: Timing survives restart
- **WHEN** proposal, admission and revision jobs launch and in-memory handles are discarded before another poll
- **THEN** each request has exactly the six specified fields, including a matching round_id for keyed revise or null for unkeyed commands, the original complete args and stage, and a deadline equal to its original start plus the command budget
- **THEN** inventory preserves IDs, execution state, deterministic ordering and timing without launching commands, contacting services or rewriting records

#### Scenario: Legacy and malformed timing
- **WHEN** inventory reads a legacy request with no timing, a compacted legacy request with null timing, and a request with partial, non-finite, boolean or inconsistent timing alongside healthy records
- **THEN** legacy jobs remain readable with timing unavailable and no inferred deadline, malformed timing produces a per-record diagnostic, and healthy rows remain visible
- **THEN** unknown fields are tolerated and no request is repaired or migrated by the read

### Requirement: Delivered attempt compaction

Only durably delivered, idle and unstopped intake attempts with validated matching identity SHALL be compacted. Landing attempts SHALL always be skipped with their full evidence retained. Automatic compaction SHALL require an eligible authorized unpaused item visit. Explicit `nightshift jobs compact` SHALL instead authorize a local historical pass, including while paused, but only with the current matching delivered receipt and the same identity, locking and ownership checks. Compaction SHALL remove logs and owned idea inputs, redact idea arguments and retain a minimal request, original request mtime, result and control records. It SHALL leave workflow receipts and build-feedback files intact.

#### Scenario: Delivered artifacts are removed
- **WHEN** a valid background attempt has completed all authorized delivery operations and its matching receipt is durably marked delivered
- **THEN** compaction removes its log, its safely owned idea file and inline or file-based idea arguments, including legacy equals-form arguments
- **THEN** the directory survives with stage, remaining exact args, original timing or legacy nulls, unchanged request mtime, byte-identical result and retained process, lock and collection records
- **THEN** inventory still reads the historical attempt without errors, and workflow receipts and build-feedback JSON files remain intact

#### Scenario: Ineligible work keeps evidence
- **WHEN** a job is live, stopped, released after a stop, overdue but still pending, failed without completed delivery, paused awaiting delivery, or awaiting authorization or notification retry
- **THEN** cleanup does not remove or rewrite its input, request, log, result or control evidence
- **THEN** completion, collection, age or current label absence alone never establishes delivery

#### Scenario: Cleanup cannot cross attempt ownership
- **WHEN** compaction encounters a replaced receipt, conflicting identity, a busy lifetime lock, malformed or symlinked evidence, or an idea-file target outside the item root, shared by another attempt, a directory or a symlink
- **THEN** it leaves unproven attempts and unsafe deletion targets intact with a diagnostic, never follows a symlink or recursively deletes the directory, and preserves replacement state
- **THEN** receipt coordination follows control-lock then existing release-lock order and creates no release lock

### Requirement: Receipt aware delivery display

For intake attempts, inventory SHALL label a finished job `delivered` only when its readable item receipt has `delivered` equal to JSON boolean true; otherwise it SHALL label a finished job `finished (delivery pending)`. This item-level display SHALL NOT establish attempt-level compaction authority or change execution state. Other execution states SHALL retain their labels. Landing attempts SHALL use no item-level delivery receipt; finished landing attempts SHALL remain finished (delivery pending), including after status reads.

#### Scenario: Finished undelivered job reads pending
- **WHEN** a finished attempt has a missing receipt or a receipt with an absent or false delivered field, including completion while paused
- **THEN** both listing modes show `finished (delivery pending)` and the default retains the row without treating a missing legacy receipt as an error
- **THEN** result, collection, age and cleanup evidence alone do not mark delivery complete

#### Scenario: Delivered item history reads delivered
- **WHEN** finished attempts share a readable item receipt whose delivered field is true, including a receipt without job identity or pointing at a later attempt
- **THEN** `--all` labels those finished rows `delivered` and default inventory hides them unless they have diagnostics
- **THEN** non-finished execution states retain their labels and the display grants no deletion permission for unmatched attempts

#### Scenario: Damaged receipt stays visible
- **WHEN** an item receipt is malformed, unreadable, symlinked, not an object, or declares a non-boolean delivered value
- **THEN** its jobs remain visible, finished rows read `finished (delivery pending)`, a diagnostic identifies the receipt, and inventory exits 1 while healthy items remain available
- **THEN** no receipt symlink is followed and no record is repaired, created or rewritten

## ADDED Requirements

### Requirement: Landing inventory

Local inventory SHALL include landing attempts with their identity, execution state, timing and log location, preserving full-history and default filtering rules. Landing deadlines SHALL be display-only and status SHALL never acknowledge delivery or compact evidence.

#### Scenario: Discover landing alongside intake
- **WHEN** jobs, jobs --all or daemon status reads mixed intake and landing state without in-memory handles
- **THEN** landing appears with stable ID, repository/PR, command, process identity and persisted start plus 2400-second reporting deadline, and existing intake ordering, diagnostics and default/all behavior are preserved
- **THEN** an overdue landing is displayed without daemon escalation, automatic retry or termination, and an unrelated intake receipt cannot mark it delivered
- **THEN** local jobs reads require no valid config, credentials or network and do not mutate evidence

#### Scenario: Compaction preserves landing evidence
- **WHEN** explicit jobs compact encounters terminal landing attempts alongside eligible delivered intake jobs, or intake cleanup runs
- **THEN** landing requests, logs, results, hold receipts and control records remain byte-identical, with explicit compaction reporting a landing-retention skip
- **THEN** intake compaction behavior and its existing eligibility, authorization and identity tests remain unchanged

### Requirement: Landing process ownership

Landing stop SHALL use a dedicated wrapper-command verifier and escalation adapter while retaining existing process-group identity, cancellation and termination guarantees. The intake verifier SHALL NOT accept landing wrapper commands.

#### Scenario: Stop verifies each job kind
- **WHEN** executable fixtures stop real intake and landing wrapper groups with descendants, including a TERM-resistant member
- **THEN** each adapter checks its own module command and exact directory, start identity and session, applies the existing 5-second TERM and 1-second KILL bounds, and confirms stopped only after execution ends
- **THEN** unrelated processes and the caller survive; wrong wrapper kinds, altered paths, reused identities and unavailable ownership refuse without unsafe signals
- **THEN** escalation rechecks the correct wrapper or previously witnessed surviving group members instead of calling the intake-only verifier for landing

#### Scenario: Cancellation wins or preserves completion
- **WHEN** durable stop intent races with landing progress or result persistence
- **THEN** stop-first remains cancelled despite a late success, while completion-first reports already finished without signalling or replacing its outcome
- **THEN** repeated confirmed stop is safe, and failed signalling or uncertain termination retains stopping state and its diagnostic rather than claiming stopped

### Requirement: Landing hold release

Offline release SHALL remove only a confirmed stopped landing attempt's matching hold reference after verifying idle lifetime, consistent identity and regular receipt evidence. It SHALL preserve cancellation and all evidence, serialize receipt changes, and never schedule a successor.

#### Scenario: Release landing without intake state
- **WHEN** release selects a confirmed stopped landing while paused and without config, credentials or an intake receipt
- **THEN** it atomically removes only that attempt's hold job field with exit 0, preserves pause and every other evidence field, and launches or delivers nothing
- **THEN** repeating release after restart writes nothing, and repeating an old release cannot detach a successor's hold
- **THEN** a fresh eligible Factory confirmation and new UUID are required for another landing; daemon polling cannot supply them

#### Scenario: Unsafe or racing release preserves holds
- **WHEN** release encounters active, stopping, finished, failed or uncertain work, inconsistent stop identity, busy lifetime, symlinked or malformed receipt, save failure or a concurrent receipt update
- **THEN** it refuses without clearing the hold or evidence, preserves a newer receipt and returns a diagnostic
- **THEN** control-lock then receipt-release-lock coordination and under-lock rereads prevent stale writers from restoring a detached hold, with no release lock created for an unvalidated refusal


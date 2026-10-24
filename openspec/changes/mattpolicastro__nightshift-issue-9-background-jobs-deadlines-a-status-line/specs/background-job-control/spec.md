## Purpose

Add durable timing, active-work visibility and delivered-attempt compaction while preserving local inventory, owned process-group stop, explicit release and historical evidence for undelivered work.

## ADDED Requirements

### Requirement: Durable command timing

New background requests SHALL persist stage, exact args, started_at, timeout_seconds and deadline_at before launch. Budgets SHALL be 3600 seconds for propose, 300 for admit and 1800 for revise. UTC epoch timing SHALL survive restart without extension and SHALL remain readable without subprocess or network access.

#### Scenario: Timing survives restart
- **WHEN** proposal, admission and revision jobs launch and in-memory handles are discarded before another poll
- **THEN** each request has exactly the five specified fields, the original complete args and stage, and a deadline equal to its original start plus the command budget
- **THEN** inventory preserves IDs, execution state, deterministic ordering and timing without launching commands, contacting services or rewriting records

#### Scenario: Legacy and malformed timing
- **WHEN** inventory reads a legacy request with no timing, a compacted legacy request with null timing, and a request with partial, non-finite, boolean or inconsistent timing alongside healthy records
- **THEN** legacy jobs remain readable with timing unavailable and no inferred deadline, malformed timing produces a per-record diagnostic, and healthy rows remain visible
- **THEN** unknown fields are tolerated and no request is repaired or migrated by the read

### Requirement: Active jobs in status

`nightshift status` SHALL read local job inventory once and display active attempts before repository network queries, including elapsed and deadline state when available. It SHALL preserve existing status output and exit behavior, omit full argv, and perform no job mutation, escalation or cleanup.

#### Scenario: Active attempts are visible
- **WHEN** explicit or default status runs with starting, running, stopping and historical jobs, including a job from a repository absent from current configuration
- **THEN** active rows appear in inventory order with full ID, stage, repository/item, command name and execution state, while finished, failed and stopped history remains available through jobs
- **THEN** timed rows display nonnegative elapsed seconds and remaining seconds or OVERDUE by seconds at or after deadline, and unavailable timing is explicit without revealing idea text

#### Scenario: Status preserves compatibility
- **WHEN** existing status and other commands run with empty job inventory
- **THEN** default and explicit status each perform one inventory read without an extra empty-jobs line, preserving liveness, alerts, worker output, configuration loading, options and exit codes
- **THEN** unrelated commands do not acquire an inventory read or a job-control side effect

#### Scenario: Status tolerates damaged inventory
- **WHEN** status encounters unreadable job records alongside a healthy active job
- **THEN** it displays diagnostics and the healthy row, continues existing status processing and retains the existing status exit behavior
- **THEN** persisted files remain unchanged, while the jobs command retains its own diagnostic exit policy

### Requirement: Delivered attempt compaction

Only durably delivered, idle and unstopped attempts with validated matching identity SHALL be compacted during an eligible authorized unpaused item visit. Compaction SHALL remove logs and owned idea inputs, redact idea arguments and retain a minimal request, original request mtime, result and control records. It SHALL leave workflow receipts and build-feedback files intact.

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

### Requirement: Recoverable cleanup

Compaction SHALL journal delivery evidence and original request timestamps before destructive changes and SHALL be idempotent after interruption. Recovery SHALL run only during an eligible authorized unpaused visit to that item, without redelivering results or mutating a replacement receipt. Receipts and journals SHALL NOT substitute for current authorization.

#### Scenario: Interruption resumes safely
- **WHEN** cleanup fails or the daemon restarts after journal creation, request replacement, input deletion or before recording completion
- **THEN** a later eligible authorized unpaused visit resumes the same attempt from its journal, tolerates already-removed files and preserves the original request mtime and result
- **THEN** it neither relaunches work nor repeats remote delivery, and filesystem cleanup failures remain local diagnostics rather than item escalation

#### Scenario: Unarmed cleanup waits for authorized eligibility
- **WHEN** a matching delivered receipt was saved but the daemon exited before creating its cleanup journal, and the item is no longer armed
- **THEN** subsequent unarmed polls leave its receipt, request, result, input, log and file set unchanged rather than running an independent cleanup scan
- **THEN** a later trusted reapplication permits journal creation and cleanup only after normal eligibility checks pass, without replaying the older attempt's delivery as a cleanup side effect
- **THEN** the same deferral applies to existing incomplete journals, closed items and blocked new-draft polls; unproven orphan jobs and legacy delivered receipts without a job identity remain retained

#### Scenario: Authorization denial preserves interrupted cleanup
- **WHEN** delivery succeeded but cleanup was interrupted, and the next poll observes a newly untrusted trigger or cannot resolve current authorization, including after restart
- **THEN** it performs no cleanup transaction or journal preparation and preserves every existing receipt, job artifact and request mtime exactly as found, whether the journal is absent or incomplete or the receipt has been replaced
- **THEN** normal rejection or lookup-failure handling remains unchanged, healthy eligible neighbors continue, and repeated denied polls neither resume cleanup nor create cleanup or release locks
- **THEN** after trusted reapplication and normal eligibility checks, cleanup may resume for validated delivered attempts; an old journal never authorizes processing of an untrusted current trigger

#### Scenario: Replacement retains cleanup authority
- **WHEN** an eligible authorized unpaused new round would replace a delivered job-bearing receipt before compaction finishes
- **THEN** intake ensures the older attempt's cleanup journal is durable before replacing the receipt, deferring replacement if journal creation fails
- **THEN** later cleanup of the older attempt requires current item authorization and eligibility as well as that journal, and leaves the newer receipt, request, result and input untouched
- **THEN** paused polls withhold cleanup, status remains read-only, and absent item state is never created merely to discover cleanup candidates

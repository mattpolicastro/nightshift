## Purpose

Make overdue background work actionable and delivered artifacts compactable without changing authorization, pause, approval recovery, revision consumption or retry behavior; bound daemon git commands by process group.

## ADDED Requirements

### Requirement: Overdue job escalation

An eligible live background attempt at or beyond its persisted deadline SHALL immediately use the existing human-intervention reporting path. Escalation SHALL retain the job reference and trigger for recovery without automatic termination or replacement. Pause, authorization, stop intent and normal result collection SHALL retain precedence.

#### Scenario: Hung job escalates after deadline
- **WHEN** an authorized eligible proposal, admission or revision remains live at its deadline on an unpaused poll
- **THEN** the daemon reports its ID, command, elapsed time, deadline budget, log location and stop/inspect/release guidance, and adds needs-human without waiting for the ordinary failure threshold
- **THEN** the same job and request remain attached, no replacement or termination occurs, and other items continue processing
- **THEN** an earlier poll before deadline reports pending without failure escalation

#### Scenario: Reporting retries retain the attempt
- **WHEN** overdue comment delivery or its subsequent needs-human label write fails, including across daemon restart
- **THEN** a later eligible poll retries reporting from the same persisted deadline and job reference, without extending the deadline or launching work
- **THEN** the existing error marker reconciles the comment and the label is attempted only after comment delivery succeeds

#### Scenario: Control and completion take precedence
- **WHEN** an expired job is paused, unauthorized, intentionally stopping or stopped, or has already finished and released its lifetime lock
- **THEN** pause or authorization denial withholds collection/reporting and leaves evidence unchanged, while stopped work remains held without age-based failure
- **THEN** an unstopped finished result uses normal collection even after its deadline, and an idle job lacking a result uses existing crash handling
- **THEN** an eligible live job is checked against its original deadline after resume or restored authorization, and a late completed result remains eligible after normal human-hold resolution

### Requirement: Failure marker reconciliation

The production GitHub retry path SHALL reconcile draft-error, admit-error, revise-error, publish-error and approval comment markers before retrying an ambiguous create. It SHALL retain existing completion-marker behavior, all-page lookup and pause guards, and SHALL NOT blindly POST after a failed reconciliation read.

#### Scenario: Error comment retry does not duplicate
- **WHEN** report_failure posts a stage-error comment which is stored remotely before HTTP 502 is returned to queue._gh
- **THEN** the real retry loop finds the marker even on a later comment page and updates that comment ID without a second POST
- **THEN** the same guarantee holds for approval markers and existing draft, admit, publish and revise markers, including round-qualified forms and distinct-marker separation

#### Scenario: Reconciliation failure prevents blind retry
- **WHEN** a marked comment POST has an ambiguous result and the retry's comment lookup fails or a pause guard fires before its next operation
- **THEN** no second POST occurs, the failure or pause propagates through existing handling, and a later eligible retry can reconcile the original comment
- **THEN** only marker recognition changes in queue.py; unmarked worker comments and the existing retry/backoff policy retain their behavior

### Requirement: Delivery identity for cleanup

Proposal, admission and both revision delivery paths SHALL persist completed delivery before invoking a shared compaction boundary. They SHALL carry the exact matching job identity through receipt reconstruction and retain existing authorization, head binding, feedback consumption and remote idempotency guarantees.

#### Scenario: Proposal and admission retain matching identity
- **WHEN** a background proposal or admission delivers a successful or human-input outcome, including recovery through an existing PR or committed admission
- **THEN** only the matching validated attempt is marked delivered and compacted after every required comment and label operation succeeds
- **THEN** admission preserves the matching job and original delivery marker through receipt reconstruction, while a different approval, colliding run or unrelated PR cannot authorize cleanup of an old attempt
- **THEN** recovery without matching local job evidence and synchronous injected runners without a job ID do no compaction

#### Scenario: Revision delivery preserves feedback history
- **WHEN** a spec or build revision delivers a success or human-input result, including a retry after successful execution or spec push
- **THEN** the matching attempt is compacted only after durable completed delivery, while saved feedback, consumed IDs, unknown receipt fields, result, round marker and build-feedback files remain intact
- **THEN** successful work consumes only its saved IDs at the existing pre-notification boundary, failed work consumes nothing, later comments remain eligible and a newer trigger application is not removed by old delivery

#### Scenario: Denied or incomplete delivery retains artifacts
- **WHEN** authorization is denied before collection, pause interrupts delivery, or a comment or label operation fails
- **THEN** no compaction occurs and the job's original request and input remain available across restart for normal authorized recovery
- **THEN** authorization-denial filesystem snapshots remain byte-for-byte unchanged; after trusted completed delivery the request retains minimal identity and timing with its original mtime and its owned idea file is absent

### Requirement: Git process group timeout

Daemon intake git commands SHALL run in a new session with the existing 30-second per-command timeout. On timeout the runner SHALL kill the process group, reap its direct child with bounded output draining, and retain the existing retryable error behavior, pause checks and explicit runner injection.

#### Scenario: Git timeout kills process group
- **WHEN** a daemon git command stalls with its transport descendant in the same group until the timeout
- **THEN** SIGKILL targets that group, the git process and descendant cease executing, the direct child is reaped, and an unrelated group remains alive
- **THEN** timeout handling allows at most one additional second for draining, reports a retryable timeout and retains receipts and arming labels for remote reconciliation
- **THEN** normal exit and nonzero exit retain stdout, stderr, return code and check semantics; signalling failure is diagnosed rather than claimed as successful termination

#### Scenario: Production runner preserves daemon progress
- **WHEN** poll_specloop dispatches git or a git timeout interrupts delivery
- **THEN** the production guarded runner uses the process-group implementation, while explicit injected git runners remain usable and pause is checked immediately before dispatch
- **THEN** other items, worker claims, heartbeat and harvest continue under existing scheduling and timeout behavior; retry reuses saved results without duplicate delivery
- **THEN** GitHub, worker git, synchronous specloop publication and status runner contracts remain unchanged

## MODIFIED Requirements

### Requirement: Durable drafting payload

Nightshift SHALL finish writing and close each distinct idea file before dispatch and retain it unchanged across polling and restart until its background attempt is durably delivered. Delivered background idea files MAY then be removed by validated compaction. Undelivered and stopped inputs SHALL remain available. Preparation failure SHALL prevent dispatch without falling back to argv.

#### Scenario: Delayed background read
- **WHEN** a drafting job reads its idea after the initiating poll returns and daemon in-memory job tracking is reset, with a relative state directory containing spaces
- **THEN** its absolute file path still resolves to the complete original idea
- **THEN** intervening polls reuse the existing job and completed delivery occurs once, after which validated compaction may remove that input

#### Scenario: Later round preserves earlier input
- **WHEN** an issue is re-armed with an answer or a spec PR begins another feedback round after its previous draft completes
- **THEN** the new drafting invocation receives a different file containing the new complete idea
- **THEN** the earlier invocation's file remains present and unchanged unless its background attempt has been durably delivered and validated for compaction; stopped, undelivered and synchronous-injection inputs remain unchanged

#### Scenario: File preparation fails
- **WHEN** creating the payload directory or writing the idea raises a filesystem error
- **THEN** no drafting runner or background launcher is invoked for that attempt and no successful-draft delivery occurs
- **THEN** the error follows existing intake failure handling without retrying with --idea

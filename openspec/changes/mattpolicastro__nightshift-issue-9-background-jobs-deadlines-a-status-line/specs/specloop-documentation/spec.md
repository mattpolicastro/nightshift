## Purpose

Keep operator descriptions of background deadlines, status, cleanup and git timeouts consistent with implemented behavior and remaining operational limits.

## ADDED Requirements

### Requirement: Deadline guidance

The root README and specloop guide SHALL document per-command reporting deadlines and the operator recovery path, distinguishing deadline escalation from process termination.

#### Scenario: Operator handles overdue work
- **WHEN** a reader consults background-job guidance
- **THEN** it states propose 3600 seconds, admit 300 seconds and revise 1800 seconds, persistence across restart, and reporting on the next authorized eligible unpaused poll
- **THEN** it explains needs-human, retained job identity, stop/inspection/release recovery, late-result recovery and delays when the daemon or GitHub is unavailable, without promising automatic termination or retry

### Requirement: Active job status guidance

The root README and specloop guide SHALL describe active background rows in nightshift status consistently with local job inventory.

#### Scenario: Operator reads job status
- **WHEN** a reader follows status instructions
- **THEN** the docs explain job ID, stage, repository/item, elapsed and remaining or overdue time, legacy timing unavailability, and historical inspection through nightshift jobs
- **THEN** they preserve the distinction between Nightshift job status, worker liveness and independent specloop run status

### Requirement: Delivered retention guidance

All three scoped operator documents SHALL distinguish delivered-attempt compaction from retention of pending and stopped evidence. Their drafting-input and job-control passages SHALL describe consistent input lifetimes.

#### Scenario: Operator understands retained artifacts
- **WHEN** the root README, KNOWN-LIMITATIONS.md and docs/specloop/README.md describe job storage
- **THEN** they explain removal of delivered logs and owned idea payloads, redacted minimal requests, retained results/control metadata and restored request mtimes for touchpoint history
- **THEN** the guide's drafting-input paragraph qualifies retention by delivery, and its job-control paragraph no longer claims all artifacts remain without automatic cleanup
- **THEN** they preserve stopped/released and undelivered evidence retention, workflow-receipt and build-feedback retention, remaining storage growth and missing-history limitations without promising secure erasure

### Requirement: Git timeout guidance

The scoped operator documents SHALL describe process-group termination for timed-out daemon git commands and retain unresolved remote-effect, platform and supervisor limits.

#### Scenario: Operator understands timeout limits
- **WHEN** the Background jobs limitations and corresponding guide passages are updated
- **THEN** they replace the direct-child-only claim with the 30-second daemon git timeout and group kill, and remove only demonstrated fixed deadline/retention claims
- **THEN** they retain ambiguous remote acceptance, escaped descendants, committed-admission independence, stopped-job recovery and revision commit/result crash caveats, citing issue #9 or a verified implementing PR
- **THEN** real stalled-origin validation is identified as a pending human check unless performed, rather than inferred from CI

## MODIFIED Requirements

### Requirement: Documentation integrity

Documentation-only refreshes SHALL stay within scoped docs and leave runtime code/tests unchanged. Runtime changes MAY update matching docs and tests within explicit scope, including justified schema, fixture or assertion migrations. All edits SHALL preserve unaffected requirements and assertions, use concise present-tense prose and verified references, and require green uv run pytest plus the applicable design's human checklist.

#### Scenario: Scope and regression checks remain intact
- **WHEN** the implementation diff and local validation are reviewed
- **THEN** documentation-only refreshes change only allowed docs, with root README edits limited to specloop/front-door passages, and leave code and tests unchanged; runtime changes stay within their declared runtime, test and documentation scope
- **THEN** only explicitly justified runtime-contract migrations may replace affected assertions, preserving equivalent behavioral coverage, and every unaffected assertion remains intact
- **THEN** this change explicitly migrates the exact request schema, git runner doubles, status inventory seam and trusted post-delivery request/input assertions while preserving authorization-denial snapshots, exact args, offline inventory and all scheduling/delivery assertions
- **THEN** relative documentation links resolve and uv run pytest passes without weakening preserved behavior

#### Scenario: References and reader checks are reviewable
- **WHEN** the documentation is prepared for merge
- **THEN** changed behavior has adjacent verified PR citations when an implementation PR identity is known, or its verified tracking issue until then, issue and PR numbers are distinguished, and no pending work is described as shipped
- **THEN** the PR carries the applicable design's human checklist for live reference verification and a readability pass, retaining ledger verification and Matt's single readability pass for documentation refreshes, without assigning these checks as implementing-agent tasks

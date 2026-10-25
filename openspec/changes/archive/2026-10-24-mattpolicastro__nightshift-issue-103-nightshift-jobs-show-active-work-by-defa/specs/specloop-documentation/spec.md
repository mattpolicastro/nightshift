## Purpose

Keep operator guidance consistent with active-first job inventory and explicit historical cleanup while preserving daemon, evidence-retention and recovery boundaries.

## ADDED Requirements

### Requirement: Job inventory view guidance

`README.md`, `KNOWN-LIMITATIONS.md` and `docs/specloop/README.md` SHALL describe default inventory, `--all`, receipt-based delivery wording and `jobs compact` consistently wherever this change invalidates existing claims. Guidance SHALL cite issue #103 or its verified implementation PR, preserve unaffected assertions, and distinguish deterministic fixture validation from the pending real-tree human check.

#### Scenario: Operator selects a view and cleanup
- **WHEN** an operator reads the updated usage, job-control and retention passages
- **THEN** they can identify default active/stopped/pending and diagnostic rows, hidden-history counts, full history including failed jobs via `--all`, and the item-receipt basis for `delivered` versus `finished (delivery pending)`
- **THEN** they can run the explicit one-pass compact command, interpret compacted/skipped counts and retained-target warnings, and understand that missing or replaced attempt identity prevents deletion even when the display says delivered
- **THEN** the docs explain pausing and comparing full inventory before/after on the real tree as a human validation step, without claiming CI performed it or changing pause/stop/release and supervisor boundaries

## MODIFIED Requirements

### Requirement: Active job status guidance

The root README and specloop guide SHALL describe active background rows in nightshift status consistently with local job inventory.

#### Scenario: Operator reads job status
- **WHEN** a reader follows status instructions
- **THEN** the docs explain job ID, stage, repository/item, elapsed and remaining or overdue time, legacy timing unavailability, and historical inspection through `nightshift jobs --all`
- **THEN** they preserve the distinction between Nightshift job status, worker liveness and independent specloop run status

### Requirement: Delivered retention guidance

All three scoped operator documents SHALL distinguish delivered-attempt compaction from retention of pending and stopped evidence. Their drafting-input and job-control passages SHALL describe consistent input lifetimes and the explicit `nightshift jobs compact` exception to automatic cleanup eligibility.

#### Scenario: Operator understands retained artifacts
- **WHEN** the root README, KNOWN-LIMITATIONS.md and docs/specloop/README.md describe job storage
- **THEN** they explain removal of delivered logs and owned idea payloads, redacted minimal requests, retained results/control metadata and restored request mtimes for touchpoint history
- **THEN** the guide's drafting-input paragraph qualifies retention by delivery, and its job-control paragraph no longer claims all artifacts remain without automatic cleanup
- **THEN** all three documents explain that automatic interrupted cleanup waits for a later eligible authorized unpaused visit, so unarmed, closed or denied items can retain residual artifacts indefinitely; they do not recommend rearming solely for cleanup because normal new work may follow
- **THEN** they describe explicit offline compaction while paused for already-delivered history whose current receipt matches, with identity/ownership skips and repeat safety; missing identity or a replacement receipt still prevents that manual cleanup, even if an old journal exists
- **THEN** they preserve stopped/released and undelivered evidence retention, workflow-receipt and build-feedback retention, remaining storage growth and missing-history limitations without promising secure erasure

## Purpose

Return explicitly released items to ordinary intake without weakening
eligibility, filesystem preservation or recovery guarantees.

## ADDED Requirements

### Requirement: Released item intake

Intake SHALL treat an explicitly released receipt as having no running job.
Subsequent work SHALL follow all existing trigger, authorization, pause,
dependency, health and recovery rules. Release SHALL NOT itself dispatch,
deliver a stopped result or require a new label application.

#### Scenario: Armed released issue starts once
- **WHEN** a stopped draft is released and its open issue remains armed, trusted, unblocked and otherwise eligible across polling and restart
- **THEN** a subsequent poll starts one new attempt with a new job ID and distinct idea input, and further polls reuse that attempt
- **THEN** the old attempt remains stopped and its result is never delivered

#### Scenario: Ineligible released item waits
- **WHEN** a released issue or PR is unarmed, closed, unauthorized, needs human input, paused, or subject to an applicable dependency or repository-health hold
- **THEN** no replacement starts until normal eligibility permits it, with existing trigger, rejection, health and pause behavior preserved
- **THEN** open or unreadable issue blockers continue to defer new drafts

#### Scenario: Recovery precedes replacement
- **WHEN** a released item already has an open draft PR under its recorded identity or a committed admission for its PR
- **THEN** normal recovery reuses that work without duplicate drafting, admission or PR creation, preserving legacy identities and approval-head checks
- **THEN** release neither cancels nor rolls back an admitted run

### Requirement: Polling filesystem preservation

Ordinary intake SHALL NOT create release-lock files or directories as a side
effect of reading receipts. Blocked drafting polls SHALL preserve existing
files byte for byte, and open-PR recovery without a receipt SHALL NOT create
the proposals directory.

#### Scenario: Blocked polls leave files unchanged
- **WHEN** a new drafting round remains blocked across polls, with no receipt, an old receipt, or an explicitly released receipt
- **THEN** the complete file set and file contents remain unchanged and no release lock, proposal receipt or job is created

#### Scenario: Existing PR recovery creates no directory
- **WHEN** an armed issue has an existing open draft PR and no local proposal receipt or proposals directory
- **THEN** normal recovery completes without creating the proposals directory or a release lock, including while an issue blocker is open

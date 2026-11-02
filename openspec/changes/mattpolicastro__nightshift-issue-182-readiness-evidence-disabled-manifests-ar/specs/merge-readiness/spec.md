## Purpose

Honor each published run's trusted evidence setting while preserving the existing advisory readiness conditions and enabled-evidence safeguards.

## ADDED Requirements

### Requirement: Disabled evidence exemption

After existing run-state and manifest identity validation succeeds, readiness SHALL return no evidence reasons when the saved run manifest has `evidence = false`, including the existing omitted-setting default. It SHALL return before reading committed tasks, evidence events or bounded section diffs. It SHALL preserve existing state/manifest read and validation errors, and SHALL leave head, CI, revision and human-check conditions unchanged.

#### Scenario: Disabled manifest needs no evidence
- **WHEN** run-state and manifest validation succeeds and the saved manifest disables evidence, with no saved section completion or bounded diff proof and empty, absent or malformed events
- **THEN** `_readiness_evidence` returns `[]` without reading committed tasks, events or diffs
- **THEN** readiness still depends on its existing head, CI, revision and human-check conditions

## MODIFIED Requirements

### Requirement: Local evidence assessment

When the saved run manifest enables evidence, readiness SHALL enumerate current task sections from local run state and committed tasks, select each section's latest evidence event in append order, and require a measured pass with zero timeouts and saved gate completion. It SHALL distinguish the documented no-mutable-lines skip and positively proven test-only diffs from missing evidence, and SHALL NOT parse per-attempt receipts or alter evidence scoring. Existing failure reasons SHALL remain unchanged. The Disabled evidence exemption SHALL govern manifests that disable evidence. The following scenarios apply when evidence is enabled.

#### Scenario: Measured sections pass
- **WHEN** every expected section has a latest passing event with a valid numeric score, zero timeouts, no uncovered scenarios, and saved evidence completion
- **THEN** evidence permits readiness, including when older attempts failed or timed out

#### Scenario: Unmeasured evidence blocks
- **WHEN** a section's latest evidence is unmeasured, failed, uncovered, or has a null score without an allowed skip
- **THEN** readiness blocks and identifies that section and its failed measurement condition
- **THEN** a test-only path classification does not override the recorded failure

#### Scenario: Any timeout blocks
- **WHEN** a latest event reports a positive timeout count even though its pass flag and score otherwise qualify
- **THEN** readiness blocks with the section and timeout count

#### Scenario: No mutable lines need no measurement
- **WHEN** a section's latest passing event has a null score, zero timeouts, saved completion, and the exact saved no-mutable-lines summary
- **THEN** readiness treats that section as measured-not-required without looking for execution receipts

#### Scenario: Test only missing event has positive proof
- **WHEN** a section has no evidence event and a valid bounded section diff contains test paths only, apart from its harness-owned tasks file
- **THEN** readiness accepts the test-only exception
- **THEN** missing boundaries, tasks-only diffs, production paths including integrated changes, and cross-boundary renames do not qualify

#### Scenario: Unknown evidence blocks
- **WHEN** required local inputs are missing, malformed, inconsistent with run identity, or omit a required section or timeout count without the test-only exception
- **THEN** readiness blocks with actionable evidence reasons rather than treating missing data as success

#### Scenario: Enabled manifest still requires events
- **WHEN** the saved manifest enables evidence and expected sections have no evidence events and no bounded test-only diff proof
- **THEN** each such section retains the exact reason `evidence section N: missing event; no bounded test-only diff proof`

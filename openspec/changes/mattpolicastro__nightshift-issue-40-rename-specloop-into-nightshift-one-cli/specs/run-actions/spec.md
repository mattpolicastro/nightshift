## Purpose

Keep human and machine action commands canonical without changing action behavior.

## ADDED Requirements

### Requirement: Canonical command hints

New run actions and notifications SHALL name canonical nightshift commands while preserving pause semantics, action availability and output schemas.

#### Scenario: Messages and status agree
- **WHEN** each pause kind produces an action and human notification, including recovery and publish hints
- **THEN** command text names the same parseable nightshift build operation, with unchanged allowed verbs, notes, reasons and machine-readable structure


## Purpose

Keep human and machine action commands canonical without changing action behavior.

## ADDED Requirements

### Requirement: Canonical command hints

New run actions and notifications SHALL name canonical nightshift commands while preserving pause semantics, action availability and output schemas.

#### Scenario: Messages and status agree
- **WHEN** each pause kind produces an action and human notification, including recovery and publish hints
- **THEN** command text names the same parseable nightshift build operation, with unchanged allowed verbs, notes, reasons and machine-readable structure
- **THEN** task 3.2 may update the expected command string in tests/test_specloop_status_json.py:132 and other expected action/hint command literals changed by this task to the nightshift spelling; the full-document equality assertion and every other field remain exactly as they are
- **THEN** this command-literal exception belongs only to task 3.2; task 1.2's help/usage-spelling rule remains unchanged

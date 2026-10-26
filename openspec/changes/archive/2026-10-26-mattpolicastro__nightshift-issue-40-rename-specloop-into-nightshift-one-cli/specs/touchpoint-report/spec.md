## Purpose

Read migration-compatible local run history without altering attribution.

## MODIFIED Requirements

### Requirement: Local report entry point

The system SHALL expose `python -m nightshift.touchpoints` using only local
records under `~/.nightshift/{proposals,admissions,revisions}` and
resolver-discovered `~/.nightshift/runs` with unmigrated `~/.specloop/runs` fallback and compatibility-link deduplication. It SHALL perform no state writes, subprocess commands,
credential reads or network calls. Missing roots SHALL represent empty history.

#### Scenario: Offline read leaves state unchanged
- **WHEN** the module reports fixture receipts and events with external calls forbidden
- **THEN** it succeeds without accessing GitHub or a host service, and source files and directory contents remain unchanged

#### Scenario: Empty local history
- **WHEN** the local roots are absent or empty
- **THEN** the report contains no changes, all scalar totals equal zero, reason maps and errors are empty, and the command exits 0 without creating directories


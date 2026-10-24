## Purpose

Preserve observed touchpoint history and draft-date filtering when delivered job producers remove heavy artifacts.

## ADDED Requirements

### Requirement: Compaction preserves history

Delivered-attempt compaction SHALL preserve the existing report's identities, round counts, deduplication and date selection through retained requests/results and original request mtimes. It SHALL require no change to reporting code, output schema or offline read-only behavior.

#### Scenario: Cleanup preserves touchpoint history
- **WHEN** multiple proposal question rounds, spec revisions, build revisions and refused admissions are reported before compaction, after compaction, and after current receipts are replaced by later rounds
- **THEN** each historical round retains its original attribution and count, current-result and build-event duplicates are still collapsed, and new rounds add only their normal observations
- **THEN** request mtimes remain unchanged, including across cleanup interruption/restart, and --since boundaries before, exactly at and after the original proposal date select the same historical changes
- **THEN** offline reporting succeeds without new cleanup-induced diagnostics, state writes, subprocesses or network calls

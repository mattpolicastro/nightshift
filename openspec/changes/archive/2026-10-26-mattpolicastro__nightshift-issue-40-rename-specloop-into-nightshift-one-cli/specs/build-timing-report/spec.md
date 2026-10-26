## Purpose

Read canonical and legacy run history without duplicate accounting or writes.

## MODIFIED Requirements

### Requirement: Read only collection

The report SHALL read local run `events.jsonl` files with an overridable runs
root, defaulting to resolver discovery in `~/.nightshift/runs` with unmigrated `~/.specloop/runs` fallback and compatibility-link deduplication, without writing run state or requiring
external services. It SHALL skip malformed lines and skip an unreadable run
with one diagnostic line, continuing with readable runs.

#### Scenario: Readable history survives bad input
- **WHEN** temporary runs contain valid events around malformed lines and one run raises a file-read error
- **THEN** valid events contribute, the unreadable run is omitted with one path-bearing stderr diagnostic, the command completes with exit 0, and source files remain unchanged

#### Scenario: Empty history
- **WHEN** the runs root is absent or empty
- **THEN** changes, profiles and errors are empty, all six phases have zero count and total with null median, and no directory is created


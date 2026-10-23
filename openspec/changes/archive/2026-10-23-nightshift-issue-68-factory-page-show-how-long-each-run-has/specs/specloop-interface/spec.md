## Purpose

Expose the latest pause event time through read-only version-1 JSON status so
display clients can calculate elapsed waits.

## ADDED Requirements

### Requirement: Pause event timestamp

Named and all-run `specloop status --json` responses SHALL add `since` to every
non-null pause, including synthetic error pauses. The value SHALL be the finite
numeric epoch-seconds `at` of the last recognizable `pause` event in the run's
`events.jsonl` append order, or null when unavailable. Status SHALL retain
version 1, existing values, null pauses, error isolation, and read-only behavior.

#### Scenario: Latest pause event reported
- **WHEN** named or all-run JSON status reads a run with a non-null pause and multiple pause events interleaved with other events
- **THEN** `pause.since` equals the last appended pause event's numeric `at`, preserving fractional seconds and zero, even if an earlier event has a larger timestamp
- **THEN** later non-pause events and the five-event context limit do not change the selected timestamp

#### Scenario: Unavailable pause time is null
- **WHEN** JSON status reads a non-null pause whose event log is missing, empty, unreadable, undecodable, or contains no recognizable pause event
- **THEN** that pause includes `since: null` and the absence of a usable event log does not itself turn a healthy run into an error row
- **WHEN** the latest recognizable pause lacks a finite numeric `at`, including a boolean or string value
- **THEN** `since` is null rather than the time of an older pause

#### Scenario: Damaged lines do not hide a pause
- **WHEN** a readable event log contains a valid pause event followed by malformed JSON, a partial trailing line, or non-object records
- **THEN** JSON status skips those unrecognizable records and reports the valid pause timestamp without writing or repairing the log

#### Scenario: Timestamp preserves status compatibility
- **WHEN** named and all-run JSON status include healthy paused runs, runs with null pauses, and a run with a core read failure
- **THEN** each non-null pause includes `since`, including the synthetic error pause using its run's readable latest pause timestamp or null
- **THEN** null pauses remain null, the envelope stays version 1, and existing field values, ordering, exit codes, error context, action availability, and per-run failure isolation remain unchanged
- **THEN** persisted state, event files, refs, and in-memory task state remain unchanged, and non-JSON status output is unaffected

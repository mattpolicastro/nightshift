## Purpose

Keep the version-1 specloop status interface usable across repositories when an individual run cannot be read, including after an interrupted finish.

## ADDED Requirements

### Requirement: Strict existing state reads

`Run` SHALL support an opt-in existing-state read that raises for absent state, while default construction SHALL retain new-run behavior for admission. Both modes SHALL propagate malformed-state and permission failures and SHALL ignore unknown fields in otherwise valid state.

#### Scenario: Strict absence preserves admission defaults
- **WHEN** a caller reads a missing `state.json` with existing-state mode enabled and separately constructs a run with the default mode
- **THEN** the strict read raises `FileNotFoundError`, the default construction returns new state, and neither operation creates files

#### Scenario: Corruption and forward compatibility
- **WHEN** state contains malformed JSON, a malformed known pause or task structure, an injected permission failure, or otherwise valid data with unknown fields
- **THEN** each malformed or unreadable case raises in both modes, while otherwise valid data loads with unknown fields ignored

### Requirement: Isolated run status failures

JSON status SHALL isolate exceptions from each run's core read, emit one error entry for that run, and preserve every healthy entry. It SHALL retain version 1, the supervisor object, deterministic ordering, and exit code 0 for completed per-run collection, even if all entries are errors. Healthy entries SHALL retain their existing fields and omit `error`.

#### Scenario: Broken run keeps other repositories visible
- **WHEN** all-run JSON status reads healthy runs from different repositories before and after a run whose state, manifest, Git data, tasks, or action construction raises an exception
- **THEN** it emits exactly one parseable version-1 document with all three entries in name order, unchanged healthy entries, a nonempty error for the broken run, and exit code 0

#### Scenario: Error rows remain compatible and non actionable
- **WHEN** a run cannot be read and its error row is consumed by the existing daemon client, attention parser, and Factory reader
- **THEN** the row has its discovered change name, synthetic status `paused`, pause reason `status_error` with empty task and zero automatic resumes, zero done/total counts, empty head and PR, generic error context, and an `error` string containing exception class and message bounded to 500 characters
- **THEN** it has no `action`, each consumer accepts the document, and healthy rows remain available

#### Scenario: Named and entirely broken collections
- **WHEN** an admitted named run fails to read, or every run in an all-run query fails to read
- **THEN** status exits 0 with the normal envelope and one error entry per selected run, without a top-level error

#### Scenario: Unknown changes and document failures
- **WHEN** a named query has an unsafe change name or no state at its initial existence check, or discovery or supervisor lookup raises an exception
- **THEN** status emits exactly one version-1 document with a top-level nonempty error and exits 1
- **THEN** an absent runs directory in an unnamed query instead returns the normal empty runs list and exits 0

#### Scenario: Read failures leave run data untouched
- **WHEN** named or all-run JSON status encounters a read failure, including state disappearing after selection
- **THEN** the selected failing run is represented by an error row without saving synthetic state, writing events, creating files, changing refs, loading credentials, or invoking a mutating command
- **THEN** `KeyboardInterrupt` and `SystemExit` raised during a run read propagate instead of being converted into error rows

### Requirement: Status after partial finish

Status SHALL read tasks from the current branch's live change or existing exact-change archive and report durable run state without retrying finish. Failure to read either location SHALL be isolated using the per-run error contract.

#### Scenario: Archive survives failed finish push
- **WHEN** finish has committed the archive and its external push fails, leaving saved state paused at `human_checks`, and named or all-run JSON status is requested
- **THEN** the run reports archived task counts and branch head, its saved PR and pause, existing finish/abort actions, and no error, while other repositories remain present in an all-run query
- **THEN** reading status leaves state, events, and refs unchanged and does not retry publication

#### Scenario: Missing live and archived tasks
- **WHEN** a run has neither readable live tasks nor readable archived tasks at its branch head
- **THEN** status returns that run as an error entry with exit code 0 and retains all selected healthy runs

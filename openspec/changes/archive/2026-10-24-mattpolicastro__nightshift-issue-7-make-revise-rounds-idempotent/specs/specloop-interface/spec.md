## Purpose

Make keyed build revisions replayable across process crashes while preserving unkeyed CLI behavior and later run-control decisions.

## ADDED Requirements

### Requirement: Durable revision identity

A keyed revision SHALL bind a run-scoped key to canonical parsed feedback, branch, parent, candidate commit and round metadata in harness-owned storage outside RunState. It SHALL persist an atomic prepared record before advancing the branch and retain evidence through replay. Invalid or conflicting evidence SHALL fail without authorizing a new append.

#### Scenario: Preparation precedes mutation
- **WHEN** preparing a new keyed revision succeeds or its journal write fails before replacement
- **THEN** the complete prepared record is durable before any branch update, and a failed write leaves the branch and run state unchanged
- **THEN** a candidate object created before a failed preparation does not count as an appended round

#### Scenario: Identity survives state rewrites
- **WHEN** a run-state save by an older-compatible reader drops unknown state fields and the keyed invocation restarts
- **THEN** its separate journal still binds the original key, feedback and commit without depending on RunState extension fields or a job result file

#### Scenario: Conflicting or corrupt evidence is refused
- **WHEN** a key is reused with different parsed feedback or its record has malformed known fields, an unsupported version or inconsistent run or branch identity
- **THEN** the command refuses with a reason, preserves existing evidence, refs and run state, and appends no tasks
- **THEN** unknown fields in an otherwise valid record are tolerated

### Requirement: Idempotent revision recovery

While holding the existing run lock, keyed revise SHALL recover prepared work or return the original reachable commit for matching feedback without appending another round. It SHALL validate Git evidence and preserve later control state. The guarantee SHALL apply across new processes and background attempts while run evidence is retained.

#### Scenario: Retry after commit does not duplicate round
- **WHEN** a process exits after its keyed review commit advances the branch but before the background wrapper persists result.json, and a fresh process retries the same key and saved feedback
- **THEN** it returns that original commit successfully and tasks.md contains exactly one added review section and one task per saved feedback block
- **THEN** no second complete revised event for that key is appended

#### Scenario: Prepared candidate resumes once
- **WHEN** a process exits after preparing the journal but before branch advancement and the branch still equals the recorded parent
- **THEN** an eligible retry advances to the exact prepared commit once after rechecking current eligibility
- **THEN** a concurrently held run lock produces existing Busy behavior without mutation and a later retry remains safe

#### Scenario: Later control state takes precedence
- **WHEN** a matching commit is on the branch and the run has subsequently paused, aborted or finished, including after tasks were archived or a pause request was created
- **THEN** replay returns the original commit and observed status/pause without replacing later state, clearing a pause request or requiring a live tasks.md at the current head
- **THEN** incomplete original running-state bookkeeping may be completed only when saved state and event evidence both still match the pre-transition snapshot

#### Scenario: Git mismatch differs from operational failure
- **WHEN** candidate evidence is missing or ancestry differs, or the ancestry command exits 1, exits above 1, times out or fails to launch
- **THEN** missing or conflicting evidence and exit 1 cause an evidence-conflict refusal, while the other cases report a Git operational failure
- **THEN** none of these failures advances a ref, replaces the journal or appends another round

#### Scenario: Different keys and runs remain independent
- **WHEN** equal feedback is submitted under distinct keys in one eligible run or the same key is used in separate runs
- **THEN** each new key within its run appends one round, without deduplicating solely by feedback text or a global key registry
- **THEN** identical keys with equivalent parsed blocks replay even if the feedback file path or serialization whitespace differs

### Requirement: Keyed revision CLI

Revise SHALL accept optional --round-id as a nonempty ASCII identifier of at most 128 characters matching `[A-Za-z0-9][A-Za-z0-9._:-]*`. Keyed success SHALL retain the version-1 revised outcome and original commit, adding round_id, replayed, status and pause observed under the run lock. Unkeyed syntax, JSON fields, fresh-append behavior and human success wording SHALL remain compatible.

#### Scenario: Keyed CLI reports replay accurately
- **WHEN** a valid keyed CLI call appends or replays a revision with --json or human output
- **THEN** JSON is exactly one version-1 document with the matching key, original commit, correct replay boolean and observed status/pause
- **THEN** human output identifies replay and reports paused, aborted or finished state when applicable instead of always claiming running

#### Scenario: Invalid key and unkeyed compatibility
- **WHEN** an explicitly empty, overlong or malformed key is supplied, or a caller invokes revise without a key
- **THEN** invalid keys refuse without run mutation, while unkeyed calls retain existing success/refusal behavior and successful human output containing running
- **THEN** no key is inferred for legacy unkeyed rounds

## MODIFIED Requirements

### Requirement: Revision round

`specloop revise <change> --feedback <file>` SHALL append a section
`## N. Review round K` to the change's tasks.md, with one task per feedback
block, commit it to the branch through the harness, and leave the run running
for a newly appended round. With --round-id, a matching already-landed round
SHALL instead follow Idempotent revision recovery, returning its original
commit and preserving later run state. Without a key, every eligible invocation
SHALL retain the existing fresh-append behavior. It SHALL refuse empty feedback
even when a key is supplied.

#### Scenario: Feedback becomes tasks
- **WHEN** revise is given two feedback blocks for an admitted change
- **THEN** tasks.md on the branch gains a review-round section with two unchecked tasks quoting them, and the run's status is running

#### Scenario: Empty feedback refused
- **WHEN** revise is given a file with no feedback
- **THEN** nothing is committed and the command exits non-zero

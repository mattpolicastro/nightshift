# specloop-interface Specification

## Purpose
The command-line contract a front end (the Nightshift daemon) uses to drive
specloop without importing it.

## Requirements

### Requirement: Status JSON
`specloop status --json` SHALL print one JSON document with `"version": 1`, the
supervisor's state, and for each run its change, status, pause (reason, task
and detail, or null), tasks done and total, branch head and PR URL.

#### Scenario: Paused run reported
- **WHEN** a run is paused as stuck on a task and `specloop status --json` is run
- **THEN** the run's entry has status "paused" and a pause with reason "stuck" and that task's key

#### Scenario: Unknown change
- **WHEN** `specloop status --json` is given a change that was never admitted
- **THEN** it prints a version-1 document with an error and exits 1

### Requirement: Command JSON
`propose`, `admit`, `revise` and `publish` SHALL accept `--json` and then print
exactly one version-1 JSON document describing the outcome, including any
questions, contradictions, risks, reasons, branch, commit or PR URL.

#### Scenario: Refused admission as JSON
- **WHEN** `specloop admit --json` refuses a change because a task is impossible as written
- **THEN** it prints outcome "refused" with the contradiction among the reasons and exits 1

#### Scenario: Questions as JSON
- **WHEN** the drafting session asks for human input during `specloop propose --json`
- **THEN** it prints outcome "needs_input" with the questions

### Requirement: Manifest templates
`admit` and `propose` SHALL accept `--change` and `--branch`, overriding the
manifest's values, so that one manifest per repository serves every change.

#### Scenario: Template admission
- **WHEN** a repository manifest naming change "x" is admitted with `--change issue-7-y --branch spec/issue-7-y`
- **THEN** the run is created for change "issue-7-y" on branch "spec/issue-7-y" and its stored manifest carries those values

### Requirement: PR adoption
`admit` SHALL accept `--pr <url>`, record it in the run state, and `publish`
SHALL then push the branch and keep using that PR rather than opening another.

#### Scenario: Publish reuses the PR
- **WHEN** a change admitted with `--pr` reaches ready-to-publish and is published
- **THEN** no new pull request is created and the recorded PR URL is reported

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

### Requirement: Remaining automatic resumes

`specloop status --json` SHALL add `auto_resumes_remaining` to every non-null
pause in named-run and all-run queries: a nonnegative integer or null for
unlimited recovery, derived from supervisor policy and consumed resumes.
Unscheduled and nonautomatic pauses SHALL report zero. Status SHALL remain
read-only, retain version 1 and existing fields, and retain null pauses.

#### Scenario: Infrastructure budget reported
- **WHEN** named-run or all-run status is read for a scheduled infra pause whose task has consumed 0, 1, 2, or 3 automatic resumes under the current cap of two
- **THEN** `pause.auto_resumes_remaining` is respectively 2, 1, 0, or 0, regardless of whether the resume time is in the past or future

#### Scenario: Unlimited scheduled recovery
- **WHEN** status is read for a scheduled usage-limit pause or a scheduled infra pause with no matching task state
- **THEN** `pause.auto_resumes_remaining` is null, matching the supervisor's uncapped behavior for these cases

#### Scenario: No automatic recovery
- **WHEN** status is read for a requested pause, another reason without an automatic policy, or an infra or usage-limit pause with no resume time
- **THEN** `pause.auto_resumes_remaining` is zero

#### Scenario: Status remains read only
- **WHEN** named-run and all-run status are read for paused runs including one without matching task state and for a run without a pause
- **THEN** the documents retain version 1 and existing fields, the unpaused entry has a null pause, and persisted state and event files remain unchanged without creating task state in memory

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

### Requirement: Known runtime scenario references

Runtime evidence and scenario context SHALL accept a backticked candidate ID only when it exactly matches a scenario in the change at the evaluated revision. They SHALL preserve the original task and feedback text. Drafting and admission SHALL retain strict validation of unknown authored scenario references.

#### Scenario: Paths in review feedback
- **WHEN** review-feedback task text contains backticked `a/b/c` and another unknown candidate alongside a known scenario ID
- **THEN** evidence and implementation/gate scenario lists contain only the known ID, and no strengthening task is generated for either unknown candidate
- **THEN** the feedback text and task identity remain unchanged

#### Scenario: Real coverage gaps remain actionable
- **WHEN** a review-feedback task cites an existing change scenario without passing coverage evidence
- **THEN** the evidence gate generates the normal strengthening task for that real scenario
- **THEN** a covered existing scenario does not generate a coverage strengthening task

#### Scenario: Strict validation and failed lookup
- **WHEN** authored tasks cite an unknown ID during drafting or admission, or a runtime evidence gate cannot load the change's scenarios
- **THEN** drafting and admission still report the unknown ID, and a runtime lookup failure prevents evidence from being marked passed or tasks from being appended

### Requirement: JSON argument errors

When argument parsing fails and the effective argv contains an exact `--json` token before any standalone `--`, specloop SHALL emit exactly one stdout document with `version` equal to `1` and a nonempty top-level `error`, and SHALL exit with status 2 before dispatching a command. This SHALL apply to top-level and subcommand parsing failures without changing successful invocation syntax.

#### Scenario: Invalid arguments produce a document
- **WHEN** a JSON-requesting invocation has a missing required argument, an invalid choice or numeric value, an unknown option, or an absent or unknown subcommand
- **THEN** stdout contains exactly one version-1 error document, the exit status is 2, and no command handler or command side effect runs
- **THEN** the behavior holds for explicit argv and process argv, including when the error precedes the JSON flag

#### Scenario: Human errors and help remain compatible
- **WHEN** parsing fails without an exact JSON flag before the option terminator, or argparse processes a help request
- **THEN** non-JSON failures retain normal stderr diagnostics and exit status 2 without a JSON document, and help remains ordinary help with exit status 0 even when `--json` is also present

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

### Requirement: Proposal source ref JSON

Successful `specloop propose --json` output SHALL set `branch` to the exact ref
returned by local proposal publication and `commit` to the drafted commit
retained there. It SHALL retain version 1, one stdout JSON document, existing
fields and exit codes. Publication SHALL continue preserving checked-out
source worktrees. Non-success outcomes SHALL retain their existing contract.

#### Scenario: Checked out branch reports private ref
- **WHEN** a successful proposal targets a branch checked out in the main source worktree or a linked worktree
- **THEN** JSON reports `branch` as `refs/specloop/proposals/<change>` and that ref resolves to the reported commit in the manifest's source repository
- **THEN** the checked-out branch tip, HEAD, index, staged and unstaged edits and untracked files remain unchanged

#### Scenario: Ordinary branch retains its spelling
- **WHEN** a successful proposal can publish to its effective branch without the checked-out-branch fallback
- **THEN** JSON reports that branch without `refs/heads/`, including an explicitly configured custom CLI branch, and the branch resolves to the reported commit
- **THEN** stdout contains exactly one version-1 document with the existing fields and exit status 0

#### Scenario: Unsuccessful proposal retains its outcome
- **WHEN** drafting needs input, is rejected, or local publication fails
- **THEN** JSON and exit status retain their existing needs-input or rejection behavior, with questions or failure details as applicable
- **THEN** the command does not report a successful fallback publication

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

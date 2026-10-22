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
block, commit it to the branch through the harness, and leave the run running.
It SHALL refuse empty feedback.

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

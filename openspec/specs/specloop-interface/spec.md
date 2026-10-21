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

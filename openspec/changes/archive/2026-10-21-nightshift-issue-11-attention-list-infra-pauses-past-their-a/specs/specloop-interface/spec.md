## Purpose

Expose the automatic-resume allowance through the status CLI so callers can
identify human waits without importing engine state or retry policy.

## ADDED Requirements

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

## Purpose

Show elapsed human waits beside Factory status chips using validated optional
pause metadata, preserving the existing page contract.

## ADDED Requirements

### Requirement: Pause display metadata

The Factory shared schema SHALL accept optional nullable pause metadata with
optional nullable finite-number `since` in epoch seconds and optional nullable
nonnegative-integer `auto_resumes_remaining`. The existing server projection
SHALL forward only those two pause fields, preserve supplied nulls, and retain
its validation, redaction, caching, and action protections.

#### Scenario: Only pause display fields cross the server
- **WHEN** version-1 status supplies pause timestamps and remaining-resume counts alongside reason, task, detail, resume time, or other internal fields
- **THEN** the Factory response includes only the supplied `since` and `auto_resumes_remaining` pause properties with their original numeric or null values
- **THEN** existing validated display fields and action behavior are unchanged

#### Scenario: Older producers remain supported
- **WHEN** status omits pause, supplies null pause, or supplies a pause object missing one or both display fields
- **THEN** the server accepts the otherwise valid response without defaulting a missing timestamp or budget to zero
- **THEN** absent pause and pause objects with no display fields are omitted from the projection, explicit null pause is retained, and any supplied valid display field is retained

#### Scenario: Invalid metadata retains validation behavior
- **WHEN** a status read supplies an invalid timestamp type or a negative, fractional, or incorrectly typed remaining-resume count
- **THEN** existing schema validation fails the read, retaining the last good snapshot marked stale or reporting unavailable when no snapshot exists
- **THEN** existing version, status, action, PR URL, and context validation are not relaxed

### Requirement: Human pause waiting time

Factory SHALL display elapsed waiting time only for runs with status `paused`,
`pause.auto_resumes_remaining` exactly zero, and a finite numeric `pause.since`.
It SHALL show floored whole minutes below one hour and floored whole hours
thereafter, using `waiting Nm` or `waiting Nh` and clamping future timestamps to
zero elapsed time. It SHALL use `foregroundMuted` through three hours and
`statusWarning` strictly after three hours, based on unrounded elapsed time.

#### Scenario: Waiting time shows for human pauses only
- **WHEN** Factory renders paused runs with known timestamps and zero remaining resumes alongside paused runs with positive, null, or missing budgets or missing/null timestamps, plus running, done, and aborted runs
- **THEN** only the paused runs with known timestamps and zero remaining resumes show waiting text, regardless of pause reason or available action verbs
- **THEN** running and finished runs show no waiting text even if their payloads retain pause metadata and finished rows are expanded

#### Scenario: Waiting duration uses whole units
- **WHEN** eligible runs have elapsed waits of 59 seconds, 60 seconds, 3,599 seconds, 3,600 seconds, and 18,000 seconds
- **THEN** their labels are respectively `waiting 0m`, `waiting 1m`, `waiting 59m`, `waiting 1h`, and `waiting 5h`
- **WHEN** an eligible run has a future pause timestamp or a valid epoch-zero timestamp
- **THEN** the future timestamp shows `waiting 0m`, and epoch zero is treated as a known timestamp rather than missing data

#### Scenario: Waiting time turns to warning after three hours
- **WHEN** an eligible run's unrounded elapsed wait is below or exactly 10,800 seconds
- **THEN** its waiting text uses the current theme's `foregroundMuted`
- **WHEN** elapsed time exceeds 10,800 seconds, including by a fractional second
- **THEN** its waiting text uses the current theme's `statusWarning` even while the floored label remains `waiting 3h`
- **THEN** a theme change updates the waiting color from the new theme's tokens without changing the text or task-progress color

#### Scenario: Waiting text preserves the existing row
- **WHEN** an eligible row renders waiting text
- **THEN** it appears once as separate readable text beside the existing status chip/progress block below the title, in a layout that allows wrapping
- **THEN** the status/progress text remains exactly `<status> · <done>/<total> tasks done`, its chip styling and muted progress remain unchanged, and rows without an indicator retain their existing presentation
- **THEN** all existing accessibility labels, roles, expanded states, action availability, confirmations, notices, Details, navigation, ordering, and finished disclosure behavior remain unchanged

#### Scenario: Existing refreshes update the wait
- **WHEN** an existing Factory refresh or render occurs with a later client time, a newer pause timestamp, or a run that has resumed or finished
- **THEN** the label and warning reflect the current elapsed time, reset for the new pause timestamp, or disappear for the resumed or finished run
- **WHEN** a refresh fails after a good snapshot
- **THEN** the retained snapshot keeps its waiting eligibility and the page's existing stale/error indications
- **THEN** waiting-time presentation adds no timer, RPC, workspace lookup, or change to refresh and cache behavior

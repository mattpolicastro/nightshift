## Purpose

Keep active Factory work visible while retaining finished runs in an accessible
disclosure, using the existing snapshot and row behavior.

## ADDED Requirements

### Requirement: Stable run groups

The Factory client SHALL classify `paused` and `running` runs as active and
`done` and `aborted` runs as finished. Active runs SHALL place paused runs first
and preserve source order within each status. Finished runs SHALL preserve
their combined source order. Grouping SHALL preserve every run exactly once
without mutating the snapshot or its run objects. The model SHALL export
`groupFactoryRuns(runs)` returning `{ active, finished }`.

#### Scenario: Mixed statuses retain stable groups
- **WHEN** `groupFactoryRuns` receives done A, running B, paused C, aborted D, paused E, running F, and done G in that order
- **THEN** active is C, E, B, F and finished is A, D, G, with original object identities and input order unchanged

#### Scenario: Empty and single group inputs
- **WHEN** `groupFactoryRuns` receives an empty list, only active runs, or only finished runs
- **THEN** it returns both arrays, with the absent groups empty and the populated groups ordered by the same rules

### Requirement: Existing ordering interface

The model SHALL leave `orderFactoryRuns` and its flat-array behavior unchanged,
without adding overloads or options. Grouping SHALL use the separate
`groupFactoryRuns` interface.

#### Scenario: Existing flat ordering remains compatible
- **WHEN** the single-argument ordering function receives done A, paused C, running B, and paused E
- **THEN** it still returns the flat array C, E, A, B without mutating its input

### Requirement: Finished disclosure

The page SHALL render active rows first and, when finished runs exist, a
`Finished (N)` button below them where N is the finished count. The disclosure
SHALL start collapsed on section mount, expose its current boolean state through
`accessibilityState.expanded`, and toggle on each press. Expanded finished rows
SHALL use the existing Factory row content and controls. Toggling SHALL not
initiate an RPC or workspace lookup.

#### Scenario: Finished rows start folded and can be toggled
- **WHEN** Factory loads mixed active and finished runs
- **THEN** active rows are visible in their group order and finished rows are absent beneath a `Finished (N)` button with button role, matching accessibility label, and expanded state false
- **WHEN** the button is pressed and then pressed again
- **THEN** finished rows first appear below active rows in their group order with expanded state true, then disappear with expanded state false, without a read, action, or workspace request caused by either press

#### Scenario: Expanded rows retain existing content
- **WHEN** the finished group is expanded for a run with tasks, a PR, and context, and that row's Details is opened
- **THEN** the row shows its change, status, task progress, working PR link, and the same Details fields, links, and more-detail command as before this change

### Requirement: Group counts and empty state

The summary SHALL count active runs as `N runs` (using `1 run` for one), append
` · M finished` when finished runs exist, and retain the existing stale suffix
after those counts. Counts SHALL be independent of disclosure state. The page
SHALL omit the disclosure when there are no finished runs and SHALL show the
existing empty message only when the entire loaded snapshot has no runs.

#### Scenario: Counts describe active and finished runs
- **WHEN** a loaded snapshot has two active and three finished runs and the disclosure is toggled
- **THEN** the summary remains `2 runs · 3 finished` in both states, becoming `2 runs · 3 finished · Stale` if that snapshot is marked stale

#### Scenario: Active only finished only and empty snapshots
- **WHEN** snapshots contain one active run only, two finished runs only, or no runs
- **THEN** their summaries are respectively `1 run`, `0 runs · 2 finished`, and `0 runs`; only the finished-only snapshot offers `Finished (2)`, and only the empty snapshot shows `No Factory runs.`

### Requirement: Existing interactions remain intact

The fold SHALL preserve existing action dispatch, confirmations, Details,
refresh triggers, and stale/error handling. Snapshot updates SHALL recompute
groups and counts without changing the section's disclosure choice.

#### Scenario: Refresh updates groups without resetting disclosure
- **WHEN** a refresh changes an active run to done or aborted while the disclosure is either collapsed or expanded
- **THEN** the run moves into finished, the counts update, and the chosen disclosure state remains unchanged, including after finished runs temporarily disappear and later return

#### Scenario: Failed refresh retains groups and stale handling
- **WHEN** a refresh fails after a mixed snapshot has loaded, with the disclosure either collapsed or expanded
- **THEN** the last good groups and counts remain available, the disclosure retains its state, and the existing stale suffix and error are displayed

### Requirement: Visible action results

An action notice SHALL appear exactly once: in its originating row while
rendered (active, or finished and expanded), otherwise in the existing
above-list slot identifying the change. Toggling SHALL move the notice between
these locations without clearing or duplicating it. Existing notice text and
lifetime SHALL remain unchanged.

#### Scenario: Action result follows a finished run
- **WHEN** a confirmed Finish or Abort returns a result and the post-action refresh marks its run done or aborted while the finished group is collapsed
- **THEN** the result is visible exactly once in the existing above-list slot identifying the originating change, although the run remains in the snapshot, and the group stays collapsed
- **WHEN** the finished group is expanded
- **THEN** the same result moves into that run's rendered row with no above-list duplicate
- **WHEN** the finished group is collapsed again
- **THEN** the result moves back to the above-list slot identifying the change, with no duplicate
- **WHEN** a confirmed Finish or Abort instead completes while the finished group is already expanded
- **THEN** the result appears exactly once in the rendered finished row with no above-list duplicate and the group stays expanded
- **WHEN** a subsequent refresh removes that run entirely
- **THEN** the result appears exactly once above the list identifying the originating change, regardless of disclosure state

#### Scenario: Result remains in an active row
- **WHEN** an action completes, is refused, or cannot be confirmed and its originating run remains active after refresh
- **THEN** the existing notice appears exactly once in that rendered row, with its existing text and no above-list duplicate, regardless of the finished disclosure state

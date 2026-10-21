# run-actions Specification

## Purpose
What an interface needs to show a paused specloop run and act on it safely.

## Requirements

### Requirement: Actions in status
`specloop status --json` SHALL include, for every paused run, an `action` with
the same headline, why and command as the run's Slack message, plus the list of
`act` verbs that fit the pause.

#### Scenario: Gate action described
- **WHEN** a run is paused at a phase gate
- **THEN** its status entry has an action whose headline matches the Slack message and whose verbs are resume and abort

#### Scenario: Running run has no action
- **WHEN** a run is running
- **THEN** its status entry has no action

### Requirement: Act
`specloop act <change> <verb> [--note] --json` SHALL perform the verb when it
fits the run's current pause, exactly as the matching command would, and print
a version-1 outcome. It SHALL refuse a verb that does not fit, an unknown change
and a note over 2,000 characters.

#### Scenario: Resume with a note
- **WHEN** act resume is called with a note on a run paused as stuck
- **THEN** the run is running again and its paused task carries the note

#### Scenario: Wrong verb refused
- **WHEN** act publish is called on a run paused at a phase gate
- **THEN** the outcome is refused and the run is unchanged

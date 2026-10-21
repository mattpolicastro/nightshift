## Purpose

The Factory section of the Nightshift Paseo page: specloop runs, and acting on
the ones waiting for Matt.

## ADDED Requirements

### Requirement: Runs listed
The page SHALL list each specloop run with its change, status, tasks done/total
and PR link, putting runs that wait on a human first, with their action headline
and why.

#### Scenario: Waiting run first
- **WHEN** one run is running and another is paused at a phase gate
- **THEN** the paused run is listed first with its headline

### Requirement: Buttons fit the pause
The page SHALL offer exactly the verbs the run's action lists, with a note field
for resume, and SHALL ask for confirmation before publish, finish or abort.

#### Scenario: Publish needs confirmation
- **WHEN** a run is ready to publish and its Publish button is pressed
- **THEN** nothing is sent until the confirmation is accepted

### Requirement: Server checks before acting
The plugin server SHALL run `specloop act` with an argument array, only for a
change present in the last good status and a verb in its action list, and SHALL
pass only display fields to the client.

#### Scenario: Unknown change refused
- **WHEN** the client asks to act on a change not in the last status
- **THEN** the server refuses without running specloop

#### Scenario: Argv not shell
- **WHEN** a note contains shell metacharacters
- **THEN** it reaches specloop as one literal argument

### Requirement: Stale tolerance
When a status read fails, the page SHALL keep the last good list, marked stale.

#### Scenario: Read failure keeps the list
- **WHEN** a refresh fails after a successful read
- **THEN** the previous runs are still shown, marked stale

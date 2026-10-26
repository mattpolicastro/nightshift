## Purpose

Invoke canonical commands through the existing validated Factory interface.

## MODIFIED Requirements

### Requirement: Server checks before acting
The plugin server SHALL run `nightshift act` with an argument array, only for a
change present in the last good status and a verb in its action list, and SHALL
pass only display fields to the client.

#### Scenario: Unknown change refused
- **WHEN** the client asks to act on a change not in the last status
- **THEN** the server refuses without running nightshift

#### Scenario: Argv not shell
- **WHEN** a note contains shell metacharacters
- **THEN** it reaches nightshift as one literal argument


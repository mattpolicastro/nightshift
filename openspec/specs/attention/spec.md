# attention Specification

## Purpose
specloop runs that are waiting for a human, in Nightshift's attention list.

## Requirements

### Requirement: Specloop pauses listed
`nightshift attention` SHALL list every specloop run paused for a human
decision with its reason, change and PR link, and its JSON contract SHALL carry
them in a `specloop` list beside `tasks`. Running and finished runs SHALL NOT
be listed.

#### Scenario: Stuck run listed
- **WHEN** a specloop run is paused as stuck
- **THEN** attention lists it with reason "stuck", its change and its PR link

#### Scenario: Running run not listed
- **WHEN** a specloop run is running
- **THEN** attention does not list it

### Requirement: Stale tolerance
If specloop's status cannot be read, attention SHALL show the last snapshot
marked stale rather than dropping it.

#### Scenario: Unreadable status marked stale
- **WHEN** `specloop status --json` fails after a successful earlier read
- **THEN** attention shows the earlier entries marked stale and exits 1

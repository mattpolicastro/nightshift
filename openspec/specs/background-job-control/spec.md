# background-job-control Specification

## Purpose
Let operators discover and stop Nightshift's durable drafting, admission and
revision jobs without a running daemon, valid repository configuration or
network access.

## Requirements

### Requirement: Local job inventory

`nightshift jobs` SHALL list persisted background attempts with full job ID,
stage, repository/item, command, execution state, available PID/PGID and log
path. It SHALL read local state independently of daemon process handles and
configuration, in deterministic stage/repository/item/ID order. It SHALL
distinguish execution completion from delivery and retain historical attempts.

#### Scenario: List jobs after daemon restart
- **WHEN** persisted drafting, admission and revision jobs exist and the daemon's process handles have been lost
- **THEN** `nightshift jobs` lists their stable IDs, context and current states, including historical attempts, without loading repository configuration or contacting external services

#### Scenario: Empty inventory
- **WHEN** no background job directories exist
- **THEN** `nightshift jobs` prints `No background jobs.` and exits 0 without creating job state

#### Scenario: Legacy and unreadable records
- **WHEN** inventory contains an older job lacking process identity and a malformed record alongside valid jobs
- **THEN** the older job remains visible with unavailable identity identified, valid jobs remain visible, the malformed record is reported, and the command exits 1

### Requirement: Owned process group termination

`nightshift jobs stop <id>` SHALL resolve an exact full job ID from local state,
verify process ownership, persist stop intent and send SIGTERM to the job's
process group. It SHALL allow 5 seconds before SIGKILL escalation and 1 second
for confirmation, and SHALL mark the job stopped only after no executing group
members remain. It SHALL preserve logs and results and require neither the
daemon nor a valid configuration. It SHALL not change the pause file or signal
other jobs.

#### Scenario: Stop wrapper and descendants
- **WHEN** an operator stops a running job whose wrapper, child and grandchild share its session's process group
- **THEN** all executing members terminate, the command exits 0, durable state reports stopped, evidence remains on disk, and an unrelated job and the caller remain alive

#### Scenario: Escalate unresponsive group
- **WHEN** the owned job group continues executing after the 5 second SIGTERM grace period
- **THEN** stop sends SIGKILL to that group and records stopped only after confirming termination within the additional 1 second bound

#### Scenario: Refuse unsafe identity
- **WHEN** the ID is unknown, process identity is absent or mismatched, the wrapper leader cannot be verified, or the group is the caller's group or PGID 0 or 1
- **THEN** stop exits 1 with a diagnostic, sends no termination signal and does not falsely mark the job stopped

#### Scenario: Report termination failure
- **WHEN** an accepted stop request encounters a signalling permission error or cannot confirm group termination within its deadline
- **THEN** stop exits 1 with a diagnostic, retains the stop intent and evidence, and does not report successful termination

### Requirement: Durable cancellation

Stop intent SHALL take precedence over later job results and SHALL survive
daemon restart. A cancelled attempt SHALL not be delivered, relaunched or
counted as a job failure for automatic escalation. Resume and unchanged intake
labels SHALL not clear cancellation. Completed attempts SHALL retain their
completion state when stop is requested after completion wins the race.

#### Scenario: Stopped job stays stopped
- **WHEN** a stopped job's issue or PR remains armed and the daemon polls, restarts, or resumes after a pause
- **THEN** that job remains visibly stopped, its result is not delivered, no replacement attempt starts, and intentional cancellation produces no health failure or escalation

#### Scenario: Stop wins completion race
- **WHEN** stop intent is durably recorded before concurrent wrapper completion or result collection can commit
- **THEN** a late success result cannot clear the cancellation or become eligible for delivery

#### Scenario: Completion wins stop race
- **WHEN** completion is durable or collection has accepted a result before the stop request is accepted
- **THEN** stop exits 0 reporting already finished, sends no termination signal, and preserves completion and its existing delivery eligibility

#### Scenario: Repeat stop safely
- **WHEN** an operator repeats stop for a confirmed stopped job, including from a new CLI process
- **THEN** the command exits 0 without signalling another process or changing the terminal stopped state

### Requirement: Job command syntax

The CLI SHALL accept `nightshift jobs` and `nightshift jobs stop <id>` without
changing existing commands' defaults or options. Invalid syntax SHALL exit 2;
IDs SHALL be treated as identifiers rather than filesystem paths.

#### Scenario: Reject malformed command
- **WHEN** jobs is given an unknown action, stop lacks an ID, or extra positional arguments are supplied
- **THEN** the CLI exits 2 with usage information and does not modify state or signal processes

#### Scenario: Reject path identifier
- **WHEN** the supplied job ID is a path or contains traversal components
- **THEN** stop exits 1 without reading outside the enumerated job directories, modifying state or sending termination signals

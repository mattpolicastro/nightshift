## Purpose

Show active specloop background jobs while retiring worker session reporting.

## MODIFIED Requirements

### Requirement: Active jobs in status

`nightshift status` SHALL read local job inventory once and display active attempts before repository network queries, including elapsed and deadline state when available. Worker-engine status output, including live worker claim/phase/transcript lines, worker working counts and the off-subscription model report, SHALL be removed with the worker engine. All other status output and exit behavior SHALL remain unchanged, including liveness, alerts, ready/blocked queues, contradiction diagnostics and active background jobs. Status SHALL omit full argv and perform no job mutation, escalation or cleanup.

#### Scenario: Active attempts are visible
- **WHEN** explicit or default status runs with starting, running, stopping and historical jobs, including a job from a repository absent from current configuration
- **THEN** active rows appear in inventory order with full ID, stage, repository/item, command name and execution state, while finished, failed and stopped history remains available through jobs
- **THEN** timed rows display nonnegative elapsed seconds and remaining seconds or OVERDUE by seconds at or after deadline, and unavailable timing is explicit without revealing idea text

#### Scenario: Status preserves compatibility
- **WHEN** existing status and other commands run with empty job inventory
- **THEN** default and explicit status each perform one inventory read without an extra empty-jobs line, preserving liveness, alerts, non-worker output, configuration loading, options and exit codes, with no worker session or off-subscription model report
- **THEN** unrelated commands do not acquire an inventory read or a job-control side effect

#### Scenario: Status tolerates damaged inventory
- **WHEN** status encounters unreadable job records alongside a healthy active job
- **THEN** it displays diagnostics and the healthy row, continues existing status processing and retains the existing status exit behavior
- **THEN** persisted files remain unchanged, while the jobs command retains its own diagnostic exit policy

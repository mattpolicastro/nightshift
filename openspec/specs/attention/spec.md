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

### Requirement: Pause driven attention

For a present `auto_resumes_remaining`, `nightshift attention` SHALL list only
runs with status `paused`, a non-null pause, and zero allowance. Positive or
null allowances SHALL be omitted without a human-reason allowlist. Entries
SHALL preserve change, reason, and optional PR link in text and the JSON
`specloop` list beside worker `tasks`. Successful reads SHALL exit 0.

#### Scenario: Exhausted infra pause listed
- **WHEN** a paused infra run has `auto_resumes_remaining` equal to zero
- **THEN** text and JSON attention include its change, infra reason, and PR link

#### Scenario: Requested pause listed
- **WHEN** a paused requested run has `auto_resumes_remaining` equal to zero
- **THEN** text and JSON attention include its change, requested reason, and PR link

#### Scenario: Automatic recovery omitted
- **WHEN** a paused run has a positive or null `auto_resumes_remaining`, including infra, usage-limit, or a new automatic reason
- **THEN** attention omits it even if its resume deadline has passed

#### Scenario: Other human pauses listed
- **WHEN** a paused run has zero `auto_resumes_remaining` and an existing human reason or a previously unknown reason
- **THEN** attention includes it without requiring a reason-list update, including when it has no PR

#### Scenario: Nonpaused runs omitted
- **WHEN** a run is running, done, or aborted, even with a retained pause whose allowance is zero, or a run has no pause
- **THEN** attention omits it

### Requirement: Allowance compatibility and snapshots

Attention SHALL accept older pause objects without `auto_resumes_remaining`,
listing them unless their reason is `usage_limit`. A present allowance SHALL
be null or a nonnegative integer excluding booleans; invalid values SHALL
fail the whole read through existing stale-snapshot handling. Offline reads
SHALL use the persisted entries without fetching status. Existing snapshot
timestamps, entry metadata, worker results, and exit-code semantics SHALL be
preserved.

#### Scenario: Legacy pause without allowance
- **WHEN** status returns paused runs without the allowance field, including infra, requested, existing human reasons, an unknown reason, and usage-limit
- **THEN** attention lists every such run except usage-limit, while an explicit null allowance is treated as unlimited and omitted

#### Scenario: Invalid allowance preserves snapshot
- **WHEN** attention has successfully cached an exhausted infra or requested entry and a later document contains a boolean, negative number, float, string, array, or object allowance
- **THEN** the whole read fails, attention retains the previous entries and checked-at time marked stale with an error, and text and JSON commands exit 1

#### Scenario: Cached entries and recovery
- **WHEN** exhausted infra and requested entries have been cached, an offline read occurs, and a later successful online read reports no human waits
- **THEN** the offline read returns the cached entries without launching status, and the successful online read clears those entries and any previous stale error and exits 0

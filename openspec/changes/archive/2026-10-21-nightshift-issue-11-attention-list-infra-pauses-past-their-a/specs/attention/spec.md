## Purpose

Include specloop pauses requiring human intervention in Nightshift attention,
using the pause's remaining automatic resumes instead of a human-reason list.

## ADDED Requirements

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

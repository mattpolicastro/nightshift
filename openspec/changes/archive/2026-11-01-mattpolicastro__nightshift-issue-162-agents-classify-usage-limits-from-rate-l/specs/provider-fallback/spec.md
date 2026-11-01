## Purpose

Extend admitted provider recovery with Claude's structured quota evidence,
reset deadlines and a human hold for observed overage. Preserve existing
requirements except the explicit modifications below, which narrow Claude
usage sources and add the overage exception.

## ADDED Requirements

### Requirement: Shared rate limit telemetry

Claude and the historical trace parser SHALL share one decoder for
`rate_limit_event.rate_limit_info`. Claude SHALL carry the last decoded
snapshot through `agents.Result`, independently of terminal output and
including schema-validation failures, preserving existing fields and
positional construction. Malformed payloads SHALL be ignored and invalid
fields SHALL use the documented defaults.

#### Scenario: Telemetry survives incomplete output
- **WHEN** a transcript contains rate-limit events before or after the final result, or contains no final result
- **THEN** Claude retains the last decoded snapshot with status, window, reset and overage in its result, preserving session and usage when available
- **THEN** the historical trace parser retains its existing valid-fixture behavior and still returns None without a terminal result

#### Scenario: Malformed telemetry is bounded
- **WHEN** events contain non-mapping rate-limit payloads or wrong field types, including boolean, string, fractional or non-finite reset values
- **THEN** parsing does not crash; invalid payloads do not replace a prior snapshot, and invalid fields normalize to the documented defaults without interpreting truthy strings as overage; only literal boolean true signifies overage, only positive integer Unix seconds excluding booleans are valid resets, and invalid scalar fields become unknown status/window, zero reset and false overage
- **THEN** a valid snapshot survives shared schema validation even when the final output becomes invalid_output

### Requirement: Structured Claude usage classification

Claude SHALL prioritize selected overage, then timeouts, then structured
rejection. Allowed and warning statuses SHALL suppress usage regex matching.
Absent or unknown status SHALL permit fallback on failed runs only, using
stderr and string provider `errors` exclusively. Agent prose, structured output
and metadata SHALL NOT be usage diagnostic sources. Authentication matching
SHALL remain unchanged.

#### Scenario: Agent prose cannot exhaust a profile
- **WHEN** a failed Claude run without a decisive rate-limit event mentions quota, rate limit, usage limit, limit reached or 429 only in final agent text, structured output or metadata
- **THEN** those mentions do not yield usage_limit or park the profile as quota-exhausted; an otherwise unrelated failure remains infra

#### Scenario: Rejection is authoritative
- **WHEN** Claude reports status rejected without overage and without a timeout, with failed, missing, or apparently successful terminal output
- **THEN** it returns usage_limit with the structured snapshot regardless of agent wording and does not accept the successful output

#### Scenario: Allowed and warning events do not exhaust
- **WHEN** the selected event has status allowed or allowed_warning without overage and stderr or final output mentions quota
- **THEN** usage regex matching is suppressed; ordinary success, invalid-output, authentication or infra handling applies

#### Scenario: Provider diagnostics remain a fallback
- **WHEN** a failed run has no recognized status or overage and an existing usage pattern appears in stderr or explicit provider errors (string list entries or a scalar string)
- **THEN** Claude returns usage_limit ahead of any matching authentication diagnostic, retaining session, usage and bounded detail
- **THEN** an unknown status alone does not cause exhaustion, and a timeout without overage remains infra even with a rejection or matching diagnostic

### Requirement: Structured quota deadlines

Claude quota recovery SHALL use a valid future structured reset plus 60
seconds, otherwise now plus `FALLBACK_SECONDS` (1800 seconds). Supplied Claude
telemetry SHALL take precedence in `quota.resume_at`; calls without it SHALL
preserve Codex behavior. The driver SHALL persist the chosen deadline, retain
ordinary fallback and record normalized evidence in `exhausted.rate_limit`
alongside `until`. No-event records SHALL retain their existing shape.

#### Scenario: Reset time drives recovery
- **WHEN** a Claude rejection reports a future reset time different from the flat fallback and the driver handles it
- **THEN** the saved profile deadline and exhausted event until equal that reset plus 60 seconds, and the event contains status, type, resets_at and using_overage
- **THEN** a single-profile run pauses until that deadline, retains it across reload, and scheduled recovery occurs no earlier than the deadline
- **THEN** with an available fallback the same work continues there; an all-blocked chain uses its earliest deadline under existing mixed-cause rules

#### Scenario: Unknown and stale resets retain backoff
- **WHEN** a usage-limited Claude result has no reset, an invalid reset, or a reset at or before now
- **THEN** recovery uses now plus 1800 seconds, including with an injected clock value of zero, without crashing or immediately retrying
- **THEN** legacy calls without Claude telemetry retain existing Codex quota reads, window selection, grace and fallback behavior

### Requirement: Structured overage hold

Selected Claude `using_overage` true SHALL pause admitted implementation,
candidate review and section-gate review with reason `overage` and zero
`resume_at` before fallback or output acceptance. The hold SHALL preserve
pending work, unrelated deadlines and retry/attempt counts. An `overage`
event SHALL identify task/section, role, profile and normalized telemetry.
Recovery SHALL require existing explicit human resume.

#### Scenario: Overage pauses despite an available fallback
- **WHEN** Claude reports overage in use during implementation, candidate review or section-gate review and another profile is usable
- **THEN** the run persists an overage pause and event, starts no successor, accepts no result and consumes no infra retry or coding attempt
- **THEN** pending candidate or gate state and unrelated profile deadlines are retained, regardless of a success, error or timeout result; the adapter reports usage_limit for the selected overage snapshot before any other disposition

#### Scenario: Overage waits for human recovery
- **WHEN** an overage-paused run is reloaded and the clock advances beyond the reported reset, or a human later resumes it
- **THEN** the supervisor does not automatically lift the hold; existing explicit resume permits work again, and another overage report pauses again

## MODIFIED Requirements

### Requirement: Unavailable failure classification

The Codex and Claude adapters SHALL classify explicit logged-out,
authentication-required, expired/invalid authentication token, 401/403, and
inactive/expired/cancelled plan or subscription failures as `unavailable`.
They SHALL examine the existing failed-response diagnostic sources, preserve
session/usage/detail fields, and keep existing `USAGE` patterns. Codex sources
and priority SHALL remain unchanged. Claude SHALL apply Structured Claude usage classification first;
only its diagnostic fallback SHALL use `USAGE`, restricted to stderr and
explicit provider `errors`, with usage matches retaining priority over
authentication matches. Claude authentication diagnostic sources SHALL remain
unchanged. Timeout, successful-output and invalid-output semantics SHALL
remain unchanged except for the explicitly specified structured rejection
and overage outcomes.

#### Scenario: Authentication and plan diagnostics
- **WHEN** either adapter receives a failed provider response without an overriding usage-limit signal reporting a logged-out session, required login, expired or invalid authentication token, standalone 401/403, or inactive, expired or cancelled plan or subscription
- **THEN** it returns disposition `unavailable` with the provider diagnostic, session identifier and usage when available
- **THEN** the same classification applies when the diagnostic occurs only in stderr

#### Scenario: Usage matching retains priority
- **WHEN** a failed response matches an existing adapter `USAGE` expression in an eligible diagnostic source, including a response that also mentions authentication or a plan, and Claude has no recognized structured status or overage signal
- **THEN** the adapter returns `usage_limit` and preserves its existing usage-limit diagnostic behavior

#### Scenario: Other outcomes retain their meaning
- **WHEN** the call has no structured rejection or overage signal and times out, fails with an unrelated transport error or a longer number containing 401/403, succeeds with output mentioning a plan or token, or succeeds with malformed structured output
- **THEN** it retains respectively `infra`, `infra`, `ok`, or `invalid_output` behavior without classifying incidental words as unavailability

### Requirement: Compatible all blocked pauses

Except for the Structured overage hold, the driver SHALL pause for provider
failure only when every profile in the affected chain is blocked, using its
earliest deadline as `resume_at`.
The reason SHALL be `usage_limit` for quota-only chains and `infra` when any
active block is unavailability. Existing supervisor, status, attention and
touchpoint recovery contracts SHALL remain unchanged.

#### Scenario: Single profile retains infra pause
- **WHEN** an ordinary infra failure count exceeds `max_infra` for a one-profile role
- **THEN** the run pauses with reason `infra`, the failure detail, unchanged counted attempts, `ts.infra` equal to the exhausted count, and `resume_at` equal to now plus 900 seconds
- **THEN** the unchanged supervisor may automatically lift at most two such pauses for the task, and an explicit `unavailable` result reaches the same pause immediately

#### Scenario: All unavailable use earliest retry
- **WHEN** every profile in a role chain is unavailable with different deadlines and another role has an earlier deadline
- **THEN** the run pauses with reason `infra` and the earliest deadline within the affected chain, describes the unavailable profiles, and ignores the unrelated role's deadline

#### Scenario: Mixed blocks retain bounded recovery
- **WHEN** a chain contains both active usage exhaustion and unavailability with no usable profile
- **THEN** the run pauses with reason `infra`, truthfully describes both causes and uses the earliest chain deadline even when that deadline belongs to the quota-limited profile
- **THEN** the existing two automatic infra resumes per task still apply, and expiry alone does not resume a task whose allowance is spent

#### Scenario: Quota only behavior remains intact
- **WHEN** every profile is usage-limited, including legacy deadlines lacking cause events or a new quota block following an expired unavailable block
- **THEN** the pause reason remains `usage_limit`, the resume time is the earliest chain deadline, and scheduled automatic recovery remains uncapped
- **THEN** cause attribution uses the latest event matching the active deadline, defaults legacy deadline-only entries to usage exhaustion, and ignores expired unavailable events

#### Scenario: Manual resume preserves single profile recovery
- **WHEN** a human uses the existing resume command on a single-profile infra pause before that profile's unavailable deadline
- **THEN** driver recovery clears that unavailable block and resets the task/profile retry allowance so the profile can be probed immediately, without clearing quota deadlines or replenishing the supervisor's automatic-resume budget
- **WHEN** an existing manual resume follows a multi-profile infra pause
- **THEN** the paused task's pair counters reset while profile deadlines remain in force

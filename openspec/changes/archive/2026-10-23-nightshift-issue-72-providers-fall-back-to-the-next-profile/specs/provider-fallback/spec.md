## Purpose

Continue admitted build work through an existing role profile chain when a
provider cannot run, while preserving bounded retries and compatible pauses.

## ADDED Requirements

### Requirement: Unavailable failure classification

The Codex and Claude adapters SHALL classify explicit logged-out,
authentication-required, expired/invalid authentication token, 401/403, and
inactive/expired/cancelled plan or subscription failures as `unavailable`.
They SHALL examine the existing failed-response diagnostic sources, preserve
session/usage/detail fields, and keep existing `USAGE` patterns and their
priority unchanged. Timeout, successful-output and invalid-output semantics
SHALL remain unchanged.

#### Scenario: Authentication and plan diagnostics
- **WHEN** either adapter receives a failed provider response reporting a logged-out session, required login, expired or invalid authentication token, standalone 401/403, or inactive, expired or cancelled plan or subscription
- **THEN** it returns disposition `unavailable` with the provider diagnostic, session identifier and usage when available
- **THEN** the same classification applies when the diagnostic occurs only in stderr

#### Scenario: Usage matching retains priority
- **WHEN** a failed response matches an existing adapter `USAGE` expression, including a response that also mentions authentication or a plan
- **THEN** the adapter returns `usage_limit` and preserves its existing usage-limit diagnostic behavior

#### Scenario: Other outcomes retain their meaning
- **WHEN** the call times out, fails with an unrelated transport error or a longer number containing 401/403, succeeds with output mentioning a plan or token, or succeeds with malformed structured output
- **THEN** it retains respectively `infra`, `infra`, `ok`, or `invalid_output` behavior without classifying incidental words as unavailability

### Requirement: Task and profile retry allowance

The driver SHALL count ordinary infrastructure failures separately for each
task/profile pair, using the existing section key for gate reviews. It SHALL
retry while the pair count is at most `max_infra`, mark the profile
unavailable when the count exceeds that limit, and leave counted coding
attempts unchanged. Changing roles SHALL NOT erase or transfer a pair's
allowance. Pair counts SHALL survive normal driver restart using event
history without changing the persisted state schema.

#### Scenario: Fallback has its own allowance
- **WHEN** task T exhausts profile A's infrastructure allowance and continues on previously unused profile B
- **THEN** B starts with zero infrastructure failures, its first `max_infra` failures remain retryable, and A's failures do not consume B's allowance or T's counted attempts

#### Scenario: Tasks and roles preserve counter identity
- **WHEN** a task returns to a previously used profile before a reset, another role uses the same task/profile pair, or a different task uses that profile
- **THEN** the first two uses retain that pair's own failure count and the different task starts with its independent count

#### Scenario: Restart retains partial allowance
- **WHEN** a driver restarts after a recorded infra retry below the limit
- **THEN** the next failure uses the saved task/profile count rather than granting a fresh allowance or charging a different profile
- **THEN** legacy single-profile state retains its saved `ts.infra`; multi-profile legacy state attributes a saved scalar only when the previous call identifies its profile

### Requirement: Unavailable profile fallback

Implementation, candidate review and gate review SHALL mark a profile
unavailable after infra retry exhaustion or immediately on `unavailable`.
The driver SHALL persist `now + 900` in `state.exhausted`, record an
`unavailable` diagnostic event, and continue the same work on the first
usable chain profile with an FYI naming the failure and successor.

#### Scenario: Infrastructure retries exhaust
- **WHEN** a profile produces its `max_infra + 1` ordinary infrastructure failure and another profile in the role chain is usable
- **THEN** the failing profile is unavailable for 900 seconds, an `unavailable` event records its detail, and the next dispatch uses the first usable alternative while the run stays running
- **THEN** the event contains task/section key, role, profile, deadline, triggering disposition and up to 300 characters of detail
- **THEN** the FYI says `X unavailable: <detail>; <role> continues on Y` and no coding attempt is charged

#### Scenario: Authentication bypasses retries
- **WHEN** a Codex or Claude authentication or plan fixture produces disposition `unavailable` on the first call and a fallback is usable
- **THEN** the driver records and persists unavailability immediately and dispatches the fallback without retrying the failing profile or incrementing the infra counter

#### Scenario: Offline local backend falls back
- **WHEN** the unchanged shared dispatcher reports an unreachable local backend as `infra` until its retry allowance is exceeded
- **THEN** the driver applies the same cooldown, event and fallback behavior without changing the shared reachability implementation

#### Scenario: Review and gate work continue
- **WHEN** a profile becomes unavailable while reviewing a verified pending candidate or reviewing a section gate and another profile is usable
- **THEN** the next profile reviews the same candidate or section, retaining pending state and existing findings without rerunning implementation, counting a review failure, passing a gate or landing work merely because the provider failed

### Requirement: Cooldown eligibility

Profile availability SHALL be shared across roles within a run. Selection
SHALL skip profiles with deadlines in the future and restore original chain
priority at or after expiry. A completed cooldown SHALL reset that profile's
infra allowance for subsequent work without resetting another profile's
allowance. Normal restart SHALL preserve active deadlines.

#### Scenario: Unavailable profile returns
- **WHEN** profile A precedes B in a chain and A's unavailability deadline is in the future, then the clock reaches that deadline
- **THEN** selection uses B before the deadline and A at the deadline, removes A's expired entry, and gives A a fresh infra allowance

#### Scenario: Other roles and restart honor availability
- **WHEN** a profile marked unavailable by one role appears in another role's chain, including after reloading the run in a new driver
- **THEN** that profile remains skipped until its deadline, while usable profiles and unrelated role chains remain available

### Requirement: Compatible all blocked pauses

The driver SHALL pause for provider failure only when every profile in the
affected chain is blocked, using its earliest deadline as `resume_at`.
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

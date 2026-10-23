## Why

A usage limit already advances a role through its configured profile chain.
Other provider failures retry the same profile and then pause, even when a
usable alternative exists. Lapsed authentication and offline local backends
therefore strand runs unnecessarily.

Blocked by #67, whose documentation refresh is present in this checkout
(PR #70). This change builds on that guide and its retained requirements.

## What Changes

- After more than `max_infra` infrastructure failures for a task/profile,
  mark the profile unavailable for the existing 15-minute `INFRA_COOLDOWN`.
  Continue the same work on the first usable profile in the role's chain.
- Classify recognizable authentication and inactive-plan failures in both
  adapters as `unavailable`, skipping infrastructure retries immediately.
  Preserve both existing `USAGE` patterns and their classification priority.
- Reuse `state.exhausted` for availability deadlines. Record an `unavailable`
  event with diagnostic detail and notify which profile takes over. Pause only
  when the affected chain has no usable profile, until its earliest deadline.
- Keep retry allowances separate by task and profile, preserve ordinary
  single-profile infrastructure pauses, and retain the supervisor's existing
  automatic-resume policy.
- Add offline regression tests in new files and expand only the guide's
  existing usage-limit paragraph to explain this behavior.

Runtime edits are limited to `specloop/loop.py`, `specloop/agents/codex.py`,
`specloop/agents/claude.py`, and, only if wording needs it,
`specloop/messages.py`. The sole allowed edit to
`specloop/agents/__init__.py` is adding `unavailable` to the disposition
comment; #15 owns its implementation. Matching canonical specs are updated
through these deltas when the change is archived.

Out of scope: manifest or CLI changes, changes to `specloop/serve.py` or its
auto-resume table, new state schemas, authentication repair, new provider
probing, adapter invocation/sandbox flags, quota-policy changes, and adding
fallback to drafting, feasibility, critic or the one-shot arbiter. Leave all
existing tests and their assertions intact. The design names the concurrent
files to avoid and the one necessary documentation overlap.

## Capabilities

### New Capabilities

- `provider-fallback`: provider failure classification, task/profile retry
  allowances, unavailable cooldowns, continued role execution and compatible
  all-blocked pauses.

### Modified Capabilities

- `specloop-documentation`: add provider fallback guidance; qualify the
  existing Documentation integrity requirement so its docs-only restriction
  continues to govern documentation-only refreshes without forbidding a
  separately specified runtime change such as this one. Allow a verified
  tracking issue until the implementation PR is known and use the applicable
  design's human checklist. Preserve assertion, accurate-reference and
  validation guarantees and all other requirements.

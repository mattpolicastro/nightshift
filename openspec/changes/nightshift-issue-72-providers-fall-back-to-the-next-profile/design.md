## Context

`loop._infra` already handles usage limits by storing a profile deadline in
`RunState.exhausted`, calling `_pick`, and letting the driver repeat its
current work. Ordinary infrastructure errors instead increment the single
`TaskState.infra` scalar and pause after `max_infra` retries. `_infra` is used
by implementation, pending candidate review and agent section-gate review.
The arbiter is deliberately one-shot and does not use this retry interface.

Both adapters classify failed provider responses using their `USAGE` regex,
otherwise returning `infra`. The shared adapter dispatcher returns `infra`
for an unreachable local backend before invoking either adapter. That
dispatcher belongs to #15 and needs no behavioral edit here.

`serve.AUTO_RESUME` lifts `usage_limit` pauses without a cap and `infra`
pauses at most twice per task. Status, attention and touchpoint accounting
depend on those reasons and budgets. Their existing requirements remain
unchanged. `state.exhausted` persists only deadlines, not reasons;
`events.jsonl` already records profile exhaustion and task lifecycle events.

The root has no `AGENTS.md`; the repository guidance in `CLAUDE.md`, both
READMEs, the relevant modules, existing tests, and canonical documentation,
interface, attention and touchpoint requirements informed this design.

## Goals / Non-Goals

Make the existing implementation, review and gate retry paths advance through
their role chains after provider unavailability, without counting a failed
provider call as a failed coding attempt. A fallback gets its own allowance;
cooldown expiry restores normal chain priority. Keep ordinary single-profile
retry/pause behavior and the existing supervisor caps.

Do not add a provider registry, background health checks, credential refresh,
new configuration, a state migration or a new pause reason. Do not broaden the
one-shot arbiter or front-door agent workflows into retry loops. Preserve
existing pending candidates, gate decisions, review validation, sandbox flags
and usage-limit behavior.

## Decisions

### Classification stays in the two adapters

In `specloop/agents/codex.py:run` and `specloop/agents/claude.py:run`, add a
case-insensitive unavailable matcher on the diagnostic sources already used
for failed provider calls (Codex error/turn.failed records and stderr; Claude
error results and stderr). Match explicit logged-out/not-logged-in or
authentication-required failures, expired/invalid authentication or OAuth
tokens, standalone 401/403 statuses, and inactive/expired/cancelled plan or
subscription failures. Do not match bare `token`, `plan` or arbitrary numbers
inside longer numbers. Keep the `USAGE` regexes byte-for-byte unchanged and
evaluate them first, then unavailable, then generic infra. Timeouts stay
infra, successful output stays successful, and malformed successful output
stays `invalid_output`. Preserve session, usage and existing diagnostic detail.

Use synthetic fixtures for each diagnostic family in each adapter, including
stderr-only failures, negative examples and overlapping quota/auth text.
Mock `spawn`, use temporary adapter homes and never invoke a real provider or
read the operator's credentials. The only shared-dispatcher edit is the
`Result.disposition` comment naming the new string.

### Retry accounting belongs to the task and profile

Use `(task.key if task else key, call.profile.name)` as the counter identity
inside `loop.py`; a section gate uses its existing `section:N` key. The same
task/profile shares its count across roles; different profiles and different
tasks do not. On an ordinary infra result, load that pair's count, increment
it, mirror it in `ts.infra`, and compare strictly with `ctx.m.max_infra`.
Thus `max_infra = 1` permits one retry and the second failure disables the
profile; zero disables it on the first failure. An explicit `unavailable`
result bypasses this counter and disables the profile immediately.

Keep the state schema unchanged. Add small loop-local history helpers using
the existing event log: record `infra_retry` with `task`, `role`, `profile`
and `count`, and recover the latest pair counts in append order on a fresh
driver context. Use the existing `pause`/`resumed` and `auto_resumed` events
to recognize task resets; `resumed` gets its task from the preceding pause.
Expired profile cooldowns start a fresh allowance for that profile. Ordinary
successful calls do not erase another profile's count. Cache replay within
the context and update it on transitions rather than rereading the entire
log per retry. Initialize/reconcile this cache at driver entry, after an
existing resume command has written its event, rather than eagerly in the
`Ctx` constructor (the CLI constructs its context before recording resume).
No new state file or synthetic task entries are needed.

For legacy histories without pair counters, retain `ts.infra` for a
single-profile role. For a multi-profile role, attribute a legacy scalar
only when the latest recorded call identifies that task's profile; otherwise
start new pair counters at zero rather than charging an unrelated fallback.
Ignore unknown events and malformed/non-object lines without modifying the
log. A missing history uses these legacy defaults. Preserve saved deadlines
even when reason history is unavailable.

### One unavailable transition, then ordinary driver continuation

In `_infra`, ordinary retry exhaustion and disposition `unavailable` share
one transition. Set `state.exhausted[profile] = now + INFRA_COOLDOWN`, using
the existing 900-second constant. Record `unavailable` with `task`, `role`,
`profile`, `until`, `disposition` (the triggering result), and up to 300
characters of diagnostic `detail`, following existing exhaustion logging.
Keep the full existing result/transcript artifacts. Save availability before
notifying or permitting another dispatch. Never increment `ts.attempts`.

`_pick` skips all future deadlines, independent of cause, and returns the
first eligible profile in configured order. Deadline equality is eligible.
Remove expired entries as today. Availability is shared across roles in one
run, like usage exhaustion; counts remain task/profile-specific. A newly
selected fallback starts with its own count, reflected in `ts.infra` when
continuing; keep the exhausted scalar on a single-profile infra pause for
compatibility with existing assertions.

When another profile is usable, emit an FYI through `messages.fyi`:
`X unavailable: <detail>; <role> continues on Y` (use `first_line` to bound
the displayed detail). Return through the existing `_infra` handled-result
path, leaving status running. The next driver iteration retries the same
implementation task, the same `pending_review` candidate, or the same gate.
Do not recursively dispatch an adapter or advance/land work on failure.
Offline local backend failures follow exactly this ordinary infra route.

### All-blocked pauses preserve supervisor policy

Extend `_all_exhausted` to determine the active causes from the latest
`exhausted` or `unavailable` event matching each saved profile deadline.
A saved deadline with no matching event is legacy usage exhaustion. An
expired unavailable event cannot change the reason of a newer quota block.
Use only profiles in the affected role's chain for the earliest deadline.

| Active blocks in this chain | Pause reason | Resume time |
| --- | --- | --- |
| All usage limits | `usage_limit` | Minimum chain deadline |
| At least one unavailable, including mixed quota/infra | `infra` | Minimum chain deadline |

Describe unavailable and usage-limited profiles truthfully in pause detail.
For one unavailable profile retain its failure detail and the existing
`infra` pause message/action. Do not introduce an `unavailable` pause reason
or change `serve.py`; infra resumes still consume the existing two-lift
allowance, even when the earliest block to expire was quota-related.
Cooldown eligibility does not itself lift a pause whose automatic allowance
is spent. Use `messages.py` only if a pause wording adjustment is needed.

Preserve an explicit manual resume's ability to retry a single unavailable
profile before its cooldown: the loop's history recovery recognizes the
existing `resumed` event for that preceding infra pause, clears that profile's
unavailable deadline and resets its pair allowance. Do not clear quota
deadlines or change the CLI. Automatic resumes only clear elapsed cooldowns.
For multi-profile chains, manual resume resets counters for the paused task
but respects the saved availability deadlines. A restart without a resume
does not clear either counters or unavailability.

### Specs, documentation and concurrent ownership

Add the new runtime requirements as `provider-fallback` and the guidance as
an addition to `specloop-documentation`. Its existing Documentation integrity
wording literally forbids any code/test change; qualify that restriction to
documentation-only refreshes, retaining the full existing scenarios and
guarantees for such refreshes. Also permit a verified tracking-issue citation
until an implementation PR identity exists, avoiding fabricated PR numbers;
apply each change's own readability checklist while retaining #67's named
reader and ledger checks for documentation refreshes. These qualifications
are confined to the same modified requirement. All
other canonical requirements remain intact, including status read-only
behavior, automatic-resume budgets, attention and touchpoint classification.

The only necessary concurrent overlap is `docs/specloop/README.md`, also used
by #76. Replace only the paragraph beginning `**Usage limits**` near the
provider section; locate it by content, not the issue's obsolete line number.
Explain immediate auth/plan fallback, ordinary infra retries, 15-minute
cooldowns, per-task/profile budgets, earliest retry and the unchanged infra
resume cap. Keep #76's arming/front-door prose untouched and reapply only this
paragraph if the file moves. Link issue #72; do not invent an implementation
PR number or claim live-provider validation.

Do not edit `KNOWN-LIMITATIONS.md`, root `README.md`, `docs/arming.md`,
`docs/landing.md`, `nightshift/arm.py`, `nightshift/cli.py`,
`nightshift/land.py`, `pyproject.toml`, `specloop/cli.py`,
`specloop/critic.py`, `tests/test_arm.py`, or `tests/test_land.py`.
Also do not edit `specloop/manifest.py`, `specloop/state.py`,
`specloop/serve.py`, or any existing test file. Add tests in the new files
named in tasks. `agents/__init__.py` remains comment-only as specified above.

### Validation and human checklist

Use deterministic fake clocks, temporary `Run` state, synthetic transcripts
and scripted adapters. Exercise `_infra`, `_pick`, `_all_exhausted` and the
existing driver continuation paths, including pending review and section
gate state. Tests must cover restart with an active cooldown and partially
spent allowance, shared profiles across roles, independent tasks, quota-only
and mixed chains, exact deadlines and the existing supervisor lift cap.
Run the new tests and the existing adapter/loop/serve/message/status/attention
tests, then require a green `uv run pytest` without weakening assertions.
Run the existing type checker for runtime edits. No new test needs a live
provider, host UI or running service; existing full-suite sandbox tests still
need the repository's supported macOS environment.

Human checklist for the implementation PR (never implementing-agent tasks):

- At the first naturally occurring lapsed provider login, inspect its actual
  retained diagnostic and `events.jsonl`. Confirm an `unavailable` event with
  the failing profile/detail and a subsequent call on the next usable profile,
  without repeated infra retries. Synthetic fixtures cannot prove the CLI's
  real error wording; record this check as pending until observed. If it falls
  through to infra, retain a sanitized fixture for a follow-up matcher fix.
- Read the changed provider paragraph once against the actual fallback and
  pause messages. Check the cited issue link and any eventual implementation
  PR citation; retain #67's reference/readability checks for docs refreshes.

## Risks / Trade-offs

- Provider wording changes. Narrow diagnostic matching and negative fixtures
  reduce false positives; generic failures still fall back after their retry
  budget even when immediate classification misses them.
- The fixed 15-minute cooldown can delay recovery after credentials are fixed
  and can cause periodic retries of a still-broken login. It reuses policy
  already present and requires no configuration expansion.
- Mixed chains use the capped infra pause policy to avoid turning an offline
  provider into unlimited automatic retries. A healthy quota reset can still
  require human resume once that cap is spent.
- Event-derived counters/reasons avoid an out-of-scope state-schema change.
  Missing legacy history cannot reconstruct exact per-profile attribution;
  the explicit defaults may grant an extra retry. State/event writes retain
  the repository's existing non-transactional crash boundary; this change
  does not promise exactly-once notifications or repair historical logs.
- The provider paragraph overlaps #76's guide file. Confining the edit to
  that paragraph and preserving every other section keeps reconciliation small.

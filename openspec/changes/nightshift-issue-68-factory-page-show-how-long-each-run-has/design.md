## Context

`specloop/cli.py::_status_entry` builds pause JSON from state and supervisor
policy. `Run.event` already records `at` as epoch seconds in `events.jsonl`;
`Run.pause` appends an event after saving state. `_context_recent` produces a
limited, formatted event list, unsuitable as the timestamp source. Synthetic
status-error rows also contain non-null pauses.

`shared/factory.ts::factoryRunSchema` currently omits pause metadata, and
`server/factory.ts::createFactory` validates with that schema before explicitly
projecting display fields. Both stages must carry the two new fields. The user
approved this minimal server extension and requires its tests in a new file.

Factory already refreshes on mount, every 60 seconds while active, on foreground
return, and manually. The server shares requests and caches them for 15 seconds.
The existing status sentence has exact text and structural assertions, and
Factory preserves action notices, confirmations, navigation, and folded finished
runs. No `AGENTS.md` was present in the checkout or inspected ancestor paths;
README and the referenced repository guidance were consulted.

## Goals / Non-Goals

Make elapsed human waits visible with deterministic, host-independent tests.
Retain version-1 compatibility, read-only status, validation, accessibility,
existing text, theme tokens, and every current Factory interaction.

Do not alter pause creation, recovery budgets, stall alerts, daemon/intake,
state persistence, non-JSON status, or deployment. Do not accumulate separate
human-only intervals: the requested duration starts at the latest pause event,
even when that pause initially had automatic recovery available.

## Decisions

1. **Timestamp lookup stays in JSON status.** Add a private helper in
   `specloop/cli.py` that reads the selected run's `events.jsonl` and finds the
   last object whose `event` equals `pause`, in append order (not maximum `at`).
   Return its finite numeric `at` in seconds, including fractions and zero;
   booleans and strings are not timestamps. Ignore malformed JSON lines and
   non-object records. If the latest recognizable pause has an invalid/missing
   timestamp, return null rather than borrowing an older pause's time. Missing,
   empty, unreadable, or undecodable logs also produce null. Do not derive time
   from file mtime, `resume_at`, context events, or the status read clock.
   Invoke this for every non-null pause, including `_status_error_entry` using
   the selected run's path without reconstructing its broken state. A synthetic
   pause gets the latest event timestamp when readable, otherwise null. Preserve
   core per-run error isolation and process-interrupt propagation. Reading status
   never repairs logs, writes state, or creates task state.

2. **Optional, validated display contract.** Extend `factoryRunSchema` with
   `pause?: { since?: number | null; auto_resumes_remaining?: number | null } | null`.
   `since` accepts finite numbers; remaining resumes accepts nonnegative integers
   or null. Both fields and the pause container are optional for older producers.
   Missing values are not defaulted to zero. Retain Zod parsing; invalid supplied
   values fail the read through the current unavailable/stale-cache behavior.
   In `server/factory.ts::project`, explicitly copy only these two properties
   when present, preserving nulls. Omit pause from the projected run when absent
   or when its object has neither display property; preserve an explicit null.
   Do not spread the source pause or forward reason, task, detail, resume time,
   or arbitrary extra fields. Existing PR/URL checks, redaction, command argv,
   action validation, cache generation, throttling, and errors stay intact.

3. **Pure elapsed-time model.** Export
   `factoryWaitingTime(run: FactoryRun, nowMs = Date.now())` from
   `client/factory-model.ts`, returning `{ text: string; warning: boolean } | null`.
   Require `status === 'paused'`, `pause.auto_resumes_remaining === 0`, and finite
   numeric `pause.since`. Null, missing, positive/unlimited budgets, and running,
   done, or aborted statuses return null. Compute
   `elapsedSeconds = Math.max(0, nowMs / 1000 - since)`; a future timestamp shows
   `waiting 0m`. Floor minutes below 3,600 seconds and hours thereafter, without
   day units or mixed units: 59 seconds is `waiting 0m`, 3,599 is `waiting 59m`,
   3,600 is `waiting 1h`, and 18,000 is `waiting 5h`. Warning uses unrounded elapsed
   time strictly greater than 10,800 seconds: exactly three hours stays muted.
   The clock argument makes boundaries testable without sleeping.

4. **Preserve the status sentence and controls.** `FactoryRow` calls the model
   on render. For eligible rows, a wrapping horizontal container places the
   existing status/progress `Text` and a separate waiting `Text` beside one
   another immediately below the title. Preserve the original nested chip and
   exact `<status> · <done>/<total> tasks done` sentence; do not insert waiting
   text inside it, duplicate it, or hide substitute text. Ineligible rows keep
   their existing structure. Waiting text uses current `colors.foregroundMuted`
   or `colors.statusWarning`, with no hard-coded colors and no changed existing
   accessibility labels, roles, or expanded states. It is ordinary readable text,
   not a new control or live alert. Keep task progress muted even for long waits.

5. **Reuse refresh and stale handling.** Recompute on existing renders and
   snapshot refreshes; no extra interval, subscription, RPC, or workspace lookup
   is needed. A new pause timestamp resets the duration; a resumed or finished
   run loses the indicator on the refreshed snapshot. Failed reads retain the
   previous snapshot and existing stale/error indications. Displayed age may
   advance while that retained status remains stale; the badge does not assert
   fresh knowledge of the run. Minute polling is sufficient for this display.

6. **Tests and completion.** Add `tests/test_status_pause_since.py` with
   `test_status_reports_pause_since` and `test_pause_since_is_null_without_events`,
   parameterized for named/all-run queries. Use controlled event timestamps and
   real temporary run fixtures; cover multiple pauses, intervening events,
   malformed records, absent logs, synthetic error rows, unchanged null pauses,
   version 1, and unchanged persisted files. Add
   `plugins/nightshift/tests/factory-pause-projection.test.ts` using injected
   `createFactory` execution/clock, including validation and stale retention.
   Add `plugins/nightshift/tests/factory-waiting-model.test.ts` for exact numeric
   boundaries and `plugins/nightshift/tests/factory-waiting-view.test.ts` using
   the existing style harness for rendered placement, colors, current-theme
   updates, and suppression. Include tests named exactly
   `waiting time shows for human pauses only` and
   `waiting time turns to warning after three hours`. Attach scenario IDs with
   pytest markers and Node subtest names as in existing suites.

   Preserve all existing assertion predicates and checks. Existing Python
   exact-equality expectations necessarily need the additive `since` field;
   treat only these expected-data additions and deterministic timestamp fixture
   setup as the user's permitted fixture changes (including inline expected
   dictionaries in `tests/test_specloop_status_json.py`). The same permitted
   additive expected-data exception applies to the existing synthetic error-row
   test in `plugins/nightshift/tests/factory.test.ts`: its `errorRun` fixture
   already supplies `auto_resumes_remaining: 0`, so add exactly
   `pause: { auto_resumes_remaining: 0 }` to the expected row passed to
   `assert.deepEqual(snapshot.runs[1], ...)`. Keep that exact-equality assertion,
   every existing expected property, and the surrounding redaction, action
   refusal, ordering, and command-call checks. Leave the input fixture unchanged;
   it intentionally has no `since`, so the projected pause must not invent one.
   This expectation migration lands with the projection in task 2.1; all new
   server test cases remain in `factory-pause-projection.test.ts`. Explain in
   the implementation PR that the newly retained display field requires this
   expected-data addition without weakening the assertion. Do not strip the new
   field from actual output, switch equality to subset comparisons, skip tests,
   or relax validation. Explain fixture changes in the implementation PR. All
   new test cases, including server coverage, belong in new files. Completion
   requires `uv run pytest` at repository root and `npm test` plus
   `npm run typecheck` in `plugins/nightshift` to pass with existing assertions
   retained. This draft does not claim those implementation checks have run.

## Risks / Trade-offs

- Epoch seconds must be converted once when compared with JavaScript's
  millisecond clock. Host/client clock skew can distort age; clamping prevents
  negative labels but does not synchronize clocks.
- Reading the complete event log costs linear time per paused run, as the
  existing context reader already does. Avoid adding storage or daemon work for
  this small display feature. Append races can briefly yield null until the next
  refresh; malformed trailing records must not break status.
- The three-hour display threshold is fixed here to the requested stall-alert
  threshold, without coupling the client to the watcher or changing its policy.
  Exactly three hours is muted; warning starts immediately after it.
- Synthetic status-error rows with a readable pause event can show a wait using
  their existing zero budget; their error context and lack of actions remain.
- CI can prove data flow, exact text, styles, theme changes, and interactions
  without the host app. It cannot prove native text wrapping, visual balance, or
  contrast on real screens. **PR human checklist for Matt after deploy:** inspect
  short and over-three-hour waits on desktop and phone, including a long change
  name and narrow layout; confirm the wait sits beside the chip/progress area,
  wraps legibly, and remains readable in the active themes. These checks are not
  implementing-agent tasks and deployment is outside this change.

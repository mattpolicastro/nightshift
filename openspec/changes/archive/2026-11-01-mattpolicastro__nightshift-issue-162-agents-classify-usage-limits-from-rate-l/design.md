## Context

`specloop/agents/claude.py:run` builds `json.dumps(final)[:800] + stderr` and
searches it with `USAGE`. That includes agent-authored `result` and
`structured_output`, and even unrelated numbers. `nightshift/trace.py:parse`
already maps `rate_limit_info` into `RateLimit(status, type, resets_at,
using_overage)`, but returns no result without a terminal event. Calling that
whole parser would miss limits in interrupted/incomplete transcripts.

`specloop/agents/__init__.py:Result` currently carries no rate-limit metadata.
`specloop/loop.py:_infra` calls `quota.resume_at(None)` for Claude, stores the
deadline in `state.exhausted`, and tries the next profile. Only Codex currently
supplies reset times. `serve.AUTO_RESUME` already leaves unrecognized pause
reasons for humans, so an overage hold needs no supervisor change.

Existing provider-fallback specs promise unchanged broad usage matching and
all-blocked-only pauses. Those two promises need explicit MODIFIED deltas.
Authentication, retry counters, candidate retention, Codex logins, cooldowns,
ordinary fallback and automatic-resume limits remain required. No AGENTS.md
was present in the checkout or searched ancestor directories.

## Goals / Non-Goals

Use provider telemetry to distinguish an actual quota limit from agent prose,
wait for the reported reset, and stop further admitted work after observed
overage. Make classification, persistence and recovery testable with local
JSONL fixtures and a fake clock.

Do not predict quota, query Claude services, kill a live session on telemetry,
prevent charges already incurred, or add automatic billing opt-in. Do not
change Codex, authentication regex sources, the persisted state schema,
supervisor recovery policy or publication behavior. In particular, do not
touch `specloop/loop.py:_fetch_publish_base` or nearby #174 changes.

## Decisions

1. **Share the event decoder, not the terminal-result parser.** Extract
   `nightshift.trace.parse_rate_limit(event: dict) -> RateLimit | None` from
   the existing `rate_limit_event` branch and call it from both `trace.parse`
   and Claude's existing JSONL loop. Changes to trace are confined to this
   decoder and its call site. Ignore non-event/non-mapping payloads without
   crashing. Missing or wrongly typed status/window become `unknown`; missing
   or invalid reset becomes zero; only literal boolean `true` means overage.
   A usable reset is a positive integer Unix timestamp, excluding booleans;
   strings, fractional numbers and non-finite values are invalid. Preserve
   historical parsing for valid fixtures and `trace.parse` returning None
   without a terminal result. Last decoded rate-limit event in stream order
   supplies the snapshot, even if it follows the terminal result; malformed
   non-mapping payloads do not erase a previous snapshot.

2. **Carry metadata without disturbing existing callers.** Append optional
   `rate_limit: RateLimit | None = None` to `specloop.agents.Result`. Preserve
   positional construction and existing fields. Carry it through Claude
   returns and through the shared schema-validation result replacement. Limit
   edits in `agents/__init__.py` to this field/import and propagation.

3. **Define classification precedence explicitly.** Apply the following order
   in Claude's adapter using the selected snapshot:

   | Signal | Outcome |
   | --- | --- |
   | `using_overage` is true | `usage_limit`, including successful or timed-out calls; driver applies the overage hold |
   | Wall/idle timeout without overage | Existing `infra` timeout; retain telemetry |
   | Status is `rejected` | `usage_limit`, including absent terminal output or apparent success |
   | Status is `allowed` or `allowed_warning` | No usage-regex classification; ordinary success, invalid-output, auth or infra rules apply |
   | No event or unrecognized status | On failed runs only, usage regex over stderr and provider `errors`; otherwise existing outcome rules |

   `allowed_warning` alone is advisory, not exhaustion. Unknown statuses do
   not imply rejection. Provider errors here mean string entries in the final
   `errors` list, or a scalar string `errors`; do not serialize arbitrary
   objects or include `result`, `structured_output`, token counts, session IDs
   or tool messages. Keep the existing stderr tail bound and `USAGE` patterns.
   Preserve the existing bounded detail text for diagnostics, but do not use
   it as the usage matcher input. Keep `UNAVAILABLE` matching unchanged. When
   fallback is applicable, a usage match still outranks an authentication match.

4. **Use the existing quota interface and persistence.** Extend
   `quota.resume_at(limits, now=None, *, rate_limit=None)` with the optional
   normalized Claude snapshot. For a future valid `resets_at`, return
   `resets_at + 60`; for an absent, invalid, equal-to-now or past reset return
   `now + FALLBACK_SECONDS`. Use `now is None`, so an injected zero clock works.
   A supplied Claude snapshot takes precedence over the Codex payload; calls
   without it preserve all Codex behavior. In `_infra`, pass the keyword only
   for results carrying telemetry; retain the old call shape otherwise.
   Continue persisting ordinary limits in `state.exhausted`, recording
   `exhausted`, selecting the first usable chain profile, and using the earliest
   affected-chain deadline for an all-blocked pause. Resume after restart uses
   that saved deadline, not a recomputed 30-minute wait.

5. **Overage is a human hold at the result boundary.** In `_infra`, handle
   `res.rate_limit.using_overage` before ordinary usage-limit fallback. Record
   an `overage` event, then `ctx.pause("overage", ..., resume_at=0)` with the
   affected task/section, role and profile in the diagnostic. Do not dispatch a
   successor, consume an infra retry/coding attempt, accept output, or replace
   pending review work. Leave unrelated profile deadlines intact. Normal
   pause persistence and the supervisor's existing policy keep the hold across
   restart and clock advancement. After inspecting billing settings, a human
   can use existing resume behavior; another overage report pauses again.
   This governs implementation, candidate review and section-gate review,
   whose results already pass through `_infra`.

6. **Make the evidence inspectable.** Add a `rate_limit` mapping containing
   normalized `status`, `type`, `resets_at` and `using_overage` to structured
   `exhausted` and `overage` events. Keep `exhausted.until` as the actual chosen
   deadline and existing detail/session/usage evidence. Legacy/no-event records
   keep their existing shape. No state migration or new status schema is needed.

7. **Preserve assertions through a narrow fixture migration.**
   `tests/test_provider_unavailable_adapters.py:test_usage_matching_retains_priority`
   currently labels Claude's final `result` prose as a provider error. For its
   Claude response cases only, feed the same diagnostic through the fixture's
   existing `alternate` source (`errors`). Retain every disposition, session,
   usage, output and detail assertion and all parameter combinations. Do not
   globally change the fixture or migrate authentication tests. Add the
   negative prose test separately. Existing fallback/cooldown tests remain
   unchanged; add structured tests alongside them. Required new names are
   `test_agent_text_mentioning_quota_is_not_usage_limit`,
   `test_rate_limit_event_reset_time_drives_wait`, and `test_overage_pauses_run`.

8. **Keep docs changes local and mechanically bounded.** Qualify the current
   Usage limits paragraph's all-blocked statement as ordinary recovery except
   overage; retain its existing facts, phrases tested by assertions and issue
   #72 citation. Explain the new contract in an adjacent paragraph beginning
   `**Claude quota telemetry**`, citing issue #162. Correct the quota bullet in
   KNOWN-LIMITATIONS.md: observation still occurs after a session, not ahead of
   time, and live validation remains pending. Root README needs a change only
   if an existing passage becomes false. For new documentation checks, select
   the unique column-zero `**Claude quota telemetry**` prefix through the next
   blank physical line or EOF. Missing/duplicate starts or an intervening
   column-zero heading (`^#{1,6} +\S`) block validation. Treat fences, HTML and
   indentation as literal text, never semantic contexts. Check the issue link
   and nonempty passage, not exact prose; retain existing documentation tests.

Validation uses synthetic CLI transcripts, mocked spawn, temporary run state
and a fixed clock; no provider login, host application or real sleep is needed.
Parameterize advisory/rejected/unknown statuses, missing final output, event
order, malformed fields, prose/metadata false positives, fallback diagnostics,
future/stale resets, ordinary profile chains and overage with a usable fallback.
Exercise the real adapter-to-driver path for the reset and overage regressions,
including saved state/events, reload and `serve.lift_due`. Run `uv run pytest`
and `uv run python scripts/check_types.py`; preserve existing assertions and
do not expand the type-error baseline.

Human checklist for the implementation PR (not implementing-agent tasks):

- At the next naturally occurring Claude limit, compare its retained
  `rate_limit_event` with the run's classification, `exhausted.rate_limit`,
  `until` and pause/resume events. Confirm the timestamp plus 60-second grace
  explains the wait, or the documented fallback explains missing/stale data.
- If overage occurs naturally, confirm an `overage` pause and no subsequent
  automatic call. Do not enable paid overage just to exercise this check.
- Read the changed quota guidance and check the issue #162 reference. Keep live
  checks pending until observed; fixtures cannot prove provider event spelling
  or that the CLI emits a rejection at a real limit.

## Risks / Trade-offs

- Only allowed telemetry is present in current recorded fixtures. Rejection and
  warning status fixtures define the intended contract; real telemetry may
  require a follow-up decoder change. Unknown statuses use diagnostic fallback.
- Last-event selection matches the existing trace parser; it does not aggregate
  multiple simultaneous windows or reconstruct earlier overage episodes. A
  later snapshot can supersede an earlier one. Extending that policy is separate.
- A reset requires an accurate wall clock; stale or malformed resets use the
  fallback to avoid immediate retry loops. The one-minute grace intentionally
  delays retry slightly beyond the provider timestamp, matching Codex policy.
- A completed session can already have spent overage before the harness pauses.
  This change prevents subsequent automatic dispatch, not prior billing.
- Parser reuse couples specloop to an existing Nightshift type, already within
  the same package. Keep the shared extraction small and historical fixtures
  intact. No change to trace's unrelated progress, verdict or cost accounting.
- #174 also edits loop.py. Keep all loop changes within `_infra` and narrowly
  necessary metadata handling; no publication refactors or whole-file cleanup.

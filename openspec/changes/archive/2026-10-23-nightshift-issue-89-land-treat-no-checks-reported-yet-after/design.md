## Context

`nightshift/land.py:ci_is_green` parses stdout before classifying `gh pr checks` exit codes. The legitimate post-push response has no JSON, causing an immediate refusal. `wait_for_ci` already runs head read → checks query → head read → classification → bounded sleep. `Operations` caps commands and sleeps against one absolute deadline, and `continue_landing` starts that budget after finish, retaining it across up to three CI rounds.

The existing `reviewed-pr-landing` specification requires at least one pass, otherwise only pass/skipping, stable inputs, validated branch updates, and a head-conditioned merge. These controls remain unchanged. Existing tests also assert the generic deadline message for JSON `[]`, pending checks, and all-skipped checks; preserve those assertions.

Issue #78 is the prerequisite and is archived in this checkout. No `AGENTS.md` exists in this checkout or its ancestor directories; `README.md`, `CLAUDE.md`, the relevant code/tests, and `openspec/specs/reviewed-pr-landing/spec.md` informed this design.

## Goals / Non-Goals

Handle the observed GitHub registration delay as pending, retain the shared deadline and head checks, and explain persistent absence of checks with the expected head and timeout. Exercise behavior offline through the existing injectable process runner and clock.

Do not broaden retries for malformed JSON or command failures, accept missing checks as green, change default timing, refactor landing, or touch provider selection. Implementation changes are limited to `nightshift/land.py` and the new `tests/test_land_no_checks.py`. Specifically avoid edits to `tests/test_land.py`, `README.md`, `KNOWN-LIMITATIONS.md`, `docs/landing.md`, and `pyproject.toml`; all other files outside this change's OpenSpec artifacts are also out of scope.

## Decisions

1. **Use an exact, shared predicate.** In `nightshift/land.py`, recognize `result.returncode == 1`, `result.stdout == ""`, and the case-sensitive substring `"no checks reported"` in `(result.stderr or "")`. A small private helper may share this predicate between `ci_is_green` and `wait_for_ci`. Keep `ci_is_green`'s boolean interface: return `False` for this response before JSON decoding. Whitespace-only stdout, other exit codes, unrelated stderr, and any nonempty stdout take their current validation paths. The branch name and surrounding stderr text need not match a template.

2. **Preserve query ordering and budgets.** Keep both head reads around each poll in their current positions. Do not sleep or return early on the special response before the second read. Continue using `Operations.poll_sleep`, `CI_POLL_INTERVAL = 10`, and the existing absolute deadline; no new grace period, deadline reset, or extra poll after expiry. All head, draft, metadata, command, and non-registration validation failures retain their current refusal paths.

3. **Remember registration state locally.** `wait_for_ci` tracks whether the most recent successfully classified, head-bracketed poll was the special response. Reset that state when a subsequent ordinary check response is successfully classified. State is local to the expected head's wait, never inherited by another round. Thus missing → pending → deadline retains the normal pending timeout; ordinary JSON `[]` and skipped-only responses retain their existing treatment. Missing → malformed or failing checks refuses immediately.

4. **Specialize only the deadline refusal.** When the shared deadline expires while the last established poll is the special response, report `no CI checks were reported for head <expected_head> within <timeout>s; inspect checks and rerun land`. Use the full SHA and configured total CI timeout in seconds, formatted with `:g`, not the last sleep or remaining budget. Allow an optional keyword-only `timeout: float | None = None` on `wait_for_ci`; `continue_landing` passes its existing `timeout` value. Existing three-argument helper callers remain valid; when omitted, capture the available deadline budget at helper entry for diagnostic purposes only. This small call-site change is the only controller change. Never translate arbitrary `Refusal` exceptions, head changes, malformed results, or subprocess timeouts into missing-check messages. Only expiry of the existing shared deadline after an established missing-check poll qualifies; expiry before any such poll keeps the existing diagnosis.

5. **Keep tests isolated from concurrent edits.** Add `tests/test_land_no_checks.py`, reusing selected helpers from `tests/test_land.py` read-only where useful, without moving fixtures or weakening assertions. Include `test_land_waits_while_no_checks_are_reported_yet` and `test_land_refuses_when_no_checks_appear_before_deadline`. Drive the real landing entry point with a scripted runner and fake clock, including a front-door finish that advances the expected head. The success case must wait through absent checks and pending checks, then reach a confirmed merge only after green checks and existing gates. The deadline case uses 25 seconds: three bracketed polls, sleeps `[10, 10, 5]`, no fourth query or merge, and the finished head in the refusal. Add compact parameterized boundary cases for each predicate conjunct, head drift before/after a query and on the next poll, and transition back to ordinary pending timeout behavior. Verify that prior budget consumption and another CI round never extend the deadline or carry stale registration state. Use scenario markers for this delta.

6. **Acceptance is automated except real timing.** Run focused tests for both landing modules, then `uv run pytest`; existing assertions must all pass. Run `uv run python scripts/check_types.py` without updating its baseline. No test uses live GitHub, real sleeps, or a running daemon.

## Risks / Trade-offs

- Matching an English CLI diagnostic is intentionally narrow. Changed or localized wording still fails safely; expanding the matcher requires separate evidence.
- Retrying absence can delay discovering a missing CI workflow until the existing timeout. No checks can authorize a merge, and no additional waiting budget is introduced.
- Deadline handling must not hide head drift or transport failures. Tests must assert refusal reasons as well as absence of merge calls.
- `nightshift/land.py` overlaps issue #72. Keep edits local to CI classification/waiting and timeout argument forwarding, preserving any concurrent provider work. New tests avoid editing the shared test module.
- Human checklist for the resulting PR: on the next real `land` of a front-door PR, observe the post-finish archive push and confirm the registration window is polled without a malformed-JSON refusal, with checks for the new head passing before merge. Record whether the window was actually observed; a run where checks were already registered does not prove this timing behavior. This live-service observation is not an implementation task.

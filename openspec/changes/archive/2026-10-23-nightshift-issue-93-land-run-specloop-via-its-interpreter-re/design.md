## Context

`nightshift/land.py:discover_run` invokes `["specloop", "status", "--json"]`, and `finish_run` invokes `["specloop", "finish", current.change]`. Both can fail under a directly invoked virtual-environment interpreter even though that interpreter can import specloop. `specloop/serve.py` already uses `sys.executable` with `-m specloop.cli`.

`update_branch` issues an expected-head REST PUT and observes the remote head. It already checks the commit SHA and exactly ordered parents `[previous_head, pre_request_main]`. Its nonzero-response classifier recognizes transport-loss phrases but misses `unexpected end of JSON input`. `Operations` caps each call and sleep against a shared absolute CI/update deadline; its default duration is 1800 seconds. Issue #89's CI registration exception is separate and remains unchanged.

No `AGENTS.md` exists in this checkout or its ancestor directories. `README.md`, `CLAUDE.md`, the landing code and tests, the operator guide, and the current `reviewed-pr-landing` requirements informed this design.

## Goals / Non-Goals

Remove landing's dependency on the specloop console script, reconcile the reported parser-error response safely, and refuse definite authentication, permission and HTTP failures promptly. Keep every existing landing requirement, assertion and recovery path unless an argv expectation directly reflects the removed console-script invocation.

Limit implementation to `nightshift/land.py`, new `tests/test_land_followups.py`, and the prerequisites passage of `docs/landing.md`. Permit only obsolete specloop argv expectations in `tests/test_land.py` to change. Do not edit any other file, including `tests/test_land_no_checks.py`, `README.md`, `KNOWN-LIMITATIONS.md`, `docs/specloop/README.md`, `nightshift/config.py`, `nightshift/daemon.py`, `nightshift/specloop_intake.py`, `nightshift/touchpoints.py`, `specloop/agents/`, `specloop/loop.py`, `specloop/messages.py`, or their existing tests. No general process-runner refactor, provider change, new retry policy, new CLI flag or change to specloop's own interface is needed.

## Decisions

1. **Use the running interpreter at both call sites.** Discovery uses `[sys.executable, "-m", "specloop.cli", "status", "--json"]`; finish uses `[sys.executable, "-m", "specloop.cli", "finish", current.change]`. Keep the injectable runner, captured text, 30-second read limit, 300-second finish limit, and remaining-budget caps. Do not search PATH for specloop, fall back to its console script, activate a virtual environment or install packages. Import/CLI failures use the existing stage-specific refusal paths. Discovery remains mandatory even for direct PRs; done runs and direct PRs still skip finish.

2. **Correct the prerequisite where operators read it.** Update only the opening prerequisites passage of `docs/landing.md` to say the interpreter running `nightshift.land` must import `specloop.cli` and have access to the applicable run environment. No specloop console script or virtual-environment activation is needed on PATH. Preserve `gh` availability/authentication, configuration/enrollment requirements and the documented `uv` launch examples; this change does not remove those tools' requirements. The daemon's separate PATH guidance in other documents is outside scope.

3. **Classify the PUT result locally, with refusal precedence.** Keep stdout and stderr separate, normalizing absent streams to empty strings. Examine completed nonzero results in `update_branch`; if needed, let `Operations.run` accept `allowed_returncodes=None` for this PUT alone, leaving its default `(0,)`, all other call-site policies and deadline checks unchanged. Apply these rules in order:

   | Result evidence | Action |
   | --- | --- |
   | Stderr contains an HTTP status, including `(HTTP 403)`, `(HTTP 409)`, `(HTTP 422)` or `HTTP 503` | Refuse immediately, even if the same diagnostic contains the parser-error or transport-loss wording. Match `HTTP` case-insensitively, an optional version such as `/1.1`, whitespace and a three-digit status in 100–599. |
   | A stream is a JSON error document | Refuse immediately, even alongside the parser-error wording. Parse stdout and, independently, stderr when it is a complete JSON document. Conservatively refuse any successfully decoded JSON document on a nonzero exit; no error-message vocabulary or schema is needed to authorize waiting. |
   | A known authentication exit (gh exit 4) | Refuse immediately, including when diagnostics are absent. |
   | Stderr contains the case-sensitive literal `unexpected end of JSON input` | Mark uncertain and enter existing observation, provided no refusal above applies. Stdout may be empty or truncated/unparseable. |
   | Both stdout and stderr are exactly empty | Mark uncertain and enter existing observation. Whitespace-only output is not this exception. |
   | Existing recognized transport-loss diagnostic (`EOF`, connection reset/lost, broken pipe, timed out/timeout) | Preserve existing reconciliation, subject to the refusal precedence above. |
   | Anything else | Refuse immediately with the existing branch-update stage, diagnostic and exit code. |

   This interprets the new empty-output exception as a silent response, not permission to ignore nonempty stderr when stdout is empty. In particular, `permission denied`, `authentication required`, login instructions and unknown plain-text failures do not enter polling. Existing transport diagnostics remain a separate compatibility case because current tests and lost-response requirements protect them. Do not generalize these exceptions to other GitHub calls or CI JSON parsing. Successful PUT results retain their existing observation behavior; an acknowledgement never proves the new head safe. Existing subprocess-exception reconciliation remains intact.

4. **Reuse reconciliation without repeating the mutation.** Keep `expected_head_sha=H`, with H refreshed before the request and main B read immediately before it. For an uncertain result, remember the original failure and enter the existing observation loop. The first changed head must have a readable matching commit SHA and exactly two parents `[H, B]` in that order. A descendant, reversed/additional parents, a different base, malformed metadata, draft/closed PR or failed read refuses. Unchanged heads sleep through `Operations.poll_sleep`; calls, parent inspection and sleeps use the original deadline, with no reset or extra grace period. Refusal retains the original error and reconciliation cause. Do not issue another PUT to resolve this result. A later normal CI round may still request its own update if main moves, under the existing three-round limit. An accepted head still needs fresh head-bracketed CI and all final checks before a head-conditioned merge.

5. **Use offline regression tests with the real landing gates.** New tests belong in `tests/test_land_followups.py`. Reuse the scripted runner, fake clock and selected helpers from `tests/test_land.py` without moving fixtures or introducing live subprocess fallbacks. Update its `STATUS` and `FINISH` command constants (and only any directly invalidated argv expectations) to the interpreter form; retain every assertion, scenario and negative case. Include these exact test names:

   - `test_land_runs_specloop_through_its_interpreter`: with PATH lacking a specloop executable, assert exact interpreter argv for every status call and the one eligible finish, and retain discovery/finish failure gates.
   - `test_land_reconciles_branch_update_with_unparseable_response`: model an accepted PUT returning nonzero with the literal parser diagnostic, observe an unchanged head then the exact merge, require fresh CI and all gates, and confirm a head-conditioned merge with exactly one PUT for that update. Parameterize empty/truncated stdout and a silent empty response.
   - `test_land_refuses_branch_update_json_error`: exercise expected-head mismatch and conflict JSON, including stderr with the parser-error phrase; assert immediate refusal, no observation/sleep, no second PUT and no merge.
   - `test_land_refuses_branch_update_http_error_without_waiting`: parameterize 403/409/422 and a diagnostic combining HTTP status with the parser-error phrase; assert immediate refusal and zero observation/sleep/merge after the PUT.

   Add compact boundary cases for plain-text permission/authentication errors, exit 4 with empty streams, unknown malformed output, whitespace-only empty near-misses, exact-parent rejection and exhausted shared budget. Exercise parser-error and silent-result recognition with nonzero exits beyond 1, excluding the known authentication code. Preserve and run the existing transport-loss and #89 tests. Mark new tests with the scenario IDs below.

6. **Acceptance.** Run `uv run pytest tests/test_land.py tests/test_land_no_checks.py tests/test_land_followups.py`, then `uv run pytest`, and `uv run python scripts/check_types.py` without modifying its baseline. Existing assertions must remain effective. Inspect the implementation diff for the file and documentation-section limits. No test needs a running daemon, real sleeps or network access.

## Risks / Trade-offs

- The parser diagnostic is a narrow English-string contract. Different gh wording can still refuse safely; do not expand to arbitrary malformed output.
- A truly silent failed request or recognized transport failure may consume the remaining default 30-minute budget without a changed head. Explicit JSON, HTTP, authentication and permission failures refuse promptly. Exact provenance checks and no repeat PUT preserve safety during uncertainty.
- JSON refusal precedence must inspect stdout even when stderr contains the parser phrase; inspecting only the preferred diagnostic would recreate the unsafe broad classification.
- The same interpreter must actually import specloop and see the intended run environment. This change removes console-script lookup, not package or run availability requirements.
- Offline tests prove argv, classification, ordering and guards, but cannot establish real gh output, GitHub's update parent shape, timing or protections.

Human checklist for the implementation PR (not implementing-agent tasks):

- [ ] On the next reviewed PR needing a branch update, run landing with an interpreter that imports `specloop.cli` while the specloop console script is absent from PATH. Observe discovery and eligible finish.
- [ ] Confirm the update is accepted without an unnecessary refusal; if the parser-error response occurs, confirm reconciliation observes exactly `[previous_head, pre_request_main]`, sends no replacement PUT, and requires fresh CI before the guarded merge. Record whether that response was actually observed; an ordinary successful response does not validate the real parser-error case.
- [ ] Record the real gh/GitHub outcome and any permission or protection restrictions in the review record. A green offline suite does not complete these checks.

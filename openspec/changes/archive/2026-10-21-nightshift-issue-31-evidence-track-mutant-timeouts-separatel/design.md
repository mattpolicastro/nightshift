## Context

The issue describes two pre-#4 paths. In the landed #4 implementation,
`specloop/evidence.py:_mutate_one` uses `verify.run_extracted` for both empty and
nonempty `Manifest.prepare`. The old evidence-local `_run_steps` has been
removed. Both paths currently discard the first failing receipt's details and
keep only its id, mapping every selected test failure to `killed`.

`specloop/verify.py:Receipt.exit` is already `None` for a timeout and an integer
for an actual exit, including a signal. No verifier change is necessary.
`Evidence.score` already includes only killed and survived; `unmeasured`
already recognizes the absence of killed, survived and equivalent results.
However, `Evidence.summary()` currently excludes only invalid/equivalent
mutants from its caught fraction and describes all unmeasured mutants as
invalid. Those strings need correction when timeout becomes a possible result.

`specloop/loop.py:section_evidence` emits the event, saves a passing summary in
`state.evidence_summary`, and pauses unmeasured evidence with `gate_evidence`.
`_gate_context` passes the saved summary to the reviewer in `context/evidence.md`.
These existing paths provide reporting and the requested pause without a policy
change. The user has explicitly approved adding a field at that event call and
changing the earlier hanging-mutant assertion as well as #4 assertions.

## Goals / Non-Goals

Make timeouts visible and prevent them from earning mutation-score credit,
consistently for prepared/unprepared and serial/parallel runs. Preserve actual
test failures as kills and preserve every unaffected existing requirement.

Do not change mutant selection, generation, worker scheduling, time limits,
process-group handling, sandboxing, verification receipts, gate decisions,
thresholds, retries, equivalence annotations or skip behavior. Do not infer
whether a timeout came from a real infinite loop or contention.

Implementation edits are limited to `specloop/evidence.py`, the single existing
evidence-event call in `specloop/loop.py`, and `tests/test_specloop_evidence.py`.
Do not edit any other `specloop/` file or any other existing test. In particular,
avoid the in-flight files `specloop/cli.py`, `specloop/state.py`,
`tests/test_specloop_act.py`, `tests/test_specloop_background.py`,
`tests/test_specloop_state_reads.py`, `tests/test_specloop_status_json.py`,
`nightshift/cli.py`, `nightshift/daemon.py`, `nightshift/specloop_job.py`,
`plugins/nightshift/client/factory.tsx`,
`plugins/nightshift/tests/factory-style-harness.ts`,
`plugins/nightshift/tests/factory-style.test.ts`, and
`plugins/nightshift/tests/factory.test.ts`.

## Decisions

1. **Classify from the receipt, in precedence order.** Keep `_mutate_one`'s
   signature and string return type. Retain the first non-passing `Receipt`
   rather than only its id. No failing receipt means `survived`. If its id is
   `configure`, `build`, or any manifest prepare-step id, return `invalid`
   regardless of exit. Otherwise `exit is None` means `timeout`; an unexpected
   integer exit, including a signal, remains `killed`. Here a selected test
   means any selected verify step outside that invalid-step set, not only a
   step literally named `test`. Update the documented `Mutant.result` values.
   Leave source mismatch, equivalent annotations and baseline failure handling
   intact. Do not reintroduce `_run_steps` or catch unrelated worker errors as
   timeouts. Retain logs, receipts and artifacts through existing cleanup.

2. **Score and unmeasured semantics.** Keep the existing explicit killed/survived
   filter in `Evidence.score`; use the same population for the summary's caught
   fraction. With one kill, one survivor, two timeouts, one invalid and one
   equivalent, the score and displayed fraction are `0.5` and `1/2`; the
   timeout line is `2 of 6 mutants timed out and were not counted`. One survivor
   plus timeouts scores zero. Generated mutants consisting only of timeouts,
   or a mix of timeouts and invalids, have score `None`, are unmeasured and fail
   `passed()` even at minimum zero. Preserve the current equivalent exception:
   equivalent results count as judged for `unmeasured`, but never enter the
   score; timeouts plus an equivalent are not unmeasured solely because no
   killed/survived results exist. Coverage and baseline failure still veto a
   pass. Empty/skipped evidence retains its current behavior.

3. **Report without changing policy.** Add no stored Evidence field or new JSON
   envelope. `gather`'s existing dataclass serialization writes the new per-mutant
   result and existing nullable score. Add
   `timeouts=sum(x.result == "timeout" for x in ev.mutants)` only to the existing
   `ctx.run.event("evidence", ...)` call; emit zero as well as positive counts.
   Preserve every existing event field. Count mutants, not steps, and exclude
   prepare/configure/build and baseline timeouts from this field. In `summary`,
   append the exact line `N of M mutants timed out and were not counted` once
   when N is positive, regardless of whether any score exists. M is
   `len(ev.mutants)`, including invalid and equivalent results. With no timeouts,
   do not add that line. For unmeasured evidence containing timeouts, describe
   the mutants as invalid or timed out, retaining the explanation that nothing
   was measured; preserve the existing all-invalid wording when N is zero.
   Preserve survivor details and equivalent counts. Let existing summary
   storage, reviewer context and pause detail transport these strings unchanged.

4. **Tests and exact existing-test exceptions.** Add new tests only in
   `tests/test_specloop_evidence.py`, using `pytest.mark.scenario` for the delta
   scenarios. Required names are `test_mutant_timeout_is_recorded_not_killed`
   (parameterized for prepared/unprepared), `test_timeouts_do_not_raise_the_score`
   and `test_all_timeouts_is_unmeasured`. Exercise actual gather serialization
   and bounded worker execution at concurrency 1 and 2, with stable ordering;
   use deterministic receipt fixtures for scoring and event cases. Include
   passing, unexpected integer-exit and signal controls, invalid preparation/
   build timeouts, mixed invalid/timeouts, and equivalent/skip preservation.
   Use new tests of `section_evidence` and `_gate_context` with a local temporary
   Run/context and stubbed gather/irrelevant extraction boundaries to verify
   event counts, saved reviewer text and the unchanged unmeasured pause. The
   all-timeout case must pause without adding strengthening tasks. Do not edit
   `tests/test_specloop_loop.py` or shared fixtures.

   These are the only existing tests authorized to change. Limit edits to
   timeout result expectations and immediately necessary expectation variables;
   preserve timing, receipts, process-group, cleanup and other assertions.
   Carry each explanation into the implementation PR description:

   | Existing test | Authorized change and PR explanation |
   | --- | --- |
   | `test_a_hanging_mutant_is_caught_by_a_scaled_timeout` | The `i += 2` mutant changes from killed to timeout because exceeding the scaled test limit is unjudged. Keep the elapsed-time bound proving the limit still works. This predates #4 and is explicitly approved. |
   | `test_real_mutant_step_timeout_stops_its_process_group` | Only the timed-out `test` result becomes timeout; the unexpected test exit stays killed, and dependencies/configure/build results stay invalid. #4 conflated the two failure kinds. |
   | `test_concurrent_private_workers_release_processes_and_trees` | The unprepared timed-out selected verify step becomes timeout; its ordinary failure stays killed. The prepared timeout remains invalid because the exercised step is preparation. Preserve resource assertions. |
   | `test_parallel_mutants_respect_time_box` | Expect three timeouts, one killed and one survived instead of four killed and one survived. Preserve exact budgets, queue-wait independence, overlap and cleanup checks. |
   | `test_serial_workers_clean_up_and_retain_diagnostics` | Expect survived, killed, timeout in both prepare modes. The final test exceeds its limit; diagnostic retention and cleanup remain unchanged. |

5. **Requirement compatibility and validation.** The delta retains all scenarios
   in Stable evidence results and Baseline step time boxes, modifying only the
   timeout classification contract and making that exception explicit. It
   preserves concurrency, baseline-first execution, private workspaces, cleanup,
   duration fallback and preparation-invalid behavior. `verify-preparation` and
   `specloop-interface` need no deltas. Run the focused evidence tests with each
   implementation task and require `uv run pytest` green at completion. Do not
   relax unrelated tests to achieve that result. The unchanged tests already
   cover retained scenarios; new timeout scenarios need their own markers.

   No daemon, GitHub, Paseo or live host is needed for behavioral tests. Real
   short subprocess timeout fixtures exercise the executor on the supported
   macOS sandbox; receipt-based unit tests do not require sandbox execution.
   Prepare fixtures must use system `/bin/sh` tools rather than an interpreter
   under the host's HOME, following #4's CI correction. Lower timeout floors
   only in fixtures and avoid tight performance ratios.

## Risks / Trade-offs

- A genuine infinite-loop mutant no longer earns kill credit. This is deliberate:
  elapsed time cannot distinguish such a bug from host contention. Results may
  contain fewer judged mutants or pause more often, without changing gate policy.
- Exclusion keeps timeouts from directly contributing credit; the score is still
  conditional on completed judged mutants, not a statistical correction for
  missing outcomes. Preserve the requested killed/(killed + survived) formula.
- Consumers of mutant results must tolerate the new `timeout` string. Evidence
  events add one field; historical artifacts are not rewritten and missing
  historical counts must not be treated as measured zero during human review.
- **Human checklist for the implementation PR:** over the next few real gates
  with `serve` running three builds and `mutation_parallel = 2`, inspect the
  `timeouts` counts in new evidence events. Record observed counts alongside
  gate/section identifiers, correlate nonzero values with per-mutant results
  and summary text, and note any unmeasured pauses. CI can prove classification
  and reporting but cannot establish how often contention causes timeouts on
  that host. This observation is a human follow-up, not an implementation task
  or a reason to alter scheduling or thresholds in this change.

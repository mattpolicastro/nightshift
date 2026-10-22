## Context

`specloop.tasks.Task.scenarios` extracts any backticked three-component slug. Revision feedback is preserved verbatim inside generated task text, so an ordinary path such as `a/b/c` becomes a candidate scenario. `loop.section_evidence` currently sends those candidates straight to `evidence.gather`; missing coverage then creates tasks for nonexistent scenarios. Implementation and gate-review context also display these candidates as unknown scenarios.

Both `loop._implement_prompt` and `feasibility.check` enumerate only `m.verify`, although the harness executes `m.prepare` first. `cli.main` calls `parse_args` before entering its JSON handling; argparse therefore exits without a document.

The dependency is issue #10, whose archived change and current `specloop-interface` requirements cover isolated run status errors. Build this work on that landed behavior. No `AGENTS.md` exists in this checkout or its ancestors; the repository's `CLAUDE.md`, README, engine documentation, relevant code and existing capability requirements inform this design.

## Goals / Non-Goals

Reporting the retained proposal ref is deferred to [issue #47](https://github.com/mattpolicastro/nightshift/issues/47) after #13 because it also requires the proposal validator in `nightshift/specloop_intake.py` to change. The intake chain (#5 → #6 → #13) owns that file now. This change leaves proposal publication and its JSON branch value unchanged; no edits to `specloop/propose.py` or the existing proposal-ref test assertions are planned.

Correct all three observable defects with small changes in `specloop/cli.py`, `specloop/tasks.py`, `specloop/loop.py`, and `specloop/feasibility.py`. Preserve successful CLI output shapes and strict validation of authored spec tasks. Keep all existing requirements, including status read isolation, revision block preservation, and preparation sandbox semantics.

Do not redesign CLI parsing, invent a new branch field, rewrite feedback or persisted tasks, alter evidence scoring, change `specloop/revise.py`, run preparation from prompt generation, or grant agents network access. No live application, provider session, GitHub service, or visual check is needed to prove these changes. The human checklist has no additional checks beyond the tests.

## Decisions

### Separate candidate syntax from known runtime references

Keep `Task.scenarios` as the raw syntactic candidate list so `propose.lint` and `cli.problems_with` still reject unknown IDs in authored tasks. Add `Task.known_scenarios(known)` in `specloop/tasks.py`, taking a collection of IDs and returning only exact matches, preserving candidate order. Do not change parsing, task text, numbering, hashes, or persisted state.

Use that helper in `loop.section_evidence`, `loop._scenario_text`, and `loop._gate_context`. Evidence obtains the authoritative map once per gate from `_scenarios_at(ctx, head)`, using the same commit being evaluated. Prompt context uses the existing lookup in its supplied checkout. Exclude unknown candidates from scenario lists while retaining all original feedback in task text. Continue to deduplicate and sort section-level citations. A known missing scenario still produces a coverage task; an unknown path never does. If a syntactically valid path actually is a scenario in this change, it counts as one.

Do not treat failure to load scenarios as an empty authoritative set that can pass evidence. Let the existing failure handling receive the lookup exception before calling `evidence.gather` or rewriting tasks. Keep prompt lookup diagnostics on errors. Leave final coverage's existing set of actual change scenarios and coverage log extraction unchanged.

Add `test_review_feedback_only_accepts_existing_scenario_ids` in new `tests/test_specloop_feedback_scenarios.py`. Exercise parsed review-feedback tasks containing a plain path, an unknown ID, and a known ID through the evidence gate and context builders. Check both a covered and uncovered known ID, verify the generated strengthening task text, and check a lookup failure cannot append tasks or mark evidence passed. Retain a regression assertion that authored unknown IDs are still rejected by drafting/admission validation. Use temporary repositories and controlled OpenSpec/verification boundaries, without executing mutation workers or a provider.

### Show the actual preparation and verification sequence

In `loop._implement_prompt` and `feasibility.check`, render two clearly labeled groups, Prepare and Verify, each in manifest declaration order, with Prepare first. Each step includes its ID, expanded argv rendered with shell-safe quoting, and expected exit status. Empty preparation is explicitly shown as `(none)`; verification remains visible.

Retain each prompt's existing substitution context: implementer `{src}=.`, `{build}=build`, `{sha}=HEAD`; feasibility `{src}=.`, `{build}=<build dir>`, `{sha}=rev`; both expand manifest variables via `verify.expand`. These values illustrate the recipe rather than claiming to be the harness's actual sandbox paths. Explain in the rendered recipe that trusted preparation runs under the harness with network access before offline verification, from the build directory with read-only extracted source. The implementer's existing prohibition on using the network remains in force. This wording can live in the generated recipe without changing `specloop/prompts.py`.

Prompt generation must not execute either group or change verification, sandbox, receipt, timeout, profile-selection, or feasibility-result behavior. Add `test_implementer_recipe_and_feasibility_review_include_prepare_steps` in new `tests/test_specloop_prepare_prompts.py`, parameterized for both prompt consumers and manifests with multiple prepare steps or none. Capture the actual prompt passed to the feasibility agent, and assert order, IDs, quoted commands containing spaces, variable expansion and nonzero expected exits.

### Handle parser errors before command dispatch

In `cli.main(argv=None)`, materialize the effective argv once, using `sys.argv[1:]` when omitted. For parse-error reporting, recognize an exact `--json` token before the first standalone `--`, irrespective of whether parsing reaches that token. This applies to malformed invocations, including missing or unknown subcommands; it does not make a new successful global flag syntax. Tokens after `--` and substrings such as `--jsonish` do not request JSON.

Use a local `argparse.ArgumentParser` subclass whose `error` raises a dedicated parse exception when JSON error mode is enabled. Ensure subparsers inherit that behavior. Catch only that exception around `parse_args`, print exactly `{"version": 1, "error": "<nonempty argparse diagnostic>"}` to stdout, and return 2 from `main`; the process entry point already converts the return value to an exit status. Do not emit an outcome, usage text, progress output, or a traceback on stdout. Usage/diagnostic text may appear on stderr. No command handler, environment loading, run reads/writes, or notifications may run on a parsing failure.

Without the flag, preserve argparse's human-readable errors and `SystemExit(2)`. Help remains ordinary successful argparse help, even with `--json`; do not broadly catch `SystemExit`, `KeyboardInterrupt`, or exceptions from command execution. Valid command refusals, status read errors, and `act` outcomes retain their existing schemas and exit codes.

Add `test_argparse_errors_emit_json` in new `tests/test_specloop_argparse_json.py`. Parameterize missing positional/required option, invalid choice, invalid numeric type, unknown option, and absent/unknown subcommand cases across JSON-capable commands (`propose`, `admit`, `revise`, `publish`, `status`, `act`). Cover the default argv path, a flag appearing after the offending token, non-JSON errors, help, and the option terminator. Guard dispatch and environment-loading boundaries so the test proves parsing failures have no command side effects.

### Validation and concurrent work

Each implementation task includes its named regression test and scenario markers (`pytest.mark.scenario`) for all scenarios it covers. Run the focused new file after each task; run `uv run pytest` after the final task. Keep every existing test. New tests should have local fixtures rather than importing fixtures from concurrently changing files.

Do not edit these files reserved by other changes: `docs/validation.md`, `nightshift/cli.py`, `nightshift/daemon.py`, `nightshift/specloop_intake.py`, `nightshift/specloop_job.py`, `plugins/nightshift/client/factory.tsx`, `plugins/nightshift/tests/factory-style-harness.ts`, `plugins/nightshift/tests/factory-style.test.ts`, `plugins/nightshift/tests/factory.test.ts`, `specloop/evidence.py`, `tests/test_engine_routing.py`, `tests/test_specloop_act.py`, `tests/test_specloop_approval.py`, `tests/test_specloop_background.py`, `tests/test_specloop_evidence.py`, `tests/test_specloop_label_authorization.py`, `tests/test_specloop_label_authorization_recovery.py`, `tests/test_specloop_state_reads.py`, and `tests/test_specloop_status_json.py`. No item requires changes to those files.

## Risks / Trade-offs

- Filtering unknown runtime citations could mask a typo introduced after admission. Strict drafting/admission validation remains unchanged; runtime filtering is deliberately about membership and prevents impossible recovery tasks. Lookup failures must remain failures.
- Resolving the current scenario map adds a read/extraction to section evidence. Reuse one map within that gate, and never cache it across commits.
- JSON parse errors and drafting questions both use exit code 2. Their documents distinguish them: top-level `error` versus `outcome="needs_input"`.
- Listing prepare commands must not imply that sandboxed implementers can execute network operations. The recipe identifies these as harness steps, preserving all agent restrictions.

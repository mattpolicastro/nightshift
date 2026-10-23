## Context

`specloop/evidence.py:mutate` selects verify steps in verify declaration order, runs a baseline at the gate output root, snapshots per-step budgets, and submits `_mutate_one` to a bounded pool. Workers copy the baseline source into `mutant-N/src`, run prepare plus selected steps, classify the first failure and clean up. `gather` skips mutation when `mutation_steps` is empty. `verify.run_extracted` already creates a fresh build, runs preparation and writes receipts; calling it twice in the same output directory would overwrite diagnostics.

`specloop/manifest.py:_gates` currently resolves mutation IDs only against verify steps. Its existing path matcher supports exact paths and directory prefixes, not general globs. `examples/specloop/self-hosting.toml` currently demonstrates Python verification only; the combined Python/plugin step motivating this issue lives in Matt's private manifest.

The existing specs protect committed-source selection, exclusions, operators, deterministic sampling, equivalence, isolation, timeout handling, null scores, summaries, coverage and cleanup. Those contracts remain. MODIFIED deltas change the single-baseline and baseline-budget wording and qualify historical edit restrictions from #49 and #67; they do not weaken their tests or documentation guarantees.

## Goals / Non-Goals

Reduce unnecessary suite execution through explicit trusted routes and optional import-based narrowing. A narrowed pass never establishes survival. Keep configuration, command selection, budgets and retained decisions deterministic and testable with local fixtures.

Do not alter mutation operators, generation, scoring, coverage, loop orchestration or verifier sandbox code. Do not add dependency tracing, transitive import analysis, coverage-based test selection, baseline caching across gates, parallel baselines, a new timing tool, or automatic private-manifest deployment.

## Decisions

### Manifest interfaces and defaults

In `specloop/manifest.py`, add frozen `MutationRoute(paths: tuple[str, ...], steps: tuple[str, ...])` and trailing default-empty `Manifest.mutation_routes` and `Manifest.mutation_step` tuples. The latter contains ordinary `Step` values. Parse top-level `[[mutation_step]]` with `_steps`, retaining `id`, argv `run`, expected exit default `0`, and timeout default `300`. These steps never join normal verification, preparation, acceptance or coverage.

Extend `_gates` to resolve IDs against verify plus mutation-only definitions. Reject mutation-only IDs colliding with verify, prepare or acceptance IDs; preserve existing rules between older step categories. Validate routes as an array of tables with nonempty string-array `paths` and `steps`; reject unknown IDs and repeated IDs within a route. A configured nonempty route list requires a nonempty fallback `mutation_steps`. This keeps mutation opt-in and prevents unmatched files from silently receiving no tests; the existing empty-fallback skip remains for manifests without routes. Preserve route order and template/load round trips.

Route matching is case-sensitive `fnmatch.fnmatchcase` against the mutant's repository-relative POSIX path. A route matches when any pattern matches; the first matching table wins. `*`, `?` and character classes use Python fnmatch semantics, including `*` matching `/`; `**` has no additional segment semantics. Reject absolute patterns, backslashes, and `..` path components. Do not change `matches`, allowed/frozen paths or `mutation_paths` semantics.

Within a route, execute IDs in its `steps` order. For fallback, preserve today's verify declaration order, followed by selected mutation-only steps in their declaration order. This avoids reordering old configure/build/test sequences even when `mutation_steps` lists IDs differently. Use the resolved ordered tuple of IDs as the baseline key; two routes selecting the same tuple share a baseline, while reversed tuples are distinct.

`{mutant_path}` is a reserved execution value. Commands in either selected verify or mutation-only steps can use it. If such a placeholder occurs in step commands, parsing supplies an empty default in `Manifest.vars` for ordinary verification and rejects a nonempty user override of that reserved variable. Legacy manifests without the placeholder retain their existing vars. Evidence execution uses a copied manifest with the reserved value set for each run; direct `Manifest` construction is supported too. Never mutate the shared manifest. Leave `verify.py` unchanged and use its existing value expansion. A full/baseline run uses `""`, not omission of an argv element; command authors must interpret empty input as all tests. Pass paths as separate argv elements in the example rather than interpolating them into shell source.

### Routing and full baselines

In `mutate`, resolve every selected mutant before creating workers. Enumerate unique step tuples in first-use order. Run each baseline serially against the same sealed head with `{mutant_path} = ""`, ordinary preparation and original manifest timeouts. Baseline 1 retains the gate output root; additional baselines use `baseline-2/`, `baseline-3/`, etc. Retain their source/build trees until workers drain, then clean all of them. Each worker copies source from its assigned baseline; no prepared build is shared.

Complete all required baselines before any worker starts. Stop on the first failed baseline, mark all selected mutants invalid and return false, preserving the gate-wide baseline-failure behavior. Unused routes are not baselined. Equivalent or mismatched selected mutants still determine a step set before their existing worker precheck; this preserves baseline-before-precheck behavior. `gather` retains all existing skip conditions. A direct legacy `mutate(..., mutants=[])` call may retain its one fallback baseline and must not create a pool.

For each step set, derive prepare and selected-step limits from that set's full baseline receipts: `min(manifest timeout, max(MIN_MUTANT_SECONDS, MUTANT_TIME_FACTOR * seconds))` for a positive duration, otherwise the manifest timeout. Keep current constants (`30.0`, `3`). A step shared by different sets uses the duration measured in the assigned set. Reuse those immutable limits in narrowed and confirmation runs; neither a narrowed duration nor queue wait can reduce a later budget.

### Narrowing and confirmation

In `_mutate_one`, retain line validation, equivalent annotations and one private mutation of the source. If no selected command contains `{mutant_path}`, execute the set once in full mode. Otherwise execute the whole selected set with the relative path in the placeholder. A passing narrowed set triggers the entire same set again with the empty placeholder, including commands without the placeholder. Both phases run the existing preparation first; confirmation uses a fresh build so cached results cannot stand in for full tests.

Keep the initial run at `mutant-N/`; put confirmation at `mutant-N/confirmation/`, reading the same private mutated source. Never re-extract the original head for confirmation. A worker holds one pool slot through both phases; the pool bounds mutants, not individual steps. Cleanup confirmation before the worker source, preserving artifacts from both runs, and drain all workers before baseline cleanup even on an exception.

Use existing receipt classifications at either phase: all expected exits pass; the first unexpected test exit (including signal termination) kills; selected-test timeout is `timeout`; prepare or IDs `configure`/`build` failing or timing out are `invalid`. A timeout is not a narrowed kill or a narrowed pass and triggers no confirmation. Equivalents and source mismatches execute no mutant commands. Unexpected errors propagate, and no successful partial evidence is written.

### Receipts without a state migration

Keep `Mutant`, `Evidence`, `evidence.json`, step receipt arrays, summaries and loop events unchanged. Add a gate-local `mutation-runs.json` decision receipt written by the coordinator in selection order after successful classification (including baseline-failure classification). It contains a `baselines` list with ordered step IDs and relative receipt paths and a `mutants` list with one-based number, path, line, op, route index (one-based, or null for fallback), selected step IDs, assigned baseline, result, `decided_by`, and ordered runs. Each run records `phase` and its relative `receipts.json` path. Values of `decided_by` are `full`, `narrowed`, `confirmation`, `precheck` or `baseline`; precheck and baseline failures have no mutant runs. Mark a failed baseline in the baseline list so the gate-wide abort is attributable. Do not copy variable timings or absolute output paths into this index.

Existing `receipts.json` files supply expanded argv, expected/actual exits, durations and diagnostics. The sidecar links the decisive phase to them without overwriting the narrowed receipts. Legacy executions retain their existing layout and behavior, with only this additive decision receipt. A process crash may leave incomplete diagnostics; this change does not add resume or cross-attempt caching.

### Example selector and trusted configuration

Add `examples/specloop/select_tests.py` with stdlib-only `select_tests(root: Path, mutant_path: str) -> list[str]` and a CLI accepting an optional path (default empty) and `--root` (default current directory). Print sorted, deduplicated repository-relative test paths, one per line. An empty result means run all tests, never run zero tests.

Use AST import nodes in `tests/**/test_*.py` and `tests/**/*_test.py`, including imports inside functions and aliased imports. Derive dotted names for repository-root Python packages, handling `__init__.py` as its package. Match exact `import package.module`, exact `from package.module import name`, and `from package import module`; resolve relative imports when the test belongs to a recognizable package. Use module-component boundaries, so `foo` does not match `foobar`. For a package initializer, conservatively include imports of that package or descendants. Do not import or execute repository code. Do not chase transitive imports or guess dynamic imports.

Return an empty list for an empty/unsupported path, an unresolvable module, no direct import matches, a missing tests tree, or any read/parse failure during the scan. Reject paths escaping the supplied root by falling back to all tests. This deliberately sacrifices narrowing on uncertainty. Static matches need not be complete: full confirmation covers omitted indirect/dynamic dependencies before any survivor is reported.

Update `examples/specloop/self-hosting.toml` with a Python route for `specloop/**` and `nightshift/**` and a mutation-only Python step using the selector. Keep existing verify, negative-control and coverage commands intact. The example copies `select_tests.py` beside the existing trusted `scenario_plugin.py` and `negative_control.py` under `{checks}`. Use a small Python argv adapter in the mutation-only command to call `select_tests(Path(src), path)`, prepend `src` to selected paths, substitute `src/tests` when empty, then run pytest with the existing interpreter, environment and pytest options, propagating its exit. This handles spaces without shell word splitting. Do not add a plugin dependency/install pipeline to the currently Python-only example; document the second route's shape in the guide for manifests that already have plugin commands.

### File boundaries and requirement updates

Implementation may edit `specloop/manifest.py`, the execution helpers in `specloop/evidence.py`, the two named example files, the matching three `openspec/specs/*/spec.md` files through these deltas, new tests, and only Section evidence in `docs/specloop/README.md`. The guide overlap is necessary to document the new public settings; preserve #76's arming/front-door prose and rebase this subsection if needed.

Do not edit `KNOWN-LIMITATIONS.md`, `README.md`, `docs/arming.md`, `docs/landing.md`, `nightshift/arm.py`, `nightshift/cli.py`, `nightshift/land.py`, `pyproject.toml`, `specloop/cli.py`, `specloop/critic.py`, `tests/test_arm.py` or `tests/test_land.py`. Also leave `specloop/loop.py`, `specloop/verify.py` and existing test files unchanged. Preserve every existing assertion, including `test_parallel_mutants_respect_time_box` and its exact budgets. The MODIFIED historical-scope requirements permit this feature's production edits, not changes to that test.

### Validation and human rollout

Put tests in `tests/test_mutation_routes.py`, `tests/test_mutation_route_baselines.py`, `tests/test_mutation_narrowing.py`, `tests/test_select_tests.py` and `tests/test_mutation_route_examples.py`. Use local fixture repositories and controlled receipts to test routing, budgets, concurrency and failure paths without the Paseo host, GitHub, live credentials or real project suites. Exercise a local behavioral mutant for which narrowing misses a killing test but confirmation finds it. Retain existing real subprocess cleanup tests; new concurrency tests use events/barriers rather than performance thresholds. Mark tests with the scenario IDs they cover. Implementation acceptance is green `uv run pytest` plus `uv run python scripts/check_types.py`; do not weaken assertions or edit the type baseline to make them pass.

Human checklist for the implementation PR, not implementing-agent tasks:

- After merge, Matt copies the reviewed selector to his trusted checks directory and updates his private manifest with Python and plugin routes, retaining the combined verify step and a full fallback. Validate the routes against actual language boundaries and verify that empty selector input runs the complete routed suite.
- Use the timing report on the next few real changes to compare section gate time with the measured 14-minute baseline. Record mutant counts, route mix, narrowed kills and confirmations alongside timings; baseline overhead, survivor rate and machine load can change the result. CI cannot prove the real-gate speed-up, and this change claims no fixed percentage improvement or new timing-report command.

## Risks / Trade-offs

Wrong route configuration can omit a relevant suite; full confirmation protects against narrowing omissions only within the selected route. Use a conservative fallback and keep route review in the trusted-manifest rollout. `fnmatch` crossing separators is explicit to prevent misleading glob assumptions.

Multiple step sets add baseline work and surviving mutants run twice. Fresh confirmation preparation costs more but avoids build/cache contamination. No runtime threshold is an acceptance condition.

Static direct imports miss dynamic and transitive dependencies and cannot reliably infer arbitrary `src/` layouts; falling back and full confirmation preserve the intended survivor guarantee. Existing rules still treat a non-timeout selector/command failure as a failed test, so the selector handles scan uncertainty with empty output and the example adapter is tested.

The guide is shared with #76. Restricting the edit to Section evidence minimizes overlap without hiding the new configuration. Archived specs contain historical file-scope restrictions; the explicit MODIFIED deltas preserve their outcomes while preventing those restrictions from freezing unrelated future implementation.

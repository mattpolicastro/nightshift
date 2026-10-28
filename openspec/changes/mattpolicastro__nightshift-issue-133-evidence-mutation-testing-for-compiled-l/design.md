## Context

`specloop/evidence.py` currently applies `OPERATORS` to every extension. Python
also uses AST/token protection and two control-flow operators. Non-Python
files use prefix exclusions and `_non_python_protected_spans`, which protects
complete blocks with comment-only boundary lines. `generate` filters before
seeded sampling and selects at most one mutant per line.

`_mutate_one` currently unions `BUILD_STEPS = ("configure", "build")` with
prepare IDs. The first failing receipt determines the result. Compilation
commands with different IDs therefore incorrectly count as kills. Baselines,
routing, narrowing, confirmation and score calculation already exist.

The repository has no `AGENTS.md` in this checkout. Existing mutation-selection,
parallel-evidence-mutants and documentation requirements remain in force except
for the explicit MODIFIED deltas. This draft replaces the proposed delimiter
scanner with the user's physical-line rule; Kotlin interpolation is handled
by excluding its quoted line, without interpreting raw strings.

## Goals / Non-Goals

Goals: configurable invalid classification with compatible defaults; a small,
deterministic C-family candidate set; unchanged Python behavior; three bounded
implementation tasks with tests alongside behavior.

Non-goals: a lexer, AST for native languages, interpolation or division
handling, rejecting entire files for unsupported syntax, general string or
inline-comment protection, new evidence schemas, new scoring semantics or
toolchain installation. This is limited textual mutation support, not a claim
that every candidate changes executable behavior.

## Decisions

### Manifest and classification

Add `Manifest.mutation_invalid_steps: tuple[str, ...]` in
`specloop/manifest.py`, with default `("configure", "build")` for both direct
construction and parsing. Put the shared default constant in `manifest.py`
and import it into `evidence.py` as `BUILD_STEPS`, preserving that public name
without a circular import or duplicated default.

`_gates` accepts an array of nonempty string IDs and rejects malformed values
with `ValueError` naming `gates.mutation_invalid_steps`. Preserve entries
verbatim in the tuple and through template overrides and reload. Duplicates
are harmless; unknown IDs are accepted as inert classification names, just
as the default currently permits absent configure/build commands. This key
does not select or define commands. An explicit array replaces the defaults;
`[]` disables only the configured invalid-step exception.

In `_mutate_one`, compute `set(m.mutation_invalid_steps) | prepare_ids`.
After a passing baseline, the first failing receipt in this union is invalid,
including `exit is None`; any other failure is timeout when `exit is None`
and killed otherwise. A fully passing required sequence survives. Apply this
identically to fallback/routes and full/narrowed/confirmation phases. Keep
baseline failure, source mismatch, equivalence, cleanup, budgets and persisted
formats unchanged. In particular, all-invalid evidence remains unmeasured.

### Extension dispatch and operator boundaries

Dispatch on the exact, case-sensitive `Path(path).suffix`. Only `.dart`, `.ts`,
`.swift`, `.kt`, `.c` and `.cpp` select the new C-family set. `.py` and `.pyi`
retain `OPERATORS`, its order and AST operators; every other suffix retains
the existing fallback. Keep `OPERATORS` unchanged for existing callers.

Use a separate ordered C-family table: `true` to `false`, `false` to `true`,
`&&` to `||`, `||` to `&&`, `==` to `!=`, `!=` to `==`, `<` to `<=`, `<=` to
`<`, `>` to `>=`, `>=` to `>`, then remove a leading logical `!`. No integer,
arithmetic, Python keyword or Python control-flow operators belong to this set.
Each candidate changes one match and preserves every other character.

Boolean literals require boundaries excluding adjacent word characters or
`$`. Symbol comparisons match with or without whitespace and cannot be a
substring of longer equality, comparison, shift or arrow tokens: adjacent
`=`, `!`, `<`, `>` block comparison matches; a preceding `-` also blocks `>`.
Logical pairs cannot be part of longer same-character runs or assignments.
For `!`, require no preceding word character or any of `$)]}!` and require,
after optional spaces/tabs, an identifier start, `(` or another `!`; an
identifier start is a Unicode non-digit word character, `_` or `$`. Remove
only the `!`, retaining whitespace. Thus `!ready`, `!(a == b)` and the first
`!` of `!!ready` qualify, while `!=` and postfix `value!` do not. These are
textual token guards, not language-specific syntax validation.

### Physical-line eligibility

For C-family candidates, inspect the entire original physical line before
operator matching. Reject it if it contains ASCII apostrophe, double quote
or backtick anywhere, or if `text.lstrip()` starts with `//`, `/*` or `*`.
Do not interpret escapes, raw prefixes, interpolation, indentation, fences
or surrounding language syntax. Ambiguous quote-containing lines are skipped,
not parsed and not a reason to reject other lines or the whole file.

Keep existing `added_lines` prefix/declaration filtering, literal
`mutation_skip`, and `_non_python_protected_spans` on full committed source.
Its complete comment-only blocks still protect bare interior lines across
unchanged context and diff hunks. The new line tests also apply to direct
`generate` calls. Missing committed source still raises; an empty candidate
pool retains the current no-mutable-lines skip and coverage behavior.

C-family candidate generation uses its own unannotated half-open overlap
check against those existing protected spans and then joins the shared pool.
Leave the legacy loop and its exact annotated predicate in place, including
indentation required by `test_repository_code_annotations_retained` in
`tests/test_mutation_prose_annotations.py`. Do not route C-family matches
through that claim or change that test. Preserve seeded shuffle, count limit,
line identity and one-mutant-per-line selection.

Skipping every quote-containing line deliberately loses otherwise useful
mutations on lines containing strings.

### Compatibility fixtures and verification

Several `tests/test_mutation_source_filtering.py` fixtures require integer
mutations for `.cpp`/`.ts`, and one expects quoted `.cpp` lines to be mutable.
Those expectations cannot describe the new C-family set. For tests that run
generation with those legacy fixtures, move `.cpp` to `.cc` and `.ts` to an
existing fallback such as `.js`, deduplicating parameter values if needed;
preserve all assertion expressions, fixture source, expected values and seeds.
Keep the prefix-only `added_lines` test on `.cpp`, since its contract does not
change. Add C-family Boolean/comparison fixtures covering the same complete
block, unsupported-boundary, committed-context and sampling behaviors. This
is a justified fixture migration, not deletion or relaxation of assertions.

Add `test_mutation_invalid_steps_default_matches_build_steps`,
`test_non_compiling_mutant_is_invalid_not_killed`,
`test_c_family_operators_for_dart_files` and
`test_python_operators_unchanged`. Use parameterized tests for the six suffixes,
operator directions and line exclusions. Include the exact Kotlin input
`val s = """${"""true"""}"""` and Dart interpolation on an unchanged line next
to eligible added code. Use one operator per line when enumerating operators
so the one-per-line sampler does not hide directions. For Python, assert
fixed expected ordered mutants for representative seeds, including literals,
arithmetic and both AST operators; do not compare the implementation to itself.

Test the executor through a small Python subprocess standing in for a compiler:
it accepts the baseline fixture and rejects its mutation under ID `dart-analyze`,
with a separately observable test step. Assert invalid, no subsequent test,
retained failing receipt and exclusion from scoring. This proves classification,
not Dart compilation. Add compact receipt-driven cases for default/empty/custom
IDs, preparation, timeouts and both confirmation phases. Keep current baseline,
parallelism and cleanup assertions. Run `uv run pytest` and
`uv run python scripts/check_types.py`; add no type errors or baseline entries.

### Guide and example

Update only `### Section evidence` in `docs/specloop/README.md`, including the
old blanket configure/build classification and runtime-string statements.
Explain replacement versus extension of defaults, prepare precedence, exact
suffixes, operators, physical-line limits and issue #133. Retain existing
coverage, Python, routing, timeout and measurement guidance and citations.

Add `examples/specloop/dart.toml`: a trusted-manifest template with explicit
toolchain/project placeholders, a prepare command copying `{src}` into a
writable `{build}/project` and fetching locked dependencies, verify steps
`dart-analyze` followed by `dart-tests`, `mutation_paths = ["lib/**"]`,
`mutation_steps = ["dart-analyze", "dart-tests"]` and
`mutation_invalid_steps = ["dart-analyze"]`. Commands run in the prepared
project, using an explicit configured Dart executable, `dart analyze` before
`dart test`; neither cached baseline build state nor tests alone classify
compilation failures. Do not invent coverage attestations: coverage setup is
project-specific and the example is not a turnkey enrollment.

Test the TOML with the real manifest parser, checking resolved order, IDs,
argv and placeholders without running Dart or downloading packages. Check
documentation structurally, not with exact prose assertions: the evidence
passage starts at the unique physical line exactly `### Section evidence`
and ends at the next column-zero heading with one to three hashes followed
by a space, or EOF. Missing/duplicate boundaries block validation; fences,
HTML and indentation never hide a recognized heading. Inside that passage,
require one standalone line of the form
`[Dart manifest](../../examples/specloop/dart.toml)` and resolve its target.
Do not add a Markdown parser or test prose phrasing.

## Risks / Trade-offs

- Physical-line filtering does not recognize quote-free interiors of multiline
  strings or inline/trailing comment text outside the existing block rule.
  Such text can still produce candidates; this limitation is accepted rather
  than hidden behind a general string/comment-safety claim. Use configured
  literal skips where needed and inspect actual mutants.
- Tokens may occur in generics, overloaded operators or non-code contexts;
  textual operators cannot establish semantic usefulness. Type/analyzer failures
  are invalid only when the responsible step is configured correctly.
- Invalid IDs replace defaults, so a custom list that omits `build` changes
  that step's classification intentionally. Document explicitly and test `[]`.
- Keep task 1.2 near 300 changed lines with a small table, short line predicate
  and parameterized tests; no scanner, dependency or per-language framework is
  justified. Tasks 1.1 and 1.3 have separate concerns and similar ceilings.
- Automated tests need no Dart SDK. Real toolchain behavior and useful mutation
  coverage are pending, and passing fixtures must not be described as proof
  that native evidence is operational on the target engine.

Human checklist for the implementation PR (not implementing-agent tasks): run
the first evidence gate on the scheming-vizier engine with its actual Dart
toolchain, package cache and manifest. Confirm the baseline passes, compilation
or analyzer failures classify invalid, eligible mutants exercise real behavior,
tests kill meaningful mutants and quote-line skips leave a useful sample.
Inspect source, receipts, skips and killed/survived/invalid totals; record the
run and findings before claiming operational support. Review the evidence
guide/example for readability and verify the issue link. If no meaningful
candidates remain, record that limitation rather than treating a skip as proof.

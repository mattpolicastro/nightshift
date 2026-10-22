## Context

`specloop/evidence.py` reads a zero-context diff in `gather`, extracts eligible
added lines in `added_lines`, then applies regex operators in `generate`.
`SKIP` recognizes some comment-only lines but cannot identify docstring bodies,
block comments opened outside a hunk, or trailing comments. Integer matching
also reaches quoted digits such as `'600'`. Selection shuffles with the section
seed and chooses at most one mutant per line.

`specloop/manifest.py` exposes immutable `Manifest` fields through `_gates`;
`template` preserves and serializes the original TOML settings. `_mutate_one`
honors inline or immediately preceding equivalence annotations. `Evidence.score`
already counts only killed and survived mutants, but `unmeasured` additionally
accepts equivalent mutants. `specloop/loop.py:section_evidence` already pauses
unmeasured evidence without creating strengthening tasks.

#31 is a prerequisite and is present in this checkout, including its archived
change and timeout specifications. Its explicit equivalent/timeout-only passing
scenario must change. Existing concurrency, isolation, preparation, time-budget,
cleanup, timeout reporting, coverage, and baseline rules remain binding.
No AGENTS.md exists in this checkout or its ancestor directories; README.md
points to CLAUDE.md, which was read with the engine documentation.

## Goals / Non-Goals

Remove cosmetic and prose mutants before they consume the sample budget, retain
code mutations on mixed code/comment lines, and make an evidence pass depend on
at least one executed behavioral outcome when mutants were selected. All new
behavior must be testable with source fixtures, temporary Git repositories and
stubbed verification, without Paseo, GitHub, launchd or live model calls.

Do not build a general semantic mutation engine or a complete compiler frontend.
Do not change normal runtime strings merely because they resemble prose. Do not
infer which external style constants are cosmetic. Do not alter worker execution
or scoring thresholds, and do not add a new manifest switch for the JSX default.

## Decisions

### 1. One simple manifest option

Append `mutation_skip: tuple[str, ...] = ()` to `Manifest` and parse
`[gates] mutation_skip` in `_gates`. Accept only a TOML array of nonempty strings;
reject a scalar, non-string member or empty string with `ValueError` naming
`gates.mutation_skip`. Preserve supplied strings verbatim, including spaces.
Match any entry as a case-sensitive literal substring of the original complete
physical line, including comments. There is no regex or glob interpretation.

For example, `mutation_skip = ["style=", "fontWeight", "padding", "gap", "margin"]`
skips whole matching lines; these entries are an example, not built-in defaults.
The empty default leaves only automatic prose and JSX filtering. Test parsing,
template override serialization and reload so intake's effective manifest keeps
the option. Store configuration only in the trusted manifest as today.

### 2. Filter spans using the committed head before sampling

Keep `added_lines` responsible for paths and new-file line numbers. Move its
comment-prefix exclusions out of `SKIP` into span analysis so a line beginning
with a block comment can still supply executable code after the closing delimiter;
adjust the existing added-lines test accordingly. In `gather`,
read each eligible path once from `head` using the existing `seal.git` wrapper
(Git object content, not the worktree). Pass a path-to-source mapping and the
skip tuple to `generate` as keyword arguments. Update direct callers and test
doubles in `tests/test_specloop_evidence.py` as needed; synthetic callers supply
complete fixture source. Existing `(path, line, text)` inputs and `Mutant` fields
retain their meanings.

Add private span-analysis helpers in `specloop/evidence.py`. They preserve line
and character offsets and exclude an operator match if any part overlaps a
protected span. Construct `before` and `after` from the unmodified original
line, replacing only the accepted match. Never mutate a whitespace-masked copy:
that would change comments or break `_mutate_one`'s source-line validation.

For `.py` and `.pyi`, use standard-library `ast` and `tokenize`: recognize module,
class, function and async-function docstrings, protect their complete string
spans (single or triple quoted), and protect COMMENT token spans. Both triple
quote forms, prefixes, escaped delimiters and one-line docstrings are covered.
Assigned triple-quoted strings are ordinary runtime strings, not docstrings.
Code following a closing docstring delimiter on the same line remains eligible.

For JS/TS/JSX/TSX and C-family source, scan `//` and `/* ... */` comments with
quote/escape state; JSX `{/* ... */}` is included. Carry state across the entire
file, including unchanged lines. The scanner must distinguish strings, JS regex
literals and template text from comment delimiters, and recognize comments in
template interpolation expressions. For other extensions retain the existing
generic lexical conventions for `#`, `//` and `/* ... */`, outside quoted text;
this is not a promise of support for every language-specific comment syntax.
Existing import/declaration exclusions stay in place.

Missing committed source, Python parse/tokenization failure, or an unterminated
lexical construct must raise an error identifying the path, without successful
partial evidence; never silently fall back to raw diff mutation or count an
analysis failure as a no-mutable-lines skip. Valid unsupported language syntax
must not be presented as fully parsed: the generic path uses only its documented
lexical conventions.

Apply line substrings and span exclusions before shuffling and counting. Retain
the seeded shuffle, at most one mutant per physical line, the existing operators
and stable selection order. Excluded candidates do not consume `mutation_count`.
If filtering leaves none, use the existing `no mutable lines added under
mutation_paths` reason, start no baseline/workers, and still enforce coverage.

### 3. Default JSX style exclusion is structural and limited

In `.jsx` and `.tsx`, recognize the JSX `style` attribute whose expression is
an object literal, including whitespace around `=` and between braces. Track
balanced braces across lines while ignoring braces in strings and comments.
Suppress the `int` operator throughout that object's span, including nested
objects, dynamic expressions and quoted values such as `fontWeight: '600'`.
Other operators in the object retain existing eligibility. Numbers outside the
object on the same line remain eligible. Ordinary JS object properties named
`style`, identifiers such as `lifestyle`, and strings containing `style={{` do
not activate the rule. Referenced styles, stylesheet declarations, `.js`/`.ts`
files containing JSX, and framework-specific equivalents require explicit
`mutation_skip` entries in this iteration.

### 4. Remove only annotations whose subject is prose

After prose exclusion lands, remove the known annotations before docstrings in
`nightshift/specloop_client.py` (`SpecloopError`), `specloop/state.py` (`Run`),
and `specloop/evidence.py` (`_mutate_one`, `mutate`). Inspect remaining markers
under both packages and remove annotation text inside docstrings or annotations
whose subject is only a comment/docstring. Preserve surrounding documentation,
the marker constant, prompt strings explaining annotation syntax, and inline or
preceding comment annotations that justify actual executable code.

Add `test_prose_equivalence_annotations_removed` in a new test file. Inspect
Python docstring AST nodes throughout both packages and assert no marker remains;
also check the known prose-only comment annotations and fixtures demonstrating
that a legitimate code annotation is retained. Do not ban all comment markers:
real-code annotations are intentionally comments. This cleanup does not redefine
the executor's existing inline/preceding annotation classification contract.

### 5. Require killed or survived results and report the reason accurately

Change `Evidence.unmeasured` to require a nonempty mutant list and the absence
of any `killed` or `survived` result. Equivalent, timeout, invalid and pending
results provide no measurement. A skipped result with no mutants remains
measured for pass-decision purposes as today; uncovered scenarios and baseline
failures still fail. `score` stays null without killed/survived mutants.

Update `Evidence.summary` so equivalent-only and mixed unmeasured results state
that nothing was measured and accurately identify equivalent annotations rather
than calling them invalid. Preserve existing all-invalid and timeout-only
summary behavior and the exact single timeout-count line required by #31.
Mention reviewing equivalence annotations when equivalents contribute to an
unmeasured result. Keep the JSON shape and classification values unchanged.

Exercise the existing `section_evidence` pause path from new tests, with zero
prior strengthening rounds: no completion record and no strengthening task is
created. No production change to `specloop/loop.py` is expected. Update the
contradictory equivalent/timeout pass assertions and scenario markers in
`tests/test_specloop_evidence.py`; new regressions go in new files.

### 6. Implementation boundaries and validation

New tests belong in `tests/test_mutation_skip.py`,
`tests/test_mutation_source_filtering.py`, `tests/test_mutation_style_filtering.py`,
`tests/test_mutation_prose_annotations.py`, and
`tests/test_mutation_measurement.py`. Use scenario markers matching this change.
Run focused tests with their implementation, then `uv run pytest` for completion.
Existing sandbox integration tests retain their platform requirements; new tests
must not need the host application or a running service.

To avoid concurrent changes, do not edit: `docs/validation.md`,
`nightshift/cli.py`, `nightshift/daemon.py`, `nightshift/specloop_intake.py`,
`nightshift/specloop_job.py`, `plugins/nightshift/tests/factory.test.ts`,
`tests/test_engine_routing.py`, `tests/test_specloop_act.py`,
`tests/test_specloop_approval.py`, `tests/test_specloop_background.py`,
`tests/test_specloop_label_authorization.py`,
`tests/test_specloop_label_authorization_recovery.py`,
`tests/test_specloop_state_reads.py`, or `tests/test_specloop_status_json.py`.
No prose annotations requiring cleanup occur in those files in this checkout.

## Risks / Trade-offs

- Full-source analysis adds one Git read per candidate file. Cache analysis per
  file for the gather call; never invoke it once per mutant or worker.
- A lightweight JS lexer needs boundary fixtures for regexes, escapes, template
  interpolation and JSX. Conservative errors are preferable to mutating known
  prose; they can pause a gate on source the baseline would otherwise reject.
- Literal substring matching can hide meaningful code on a mixed line. The
  documented empty default avoids imposing broad property-name exclusions.
- JSX style expressions can contain meaningful numeric calculations. The
  chosen default excludes those integers too; other operators remain eligible.
- Filtering changes the sampled population and can yield a legitimate skip.
  Historical scores are not directly comparable, and skipped is not proof of
  test strength. Persisted evidence is not rewritten.
- Human PR checklist: the manifest author reviews whether configured exclusions
  are appropriately narrow, whether remaining mutants represent meaningful
  defects for this codebase, and whether retained equivalence claims justify
  real code. CI cannot establish these judgments. No live host or visual check
  is required to validate the filtering implementation.

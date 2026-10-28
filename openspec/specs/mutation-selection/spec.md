# mutation-selection Specification

## Purpose
Select behavioral mutation candidates without spending the sample on configured
cosmetic lines, Python docstrings and comments, and supported non-Python comment
lines.

## Requirements

### Requirement: Configurable line exclusions

The trusted manifest SHALL accept `[gates] mutation_skip` as an array of nonempty
strings, defaulting to an empty array, and expose it as `Manifest.mutation_skip`.
Each entry SHALL match a case-sensitive literal substring of the complete
original physical line. A matching line SHALL produce no mutants of any operator.

#### Scenario: Defaults and explicit patterns
- **WHEN** the option is omitted, empty, or contains `fontWeight` and `padding`
- **THEN** the effective tuple is respectively empty, empty, or the supplied strings, and template serialization with run overrides and subsequent loading preserve explicit entries verbatim

#### Scenario: Invalid patterns rejected
- **WHEN** the option is a scalar, contains a non-string member, or contains an empty string
- **THEN** parsing raises `ValueError` identifying `gates.mutation_skip`

#### Scenario: Matching lines excluded before sampling
- **WHEN** added lines contain a configured substring in code or a trailing comment, alongside unmatched mutable lines
- **THEN** matching lines produce no mutants, excluded candidates consume no sample budget, unmatched lines remain eligible, and matching is case-sensitive with regex punctuation treated literally

### Requirement: Committed source context

The gate SHALL determine protected spans from complete source at the requested
committed head and mutate only eligible added lines under `mutation_paths`.
Filtering SHALL preserve original line numbers and `Mutant.before` text, change
only the selected unprotected match, and precede seeded sampling. Source-analysis
failure SHALL propagate without successful partial evidence.

#### Scenario: Unchanged context determines eligibility
- **WHEN** added lines are inside a docstring or block comment opened on unchanged lines, and the worktree differs from the requested head
- **THEN** eligibility follows the committed full-file context across diff hunks, never the worktree or diff fragment alone

#### Scenario: Stable selection and exact replacement
- **WHEN** the same committed source, exclusions, count and seed are supplied repeatedly
- **THEN** the selected mutants and their order are identical, at most one mutant is selected per physical line, original line numbers and before text are preserved, and after text differs only at an eligible operator match

#### Scenario: Analysis failure cannot become a skip
- **WHEN** required committed source cannot be read or Python source cannot be parsed or tokenized
- **THEN** the operation raises an error naming the path and does not write successful partial evidence or label the analysis failure as no mutable lines

#### Scenario: Exclusions leave no mutable lines
- **WHEN** automatic or configured exclusions remove every candidate
- **THEN** gather records `no mutable lines added under mutation_paths`, starts neither a baseline nor mutant workers, retains a null score and existing coverage checks, and does not mark the skipped evidence unmeasured

### Requirement: Prose is never mutated

The generator SHALL protect Python docstrings/comments with full-source ast
and tokenize, retaining adjacent code. Non-Python filtering SHALL preserve
prefix exclusions and complete comment-only boundary blocks; supported
C-family suffixes SHALL also exclude quote-containing lines. No general lexer,
regex/division detection, interpolation parser or inline-comment parser SHALL
be introduced.

#### Scenario: Python docstrings and comments excluded
- **WHEN** module, class, function or async-function docstrings contain mutable prose or numbers, including both triple-quote forms, prefixes, escaped delimiters and multiline bodies, alongside comment-only and trailing-comment text
- **THEN** no mutation overlaps a docstring or comment, while executable code before a comment or after a closing docstring remains eligible and its adjacent prose is preserved exactly

#### Scenario: Non Python comment lines excluded
- **WHEN** a non-Python file contains lines matching existing `#`, `//`, `/*` or `*` prefixes and a block beginning with `/*` on a comment-only line and ending with `*/` on a comment-only line, with bare prose between those lines
- **THEN** the prefix-matching lines and the complete block produce no mutants, block tracking spans unchanged lines and diff hunks, and eligible lines after the closing boundary remain candidates

#### Scenario: Python runtime strings retain their meaning
- **WHEN** Python runtime strings contain apparent comment delimiters or an assigned triple-quoted string contains mutable text
- **THEN** they are not classified as comments or docstrings, and their existing mutation eligibility is preserved

#### Scenario: Non Python tracking stays line level
- **WHEN** a non-Python file has inline or trailing comments, a block opener after executable code, or an opener without a matching comment-only closing line
- **THEN** the new block filter adds no protected range for those forms, existing prefix exclusions remain in effect, code outside supported blocks remains subject to its extension operator set and quote-line exclusions, and no lexical parsing error is raised

### Requirement: Prose equivalence annotations removed

Repository Python sources under `nightshift/` and `specloop/` SHALL contain no
`mutation-equivalent` marker inside a docstring and no equivalence annotation
whose subject is only a docstring or comment. Cleanup SHALL preserve annotations
that justify executable code and ordinary documentation or strings explaining
the annotation mechanism.

#### Scenario: Prose markers removed
- **WHEN** the package sources are scanned after cleanup on a base that includes #8's merged `nightshift/specloop_job.py`
- **THEN** no docstring in either package, including `nightshift/specloop_job.py`, contains the marker and known annotations preceding prose-only docstrings or comments are removed without deleting the surrounding documentation

#### Scenario: Real code annotations retained
- **WHEN** a valid inline or immediately preceding comment annotation justifies an executable line
- **THEN** it is preserved, mutations of that code retain equivalent classification without verification commands, and its comment text is never mutated

### Requirement: Bare condition negation

For eligible added Python lines, the generator SHALL offer a `negate-condition`
mutant for a single-line `if` or `while` header with an `ast.Name` test and a body
on later lines. It SHALL insert `not ` before the name, preserving all surrounding
source text, including redundant parentheses. It SHALL NOT apply this operator
to other condition expressions, `elif`, inline suites or multiline headers.

#### Scenario: If and while names are negated
- **WHEN** eligible added lines contain `if blocked:` and `while active:` in complete Python source
- **THEN** each line offers a `negate-condition` candidate replacing it respectively with `if not blocked:` and `while not active:`, preserving indentation and producing compilable source

#### Scenario: Condition formatting is preserved
- **WHEN** an eligible header uses tabs or spaces, a Unicode name, redundant single-line parentheses, colon spacing or a trailing comment
- **THEN** only `not ` is inserted before the name's original source text, and the remaining line and all other source lines are unchanged

#### Scenario: Other condition forms retain existing eligibility
- **WHEN** source contains already-negated tests, comparisons, Boolean combinations, constants, attribute access, subscripts, calls, `elif`, inline suites or multiline headers
- **THEN** these forms offer no `negate-condition` candidate and their existing operator eligibility remains unchanged

### Requirement: Guard return replacement

For eligible added Python lines, the generator SHALL offer `drop-return` only
for a standalone, single-line return ending an `if`, `elif` or `else` branch
whose owning `if` has a later statement in its immediate enclosing list.
It SHALL replace the complete return with `pass`, preserving indentation,
trailing whitespace/comments and line numbers, and producing compilable source.
Other operators SHALL retain their eligibility.

#### Scenario: Bare and valued guard returns are dropped
- **WHEN** an added standalone `return` or `return <expr>` ends an `if`, `elif` or `else` branch in a normal or async function, and a statement follows the owning `if` in its enclosing statement list
- **THEN** it offers a `drop-return` candidate with `pass` replacing the complete return statement, exact indentation and trailing whitespace/comment preservation, and compilable source that permits execution to continue after the guard
- **THEN** a sole branch return and a return preceded by logging are both eligible

#### Scenario: Non guard returns stay intact
- **WHEN** a return has later statements in its own branch, belongs directly to a function or a non-conditional suite, or ends a branch whose owning `if` has no later statement in its immediate enclosing list
- **THEN** it offers no `drop-return` candidate, including when only comments, blank lines, another clause or more distant outer-scope statements follow

#### Scenario: Branch ownership determines continuation
- **WHEN** returns end `elif` or final `else` branches in a chain followed by another statement, or end ordinary nested `if` branches inside an `else`
- **THEN** chain branches use the continuation after the initial `if`, while an ordinary nested `if` requires its own following sibling inside the `else` suite
- **THEN** guards nested in other suites qualify only with a following statement in that same enclosing suite; statements inside nested definitions or in a more distant outer scope do not supply continuation

#### Scenario: Committed continuation determines return eligibility
- **WHEN** only a guard return line is added and the owning `if` and following statement are unchanged outside the diff hunk, while the worktree differs from the requested committed head
- **THEN** eligibility follows branch membership and continuation in the committed complete file, and only the added return line receives the new candidate

#### Scenario: Unsupported return layouts are untouched
- **WHEN** a guard return shares a physical line with another statement, uses an inline suite, or spans multiple physical lines through parentheses, strings or explicit continuation
- **THEN** no `drop-return` candidate is offered and existing operator eligibility is preserved

### Requirement: Control flow candidate compatibility

Python control-flow operators SHALL retain committed-source, path, exclusion,
protection and error contracts. They SHALL join seeded sampling with exact
line identity, count limits, one mutant per line and only the selected span
changed. Execution, equivalence, scoring and timeouts SHALL remain unchanged
apart from configured invalid IDs. New C-family dispatch SHALL preserve Python
and unsupported-extension behavior.

#### Scenario: Existing exclusions cover new operators
- **WHEN** condition or return syntax appears in comments, docstrings, runtime strings, non-Python files, unchanged lines, paths outside `mutation_paths`, or lines matching `mutation_skip`
- **THEN** those occurrences produce no candidates from either new operator, Python and unsupported-extension operator behavior remains unchanged; supported C-family files follow their explicit operator and line rules, and excluded candidates consume no sample budget

#### Scenario: Mixed operators retain stable spreading
- **WHEN** the same inputs, count and seed are used repeatedly on a pool containing old and new operators, including multiple operators on a return line
- **THEN** selected mutants and their order are identical, the requested positive count is not exceeded, at most one mutant per path and physical line is selected, and before text and line numbers match the original source
- **THEN** Python and unsupported-extension inputs with no eligible new control-flow operators retain their previous ordered selection

#### Scenario: Analysis failures still propagate
- **WHEN** a file containing potential new candidates has missing committed source or fails required Python parsing or tokenization, even if its lines match an exclusion
- **THEN** generation raises an error identifying the path before sampling and gather writes no successful partial evidence or no-mutable-lines skip

### Requirement: Guard clause diffs produce evidence

With mutation enabled and no exclusions, a Python diff adding only `if blocked:`
and its indented `return None` before unchanged function code SHALL offer both
`negate-condition` and `drop-return`. A budget of at least two SHALL select both;
a budget of one SHALL select one using existing seeded sampling without operator
priority. Execution and measurement rules SHALL remain unchanged.

#### Scenario: Guard only change is measured
- **WHEN** a committed diff adds only the two guard lines before an unchanged `return fallback`, with a non-`None` fallback, the source under `mutation_paths`, a mutation budget of two, and passing baseline checks for blocked and unblocked inputs
- **THEN** real generation yields both `negate-condition` and `drop-return`, the latter replacing the added `return None` with an equally indented `pass`, and checks against each separately mutated source kill both mutants
- **THEN** no `no mutable lines added under mutation_paths` skip is reported, evidence is measured with score `1.0`, and persisted evidence contains both mutants and their killed results; condition negation alone does not satisfy the scenario

### Requirement: Mutation command definitions

The manifest SHALL accept optional `[[mutation_step]]` definitions with the verify-step shape and defaults as `Manifest.mutation_step`, defaulting to empty. Mutation references SHALL resolve against verify and mutation-only definitions. Mutation-only steps SHALL have unique IDs across verify, prepare and acceptance namespaces and SHALL NOT join those phases or coverage. Existing valid manifests SHALL retain their commands and defaults.

#### Scenario: Definitions round trip
- **WHEN** a manifest defines mutation-only steps with omitted or explicit expect and timeout values and is templated with run overrides and loaded again
- **THEN** argv, IDs, default expect `0`, default timeout `300`, explicit values and mutation references are preserved, and normal verify and coverage selections are unchanged

#### Scenario: Invalid definitions rejected
- **WHEN** mutation-only steps have malformed argv, duplicate or colliding IDs, or mutation references name undefined steps
- **THEN** parsing raises `ValueError` identifying the offending definition or setting before any verification starts

#### Scenario: Placeholder defaults to full outside mutation
- **WHEN** a referenced verify command contains `{mutant_path}` and ordinary verification is requested
- **THEN** its placeholder expands to an empty string without adding mutation-only steps to verification; evidence may override that reserved value only in its own copied manifest
- **THEN** a nonempty configured override of the reserved value is rejected, and manifests without that placeholder retain their existing variables

### Requirement: Ordered mutation routes

The manifest SHALL accept ordered `[[gates.mutation_route]]` tables with nonempty `paths` and `steps` arrays as `Manifest.mutation_routes`, defaulting to empty. Routes SHALL require fallback `mutation_steps`. The first matching route SHALL supply commands in listed order; unmatched files SHALL use fallback declaration order. Routing SHALL preserve mutant selection and existing path-policy semantics.

#### Scenario: First matching route wins
- **WHEN** a mutant matches overlapping routes whose step lists differ
- **THEN** only the first matching route's commands execute in its listed order, and later routes and fallback commands do not run for that mutant

#### Scenario: Unmatched file uses fallback
- **WHEN** a mutant's path matches no configured route
- **THEN** it executes the resolved `mutation_steps`, with verify commands in their original declaration order followed by selected mutation-only commands in declaration order

#### Scenario: Routes validate and round trip
- **WHEN** routes are saved and reloaded through a manifest template, or contain malformed tables, empty arrays, non-string entries, unknown or repeated step IDs, absolute or traversing patterns, backslashes, or no fallback steps
- **THEN** valid routes retain their exact declaration order and entries, and invalid configurations raise `ValueError` naming `gates.mutation_route` and the offending entry

#### Scenario: Globs have explicit semantics
- **WHEN** routes use `*`, `?`, character classes and directory patterns against mixed-case repository-relative paths
- **THEN** matching follows case-sensitive `fnmatchcase`, including `*` crossing `/` and no special additional meaning for `**`, while `mutation_paths`, allowed paths and frozen paths keep their current matching rules

#### Scenario: Legacy manifest is unchanged
- **WHEN** a legacy manifest has neither routes nor mutation-only definitions nor a mutant-path placeholder
- **THEN** it retains its defaults, mutation enablement and skip behavior, fallback verify declaration order, one full baseline, one run per executable mutant, timeout budgets, output locations, evidence JSON shape, classifications and score
- **THEN** the only additional output is the decision receipt, and omitting routes or explicitly supplying an empty route array has the same behavior

### Requirement: Python import selector

`examples/specloop/select_tests.py` SHALL use only the standard library and expose `select_tests(root: Path, mutant_path: str) -> list[str]` plus a CLI with an optional path and `--root`. It SHALL statically inspect Python test imports without executing project code, return sorted unique repository-relative test paths, and print one per line. Empty output SHALL mean the caller runs all tests. Selection SHALL be a performance hint, with full confirmation responsible for survival.

#### Scenario: Importing tests are selected
- **WHEN** fixture tests under `tests/` import a mutated repository-root Python module with `import`, `from`, aliases, imports inside functions or resolvable relative imports
- **THEN** the selector includes the matching `test_*.py` and `*_test.py` files once in sorted order, excludes comments, string lookalikes and unrelated module-name prefixes, and handles a package initializer by including imports of that package or descendants
- **THEN** its CLI and function agree, work with an explicit root outside the current directory, and never execute the inspected imports

#### Scenario: Uncertainty falls back to all tests
- **WHEN** input is empty, unsupported, outside the root or unresolvable, the test tree is absent, no direct imports match, or a scanned file cannot be read or parsed
- **THEN** the selector returns an empty list and the CLI prints nothing with exit zero, allowing the caller to run the full test suite
- **THEN** no transitive dependency graph or dynamic import execution is attempted

### Requirement: Extension operator selection

The generator SHALL use the C-family set only for exact suffixes `.dart`, `.ts`,
`.swift`, `.kt`, `.c` and `.cpp`. Python `.py` and `.pyi` SHALL retain the current
ordered operators and AST operators. Other suffixes SHALL retain the legacy
fallback. Only eligible added lines under configured mutation paths SHALL
enter the shared seeded sampler.

#### Scenario: Dart operator directions
- **WHEN** separate eligible Dart lines contain each of `true`, `false`, `&&`, `||`, `==`, `!=`, `<`, `<=`, `>`, `>=` and a leading logical `!`
- **THEN** candidates swap each pair in both directions or remove only the leading `!`, preserving all other characters and original line identity
- **THEN** standalone integers, arithmetic and Python keywords offer no C-family candidates

#### Scenario: Suffix dispatch and token boundaries
- **WHEN** the same lines are supplied under each supported suffix, alongside Boolean identifier fragments, strict equality, shifts, arrows, logical assignments and postfix exclamation marks
- **THEN** all six suffixes use the same ordered table and token guards defined in the design, including compact comparisons; fragments and longer tokens offer no embedded matches
- **THEN** unsupported suffixes including `.js`, `.tsx`, `.cc` and `.DART` retain their legacy fallback without any new refusal

#### Scenario: Python operators remain unchanged
- **WHEN** representative `.py` and `.pyi` fixtures exercise current Boolean, comparison, arithmetic, integer, bare-condition and guard-return operators with fixed seeds
- **THEN** exact ordered mutants, replacements and existing exclusions match the pre-change behavior, including Python runtime-string eligibility
- **THEN** the annotated legacy overlap predicate and all assertions in `tests/test_mutation_prose_annotations.py` remain unchanged, and C-family candidates use a separate overlap check

### Requirement: C family quote line exclusion

A C-family mutation candidate SHALL be excluded whenever its original physical
line contains an apostrophe, double quote or backtick anywhere. This test SHALL
precede sampling, apply to direct generation and require no string parsing or
whole-file refusal.

#### Scenario: Quoted lines are excluded
- **WHEN** lines contain quoted strings, quote characters in comments, backticks, escaped quotes or the Kotlin text `val s = """${"""true"""}"""`
- **THEN** those entire lines produce no mutants, including apparent code outside the quoted text, and consume no sampling budget

#### Scenario: Other lines remain eligible
- **WHEN** a committed file contains unchanged Dart interpolation, Kotlin raw strings or TypeScript/Swift division and an eligible added quote-free line
- **THEN** the added line remains eligible without interpolation, division or language-specific parsing and without rejecting the file
- **THEN** repeated inputs and seeds produce identical selection with at most one mutant per line

### Requirement: C family comment line exclusion

C-family generation SHALL reject lines whose first non-whitespace text begins
with `//`, `/*` or `*`. It SHALL retain existing prefix/declaration exclusions
and full committed-source protection for complete comment-only boundary blocks,
using a separate overlap check and no inline-comment parser.

#### Scenario: Prefixes and complete blocks are protected
- **WHEN** C-family source contains indented comment prefixes or bare Boolean/comparison prose inside a complete block whose boundaries occur on unchanged lines across diff hunks
- **THEN** those lines produce no mutants while eligible added code after the block remains available, even if the worktree differs from the committed head
- **THEN** direct generation applies the three specified prefixes and existing added-line filtering retains its other exclusions

#### Scenario: Existing skips and empty pools are preserved
- **WHEN** configured cosmetic-literal substrings or automatic line/block exclusions remove some or all C-family candidates
- **THEN** exclusions precede sampling and consume no budget; unexcluded candidates preserve exact line numbers and before text
- **THEN** an empty pool retains `no mutable lines added under mutation_paths`, null score and coverage checks without baseline/workers or unmeasured status, while missing committed source still raises an error

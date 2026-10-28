## Purpose

Select extension-appropriate mutants while preserving legacy Python behavior
and applying conservative physical-line exclusions to C-family files.

## ADDED Requirements

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

## MODIFIED Requirements

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

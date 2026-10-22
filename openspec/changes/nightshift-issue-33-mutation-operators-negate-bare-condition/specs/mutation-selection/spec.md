## Purpose

Extend Python mutation generation to cover bare conditions and nonterminal
returns while retaining the existing source, exclusion and sampling contracts.

## ADDED Requirements

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

### Requirement: Nonterminal return replacement

The generator SHALL offer `drop-return` for an eligible added, standalone,
single-line `return` or `return <expr>` only with a later sibling in its immediate
AST statement list. It SHALL replace the statement span with `pass`, preserving
indentation, trailing whitespace, comments and line numbers. Other suites,
comments and blank lines SHALL NOT establish eligibility. Unsupported layouts
SHALL receive no `drop-return` candidate; existing operators SHALL remain eligible.

#### Scenario: Bare and valued nonterminal returns are dropped
- **WHEN** an added standalone `return` or `return <expr>` has a later sibling statement in its immediate suite, in a normal or async function
- **THEN** it offers a `drop-return` candidate with `pass` replacing the complete return statement, exact indentation and trailing comment preservation, and compilable source that permits execution to continue to the next statement

#### Scenario: Terminal returns stay intact
- **WHEN** a return is last in its immediate suite, including a sole guard-body return followed by outer function code or a return followed only by another clause, comments or blank lines
- **THEN** it offers no `drop-return` candidate, even when other suites or enclosing scopes contain later statements

#### Scenario: Committed siblings determine return eligibility
- **WHEN** only a return line is added and its later sibling is unchanged outside the diff hunk, while the worktree differs from the requested committed head
- **THEN** eligibility follows the immediate statement list in the committed complete file, including nested suites, and neither worktree contents nor statements inside a different nested scope determine eligibility

#### Scenario: Unsupported return layouts are untouched
- **WHEN** a nonterminal return shares a physical line with another statement, uses an inline suite, or spans multiple physical lines through parentheses, strings or explicit continuation
- **THEN** no `drop-return` candidate is offered and existing operator eligibility is preserved

### Requirement: Control flow candidate compatibility

Both operators SHALL recognize executable `.py` and `.pyi` syntax and obey
existing committed-source, path, line-exclusion, prose-protection and error
contracts. Candidates SHALL enter the existing pool before seeded sampling,
retaining the count limit and one-mutant-per-line rule. Original line numbers
and `Mutant.before` SHALL remain exact; only the selected code span SHALL change.
Execution, equivalence, scoring and timeout requirements SHALL remain unchanged.

#### Scenario: Existing exclusions cover new operators
- **WHEN** condition or return syntax appears in comments, docstrings, runtime strings, non-Python files, unchanged lines, paths outside `mutation_paths`, or lines matching `mutation_skip`
- **THEN** those occurrences produce no candidates from either new operator, existing operator behavior remains unchanged, and excluded candidates consume no sample budget

#### Scenario: Mixed operators retain stable spreading
- **WHEN** the same inputs, count and seed are used repeatedly on a pool containing old and new operators, including multiple operators on a return line
- **THEN** selected mutants and their order are identical, the requested positive count is not exceeded, at most one mutant per path and physical line is selected, and before text and line numbers match the original source
- **THEN** inputs with no eligible new operators retain their previous ordered selection

#### Scenario: Analysis failures still propagate
- **WHEN** a file containing potential new candidates has missing committed source or fails required Python parsing or tokenization, even if its lines match an exclusion
- **THEN** generation raises an error identifying the path before sampling and gather writes no successful partial evidence or no-mutable-lines skip

### Requirement: Guard clause diffs produce evidence

With mutation enabled, a positive mutation count and no applicable exclusions,
the gate SHALL generate at least one mutant for a Python diff adding only
`if blocked:` and its indented `return None` before unchanged function code.
The gate SHALL retain its existing execution and measurement rules for the
generated candidates.

#### Scenario: Guard only change is measured
- **WHEN** a committed diff adds only the two guard lines before an unchanged `return fallback`, with the source under `mutation_paths` and passing baseline checks for blocked and unblocked inputs
- **THEN** real generation yields at least one mutant through condition negation, no `no mutable lines added under mutation_paths` skip is reported, and checks that detect the negated behavior kill the mutant
- **THEN** evidence is measured with score `1.0`, and persisted evidence contains the generated mutant and its killed result

## Purpose

Select behavioral mutation candidates without spending the sample on configured
cosmetic lines, docstrings, comments or inline JSX style integers.

## ADDED Requirements

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
- **WHEN** added lines are inside a docstring, block comment or JSX style object opened on unchanged lines, and the worktree differs from the requested head
- **THEN** eligibility follows the committed full-file context across diff hunks, never the worktree or diff fragment alone

#### Scenario: Stable selection and exact replacement
- **WHEN** the same committed source, exclusions, count and seed are supplied repeatedly
- **THEN** the selected mutants and their order are identical, at most one mutant is selected per physical line, original line numbers and before text are preserved, and after text differs only at an eligible operator match

#### Scenario: Analysis failure cannot become a skip
- **WHEN** required committed source cannot be read, Python source cannot be parsed or tokenized, or the lexical scan encounters an unterminated construct
- **THEN** the operation raises an error naming the path and does not write successful partial evidence or label the analysis failure as no mutable lines

#### Scenario: Exclusions leave no mutable lines
- **WHEN** automatic or configured exclusions remove every candidate
- **THEN** gather records `no mutable lines added under mutation_paths`, starts neither a baseline nor mutant workers, retains a null score and existing coverage checks, and does not mark the skipped evidence unmeasured

### Requirement: Prose is never mutated

The generator SHALL exclude Python docstring spans and comment spans from
mutation. It SHALL recognize Python comments and docstrings using full-source
syntax, and `//` and `/* ... */` comments in JS/TS/JSX/TSX and C-family source
using lexical context. Other extensions SHALL retain generic `#`, `//` and
`/* ... */` comment conventions outside quoted text. Comment-only lines SHALL
produce no mutants; executable portions of mixed lines SHALL remain eligible.

#### Scenario: Python docstrings and comments excluded
- **WHEN** module, class, function or async-function docstrings contain mutable prose or numbers, including both triple-quote forms, prefixes, escaped delimiters and multiline bodies, alongside comment-only and trailing-comment text
- **THEN** no mutation overlaps a docstring or comment, while executable code before a comment or after a closing docstring remains eligible and its adjacent prose is preserved exactly

#### Scenario: C style and JSX comments excluded
- **WHEN** JS, TS, JSX, TSX or C-family source contains line comments, multiline block comments and code on either side of a block comment, including JSX comment expressions
- **THEN** no comment span is mutated, block-comment state spans unchanged lines, and executable spans outside comments remain eligible

#### Scenario: Delimiters in runtime strings retain their meaning
- **WHEN** Python runtime strings, JS strings or regexes contain apparent comment delimiters, or a JS template includes comment-like text and an interpolation containing executable code and a real comment
- **THEN** quoted or regex delimiters do not start comments, interpolation comments are protected, and eligible executable expressions outside those comments remain available for mutation

### Requirement: JSX style integers excluded by default

Without manifest configuration, the generator SHALL suppress integer-literal
mutations inside JSX `style` attributes containing object expressions in `.jsx`
and `.tsx` files. Recognition SHALL tolerate whitespace and multiline nested
objects and distinguish actual attributes from strings and ordinary properties.
This default SHALL NOT suppress other mutation operators or numbers outside
the style object.

#### Scenario: Inline and multiline styles excluded
- **WHEN** JSX or TSX has `style={{ fontWeight: '600', paddingVertical: 12 }}` or a whitespace-separated multiline equivalent containing nested objects and numeric expressions
- **THEN** no integer mutant is produced within the style object, including quoted digits, irrespective of whether its opening line was changed

#### Scenario: Style boundaries preserve behavioral candidates
- **WHEN** a line contains a style object and numeric code outside it, or the source contains ordinary objects named `style`, `lifestyle` identifiers, strings containing `style={{`, braces inside strings/comments, or non-integer operators inside an actual style object
- **THEN** only integer matches inside the actual style object are suppressed, its closing boundary is recognized correctly, and the other eligible matches remain candidates

### Requirement: Prose equivalence annotations removed

Repository Python sources under `nightshift/` and `specloop/` SHALL contain no
`mutation-equivalent` marker inside a docstring and no equivalence annotation
whose subject is only a docstring or comment. Cleanup SHALL preserve annotations
that justify executable code and ordinary documentation or strings explaining
the annotation mechanism.

#### Scenario: Prose markers removed
- **WHEN** the package sources are scanned after cleanup
- **THEN** no docstring contains the marker and known annotations preceding prose-only docstrings or comments are removed without deleting the surrounding documentation

#### Scenario: Real code annotations retained
- **WHEN** a valid inline or immediately preceding comment annotation justifies an executable line
- **THEN** it is preserved, mutations of that code retain equivalent classification without verification commands, and its comment text is never mutated

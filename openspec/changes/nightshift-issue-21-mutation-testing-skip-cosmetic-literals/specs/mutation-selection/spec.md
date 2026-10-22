## Purpose

Select behavioral mutation candidates without spending the sample on configured
cosmetic lines, Python docstrings and comments, supported non-Python comment
lines or inline JSX style integers.

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
- **WHEN** required committed source cannot be read or Python source cannot be parsed or tokenized
- **THEN** the operation raises an error naming the path and does not write successful partial evidence or label the analysis failure as no mutable lines

#### Scenario: Exclusions leave no mutable lines
- **WHEN** automatic or configured exclusions remove every candidate
- **THEN** gather records `no mutable lines added under mutation_paths`, starts neither a baseline nor mutant workers, retains a null score and existing coverage checks, and does not mark the skipped evidence unmeasured

### Requirement: Prose is never mutated

The generator SHALL protect Python docstring and comment spans using full-source
`ast` and `tokenize`, retaining adjacent code eligibility. Non-Python filtering
SHALL retain existing comment-prefix exclusions and add only line-level tracking
of `/* ... */` blocks with comment-only boundary lines. It SHALL NOT add a general
C-style or JS/TS lexer, regex-versus-division detection, template interpolation
parsing or non-Python inline comment parsing.

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
- **THEN** the new block filter adds no protected range for those forms, existing prefix exclusions remain in effect, eligible code outside supported blocks remains available, and no lexical parsing error is raised

### Requirement: JSX style integers excluded by default

By default, the generator SHALL suppress integer mutants within confirmed JSX
`style` object spans in `.jsx` and `.tsx`, using brace counting that ignores
single, double and backtick-quoted strings and honors escapes. It SHALL leave
uncertain candidates eligible without modeling regex or template interpolation.
It SHALL NOT suppress other operators or numbers outside the style object.

#### Scenario: Inline and multiline styles excluded
- **WHEN** JSX or TSX has `style={{ fontWeight: '600', paddingVertical: 12 }}` or an equivalent with whitespace around `=` and between braces, multiline nested objects and supported numeric expressions
- **THEN** no integer mutant is produced within the style object, including quoted digits, irrespective of whether its opening line was changed

#### Scenario: Style boundaries preserve behavioral candidates
- **WHEN** a line contains a style object and numeric code outside it, or the source contains ordinary objects named `style`, `lifestyle` identifiers, strings containing `style={{`, braces inside single, double or backtick-quoted strings including escaped quotes, or non-integer operators inside an actual style object
- **THEN** only integer matches inside the actual style object are suppressed, its closing boundary is recognized correctly with backtick contents treated as opaque including `${...}` text, and the other eligible matches remain candidates

#### Scenario: Uncertain style spans remain eligible
- **WHEN** a candidate style object has no confirmed closing boundary or contains an unquoted slash that could be division, a regex or an inline comment
- **THEN** that candidate adds no integer exclusions and causes no parsing error, numeric candidates outside it remain eligible, and the filter does not distinguish regex from division or parse template interpolation

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

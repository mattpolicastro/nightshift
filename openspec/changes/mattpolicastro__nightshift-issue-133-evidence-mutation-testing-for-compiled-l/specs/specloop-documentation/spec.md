## Purpose

Explain native mutation configuration and its limits with a structurally tested
Dart example, leaving real-project usefulness to human validation.

## ADDED Requirements

### Requirement: Native evidence example

The evidence guide SHALL document invalid-step replacement/defaults, prepare
precedence, C-family suffixes and operators, physical-line exclusions and their
limits, citing issue #133. It SHALL link a parseable Dart manifest example and
identify first-run validation on scheming-vizier as a pending human check.

#### Scenario: Dart configuration is structurally valid
- **WHEN** `examples/specloop/dart.toml` is loaded with the real manifest parser
- **THEN** mutation selects defined `dart-analyze` then `dart-tests` steps, classifies `dart-analyze` failures as invalid and targets `lib/**`, with commands using the configured toolchain and prepared writable project
- **THEN** tests check argv, IDs, order and placeholders without invoking Dart, installing dependencies or claiming project-specific coverage integration

#### Scenario: Guidance states operational limits
- **WHEN** the evidence passage and example are reviewed using the design's deterministic physical-line boundary and standalone-link rules
- **THEN** the link resolves, ambiguous boundaries block validation and no prose-exact assertions or Markdown parsing are introduced
- **THEN** guidance explains the conservative loss of string-containing lines, limited comment protection, lack of whole-file refusal and pending real-engine usefulness check without claiming fixtures prove operational support

## MODIFIED Requirements

### Requirement: Mutation evidence guidance

The guide SHALL explain mutant concurrency, classifications, exclusions and
guard-clause operators with their implementation PR citations, plus native
operator selection and configurable invalid steps with the issue #133 citation. It SHALL
preserve the distinction between measured evidence, unmeasured generated
mutants and a no-candidate skip.

#### Scenario: Parallel and timed out mutants are interpreted
- **WHEN** a reader configures or reads a section mutation result
- **THEN** the guide describes baseline-first isolated execution, default `mutation_parallel = 2`, serial value `1`, and scoring only killed/survived results
- **THEN** it distinguishes selected-test `timeout` from preparation/configured-step `invalid` (configure/build by default, replaced by an explicit array), excludes timeouts from both score terms, and cites issue #4 / PR #27 and issue #31 / PR #44

#### Scenario: Exclusions do not fabricate evidence
- **WHEN** prose, comment lines or configured skip patterns remove candidates, or all generated results are equivalent, invalid or timed out
- **THEN** the guide describes Python docstring/comment protection, limited non-Python comment filtering and case-sensitive literal-substring `mutation_skip` before sampling
- **THEN** it explains that generated mutants require at least one killed or survived result; otherwise evidence is unmeasured and pauses without strengthening tasks, while no mutable lines is a separate skip retaining coverage checks, citing issue #21 / PRs #48 and #63

#### Scenario: Guard clauses produce candidates
- **WHEN** a reader checks evidence for Python guard changes
- **THEN** the guide illustrates `negate-condition` for eligible bare-name `if`/`while` headers and `drop-return` replacing an eligible branch-ending guard return with `pass` when its owning conditional has a following statement
- **THEN** it names the restrictions and preserved exclusions rather than promising mutation of every condition or return, citing issue #33 / PR #64

## Purpose

Use native files and manifest-selected task parsing throughout drafting and
build prompts while preserving OpenSpec behavior.

## ADDED Requirements

### Requirement: Format aware build prompts

All four build consumers SHALL select IMPLEMENT, REVIEW, GATE_REVIEW and ARBITER
variants through `specloop/prompts.py` using manifest format, without loop format
detection. Native variants SHALL name real spec, plan and constitution paths.
Scenario text, the scenario-ID testing sentence, schemas, review bases and
arbitration rules SHALL remain unchanged. Omitted or explicit OpenSpec format
SHALL preserve the original rendered bytes.

#### Scenario: Native consumers read real files
- **WHEN** implementation, task review, phase review or arbitration renders its prompt for a native feature
- **THEN** every named native document resolves to spec.md, plan.md or .specify/memory/constitution.md in its context, with no dependency on the removed aliases
- **THEN** task.md or section.md retains full scenario text and the implementation scenario-ID sentence is unchanged

#### Scenario: OpenSpec consumers retain exact prompts
- **WHEN** the same four consumers render with omitted or explicit openspec format
- **THEN** every existing exact OpenSpec prompt assertion passes unchanged, with original proposal.md, design.md and specs/ context destinations

## MODIFIED Requirements

### Requirement: Native context and human checks

The native adapter SHALL provide the selected feature's spec and plan and the
repository constitution as existing context files, in that order, with
context-relative destinations `spec.md`, `plan.md` and
`.specify/memory/constitution.md`. It SHALL no longer supply proposal.md,
design.md, specs/spec.md or specs/constitution.md aliases because native
consumers now name real files. It SHALL read human checks from committed
plan.md, use the existing literal collector and preserve publication's
deduplication/rendering. Missing or unreadable committed plans SHALL block
rather than imply no checks.

#### Scenario: Selected feature context
- **WHEN** implementation, task review, phase review or arbitration assembles native context
- **THEN** native spec content is copied to spec.md, plan content to plan.md and constitution content to .specify/memory/constitution.md, without transitional OpenSpec aliases
- **THEN** copies retain original native bytes, with no invented capability-directory assumption, OpenSpec document conversion or automatic historical feature traversal
- **THEN** OpenSpec retains its existing context destinations and directory copying

#### Scenario: Plan checks use the committed revision
- **WHEN** a committed plan contains unchecked lines under exact ## Human checks, nested headings, repeated sections and literal examples, while source worktree text differs
- **THEN** the collector returns the committed pending lines in order with duplicates, following existing physical-line boundaries and excluding checked/empty/unrelated items
- **THEN** readiness and the PR body retain existing deduplication, manifest exemptions and checklist rendering

#### Scenario: Plan failure blocks readiness
- **WHEN** the committed plan is missing, Git lookup/read fails or UTF-8 decoding fails
- **THEN** readiness/publication fails with a diagnostic instead of dropping human checks
- **THEN** a readable plan without pending selected lines returns an empty list

### Requirement: Manifest selected task operations

Task operations SHALL dispatch only through the manifest-selected adapter.
OPENSPEC SHALL delegate at call time to unchanged tasks functions; SPECKIT
SHALL delegate to speckit.parse_tasks and native mutation helpers. The build
consumers SHALL use adapter parsing, mutations and review-round reconstruction.
Proposal lint and overlap checks SHALL also use adapter parsing, replacing
#188's explicit temporary exclusion of drafting from that routing.
No implementation SHALL detect or refuse mixed heading styles. tasks.py and
Engine routing compatibility SHALL remain unchanged.

#### Scenario: OpenSpec ignores native heading declarations
- **WHEN** an OpenSpec task document contains literal ## Phase 1: Example lines, including inside examples, and is parsed, ticked, split, extended or appended through OPENSPEC
- **THEN** task records and returned mutation bytes exactly match the unchanged corresponding tasks functions, including their numeric-section recognition, checkbox spelling, numbering and error behavior
- **THEN** adapter parse_tasks delegates to tasks.parse, and tick, split, extend_section and append_section delegate to their same-named tasks functions
- **THEN** native headings neither select SPECKIT nor cause mixed-style refusal, and late replacement of a delegated tasks function is observed with its original arguments, return value and exception identity

#### Scenario: Native ignores OpenSpec phase declarations
- **WHEN** a native task document contains ## N. Title headings alongside literal native phase headings and is read or mutated through SPECKIT
- **THEN** only native PHASE headings create phases; numeric OpenSpec headings close the current phase under the existing same-or-higher heading rule and tasks outside native phases remain unrecognized
- **THEN** the document is not refused for mixing heading styles, native edits round-trip through speckit.parse_tasks, and late replacement of a delegated native helper is observed
- **THEN** repeated native phase numbers, reused numeric T-label identities and ambiguous native keys still refuse according to native rules

#### Scenario: Consumers use selected task operations
- **WHEN** admission, run, accept, status, PR body construction, finish, revision, proposal lint or overlap detection needs to read or mutate tasks
- **THEN** the manifest-selected adapter supplies parsing, ticking, splitting, extension, append or review-round reconstruction as applicable, including candidate replay and revision replay
- **THEN** loop.py contains no heading-style detection or format-specific task branch, drafting now uses the selected parser, and no run-state field or serialized adapter is added


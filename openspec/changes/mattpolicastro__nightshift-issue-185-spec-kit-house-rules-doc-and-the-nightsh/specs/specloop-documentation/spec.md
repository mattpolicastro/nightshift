## Purpose

Document the spec-kit authoring contract and its reasons without changing
Nightshift's engine behavior or implying that pending native support has shipped.

## ADDED Requirements

### Requirement: Spec-kit feature identity

`docs/specloop/speckit.md` SHALL explain feature directories `specs/<NNN>-<slug>/`
and branches `spec/<NNN>-<slug>`, using the repository's positive issue number
padded to at least three digits without truncation and the intake title-slug
algorithm (at most 40 lowercase ASCII characters, fallback `issue`). It SHALL
explain repository-local numbering, acceptable gaps, parallel-draft collision
avoidance and upstream directory-rule compatibility.

#### Scenario: Author chooses a stable feature identity
- **WHEN** an author reads the Feature directory and branch rule
- **THEN** the guide gives `042-add-a-gate` and `1234-big-feature` examples, the matching spec directories and spec branches, the precise slug rule and the numbering rationale
- **THEN** it explains repository-local issue numbers on GitHub and Gitea alike, why sequential scans collide, and compatibility with `^[0-9]{3,}-`
- **THEN** it preserves committed identities rather than renaming them after title edits, without changing existing OpenSpec naming

### Requirement: Spec-kit issue-first authoring

The guide SHALL instruct authors to open the issue before running
`/speckit.specify`, passing its number as the first argument (`42` or `#42`).
It SHALL explain the preset wrapper's `SPECIFY_FEATURE_DIRECTORY` override,
link spec-kit's `docs/overview/core.md`, and explain why sequential or timestamp
numbering configuration cannot derive a repository issue number.

#### Scenario: Author opens the issue before specifying
- **WHEN** an author follows the Open the issue first rule
- **THEN** both invocation forms use the issue title after the number and select `specs/042-add-a-gate` for Add a gate
- **THEN** the guide states the issue-first ordering and its reason without requiring a forge API lookup or a new numbering script

### Requirement: Spec-kit labelled scenarios

The guide SHALL require acceptance lines in the form
`N. **ASn** **Given** ... **When** ... **Then** ...`, numbered within each
user story and never renumbered once committed. It SHALL explain stable IDs
`<NNN>-<slug>/us<k>/as<n>`, include `042-add-a-gate/us1/as2`, and connect this
three-part shape to the unchanged scenario regex, pytest marker and per-story
grouping. It SHALL relate stable labels to `/speckit.converge`'s task-ID rule.

#### Scenario: Tests retain a scenario reference
- **WHEN** an author reads Labelled acceptance scenarios before adding or revising a story
- **THEN** the guide demonstrates the labelled list and full scenario citation, forbids renumbering or reusing committed IDs, and explains why tests can retain their references

### Requirement: Spec-kit task traceability

The guide SHALL retain spec-kit's task grammar
`- [ ] T001 [P] [US1] Description with file path`, require backticked scenario
IDs on every user-story task and at least one covering task for every scenario,
and explain the traceability reason. It SHALL document harness-owned review and
coverage additions under `## Phase N: Review round K`, using the next
`T{M+1:03d}` IDs after the maximum existing number and ticking tasks `[X]`.

#### Scenario: Author links tasks and scenarios
- **WHEN** an author reads Tasks cite scenarios
- **THEN** examples retain optional parallel markers, story tags and file paths while citing scenario IDs in backticks, and setup tasks need no invented story reference
- **THEN** the guide requires full scenario coverage and describes append-only harness rounds without implementing them in this change

### Requirement: Spec-kit human checks

The guide SHALL place human-only checks in `plan.md` under `## Human checks`
as `- [ ]` items, each naming exactly one scenario ID. It SHALL place
`<!-- nightshift:post-deploy -->` in the corresponding scenario body for
post-deploy checks, explain the analogy to OpenSpec's `design.md`, and explain
why human checks are neither implementing-agent tasks nor automated proof.

#### Scenario: Author records a live check
- **WHEN** an author reads Human-only checks
- **THEN** the guide shows the plan checklist, one scenario per item and the post-deploy marker's location in the scenario body
- **THEN** it does not require a plan template override or claim CI performed the check

### Requirement: Spec-kit flow-forward records

The guide SHALL describe immutable delivered feature records with harness task
updates and final `**Status**: Complete` in `spec.md`, without archival or merging
into a consolidated spec. It SHALL identify `.specify/memory/constitution.md`
as the overarching specification and require later behavior changes to name
superseded features in a new feature's `## Assumptions`. It SHALL explain stable
history and distinguish record persistence from Git PR merging.

#### Scenario: Author changes delivered behavior
- **WHEN** an author reads Flow-forward persistence
- **THEN** the guide explains the Complete status and new-feature supersession workflow rather than editing or archiving the delivered feature
- **THEN** it distinguishes drafting edits from delivered records and preserves existing OpenSpec lifecycle guidance

### Requirement: Spec-kit frozen authoring paths

The guide SHALL state that `specs/**` and `.specify/**` are frozen for
implementing agents, with the harness writing `tasks.md` and the Status line.
It SHALL explain protection of acceptance criteria and separation of authoring
from implementation without claiming that this documentation change implements
runtime freezing.

#### Scenario: Implementer leaves acceptance criteria intact
- **WHEN** an author reads Frozen paths
- **THEN** the guide names both globs, the harness-owned writes and the protection against an agent changing the criteria that judge its work

### Requirement: Spec-kit clarification boundary

The guide SHALL forbid unresolved `[NEEDS CLARIFICATION]` markers at approval,
including markers with a question suffix, and explain that remaining ambiguity
becomes drafter questions to the issue rather than a build. The reason SHALL
be unresolved intent requiring human input, not a claim of newly shipped
validator behavior.

#### Scenario: Draft still needs an answer
- **WHEN** an author reads No [NEEDS CLARIFICATION] at approval
- **THEN** the guide explains returning unresolved questions to the issue before approval and keeps enforcement work out of this change

### Requirement: Spec-kit discovery

The engine guide SHALL link the house rules with one sentence under How the
engine works, and the root README SHALL identify `presets/` under Layout.
Guidance SHALL distinguish authoring assets from pending engine/validator
support, preserve other in-flight changes and existing assertions, and correct
only passages actually made false in the three authorized operator docs.

#### Scenario: Find authoring guidance
- **WHEN** a reader follows the engine guide's new link or the root Layout entry
- **THEN** the house-rules document and preset are discoverable, local links resolve, and no unperformed installation or pending engine support is described as validated or shipped
- **THEN** the eight rules have distinct headings or bold leads and the guide contains `042-add-a-gate/us1/as2`, with the named additive documentation test checking those structures

## MODIFIED Requirements

### Requirement: Documentation integrity

Documentation-only refreshes SHALL stay within scoped docs and leave runtime code unchanged. They MAY add documentation-integrity tests in `tests/test_specloop_documentation_integrity.py` for the structures they introduce, and MAY add Markdown or YAML authoring assets under `presets/`, without changing any existing assertion, fixture or other test file. Runtime changes MAY update matching docs and tests within explicit scope, including justified schema, fixture or assertion migrations. All edits SHALL preserve unaffected requirements and assertions, use concise present-tense prose and verified references, and require green uv run pytest plus the applicable design's human checklist.

#### Scenario: Scope and regression checks remain intact
- **WHEN** the implementation diff and local validation are reviewed
- **THEN** documentation-only refreshes stay within scoped docs and may add documentation-integrity tests in `tests/test_specloop_documentation_integrity.py` for the structures they introduce and Markdown or YAML authoring assets under `presets/`, leaving runtime code, existing assertions, fixtures and other test files unchanged
- **THEN** root README edits remain limited to scoped specloop/front-door passages and authoring-asset Layout entries; runtime changes stay within their declared runtime, test and documentation scope
- **THEN** only explicitly justified runtime-contract migrations may replace affected assertions, preserving equivalent behavioral coverage, and every unaffected assertion remains intact
- **THEN** the previously specified migrations of the exact request schema, git runner doubles, status inventory seam and trusted post-delivery request/input assertions remain intact, preserving authorization-denial snapshots, exact args, offline inventory and all scheduling/delivery assertions
- **THEN** relative documentation links resolve and uv run pytest passes without weakening preserved behavior

#### Scenario: References and reader checks are reviewable
- **WHEN** the documentation is prepared for merge
- **THEN** documentation-only refreshes stay within scoped docs and may add documentation-integrity tests in `tests/test_specloop_documentation_integrity.py` for the structures they introduce and Markdown or YAML authoring assets under `presets/`, leaving runtime code, existing assertions, fixtures and other test files unchanged
- **THEN** changed behavior has adjacent verified PR citations when an implementation PR identity is known, or its verified tracking issue until then, issue and PR numbers are distinguished, and no pending work is described as shipped
- **THEN** the PR carries the applicable design's human checklist for live reference verification and a readability pass, retaining ledger verification and Matt's single readability pass for documentation refreshes, without assigning these checks as implementing-agent tasks

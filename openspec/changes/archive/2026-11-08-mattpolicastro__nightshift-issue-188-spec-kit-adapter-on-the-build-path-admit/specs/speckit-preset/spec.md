## Purpose

Keep the shipped preset's support guidance and task scaffold consistent with
the native build adapter while retaining its authoring and installation contracts.

## ADDED Requirements

### Requirement: Native build support guidance

The preset README SHALL replace pending build-support claims with explicit
native builds using a trusted `format = "speckit"` manifest and links to the
engine guide and manifest example. It SHALL distinguish asset installation
from build configuration, retain prerequisites and installation guidance, and
leave native drafting/front-door support and unperformed live checks pending.

#### Scenario: Preset reader finds the native build path
- **WHEN** an operator reads the shipped preset README after installing its authoring assets
- **THEN** guidance points to explicit native admission/build/publication with a trusted native manifest, manifest-frozen `specs/**` and `.specify/**`, and Finish setting `**Status**: Complete` without archival
- **THEN** installation alone does not enroll or configure a build; the v1.3.2 baseline, PyYAML/interpreter advice, checkout/release installation commands, issue-first example, house-rules/upstream links and pending scratch-install/live-reference checks on #184 remain intact
- **THEN** new checks use deterministic physical-line boundaries and resolving relative links, and all existing preset and plugin test assertions remain unchanged

## MODIFIED Requirements

### Requirement: Scenario-citing task scaffold

`templates/tasks-template.md` SHALL keep spec-kit's checkbox/T-ID/optional-P/
story-tag/file-path grammar. Its Format section SHALL require every story task
to cite its covered scenario IDs in backticks and every scenario to have at
least one task. All example story tasks SHALL follow that rule. It SHALL
document stable IDs, harness-only uppercase ticks and three distinct harness
addition forms: review feedback appends `## Phase N: Review round K`, section
evidence/reviewer fixes extend their existing phase, and final coverage or
acceptance fixes append `## Phase N: Coverage` or `## Phase N: Acceptance fixes`.
New phases SHALL use the largest existing phase number plus one; review rounds
SHALL use the largest existing review-round number plus one; every new base
task ID SHALL follow the largest existing numeric T root as `T{M+1:03d}` without
truncation. The scaffold SHALL keep human-only checks outside implementation
tasks. This replaces the prior wording that conflated review and coverage
rounds; this template change adds no harness code.

#### Scenario: Task examples preserve scenario traceability
- **WHEN** an author uses the preset task scaffold
- **THEN** its Format section and every story-task example demonstrate backticked three-part IDs, story tags and file paths, while setup tasks need no artificial scenario
- **THEN** the template explains `## Phase N: Review round K` for review feedback, subsequent `T{M+1:03d}` IDs and `[X]` harness completion without assigning human-only checks to implementers

#### Scenario: Harness additions use the matching phase form
- **WHEN** an author reads `## Harness-owned Review and Completion` in the shipped task template
- **THEN** review feedback alone creates a Review round K phase, section evidence/reviewer fixes extend the current phase, and final coverage/acceptance fixes create phases with their supplied Coverage or Acceptance fixes titles
- **THEN** existing task IDs and scenario citations remain stable, new phases and T roots advance from their respective maxima, and only the harness changes completion ticks or frozen authoring files
- **THEN** documentation checks select the unique exact column-zero heading through the next same-or-higher heading and refuse missing or duplicate boundaries without interpreting fences, HTML or indentation

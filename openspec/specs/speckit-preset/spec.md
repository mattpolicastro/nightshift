# speckit-preset Specification

## Purpose
Supply a spec-kit preset that expresses Nightshift's issue-first feature identity
and scenario traceability rules using Markdown templates and a composed command.

## Requirements

### Requirement: Preset manifest

`presets/nightshift-speckit/preset.yml` SHALL use spec-kit's scaffold schema,
`schema_version` string `1.0`, `preset.id` equal to `nightshift-speckit`, versioned
metadata and a spec-kit minimum of 1.3.2. Its `provides.templates` SHALL declare
`spec-template`, `tasks-template` and `speckit.specify` with matching `replaces`
values and existing package-relative files, using replace for templates and wrap
for the command. No plan or script override is provided.

#### Scenario: Manifest resolves all provided files
- **WHEN** `test_speckit_preset_manifest_parses` reads the preset's JSON-compatible YAML
- **THEN** standard-library JSON parsing establishes the restricted YAML document's validity and assertions verify the preset identity, three entries, replacement targets, strategies and existing files within the package root
- **THEN** no new parser, dependency, tool installation or network access is needed for the test

### Requirement: Labelled spec scaffold

`templates/spec-template.md` SHALL retain the core spec scaffold sections and
three initial user-story blocks, each with `**Acceptance Scenarios**:` and
labelled `N. **ASn** **Given** ... **When** ... **Then** ...` entries starting
at AS1 per story. It SHALL state the full scenario-ID convention, committed-ID
stability, `spec/<NNN>-<slug>` branch, Draft-to-Complete status convention and
supersession in Assumptions, without adding a plan template.

#### Scenario: Each story starts with a labelled acceptance list
- **WHEN** `test_speckit_spec_template_labels_scenarios` reads the shipped spec template
- **THEN** each of the three initial user-story blocks has an AS1 entry within its acceptance list and every example entry has matching numeric and AS labels plus Given, When and Then
- **THEN** deterministic physical-line boundaries reject missing, duplicate or indented structural candidates without interpreting Markdown fences, HTML or indentation semantics

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

### Requirement: Issue-first command wrapper

`commands/speckit.specify.md` SHALL wrap one `{CORE_TEMPLATE}` after an issue-first
preamble. It SHALL consume a positive issue token and nonempty title, calculate
the house-rule identity, and establish `SPECIFY_FEATURE_DIRECTORY` before core
steps/hooks with explicit cross-process propagation. It SHALL pass
`GIT_BRANCH_NAME` to the Git hook or require an author-selected matching branch,
preserving core composition and feature-directory persistence without a script.

#### Scenario: Select the issue directory before core execution
- **WHEN** an author invokes `/speckit.specify 42 Add a gate` or `/speckit.specify #42 Add a gate`
- **THEN** the wrapper instructs the agent to use `specs/042-add-a-gate` and `spec/042-add-a-gate`, strip the issue token from effective description arguments, propagate explicit overrides before hooks and core resolution, and preserve the core's composed template and `.specify/feature.json` persistence
- **THEN** the positive decimal issue token accepts an optional leading `#`, and the remaining nonempty title is treated as feature-description text rather than shell code
- **THEN** larger issue numbers are never truncated and a semantic short-name suggestion or sequential/timestamp configuration cannot replace the calculated identity

#### Scenario: Refuse an ambiguous or conflicting creation
- **WHEN** the invocation lacks a positive issue token or title, provides conflicting explicit overrides, targets an existing feature directory, or cannot establish the required branch
- **THEN** the preamble instructs refusal with an actionable diagnostic before creating feature files, without falling back to inferred numbering or overwriting an existing record
- **THEN** missing or invalid arguments refuse before hooks create branches, while a hook-produced branch mismatch refuses before core spec creation

### Requirement: Preset installation

The preset README SHALL document prerequisites, the house rules, issue-first
invocation, branch setup, dependency-free manifest serialization and both
checkout and release-archive installation. It SHALL link the inspected upstream
v1.3.2 sources and distinguish static artifact checks from actual installation
and command execution.

#### Scenario: Install from checkout or release
- **WHEN** an author reads the preset README
- **THEN** `specify preset add --dev ./presets/nightshift-speckit` and `specify preset add --from <release-archive-url>`, the issue-first example, expected path, branch-hook versus author-selected branch prerequisites and house-rules link are present, and the release URL is a placeholder to replace rather than an invented published archive
- **THEN** a scratch-project check on spec-kit 1.3.2 in Claude Code or Codex remains Matt's human checklist item recorded on #184, not a claimed CI result or implementation task

### Requirement: Native build support guidance

The preset README SHALL describe explicit native drafting and builds using a
trusted `format = "speckit"` manifest and links to the engine guide and manifest
example. It SHALL distinguish asset installation from build configuration,
retain prerequisites and installation guidance, describe native front-door support under trusted-manifest enrollment, and leave unperformed live checks pending. It SHALL describe
skip-feasibility only as an optional existing bypass. This replaces the obsolete pending-front-door claim; preset assets and command behavior do not
change.

#### Scenario: Preset reader finds the native build path
- **WHEN** an operator reads the shipped preset README after installing its authoring assets
- **THEN** guidance points to explicit native drafting/admission/build/publication with a trusted native manifest, manifest-frozen specs/** and .specify/**, and Finish setting Status Complete without archival
- **THEN** installation alone does not enroll or configure a build; the v1.3.2 baseline, PyYAML/interpreter advice, checkout/release installation commands, issue-first example, house-rules/upstream links and pending scratch-install/live-reference checks on issue 184 remain intact
- **THEN** new checks use deterministic physical-line boundaries and resolving relative links, and all existing preset and plugin test assertions remain unchanged

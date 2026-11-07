## Purpose

Define offline parsing, validation and text helpers for spec-kit feature
directories while retaining the harness's existing task type and scenario grammar.

## ADDED Requirements

### Requirement: Local feature API

`specloop.speckit` SHALL expose the eight interfaces specified in the design using
pure Python, with no new dependencies or external tool calls. `feature_dir(repo,
change)` SHALL return `repo / "specs" / change`. This module SHALL leave existing
engine, CLI, OpenSpec, task mutation and daemon behavior unchanged.

#### Scenario: Resolve a feature without integration

- **WHEN** a caller resolves `042-add-a-gate` under a repository path
- **THEN** the result is that repository's `specs/042-add-a-gate` path without filesystem access
- **THEN** importing or using the module does not dispatch a command, select a format or change existing engine behavior

### Requirement: Physical line spec parsing

`parse_spec` SHALL retain title, status, numbered stories, priorities, labelled
acceptance scenarios, FR/SC lines and clarification markers with the design's
physical-line grammar and one-based source locations. It SHALL retain malformed
priority and duplicate-label evidence for validation and order stories by numeric
priority with stable source-order ties and absent priorities last.

#### Scenario: Parse the authored feature

- **WHEN** the synthetic `042-add-a-gate/spec.md` is parsed
- **THEN** its title, status, three priority-ordered stories, labelled scenarios, FR lines and SC lines are available with their source locations
- **THEN** scenario text comes from its labelled physical line and numeric identity comes from the AS label rather than list position

#### Scenario: Retain invalid and literal evidence

- **WHEN** a story lacks a valid priority or repeats an AS label, or matching spec lines appear inside fences, HTML or indentation
- **THEN** the parser retains the story, its labels and those matching lines according to the design's literal-line rules without interpreting Markdown containers
- **THEN** a missing mandatory section heading does not erase otherwise recognizable stories and scenarios

### Requirement: Stable scenario identities

`scenario_ids` SHALL produce exactly `<change>/us<k>/as<n>` mapped to
`US<k> — AS<n>\n<scenario text>`. It SHALL reuse `openspec.slug` to require a
canonical change name and `tasks.SCENARIO_ID` unchanged to require whole-string
matches. Invalid or duplicate identities SHALL raise the existing `SpecError`.

#### Scenario: IDs fit the harness

- **WHEN** IDs are built for the fixture
- **THEN** `042-add-a-gate/us1/as2` maps to `US1 — AS2` followed by a newline and that scenario's text
- **THEN** every ID matches the whole unchanged harness regex, and changing a title, priority or list ordinal leaves numeric scenario identity unchanged

#### Scenario: Collisions and invalid names refuse

- **WHEN** two parsed scenarios generate the same ID, including numerically equivalent labels, or the change name is noncanonical or an ID fails the harness regex
- **THEN** `scenario_ids` raises `SpecError` without silently overwriting or renaming an identity

### Requirement: Shared task records

`parse_tasks` SHALL return existing `specloop.tasks.Task` objects using
`tasks.TASK`, the design's phase and label regexes, and unchanged
`tasks.SCENARIO_REF`. It SHALL preserve phase titles, numeric phase numbers,
T labels including dotted suffixes, done states, flags, task text and zero-based
physical line indices. It SHALL NOT implement or invoke task mutation.

#### Scenario: Parse phases and references

- **WHEN** the fixture's tasks and additional checked, parallel and dotted-label task examples are parsed
- **THEN** each recognized task has the existing Task type and its correct phase, label, flags, done state and source line
- **THEN** at least one fixture task's `scenarios` contains `042-add-a-gate/us1/as2`, and keys and scenario-reference behavior come from the existing dataclass

#### Scenario: Bound phases literally

- **WHEN** task text contains deeper headings, a new level-one or level-two heading, invalid labels, or matching lines inside fences or HTML
- **THEN** deeper headings retain the phase, new same-or-higher headings close it, and only valid labelled checkbox lines within a recognized phase become tasks
- **THEN** examples receive no Markdown-container exemption, and a prose `[USk]` mention is not a positional ownership flag

### Requirement: Feature validation

`validate` SHALL return deterministic repository-relative `path:line: message`
diagnostics for independent failures, using one-based lines and line 1 for
absent files, missing sections and document-wide failures. It SHALL read the
spec, tasks, plan and direct Markdown checklists without writes or external
tools, report unavailable inputs, and skip checks dependent on unreadable inputs.

#### Scenario: Valid feature passes

- **WHEN** the complete synthetic feature is validated
- **THEN** the result is an empty list despite pending implementation tasks and plan human checks
- **THEN** template placeholders outside the feature directory do not cause clarification failures

#### Scenario: Unavailable inputs refuse

- **WHEN** a required feature file or a listed checklist cannot be read or decoded
- **THEN** validation returns a diagnostic for that path at line 1 and does not substitute successful empty content

### Requirement: Required spec sections

`validate` SHALL require `## User Scenarios & Testing`, `## Requirements` and
`## Success Criteria` according to the design's physical heading rule.
Missing headings SHALL NOT erase otherwise recognizable stories or scenarios.

#### Scenario: Missing section reports

- **WHEN** exactly one mandatory heading is removed from an otherwise valid fixture copy, parametrized across all three mandatory sections
- **THEN** validation reports exactly the missing-section rule at `specs/042-add-a-gate/spec.md:1` without losing the recognized stories

### Requirement: Story readiness

`validate` SHALL require every parsed story to have a priority and at least one
labelled nonblank acceptance scenario. When no story has such a scenario, it
SHALL emit the minimum-content diagnostic instead of per-story missing-scenario
errors.

#### Scenario: Missing priority reports

- **WHEN** one fixture story's priority is removed while its heading and scenarios remain
- **THEN** validation reports exactly the missing-priority rule at that story's heading line

#### Scenario: Missing scenario label reports

- **WHEN** one story has no AS-labelled scenario, its old task references are removed and other stories retain labelled scenarios
- **THEN** validation reports exactly that story's missing-labelled-scenario rule at its heading line

### Requirement: Unique scenario labels

`validate` SHALL require unique scenario labels within each story and unique
generated IDs across the feature. It SHALL report a collision at the later
source line and retain the unique ID set for coverage without overwriting
evidence or allowing `SpecError` to escape.

#### Scenario: Duplicate label reports

- **WHEN** an otherwise valid fixture gains a second copy of one labelled scenario in the same story
- **THEN** validation reports exactly the duplicate rule at the later scenario line without an uncaught exception or spurious coverage error

### Requirement: Story phase ownership

`validate` SHALL require each user-story phase to contain a task with that
story's positional `[USk]` flag. Checked tasks SHALL count. When no task
qualifies, it SHALL emit the minimum-content diagnostic instead of per-phase
ownership errors.

#### Scenario: Missing phase ownership reports

- **WHEN** all positional flags for one user-story phase are removed while other phases retain matching tasks and all scenario references remain
- **THEN** validation reports exactly that phase's missing matching `[USk]` task at its heading line
- **THEN** a task in another phase or a prose-only mention does not satisfy that phase

### Requirement: Scenario task coverage

`validate` SHALL require every generated scenario to be cited by a parsed task
and reject unknown cited IDs using unchanged `tasks.SCENARIO_REF`. Checked
tasks SHALL provide coverage. It SHALL locate uncovered scenarios at their
source lines and unknown references at their task lines.

#### Scenario: Uncovered scenario reports

- **WHEN** a fixture scenario's last backticked task reference is removed while task ownership remains intact
- **THEN** validation reports exactly the uncovered-scenario rule at that scenario's source line

#### Scenario: Unknown scenario reports

- **WHEN** a fixture task additionally cites an unknown backticked ID matching `tasks.SCENARIO_REF`
- **THEN** validation reports exactly the unknown-scenario rule at the task line and retains valid coverage

### Requirement: Clarification completion

`validate` SHALL reject remaining clarification markers in spec, tasks, plan
and direct Markdown checklist files using the design's literal-line rule.
It SHALL exclude template files outside the feature directory from this check.

#### Scenario: Clarification reports

- **WHEN** one bare or colon-bearing clarification marker remains in a spec, task, plan or checklist line, including a literal example
- **THEN** validation reports the marker's file and physical line rather than treating the feature as complete

### Requirement: Checklist completion

`validate` SHALL require each checkbox in direct `checklists/*.md` files to
be checked using the design's literal-line rule. Only `x` and `X` SHALL count
as checked. Absent checklists or readable checklists without checkbox lines
SHALL add no checkbox failure.

#### Scenario: Unchecked checklist reports

- **WHEN** one checklist checkbox has a blank or unknown state, including within a fenced, HTML or indented example
- **THEN** validation reports exactly the unchecked-checklist rule at that line
- **THEN** only `x` and `X` satisfy the design's checkbox-state rule

### Requirement: Plan availability

`validate` SHALL require a readable `plan.md` in the feature directory.
Pending plan human checks SHALL NOT prevent validation success.

#### Scenario: Missing plan reports

- **WHEN** only `plan.md` is removed from a valid fixture copy
- **THEN** validation reports exactly the missing-plan rule at `specs/042-add-a-gate/plan.md:1`

### Requirement: Minimum feature content

`validate` SHALL require at least one story with a labelled nonblank acceptance
scenario and at least one task in a user-story phase with its matching
positional `[USk]` flag. It SHALL report each missing minimum independently
without suppressing unrelated validation failures.

#### Scenario: Empty story content refuses

- **WHEN** the spec has no story with a labelled nonblank acceptance scenario, while matching story-phase tasks remain and cite no nonexistent scenarios
- **THEN** validation reports `specs/042-add-a-gate/spec.md:1: no user story with a labelled acceptance scenario`
- **THEN** it suppresses redundant per-story missing-scenario errors

#### Scenario: Empty user story work refuses

- **WHEN** valid scenario coverage is retained in setup tasks but no matching user-story-phase task remains
- **THEN** validation reports `specs/042-add-a-gate/tasks.md:1: no user-story task`
- **THEN** it suppresses redundant per-phase ownership failures

#### Scenario: Headings alone refuse

- **WHEN** the spec contains only the three mandatory headings and readable tasks and plan files are empty with no checklists
- **THEN** validation returns both minimum-content diagnostics instead of an empty list

### Requirement: Clarification questions

`questions(spec)` SHALL return complete bracketed clarification marker strings
from the parsed spec in source order, preserving repeated occurrences. Bare and
colon-bearing markers SHALL be recognized with the design's physical-line rule.

#### Scenario: Collect questions with locations

- **WHEN** a spec contains multiple markers on one line, repeated markers and markers inside literal examples
- **THEN** parsing retains their physical line numbers and `questions` returns all complete marker strings in source order
- **THEN** a spec without markers returns an empty question list

### Requirement: Plan human checks

`human_checks` SHALL collect complete unchecked checkbox lines under exact
`## Human checks` headings using the existing loop collector's physical heading
boundary rule, outer ASCII trim and literal pending-checkbox rule. It SHALL
preserve source order and duplicates without editing the loop collector.

#### Scenario: Collect literal pending checks

- **WHEN** a plan contains selected sections with deeper headings, repeated selected headings and matching unchecked lines inside examples
- **THEN** all matching pending lines are returned in source order, including duplicates and examples
- **THEN** checked items, empty items, unrelated sections and the legacy design-only heading contribute nothing, and same-or-shallower headings close collection

### Requirement: Status completion

`mark_complete` SHALL set each matching physical status line to
`**Status**: Complete` or insert that line immediately after the first title
when absent. It SHALL preserve unrelated text and line endings, refuse a missing
title, and return identical text on repeated application.

#### Scenario: Replace and insert idempotently

- **WHEN** completion is applied twice to specs with an existing status, multiple statuses, no status, CRLF endings or a title without a trailing newline
- **THEN** the resulting status is Complete, insertion is directly after the title where necessary, and the second call changes nothing
- **THEN** unrelated content is preserved, and an input without a title raises `SpecError`

### Requirement: Offline fixture provenance

The synthetic project SHALL include byte-identical spec, plan and tasks template
copies from local release tag v1.3.2 at commit
`55264c2f2f2d1146f02abc543cb913dcdf1f9342`, attribution, the upstream MIT license
and a synthetic constitution. It SHALL require no network, preserve the upstream
source directory, and separate pinned templates from authored feature documents.

#### Scenario: Copy spec and plan inputs locally

- **WHEN** the fixture's spec and plan template copies are compared with `tests/fixtures/speckit-upstream/v1.3.2/templates/`
- **THEN** their bytes match and fixture attribution names the release tag, commit, local source and accompanying MIT license

#### Scenario: Copy task input locally

- **WHEN** the fixture's tasks template is compared with the pinned local source
- **THEN** its bytes match, and the fixture also includes `.specify/memory/constitution.md` without altering upstream inputs

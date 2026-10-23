## Purpose

Guide task and section sizing by risk, concern and dependencies while retaining
single-session work limits and scenario coverage.

## ADDED Requirements

### Requirement: Risk based task sizing

The drafter's `tasks.md` instructions SHALL combine work touching the same file
for one concern, recommend six tasks or fewer for most changes, and minimize
sections as dependencies allow. They SHALL explain that every task incurs a
review cycle and every section an evidence gate. They SHALL retain the roughly
300-changed-line ceiling, one-concern session boundary, dependency ordering,
scenario coverage, tests with code and human checks in the design.

#### Scenario: Related work shares a task
- **WHEN** the drafter receives instructions for a change whose same-file work addresses one concern
- **THEN** the instructions call for combining that work within the task ceiling, normally using six tasks or fewer and as few sections as dependencies allow
- **THEN** they explain the review-cycle and evidence-gate costs without making six tasks a hard rejection limit

#### Scenario: Necessary boundaries remain
- **WHEN** separate concerns, dependencies or the task ceiling require splitting work
- **THEN** the instructions retain single-session tasks of about 300 changed lines at most, dependency ordering, scenario references and coverage, tests with their code, and human-only checks in the design

### Requirement: Compact documentation plans

The drafter SHALL be instructed to use one or two tasks in one section for a
documentation-only change, subject to the retained single-session task ceiling.

#### Scenario: Documentation only sizing
- **WHEN** a change updates documentation without changing executable behavior
- **THEN** the drafter's instructions specify one or two tasks in one section instead of splitting tasks or sections by document heading

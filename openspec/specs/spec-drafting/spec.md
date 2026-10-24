# spec-drafting Specification

## Purpose
Guide task and section sizing by risk, concern and dependencies while retaining
single-session work limits and scenario coverage.

## Requirements

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

### Requirement: Draft base

A draft SHALL branch from the base as resolved in the source checkout, independent of the branch that checkout currently has out. Before resolving, drafting SHALL fetch the base from the source's `origin` when there is one (best effort, bounded) and prefer that copy, so a blocker merged upstream moments earlier is included. Without an origin, or when the fetch fails, the local base is used. A reviewed `from_branch` tip is used as given.

#### Scenario: A just-merged blocker is in the base
- **WHEN** a blocker's PR merges upstream and its dependent is drafted before the source checkout pulls
- **THEN** the draft starts from the upstream base that includes the blocker

#### Scenario: The source checkout branch does not matter
- **WHEN** the source checkout has a feature branch checked out, or has no origin
- **THEN** the draft still starts from the base (upstream if available, otherwise local), not from the checked-out branch


## Purpose

Retain issue-attributed overlap decisions when front-door PRs and runs use native feature names.

## ADDED Requirements

### Requirement: Native source issue attribution

Arming SHALL additionally recognize safe native front-door basenames beginning ^(\d{3,})-, including the exact current-repository qualification defined in the design, and map the positive numeric prefix to its source issue. The same recognition SHALL serve same-repository spec PR paths and in-flight runs, self-exclusion and dependency exemptions. Existing legacy/owner-qualified attribution, repository/fork checks, unrecognized-run diagnostics and forge transcripts SHALL remain unchanged.

#### Scenario: Native PR paths retain source issue
- **WHEN** a same-repository spec/007-add-a-gate PR supplies overlapping paths, including renamed paths, on either forge
- **THEN** the paths are attributed to issue 7 rather than the PR number and participate in overlap findings
- **THEN** fork, ordinary, foreign-qualified and unsafe branch evidence is not attributed as that issue

#### Scenario: Native runs permit normal exemptions
- **WHEN** running or paused native 007-add-a-gate work overlaps its own issue or an issue connected through a proven blocked-by chain
- **THEN** its source is issue 7, it is not reported as an unattributed run, self-evidence is excluded and the existing directed dependency exemption applies
- **THEN** unchained overlaps still report issue 7 and unrecognized runs still block without a guessed dependency

#### Scenario: Native roots remain reserved
- **WHEN** Scope names specs/007-add-a-gate/spec.md, openspec/changes/example/tasks.md and an editable source path
- **THEN** scope extraction retains the source path and excludes both specification roots through existing shared path helpers, without changing ordinary OpenSpec scope assertions

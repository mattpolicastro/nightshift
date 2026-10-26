# run-context Specification

## Purpose
TBD - created by archiving change factory-context. Update Purpose after archive.

## Requirements

### Requirement: Context in status
`specloop status --json` SHALL include a `context` object for every run with the
change summary, repository, branch, current task, full pause detail, the last five
events in plain words, and a GitHub link to the change folder when the branch is
published.

#### Scenario: Paused run has context
- **WHEN** a run with a proposal whose Why section begins "The page needs links." is paused stuck on task 1.2 with detail "Failed twice: x"
- **THEN** its context has summary "The page needs links.", task label "1.2" with that task's text, detail "Failed twice: x", and the run's repository and branch

#### Scenario: Recent events in plain words
- **WHEN** a run's events include task 1.1 landing and then task 1.2's first attempt failing
- **THEN** its context lists "task 1.2 attempt 1 failed" before "task 1.1 landed", with at most five entries

#### Scenario: GitHub link only when published
- **WHEN** one run has a PR and its manifest names GitHub repository owner/repo, and another run has no PR
- **THEN** the first run's context links to https://github.com/owner/repo/tree/<branch>/openspec/changes/<change> and the second run's link is empty

#### Scenario: Broken context does not break status
- **WHEN** a run's branch has no proposal.md
- **THEN** status still succeeds and that run's summary is empty

### Requirement: Details in the Factory section
Each Factory row SHALL offer a collapsed Details section that shows the run's
context, the GitHub link when present, the command that shows more on the Studio,
and an Open in Paseo button when a Paseo workspace for the run's repository exists.

#### Scenario: Workspace matched for Open in Paseo
- **WHEN** workspaces include one being archived and an active one whose workspaceDirectory, with a leading /Users/<name> written as ~, equals the run's repo
- **THEN** the active workspace is chosen, and no workspace is chosen when only ones being archived match

#### Scenario: Context redacted and links checked
- **WHEN** a status context has a repo under the home directory and a github link that is not an https://github.com/ URL
- **THEN** the projected repo starts with ~ and the github link is dropped

#### Scenario: More command offered
- **WHEN** Details is shown for change factory-context
- **THEN** it offers the command `nightshift status factory-context --events 20`

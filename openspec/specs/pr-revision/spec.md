# pr-revision Specification

## Purpose
Steering a specloop change from its pull request with `agent:revise`, before
and after the spec is approved.

## Requirements

### Requirement: Spec-stage revision
`agent:revise` on a spec PR whose change is not admitted SHALL re-run
`specloop propose` with the PR's new review feedback added to the idea, push
the revised branch, remove the label and comment what changed.

#### Scenario: Spec revised from review
- **WHEN** a reviewer comments on an unapproved spec PR and labels it agent:revise
- **THEN** propose runs again with that comment as feedback, the branch is updated, and agent:revise is removed

### Requirement: Build-stage revision
`agent:revise` on the PR of an admitted change SHALL turn the review feedback
newer than the branch tip into a review round with `specloop revise`, and
remove the label once the round is queued.

#### Scenario: Review comments become tasks
- **WHEN** an admitted change's PR has two new inline review comments and is labelled agent:revise
- **THEN** specloop revise receives both, each with its file and line, and agent:revise is removed

### Requirement: No instruction, no revision
A revise request with no new review feedback SHALL be refused with a comment
that says so, leaving the change untouched.

#### Scenario: Empty revise refused
- **WHEN** a PR is labelled agent:revise with no review feedback since the branch tip
- **THEN** the daemon comments that there is nothing to revise, removes the label, and runs no specloop command

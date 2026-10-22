## Purpose

Require trusted application of the revision label before steering a specloop change from its pull request, both before and after admission.

## ADDED Requirements

### Requirement: Revision actor gate

Revision requests SHALL use issue-intake's Latest label actor authorization, Actor relationship resolution, and Rejection comment idempotency requirements for the configured `agent:revise` label. The gate SHALL run before feedback/status reads, new drafting or revision dispatch, recovery delivery, and empty-feedback handling. Feedback trust SHALL remain independent of label authorization.

#### Scenario: Unauthorized revision ignored at either stage
- **WHEN** an unadmitted or admitted change has a revision label last applied by an untrusted actor
- **THEN** one authorization rejection comment is posted without fetching feedback, invoking specloop/git, changing labels, or altering revision receipts
- **THEN** the same result holds when no new feedback exists

#### Scenario: Trusted label does not trust feedback
- **WHEN** a trusted actor applies the revision label and the PR includes new feedback from trusted and untrusted authors
- **THEN** only the feedback accepted by the existing author and timestamp filters reaches the revision command

## MODIFIED Requirements

### Requirement: Spec-stage revision

`agent:revise` on an otherwise eligible spec PR whose change is not admitted SHALL re-run `specloop propose` only after Revision actor gate succeeds, with the PR's new review feedback added to the idea, push the revised branch, remove the label and comment what changed.

#### Scenario: Spec revised from review
- **WHEN** a reviewer comments on an unapproved spec PR and a trusted actor labels it agent:revise
- **THEN** propose runs again with that eligible comment as feedback, the branch is updated, and agent:revise is removed

### Requirement: Build-stage revision

`agent:revise` on the PR of an admitted change SHALL turn eligible review feedback newer than the branch tip into a review round with `specloop revise` only after Revision actor gate succeeds, and remove the label once the round is queued.

#### Scenario: Review comments become tasks
- **WHEN** an admitted change's PR has two new eligible inline review comments and is labelled agent:revise by a trusted actor
- **THEN** specloop revise receives both, each with its file and line, and agent:revise is removed

### Requirement: No instruction, no revision

An authorized revise request with no new review feedback SHALL be refused with a comment that says so, leaving the change untouched. An unauthorized request SHALL use the authorization rejection behavior and retain its label instead.

#### Scenario: Empty revise refused
- **WHEN** a PR is labelled agent:revise by a trusted actor with no review feedback since the branch tip
- **THEN** the daemon comments that there is nothing to revise, removes the label, and runs no specloop command

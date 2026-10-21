## Purpose

The Nightshift daemon as the GitHub front end for specloop repositories:
engine routing, drafting from issues, approval and publication.

## ADDED Requirements

### Requirement: Engine per repository
Each enrolled repository SHALL have an engine, `worker` (the default, unchanged)
or `specloop` with a `manifest` path. The daemon SHALL never run worker
claims for a specloop repository, nor specloop intake for a worker repository.

#### Scenario: Worker repository unchanged
- **WHEN** a repository has no engine set and an issue is labelled agent:ready
- **THEN** the daemon claims it for the worker exactly as before

#### Scenario: Specloop repository skips worker claims
- **WHEN** a repository has engine specloop and an issue is labelled agent:ready
- **THEN** no worker claim is made and the issue is drafted as a spec instead

### Requirement: Drafting from an issue
For a specloop repository, an issue labelled `spec:draft` (or `agent:ready`)
SHALL be drafted with `specloop propose`, using its title, body and comments.
On success the daemon SHALL push `spec/<change>`, open a draft PR titled
`Spec: <issue title>` that links the issue, label it `spec:review`, comment the
PR link on the issue and remove the drafting label.

#### Scenario: Draft PR opened
- **WHEN** issue 7 "Add a gate" in repository owner/sample is labelled spec:draft and propose drafts successfully
- **THEN** a draft PR from spec/sample-issue-7-add-a-gate is opened, labelled spec:review, linked from a comment on issue 7, and spec:draft is removed

#### Scenario: Questions posted
- **WHEN** propose returns questions for the issue
- **THEN** the questions are posted as a comment on the issue, it is labelled needs-human, and no PR is opened

#### Scenario: Unfixable draft escalated
- **WHEN** propose reports contradictions or problems that survive its rounds
- **THEN** they are posted on the issue, it is labelled needs-human, and no PR is opened

### Requirement: Approval admits
`spec:approved` on a spec PR SHALL admit its change with `specloop admit --pr`.
A refused admission SHALL be explained in a PR comment with needs-human added.

#### Scenario: Approved spec admitted
- **WHEN** a spec PR is labelled spec:approved and admission succeeds
- **THEN** the change is admitted with that PR recorded and the PR gets a comment saying the build has started

#### Scenario: Refused admission explained
- **WHEN** admission is refused for a contradiction
- **THEN** the PR gets a comment with the contradiction and evidence and is labelled needs-human

### Requirement: Publication
When a daemon-managed run is paused ready-to-publish, the daemon SHALL run
`specloop publish` and comment on the PR with the human checks still to make.

#### Scenario: Ready run published
- **WHEN** an admitted change's run pauses ready_to_publish
- **THEN** the daemon publishes it to its recorded PR and comments the remaining human checks

### Requirement: Kill switch
While the Nightshift pause file exists, the daemon SHALL perform no drafting,
admission, revision or publication.

#### Scenario: Paused daemon does nothing
- **WHEN** the pause file exists and an issue is labelled spec:draft
- **THEN** no specloop command runs and no label changes

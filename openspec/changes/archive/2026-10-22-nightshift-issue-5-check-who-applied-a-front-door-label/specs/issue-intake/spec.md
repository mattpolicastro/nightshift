## Purpose

Authorize the latest front-door label application before drafting, admission, or revision while retaining existing intake and recovery behavior for trusted requests.

## ADDED Requirements

### Requirement: Latest label actor authorization

For specloop repositories, Nightshift SHALL authorize the latest current application of configured `spec:draft`, `agent:ready`, `spec:approved`, and `agent:revise` labels before acting. Only a label actor resolved as OWNER, MEMBER, or COLLABORATOR SHALL be trusted. Missing identities SHALL be untrusted. Both present intake labels SHALL be authorized before drafting or intake delivery.

#### Scenario: Trusted actor accepted
- **WHEN** the latest application of a trigger is by an actor resolved as OWNER, MEMBER, or COLLABORATOR and all other eligibility checks pass
- **THEN** the requested stage proceeds through its existing behavior without an authorization rejection

#### Scenario: Untrusted actor ignored
- **WHEN** an otherwise eligible trigger was last applied by an actor with no trusted relationship or with missing or malformed actor identity
- **THEN** no triggered specloop/git command, background launch, approval-head write, workflow receipt write, or delivery occurs
- **THEN** the daemon explains the ignored application once and leaves all labels unchanged

#### Scenario: Latest application wins
- **WHEN** paginated history contains a trusted application, its removal, and a newer untrusted application of the same current label
- **THEN** the newer event controls authorization and the older trusted event cannot authorize work

#### Scenario: Trusted reapplication recovers
- **WHEN** a rejected label is removed and reapplied by a trusted actor, with all other active intake triggers trusted and normal eligibility satisfied
- **THEN** the new event authorizes normal processing without clearing or overwriting the old rejection comment

#### Scenario: Alias and configured names protected
- **WHEN** an issue carries the ready alias or configured trigger names, including both intake labels with one untrusted application
- **THEN** authorization uses those configured labels and the actual actors, and an untrusted intake application blocks drafting even if the other intake label was trusted

### Requirement: Actor relationship resolution

Nightshift SHALL resolve label actors from GitHub event identity and positive repository-owner, collaborator-membership, or organization-membership evidence. Public read access, issue/PR authorship, feedback authorship, account type, and site-administrator flags SHALL NOT independently authorize a label. Unknown event history or failed relationship lookups SHALL prevent action and retain the existing error/retry behavior.

#### Scenario: Identity without association resolved
- **WHEN** a label event contains an actor login without an association field
- **THEN** positive personal-owner, repository-collaborator, or organization-member evidence resolves it to OWNER, COLLABORATOR, or MEMBER respectively
- **THEN** negative membership evidence does not become trusted merely because the repository is public

#### Scenario: Lookup failure defers action
- **WHEN** event history or membership lookup has a transport error, authorization error, rate limit, malformed response, missing event ID, or a last event that is an unlabel
- **THEN** the stage performs no triggered action or authorization rejection write and preserves its workflow receipts and labels for the existing failure/retry path

#### Scenario: Other authors cannot authorize
- **WHEN** an issue author, PR author, or feedback author is trusted but the latest trigger actor is untrusted
- **THEN** the trigger is ignored regardless of the other authors' associations

### Requirement: Rejection comment idempotency

An unauthorized application SHALL receive one explanatory comment keyed by repository, issue/PR number, and label-event ID. Repeated polls and restart SHALL NOT POST or PATCH it again. Lost-response retry SHALL create no second comment; transport reconciliation MAY PATCH the same body to the original. Rejection SHALL leave labels unchanged and SHALL NOT prevent processing later eligible items.

#### Scenario: Repeated rejection comments once
- **WHEN** the same unauthorized event is observed across multiple polls and a daemon restart
- **THEN** exactly one rejection comment exists and subsequent polls neither create nor edit it
- **THEN** the comment identifies the label and known actor, explains the permitted associations, and describes trusted reapplication
- **THEN** later eligible issues or PRs continue to be processed

#### Scenario: Lost comment response reconciled
- **WHEN** the rejection comment POST succeeds remotely but its response is lost
- **THEN** retry finds the event marker across all comment pages and does not create a duplicate
- **THEN** a failed comment lookup prevents a blind POST and no triggered work runs

#### Scenario: New rejection has separate explanation
- **WHEN** an untrusted actor removes and reapplies a previously rejected label
- **THEN** the new event gets its own single rejection comment and the previous explanation remains unchanged

### Requirement: Authorization preserves intake recovery

Authorization SHALL precede new work, approval-head recording, and recovery delivery; receipts SHALL NOT substitute for it. Denial SHALL preserve receipts and jobs without cancellation. Authorized recovery SHALL retain deduplication, head binding, blocker-independent delivery, and file-based drafting. New drafting rounds with unmet dependencies SHALL retain the silent blocked skip before authorization rejection comments.

#### Scenario: Receipt cannot bypass authorization
- **WHEN** an issue or PR has an existing receipt or recoverable open draft PR and its current trigger's latest application is unauthorized
- **THEN** no recovery delivery or new dispatch occurs and existing receipts and jobs remain intact
- **THEN** after a trusted reapplication, normal recovery reuses completed work where existing lifecycle rules permit it

#### Scenario: Approval head remains protected
- **WHEN** an unauthorized approval arrives after an earlier approved head was recorded
- **THEN** it does not create or replace the approval-head record or admit the change
- **THEN** authorized approval retains the existing rejection of a head that moved after that approval event was first observed

#### Scenario: Blocked draft remains silent
- **WHEN** a new drafting round has an open or unreadable blocker and an untrusted trigger actor
- **THEN** it retains the existing blocked log and skip with no comment, label, or receipt changes
- **THEN** after blockers clear, authorization determines whether the round may start

## MODIFIED Requirements

### Requirement: Engine per repository

Each enrolled repository SHALL have an engine, `worker` (the default, unchanged) or `specloop` with a `manifest` path. The daemon SHALL never run worker claims for a specloop repository, nor specloop intake for a worker repository. Specloop triggers SHALL satisfy Latest label actor authorization before acting.

#### Scenario: Worker repository unchanged
- **WHEN** a repository has no engine set and an issue is labelled agent:ready
- **THEN** the daemon claims it for the worker exactly as before

#### Scenario: Specloop repository skips worker claims
- **WHEN** a repository has engine specloop and an eligible issue is labelled agent:ready by a trusted actor
- **THEN** no worker claim is made and the issue is drafted as a spec instead

### Requirement: Drafting from an issue

For a specloop repository, an otherwise eligible issue labelled `spec:draft` (or `agent:ready`) SHALL be drafted with `specloop propose`, using its title, body and comments, only after Latest label actor authorization succeeds. Existing issue-author and comment trust checks SHALL remain in force. On success the daemon SHALL push `spec/<change>`, open a draft PR titled `Spec: <issue title>` that links the issue, label it `spec:review`, comment the PR link on the issue and remove the drafting label.

#### Scenario: Draft PR opened
- **WHEN** eligible issue 7 "Add a gate" in repository owner/sample is labelled spec:draft by a trusted actor and propose drafts successfully
- **THEN** a draft PR from spec/sample-issue-7-add-a-gate is opened, labelled spec:review, linked from a comment on issue 7, and spec:draft is removed

#### Scenario: Questions posted
- **WHEN** an authorized drafting request returns questions for the issue
- **THEN** the questions are posted as a comment on the issue, it is labelled needs-human, and no PR is opened

#### Scenario: Unfixable draft escalated
- **WHEN** an authorized drafting request reports contradictions or problems that survive its rounds
- **THEN** they are posted on the issue, it is labelled needs-human, and no PR is opened

### Requirement: Approval admits

`spec:approved` on an otherwise eligible spec PR SHALL admit its change with `specloop admit --pr` only after Latest label actor authorization succeeds. A refused admission SHALL be explained in a PR comment with needs-human added. Unauthorized label applications SHALL use the separate rejection behavior instead of admission refusal.

#### Scenario: Approved spec admitted
- **WHEN** a spec PR is labelled spec:approved by a trusted actor and admission succeeds
- **THEN** the change is admitted with that PR recorded and the PR gets a comment saying the build has started

#### Scenario: Refused admission explained
- **WHEN** an authorized admission is refused for a contradiction
- **THEN** the PR gets a comment with the contradiction and evidence and is labelled needs-human

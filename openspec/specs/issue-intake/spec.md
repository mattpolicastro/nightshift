# issue-intake Specification

## Purpose
The Nightshift daemon as the GitHub front end for specloop repositories:
engine routing, drafting from issues, approval and publication.

## Requirements

### Requirement: Engine per repository

Each enrolled repository SHALL have an engine, `worker` (the default, unchanged) or `specloop` with a manifest path required for new work. An omitted or unusable manifest SHALL be a repository health hold under Repository manifest health rather than preventing all configuration loading. The daemon SHALL never run worker claims for a specloop repository, nor specloop intake for a worker repository; reconciling leftover worker claims on a switch SHALL not launch workers. Specloop triggers SHALL satisfy Latest label actor authorization before acting.

#### Scenario: Worker repository unchanged
- **WHEN** a repository has no engine set and an issue is labelled agent:ready
- **THEN** the daemon claims it for the worker exactly as before

#### Scenario: Specloop repository skips worker claims
- **WHEN** a healthy repository has engine specloop and an eligible issue is labelled agent:ready by a trusted actor
- **THEN** no worker claim is made and the issue is drafted as a spec instead

### Requirement: Drafting from an issue

For a healthy specloop repository, an otherwise eligible issue labelled `spec:draft` (or `agent:ready`) SHALL be drafted with `specloop propose`, using its title, body and comments, only after Latest label actor authorization succeeds. New drafts SHALL use Owner qualified new identities, while recovery SHALL follow Recorded identities remain authoritative. Existing issue-author and comment trust checks SHALL remain in force. On success the daemon SHALL push `spec/<change>`, open a draft PR titled `Spec: <issue title>` that links the issue, label it `spec:review`, comment the PR link on the issue and remove the drafting label. Unhealthy enrollment SHALL defer new dispatch without consuming its trigger.

#### Scenario: Draft PR opened
- **WHEN** eligible issue 7 "Add a gate" in healthy repository owner/sample is labelled spec:draft by a trusted actor and propose drafts successfully
- **THEN** a draft PR from spec/owner__sample-issue-7-add-a-gate is opened, labelled spec:review, linked from a comment on issue 7, and spec:draft is removed

#### Scenario: Questions posted
- **WHEN** an authorized drafting request returns questions for the issue
- **THEN** the questions are posted as a comment on the issue, it is labelled needs-human, and no PR is opened

#### Scenario: Unfixable draft escalated
- **WHEN** an authorized drafting request reports contradictions or problems that survive its rounds
- **THEN** they are posted on the issue, it is labelled needs-human, and no PR is opened

### Requirement: Approval admits

`spec:approved` on an otherwise eligible spec PR SHALL admit its change with `specloop admit --pr` only after Latest label actor authorization succeeds and repository readiness permits new admission. Unhealthy enrollment SHALL retain the request without a new admission, approval-head write or refusal escalation; already-committed admissions SHALL retain completion recovery. A refused admission SHALL be explained in a PR comment with needs-human added. Unauthorized label applications SHALL use the separate rejection behavior instead of admission refusal.

#### Scenario: Approved spec admitted
- **WHEN** a spec PR in a healthy repository is labelled spec:approved by a trusted actor and admission succeeds
- **THEN** the change is admitted with that PR recorded and the PR gets a comment saying the build has started

#### Scenario: Refused admission explained
- **WHEN** an authorized admission is refused for a contradiction
- **THEN** the PR gets a comment with the contradiction and evidence and is labelled needs-human

#### Scenario: Unhealthy enrollment retains approval
- **WHEN** a trusted approval requests new admission while enrollment is unhealthy
- **THEN** no admission starts, the approval label and existing receipts remain, and no needs-human refusal is posted for the configuration problem

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

### Requirement: Dependency gated drafting

For an otherwise eligible issue armed with either configured intake label,
`propose_pending` SHALL permit a new drafting round only when
`queue.open_blockers(repo.name, queue.blockers(raw["body"]), runner=gh)` returns
no blockers. It SHALL recheck the current body and blocker states on each
eligible poll until drafting can proceed.

#### Scenario: Open blocker defers round
- **WHEN** issue 7 is armed with `spec:draft` or `agent:ready`, has `blocked-by: #6` in its current body, and issue 6 remains open across two polls
- **THEN** each poll checks issue 6, starts no drafting round for issue 7, and logs `#7 is blocked by #6 — not drafting` at INFO level
- **THEN** the blocked skip does not prevent processing other eligible issues

#### Scenario: All blockers closed allows round
- **WHEN** armed issue 7 has `blocked-by: #5, #6`, both blockers are initially open, only issue 5 closes on the next poll, and both are closed on a later poll
- **THEN** the initial poll skips drafting and logs `#7 is blocked by #5, #6 — not drafting` at INFO level, listing blockers in helper-returned order
- **THEN** the next poll still skips drafting and logs `#7 is blocked by #6 — not drafting` at INFO level, listing only the unsatisfied blocker
- **THEN** the later poll starts one normal drafting round without re-arming the issue

#### Scenario: Unreadable blocker defers round
- **WHEN** an armed issue needs a new round and reading a listed blocker's state raises a lookup error handled by `queue.open_blockers`
- **THEN** that blocker counts as open, the issue is logged and skipped without escalation, and its state is retried on the next eligible poll

#### Scenario: Blocked poll preserves state
- **WHEN** an issue needing a new round remains blocked across repeated polls
- **THEN** its labels, comments and PRs remain unchanged, no GitHub mutation or drafting/git command runs, and no proposal receipt or job is created
- **THEN** any pre-existing receipt remains byte-for-byte unchanged

#### Scenario: Rearmed round waits
- **WHEN** existing intake logic selects a new round after a question/rejection response or a closed draft PR, and a listed blocker is open
- **THEN** the new round is skipped and any old receipt is preserved on disk until a subsequent poll can start normally after closure

#### Scenario: No dependencies allows round
- **WHEN** an otherwise eligible armed issue's current body contains no explicit `blocked-by:` dependencies
- **THEN** drafting proceeds normally without blocker state lookups

### Requirement: Draft lifecycle independent of blockers

The dependency gate SHALL apply only to starting a new round. Existing
running-job collection, completed-draft delivery and open-PR recovery SHALL
retain their normal behavior without querying blocker state or being deferred
by an open or unreadable blocker. Existing eligibility and pause rules SHALL
continue to apply.

#### Scenario: Finished draft is delivered
- **WHEN** a round completed while its blocker was closed, delivery was interrupted, and the blocker reopens before the next eligible poll
- **THEN** the saved draft is delivered normally, its receipt is marked delivered, and no new drafting round or blocker lookup occurs

#### Scenario: Running job is preserved
- **WHEN** a drafting job is running and a listed blocker reopens before another eligible poll
- **THEN** the poll retains the same job and receipt and reports it through the normal pending callback without checking blockers or starting another job
- **THEN** once the job completes, its result is collected and delivered normally even while the blocker remains open

#### Scenario: Existing PR recovery continues
- **WHEN** an eligible issue has an existing open draft PR but no local receipt, and a listed blocker is open
- **THEN** normal PR recovery finishes the issue's delivery without drafting again or checking blockers

### Requirement: File based drafting input

For each initial issue draft and spec-stage PR re-draft, Nightshift SHALL write the complete assembled idea as UTF-8 under the corresponding receipt's `<stem>.jobs` directory before dispatching `specloop propose`. It SHALL pass exactly one absolute `--idea-file` path and SHALL omit `--idea` and the idea payload from process arguments. It SHALL preserve existing text assembly, trust filtering, receipt content, branch selection and result delivery.

#### Scenario: Long issue thread
- **WHEN** an eligible trusted issue has at least 1 MiB of assembled title, body and trusted comments, and the drafting runner returns a successful draft
- **THEN** the runner reads the complete, untruncated idea from the file supplied by `--idea-file`, with no payload or `--idea` in argv
- **THEN** normal draft PR creation, issue linking and label updates complete

#### Scenario: Option like title
- **WHEN** an eligible issue's title begins with `--`
- **THEN** the file begins with that exact title and the title is not supplied as a command-line argument, allowing drafting and delivery to proceed normally

#### Scenario: Text and trust preserved
- **WHEN** an eligible issue contains Unicode, blank lines, trusted comments and comments from untrusted authors
- **THEN** the UTF-8 file contains exactly the title, body and trusted comment bodies in their existing order and blank-line format, without an extra trailing newline
- **THEN** untrusted comments remain excluded and the receipt retains the same assembled idea

#### Scenario: Spec revision feedback
- **WHEN** an unadmitted spec PR is revised with large new review feedback, using either an original proposal receipt or the PR title/body fallback
- **THEN** the file contains the complete existing revision idea and eligible feedback with their existing formatting
- **THEN** `specloop propose` receives `--idea-file` and the existing `--from-branch` ref, and successful revision updates the same PR normally

### Requirement: Durable drafting payload

Nightshift SHALL finish writing and close the idea file before dispatch. Each drafting invocation SHALL receive a distinct file that remains unchanged and available across polling and daemon restart. File preparation failure SHALL prevent dispatch and use the existing error path without falling back to argv or successful-draft delivery.

#### Scenario: Delayed background read
- **WHEN** a drafting job reads its idea after the initiating poll returns and daemon in-memory job tracking is reset, with a relative state directory containing spaces
- **THEN** its absolute file path still resolves to the complete original idea
- **THEN** intervening polls reuse the existing job and completed delivery occurs once

#### Scenario: Later round preserves earlier input
- **WHEN** an issue is re-armed with an answer or a spec PR begins another feedback round after its previous draft completes
- **THEN** the new drafting invocation receives a different file containing the new complete idea
- **THEN** the earlier invocation's file remains present and unchanged

#### Scenario: File preparation fails
- **WHEN** creating the payload directory or writing the idea raises a filesystem error
- **THEN** no drafting runner or background launcher is invoked for that attempt and no successful-draft delivery occurs
- **THEN** the error follows existing intake failure handling without retrying with `--idea`

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

### Requirement: Owner qualified new identities

New issue drafts SHALL use `<owner>__<repository>-issue-<number>-<title-slug>` and branch `spec/<change>`, preserving owner/repository spelling and the existing title slug algorithm. The generated component SHALL pass the existing admission/revision branch predicate. Unsupported repository spellings SHALL report repository health and prevent new drafting and admission, without encoding or relaxing safety assertions.

#### Scenario: Different owners have distinct changes
- **WHEN** repositories a/x and b/x each draft issue 7 titled Add a gate
- **THEN** their new changes are a__x-issue-7-add-a-gate and b__x-issue-7-add-a-gate, with matching distinct branches, receipts, CLI arguments and PR lookups

#### Scenario: Safe punctuation is preserved
- **WHEN** owner/widget.js_tools-x drafts an eligible issue
- **THEN** the new name preserves widget.js_tools-x and uses the existing bounded title slug or issue fallback, passing the unchanged branch predicate

#### Scenario: Unsafe spelling holds new work
- **WHEN** owner/widget..js would generate spec/owner__widget..js-issue-7-add-a-gate
- **THEN** repository health reports the unsupported name before dispatch, no new draft or admission runs, and existing unsafe-branch no-command assertions remain valid
- **THEN** a safe recorded branch of an admitted run remains eligible for publication and build revision

### Requirement: Recorded identities remain authoritative

Existing receipts, PR branches and admitted runs SHALL retain their recorded identity. Without a receipt, intake SHALL look up exact qualified and legacy basename candidates within the repository before creating a new draft, and SHALL treat lookup failures as errors rather than absence. This bounded compatibility SHALL NOT rename runs or claim recovery after both receipt loss and title change.

#### Scenario: Legacy receipt survives title edits
- **WHEN** an existing active proposal receipt records an ownerless identity and the issue title changes before restart or delivery
- **THEN** intake reuses its saved change, branch, job and result without redrafting or renaming, and legacy PR admission/revision/publication use recorded identities

#### Scenario: Legacy PR is recovered
- **WHEN** no local proposal receipt exists but exact lookup finds the open legacy branch for the current issue/title
- **THEN** authorized intake recovers delivery to that PR without creating another draft or PR

#### Scenario: Lookup failure does not create duplicate work
- **WHEN** either required exact PR lookup fails
- **THEN** intake preserves existing state, reports the failure and dispatches no new proposal

#### Scenario: Closed legacy PR starts a qualified round
- **WHEN** normal lifecycle rules permit a new round after the legacy PR was closed and the issue is rearmed
- **THEN** new work uses the owner-qualified identity, retains replacement bookkeeping and preserves merged-PR suppression and existing recovery authorization

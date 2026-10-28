# issue-intake Specification

## Purpose
The Nightshift daemon as the GitHub front end for specloop repositories:
engine routing, drafting from issues, approval and publication.

## Requirements

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

For an otherwise eligible issue armed with either intake label,
`propose_pending` SHALL start a new drafting round only when
`forge.open_blockers(repo.name, queue.blockers(raw["body"]))` returns no
blockers. GitHubForge SHALL delegate to `queue.open_blockers` with its bound
runner, preserving results, errors and body parsing. Intake SHALL recheck
the current body and blocker states on each eligible poll until drafting
can proceed.

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

Nightshift SHALL finish writing and close each distinct idea file before dispatch and retain it unchanged across polling and restart until its background attempt is durably delivered. Delivered background idea files MAY then be removed by validated compaction. Undelivered and stopped inputs SHALL remain available. Preparation failure SHALL prevent dispatch without falling back to argv.

#### Scenario: Delayed background read
- **WHEN** a drafting job reads its idea after the initiating poll returns and daemon in-memory job tracking is reset, with a relative state directory containing spaces
- **THEN** its absolute file path still resolves to the complete original idea
- **THEN** intervening polls reuse the existing job and completed delivery occurs once, after which validated compaction may remove that input

#### Scenario: Later round preserves earlier input
- **WHEN** an issue is re-armed with an answer or a spec PR begins another feedback round after its previous draft completes
- **THEN** the new drafting invocation receives a different file containing the new complete idea
- **THEN** the earlier invocation's file remains present and unchanged unless its background attempt has been durably delivered and validated for compaction; stopped, undelivered and synchronous-injection inputs remain unchanged

#### Scenario: File preparation fails
- **WHEN** creating the payload directory or writing the idea raises a filesystem error
- **THEN** no drafting runner or background launcher is invoked for that attempt and no successful-draft delivery occurs
- **THEN** the error follows existing intake failure handling without retrying with --idea

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

### Requirement: Released item intake

Intake SHALL treat an explicitly released receipt as having no running job.
Subsequent work SHALL follow all existing trigger, authorization, pause,
dependency, health and recovery rules. Release SHALL NOT itself dispatch,
deliver a stopped result or require a new label application.

#### Scenario: Armed released issue starts once
- **WHEN** a stopped draft is released and its open issue remains armed, trusted, unblocked and otherwise eligible across polling and restart
- **THEN** a subsequent poll starts one new attempt with a new job ID and distinct idea input, and further polls reuse that attempt
- **THEN** the old attempt remains stopped and its result is never delivered

#### Scenario: Ineligible released item waits
- **WHEN** a released issue or PR is unarmed, closed, unauthorized, needs human input, paused, or subject to an applicable dependency or repository-health hold
- **THEN** no replacement starts until normal eligibility permits it, with existing trigger, rejection, health and pause behavior preserved
- **THEN** open or unreadable issue blockers continue to defer new drafts

#### Scenario: Recovery precedes replacement
- **WHEN** a released item already has an open draft PR under its recorded identity or a committed admission for its PR
- **THEN** normal recovery reuses that work without duplicate drafting, admission or PR creation, preserving legacy identities and approval-head checks
- **THEN** release neither cancels nor rolls back an admitted run

### Requirement: Polling filesystem preservation

Ordinary intake SHALL NOT create release-lock files or directories as a side
effect of reading receipts. Blocked drafting polls SHALL preserve existing
files byte for byte, and open-PR recovery without a receipt SHALL NOT create
the proposals directory.

#### Scenario: Blocked polls leave files unchanged
- **WHEN** a new drafting round remains blocked across polls, with no receipt, an old receipt, or an explicitly released receipt
- **THEN** the complete file set and file contents remain unchanged and no release lock, proposal receipt or job is created

#### Scenario: Existing PR recovery creates no directory
- **WHEN** an armed issue has an existing open draft PR and no local proposal receipt or proposals directory
- **THEN** normal recovery completes without creating the proposals directory or a release lock, including while an issue blocker is open

### Requirement: Overdue job escalation

An eligible live background attempt at or beyond its persisted deadline SHALL immediately use the existing human-intervention reporting path. Escalation SHALL retain the job reference and trigger for recovery without automatic termination or replacement. Pause, authorization, stop intent and normal result collection SHALL retain precedence.

#### Scenario: Hung job escalates after deadline
- **WHEN** an authorized eligible proposal, admission or revision remains live at its deadline on an unpaused poll
- **THEN** the daemon reports its ID, command, elapsed time, deadline budget, log location and stop/inspect/release guidance, and adds needs-human without waiting for the ordinary failure threshold
- **THEN** the same job and request remain attached, no replacement or termination occurs, and other items continue processing
- **THEN** an earlier poll before deadline reports pending without failure escalation

#### Scenario: Reporting retries retain the attempt
- **WHEN** overdue comment delivery or its subsequent needs-human label write fails, including across daemon restart
- **THEN** a later eligible poll retries reporting from the same persisted deadline and job reference, without extending the deadline or launching work
- **THEN** the existing error marker reconciles the comment and the label is attempted only after comment delivery succeeds

#### Scenario: Control and completion take precedence
- **WHEN** an expired job is paused, unauthorized, intentionally stopping or stopped, or has already finished and released its lifetime lock
- **THEN** pause or authorization denial withholds collection/reporting and leaves evidence unchanged, while stopped work remains held without age-based failure
- **THEN** an unstopped finished result uses normal collection even after its deadline, and an idle job lacking a result uses existing crash handling
- **THEN** an eligible live job is checked against its original deadline after resume or restored authorization, and a late completed result remains eligible after normal human-hold resolution

### Requirement: Failure marker reconciliation

The production GitHub retry path SHALL reconcile draft-error, admit-error, revise-error, publish-error and approval comment markers before retrying an ambiguous create. It SHALL retain existing completion-marker behavior, all-page lookup and pause guards, and SHALL NOT blindly POST after a failed reconciliation read.

#### Scenario: Error comment retry does not duplicate
- **WHEN** report_failure posts a stage-error comment which is stored remotely before HTTP 502 is returned to queue._gh
- **THEN** the real retry loop finds the marker even on a later comment page and updates that comment ID without a second POST
- **THEN** the same guarantee holds for approval markers and existing draft, admit, publish and revise markers, including round-qualified forms and distinct-marker separation

#### Scenario: Reconciliation failure prevents blind retry
- **WHEN** a marked comment POST has an ambiguous result and the retry's comment lookup fails or a pause guard fires before its next operation
- **THEN** no second POST occurs, the failure or pause propagates through existing handling, and a later eligible retry can reconcile the original comment
- **THEN** only marker recognition changes in queue.py; unmarked worker comments and the existing retry/backoff policy retain their behavior

### Requirement: Delivery identity for cleanup

Proposal, admission and both revision delivery paths SHALL persist completed delivery before invoking a shared compaction boundary. They SHALL carry the exact matching job identity through receipt reconstruction and retain existing authorization, head binding, feedback consumption and remote idempotency guarantees.

#### Scenario: Proposal and admission retain matching identity
- **WHEN** a background proposal or admission delivers a successful or human-input outcome, including recovery through an existing PR or committed admission
- **THEN** only the matching validated attempt is marked delivered and compacted after every required comment and label operation succeeds
- **THEN** admission preserves the matching job and original delivery marker through receipt reconstruction, while a different approval, colliding run or unrelated PR cannot authorize cleanup of an old attempt
- **THEN** recovery without matching local job evidence and synchronous injected runners without a job ID do no compaction

#### Scenario: Revision delivery preserves feedback history
- **WHEN** a spec or build revision delivers a success or human-input result, including a retry after successful execution or spec push
- **THEN** the matching attempt is compacted only after durable completed delivery, while saved feedback, consumed IDs, unknown receipt fields, result, round marker and build-feedback files remain intact
- **THEN** successful work consumes only its saved IDs at the existing pre-notification boundary, failed work consumes nothing, later comments remain eligible and a newer trigger application is not removed by old delivery

#### Scenario: Denied or incomplete delivery retains artifacts
- **WHEN** authorization is denied before collection, pause interrupts delivery, or a comment or label operation fails
- **THEN** no compaction occurs and the job's original request and input remain available across restart for normal authorized recovery
- **THEN** authorization-denial filesystem snapshots remain byte-for-byte unchanged; after trusted completed delivery the request retains minimal identity and timing with its original mtime and its owned idea file is absent

### Requirement: Git process group timeout

Daemon intake git commands SHALL run in a new session with the existing 30-second per-command timeout. On timeout the runner SHALL kill the process group, reap its direct child with bounded output draining, and retain the existing retryable error behavior, pause checks and explicit runner injection.

#### Scenario: Git timeout kills process group
- **WHEN** a daemon git command stalls with its transport descendant in the same group until the timeout
- **THEN** SIGKILL targets that group, the git process and descendant cease executing, the direct child is reaped, and an unrelated group remains alive
- **THEN** timeout handling allows at most one additional second for draining, reports a retryable timeout and retains receipts and arming labels for remote reconciliation
- **THEN** normal exit and nonzero exit retain stdout, stderr, return code and check semantics; signalling failure is diagnosed rather than claimed as successful termination

#### Scenario: Production runner preserves daemon progress
- **WHEN** poll_specloop dispatches git or a git timeout interrupts delivery
- **THEN** the production guarded runner uses the process-group implementation, while explicit injected git runners remain usable and pause is checked immediately before dispatch
- **THEN** other eligible specloop items, heartbeat and background-child reaping continue under existing scheduling and timeout behavior; retry reuses saved results without duplicate delivery
- **THEN** GitHub, shared git utilities, synchronous specloop publication and status runner contracts remain unchanged

### Requirement: Exact proposal source refs

For a successful proposal with a nonempty commit, Nightshift SHALL accept
exactly `spec/<change>` or `refs/specloop/proposals/<change>`, where `change`
is the receipt's recorded identity. It SHALL apply this validation to direct
results and collected background results, retain the reported value in the
receipt, and reject all other source-ref values before successful delivery.

#### Scenario: Both supported forms are accepted
- **WHEN** direct invocation or background collection returns a drafted result with a commit and either exact supported ref for the recorded change
- **THEN** validation succeeds and the saved result retains the supplied branch and commit, including for a recorded legacy change name

#### Scenario: Unrelated or incomplete results are rejected
- **WHEN** a drafted result lacks a commit or has a missing, non-string, unrelated, differently namespaced or extended ref instead of either exact supported value
- **THEN** proposal validation fails through the existing error path without pushing, creating a PR or delivering success

### Requirement: Proposal source and destination separation

Nightshift SHALL deliver the recorded proposal commit from the manifest's
source repository to `refs/heads/spec/<change>` for either supported ref.
Proposal-output fetches SHALL use the reported local ref unchanged for private
refs; output already present SHALL need no additional fetch. Delivery SHALL
pin the saved commit and preserve public PR identity, existing lifecycle
effects, eligibility gates, replacement leases and retries.

#### Scenario: Fallback proposal opens public spec PR
- **WHEN** an eligible issue's successful proposal is retained under its private ref while the local spec branch remains checked out at an older commit
- **THEN** the reported ref can be fetched from the source repository and the daemon pushes the reported proposal commit to the public `spec/<change>` branch
- **THEN** it opens the normal draft PR with its issue link, risks and review label, comments the link, removes intake labels and leaves the review checkout unchanged
- **THEN** it does not publish a private ref or use one as the PR head

#### Scenario: Saved fallback result retries delivery
- **WHEN** delivery of a successful fallback result fails during push or GitHub notification and a later eligible poll resumes after restart
- **THEN** the retained result delivers its original commit without redrafting or substituting the current local spec branch tip
- **THEN** existing PR/comment reconciliation prevents duplicate delivery artifacts and completion is recorded only through the existing success path

### Requirement: Specloop repository engine

Every repository SHALL use specloop when engine is omitted or explicitly specloop. Worker and unknown engines SHALL fail loading before dispatch. Missing or unusable manifests SHALL hold only that repository; supplied empty/wrong-type paths SHALL remain config errors. Dispatch SHALL preserve Latest label actor authorization and never claim worker work. Legacy recovery SHALL never launch workers.

#### Scenario: Omitted engine uses specloop
- **WHEN** a repository omits engine and has a healthy manifest and an otherwise eligible agent:ready issue authorized by a trusted actor
- **THEN** it drafts through specloop with no worker claim, just as explicit specloop enrollment does

#### Scenario: Specloop repository skips worker claims
- **WHEN** a healthy repository has engine specloop and an eligible issue is labelled agent:ready by a trusted actor
- **THEN** no worker claim is made and the issue is drafted as a spec instead

#### Scenario: Explicit worker fails clearly
- **WHEN** configuration names engine worker
- **THEN** loading fails before dispatch with the repository name, retired-engine explanation and specloop manifest guidance, including when verify or manifest is absent
- **THEN** the CLI reports the configuration error on stderr with exit 2 and no traceback

#### Scenario: Omitted manifest holds only its repository
- **WHEN** a repository omits both engine and manifest alongside a healthy specloop repository
- **THEN** config loading succeeds, the missing-manifest repository holds new work without consuming triggers, and the healthy repository continues

### Requirement: Forge mediated intake

Intake SHALL obtain GitHub facts and perform GitHub mutations through named
Forge operations, retaining its existing runner injection and all current
workflow requirements. Git pushes and engine commands SHALL retain their
existing runners. Existing runner-form argv assertions SHALL pass unchanged.

#### Scenario: Existing workflows retain transcripts
- **WHEN** drafting, approval, revision, publication delivery and their recovery or failure-reporting paths run through GitHubForge
- **THEN** existing runner-form tests pass without assertion or fixture edits, with the same GitHub calls, git operations, outputs and receipt effects
- **THEN** backend construction causes no read and no operation bypasses the forge to construct GitHub argv in intake

### Requirement: Design checklist collection

Publication check collection SHALL append unchecked physical lines from the run head's committed `design.md` under exact `## Human checks` or `### Verification and human rollout checklist` headings. It SHALL preserve existing sources, order and rendering, append design items in document order, and omit only exact-text duplicates of earlier collected rows.

#### Scenario: Both design headings contribute
- **WHEN** the committed design contains unchecked items beneath both supported headings, including repeated selected sections and deeper subsections
- **THEN** collection uses `splitlines()` and removes only outer ASCII spaces/tabs from each line; the two exact, case-sensitive headings open selected sections
- **THEN** a line matching `^(#{1,6})[ \t]+` at the same or shallower depth closes the active section and may open another selected section, while deeper headings retain it
- **THEN** collection appends all those items in physical document order through each section's next same-or-shallower heading or EOF
- **THEN** only a literal `- [ ]` followed by a space or tab and nonblank text contributes an item; its complete normalized line, including the checkbox, is emitted
- **THEN** checked `[x]`/`[X]` items, other bullet styles, unrelated sections, prose, continuation lines, and unsupported heading spellings (including different case or closing hashes) contribute no items

#### Scenario: Task checks precede design checks
- **WHEN** existing task/scenario checks and design items include exact duplicates and near duplicates differing in case, punctuation, or internal spacing
- **THEN** existing checks retain their original source order and rendering before design additions, and only design rows exactly equal to an earlier normalized checklist row are omitted
- **THEN** comparison uses the complete line with only outer ASCII spaces/tabs removed, preserving case, punctuation, backticks and internal whitespace; existing duplicate rows remain unchanged, and ordinary implementation checkboxes in `tasks.md` do not become human checks
- **THEN** both readiness detail and generated PR bodies preserve their existing task/scenario punctuation and retain distinct near duplicates

#### Scenario: Literal examples remain pending
- **WHEN** matching selected headings or unchecked item lines appear inside fences, HTML blocks, or indentation
- **THEN** the same physical-line rules apply, including section boundaries, without parsing those containers
- **THEN** a collected unchecked example remains an outstanding or unknown check under existing readiness rules unless its referenced scenario already explicitly permits post-deploy checking

#### Scenario: Committed head supplies checks
- **WHEN** the source working tree's design differs from the run's committed design, or base preparation advances the run head
- **THEN** newly generated readiness detail and publication body collect from the applicable committed run head, including the integrated head after preparation, and never from uncommitted source text

#### Scenario: Absent or empty design preserves checks
- **WHEN** the committed tree has no design file or its readable design has no unchecked items under a supported heading
- **THEN** existing checks and their output formatting remain unchanged, including existing empty-check behavior

#### Scenario: Read failure cannot erase checks
- **WHEN** Git cannot establish design absence or cannot read or decode the committed design during readiness collection
- **THEN** collection fails with a diagnostic and does not generate a ready-to-publish pause with an assumed empty design checklist

### Requirement: Design checks survive publication

Collected design checks SHALL flow through readiness detail into the publication receipt's `checks`, the delivered human-check comment and newly generated PR bodies. Recovery SHALL preserve these checks under existing publication contracts, without ticking items or rewriting historical receipts.

#### Scenario: Design only checks reach the receipt
- **WHEN** a change with no task/scenario human checks and an unchecked committed design check completes readiness and publishes to its adopted spec PR
- **THEN** the ready detail, matching saved publication receipt's `checks`, and delivered human-check comment all contain the design item
- **THEN** the ready detail contains the item before receipt capture, retaining its existing header and `Next:` line, and publication preserves base-sync gates and JSON contracts
- **THEN** the existing adopted PR is reused and its body is not rewritten

#### Scenario: New PR body lists design checks
- **WHEN** publication creates a PR for a change with collected design items
- **THEN** its `## Checks a human must make` section lists existing task/scenario checks first and the deduplicated design additions afterward

#### Scenario: Recovery retains design checks
- **WHEN** publication succeeds but its response is lost, or comment delivery is interrupted before acknowledgement
- **THEN** existing recovery retains the matching receipt's complete checklist and delivers or reconciles exactly one publication comment without republishing solely for comment delivery
- **THEN** receipt identity `(change, head, pr)`, round markers and retry behavior remain unchanged, the comment retains the hosted-CI reminder, and unrelated historical receipts are not rewritten

#### Scenario: Unannotated design check blocks readiness
- **WHEN** a delivered front-door publication contains an unannotated or unmapped design check and the adopted PR body lacks an explicit human-check section
- **THEN** existing merge readiness reads the receipt fallback and blocks for outstanding or unknown human checks even when other readiness conditions pass
- **THEN** collection does not tick the item, infer a post-deploy exemption, or change PR-body precedence

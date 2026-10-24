# pr-revision Specification

## Purpose
Steering a specloop change from its pull request with `agent:revise`, before
and after the spec is approved.

## Requirements

### Requirement: Spec-stage revision

`agent:revise` on an otherwise eligible spec PR whose change is not admitted SHALL re-run `specloop propose` only after Revision actor gate succeeds and repository readiness permits new drafting, with the PR's eligible unconsumed review feedback added to the idea, push the revised branch, acknowledge the selected IDs, remove the corresponding label application and comment what changed. Selection and acknowledgement SHALL follow Consumption based eligibility and Durable round snapshot. Unhealthy enrollment SHALL hold new drafting without consuming feedback or its trigger; existing successful work SHALL retain recovery under Health gates new work only. Build-stage revision and empty-feedback handling SHALL remain unchanged.

#### Scenario: Spec revised from review
- **WHEN** a reviewer comments on an unapproved spec PR in a healthy repository with an eligible unconsumed ID and a trusted actor labels it agent:revise
- **THEN** propose runs again with that comment as feedback, the branch is updated, its ID is consumed, and the corresponding agent:revise application is removed

### Requirement: Build-stage revision

`agent:revise` on the PR of an admitted change SHALL turn eligible unconsumed review feedback into a review round with `specloop revise` only after Revision actor gate succeeds, acknowledge the selected IDs when the successful result is durable, and remove the corresponding label application once the round is queued. Selection SHALL NOT depend on the remote branch tip time or await publication.

#### Scenario: Review comments become tasks
- **WHEN** an admitted change's PR has two eligible unconsumed inline review comments and is labelled agent:revise by a trusted actor
- **THEN** specloop revise receives both, each with its file and line, their IDs are consumed on success, and the corresponding agent:revise application is removed

### Requirement: No instruction, no revision

An authorized revise request with no eligible unconsumed review feedback SHALL be refused with a comment explaining that no unconsumed review feedback is available, leaving the change untouched and preserving consumed history. An unauthorized request SHALL use the authorization rejection behavior and retain its label instead.

#### Scenario: Empty revise refused
- **WHEN** a PR is labelled agent:revise by a trusted actor and every eligible feedback ID is already consumed or no actionable trusted feedback exists
- **THEN** the daemon comments that there is nothing to revise, removes the corresponding label application, and runs no specloop command, including status
- **THEN** it preserves consumed history and does not describe the branch tip as the feedback cutoff

### Requirement: Revision actor gate

Revision requests SHALL use issue-intake's Latest label actor authorization, Actor relationship resolution, and Rejection comment idempotency requirements for the configured `agent:revise` label. The gate SHALL run before feedback/status reads, feedback-state writes, new drafting or revision dispatch, recovery delivery, and empty-feedback handling. Feedback trust SHALL remain independent of label authorization. The consumed-ID filter SHALL replace the timestamp filter without broadening author trust.

#### Scenario: Unauthorized revision ignored at either stage
- **WHEN** an unadmitted or admitted change has a revision label last applied by an untrusted actor
- **THEN** one authorization rejection comment is posted without fetching feedback, invoking specloop/git, changing labels, or altering revision receipts or consumption history
- **THEN** the same result holds when no unconsumed feedback exists

#### Scenario: Trusted label does not trust feedback
- **WHEN** a trusted actor applies the revision label and the PR includes feedback from trusted, untrusted, and missing-association authors, along with self-prefixed comments and consumed IDs
- **THEN** only trusted actionable feedback with unconsumed IDs reaches the revision command, including eligible comments older than the tip

### Requirement: Identified feedback

Specloop revision feedback SHALL carry a stable source-qualified GitHub ID for each eligible conversation comment, review body, and inline comment. Intake SHALL read all pages of all three sources, retain existing trusted-author and instruction filters and rendered context, and distinguish IDs even when bodies match. Missing or invalid identities on eligible feedback, conflicting duplicate records, malformed pages, and read failures SHALL fail the read without a partial or empty-success result.

#### Scenario: All feedback sources retain identity
- **WHEN** a PR has eligible conversation, review, and inline feedback spanning multiple pages, with an overlapping numeric ID across sources and equal bodies on different IDs
- **THEN** each source-qualified ID appears once with its complete block, review-state suffix where applicable, and inline path and current or original line
- **THEN** identical repeated records are deduplicated by ID while distinct IDs with equal bodies remain distinct

#### Scenario: Feedback read failure preserves request
- **WHEN** any feedback source fails or contains a malformed page, an eligible record with no valid ID, or conflicting records for one ID
- **THEN** no revision or empty-feedback delivery occurs and no IDs are consumed
- **THEN** the existing item failure path receives the error without replacing the current revision snapshot

### Requirement: Consumption based eligibility

Intake SHALL select unconsumed IDs per repository and PR without timestamp filtering, except for legacy initialization under Feedback state compatibility. A PR without a receipt SHALL start with an empty consumed set. Consumption SHALL survive label events, head changes, spec-to-build transitions and restart, remain isolated by repository and PR, and exclude same-ID edits.

#### Scenario: Comments during revision feed the next round
- **WHEN** feedback A is selected, comment B is posted during that spec-stage or build-stage revision, the round succeeds, and a trusted actor requests another round after a newer tip is published
- **THEN** the next round receives B once even though B predates the new tip, and it does not receive A

#### Scenario: Relabel does not requeue consumed feedback
- **WHEN** a build revision successfully queues feedback A, the remote tip is unchanged because publication has not happened, and a trusted actor reapplies the revision label
- **THEN** A is excluded and no second revision command runs in the absence of other eligible IDs
- **THEN** this remains true after restart and across further label applications

#### Scenario: Edited consumed comment stays consumed
- **WHEN** a consumed comment or review is edited while retaining its ID and a new comment with a different ID is posted, including one with the same body
- **THEN** only the new ID is eligible for the next round regardless of either item's timestamps

#### Scenario: First selection and state isolation
- **WHEN** a PR has no revision receipt and has trusted actionable comments before, at, and after its branch tip time, including IDs consumed in another repository or PR
- **THEN** all those comments are eligible for that PR without a commit-date lookup
- **THEN** consumed IDs from other PRs or repositories do not suppress them

#### Scenario: Spec consumption survives admission
- **WHEN** feedback is consumed by a successful spec revision, the same PR's change is admitted, and another revision is requested with both old and new IDs present
- **THEN** only unconsumed IDs reach the build revision command

### Requirement: Durable round snapshot

Intake SHALL atomically save selected IDs, rendered blocks, and consumed history before dispatch and retain that snapshot through edits and restart. It SHALL durably consume only selected IDs after a validated build revision result with a landed commit or a successful spec push, before notifications or label removal and without waiting for publication. Unsuccessful new attempts SHALL consume nothing; legacy baseline initialization SHALL follow Feedback state compatibility. Explicit
release MAY detach a stopped attempt for normal selection of a new snapshot,
without consuming the stopped result or erasing prior consumed history.

#### Scenario: Snapshot is fixed before dispatch
- **WHEN** either revision stage starts, then a selected comment is edited and another comment appears while its job runs across a daemon restart
- **THEN** the runner reads the originally persisted blocks and IDs, and intervening polls start no replacement job
- **THEN** success consumes only the saved IDs, leaving the later comment for another round

#### Scenario: Unsuccessful work keeps feedback eligible
- **WHEN** a revision attempt fails to launch, returns refused, rejected, needs-input, or an invalid result, or its spec push fails
- **THEN** it adds no IDs to the consumed set and preserves previously consumed IDs
- **THEN** after normal failure resolution, the unconsumed feedback remains available; a saved successful draft retries delivery without drafting again

#### Scenario: Completion delivery retries preserve consumption
- **WHEN** a successful spec push or validated build revision is followed by a lost completion-comment response, failed label removal, or restart before delivery completes
- **THEN** the selected IDs are already durably consumed and retries use the saved result and original comment marker without invoking another revision
- **THEN** a later request selects only remaining IDs, including when the label removal succeeded remotely but its response was lost

#### Scenario: New label preserves completed round
- **WHEN** a trusted actor removes and reapplies the revision label while an earlier round is running or awaiting completion delivery
- **THEN** intake retains and completes the earlier round's saved work and consumption before selecting another round
- **THEN** delivering the earlier event does not remove the newer application, and a later poll handles it using the cumulative consumed set

#### Scenario: Pause and stop preserve snapshots
- **WHEN** a selected revision job completes while daemon delivery is paused, or the selected job has durable stop intent
- **THEN** pause retains the saved result for authorized resume without duplicate dispatch, and stopped work remains held without delivery or consumption until explicit release detaches its receipt; the stopped result remains ineligible afterward
- **THEN** resume of an eligible unstopped result acknowledges only the saved snapshot under the existing stage success rule

### Requirement: Feedback state compatibility

Intake SHALL validate state and preserve consumed IDs and unknown fields. Legacy receipts SHALL automatically consume all trusted IDs created at or before their saved time, or the current tip time if no time is recorded. Intake SHALL atomically persist that baseline once before further processing, without operator initialization. Invalid state SHALL fail without resetting history.

#### Scenario: Legacy saved time initializes consumption
- **WHEN** an authorized legacy receipt records a save time and trusted feedback from all three sources exists before, exactly at, and after that time, with the branch tip now later than all of it
- **THEN** intake automatically consumes only the IDs at or before the saved time using conversation/inline `created_at` and review `submitted_at`, including blank or self-prefixed trusted records, and persists the baseline without a branch-tip lookup
- **THEN** later IDs remain eligible, edits do not move the creation cutoff, and restart preserves the same baseline without operator initialization

#### Scenario: Legacy missing time uses tip once
- **WHEN** an authorized legacy receipt has no recorded save time and trusted feedback exists before, exactly at, and after the current PR head's committer time
- **THEN** intake initializes consumed IDs at or before that tip time using conversation/inline `created_at` and review `submitted_at`, and persists them before dispatch or delivery
- **THEN** a later tip advance or daemon restart does not recompute consumption, so remaining IDs still feed the next round

#### Scenario: Legacy recovery preserves saved work
- **WHEN** an authorized legacy receipt with a pending job or delivery is automatically initialized
- **THEN** the original payload, job, result, event and marker remain available under existing pause and stop rules, without inferring snapshot IDs from rendered text
- **THEN** eligible saved work finishes before a newer request starts, adds no IDs beyond the migration baseline, and subsequent new snapshots use exact IDs
- **THEN** stopped work remains held without delivery or replacement dispatch until explicit release; release retains the initialized ledger and permits only normal new-round selection

#### Scenario: Legacy initialization failure preserves receipt
- **WHEN** legacy initialization encounters malformed recorded time, missing or invalid required feedback timestamps or IDs, a failed page or fallback commit read, or an atomic save failure
- **THEN** the original receipt remains unchanged, no revision or delivery starts, and the existing error path retains the request for retry
- **THEN** malformed recorded time is not treated as absent, and a partial read is not saved as a baseline

#### Scenario: Validated state survives replacement
- **WHEN** a version-1 receipt contains consumed IDs and unknown fields, and a later empty or refused round replaces the prior round
- **THEN** the consumed IDs and unknown fields survive and previously consumed feedback stays excluded
- **THEN** malformed known fields or an unsupported version instead cause an error without overwriting the receipt or launching work

### Requirement: Revision comment retry reconciliation

Revision completion comments SHALL reconcile the original `revise` marker inside `queue._gh` before retrying an ambiguous create, for both legacy and round-qualified markers. Reconciliation SHALL search all comment pages, update the matching comment when found, and allow another create only after confirming absence. Failed reconciliation reads SHALL prevent a blind POST. Existing draft, admit and publish marker behavior and pause guards SHALL remain intact.

#### Scenario: Lost revision comment response reconciled
- **WHEN** a revision completion POST stores a comment remotely but its response is lost and the production `queue._gh` transient retry runs
- **THEN** reconciliation finds the original marker even on a later page and PATCHes that comment ID without a second comment-create call
- **THEN** both legacy and round-qualified markers behave this way, while a distinct round marker remains a separate comment

#### Scenario: Revision reconciliation read fails
- **WHEN** a revision comment POST has an ambiguous outcome and the production retry cannot read the comment history
- **THEN** the read error propagates without another POST, consumed IDs and the saved revision result remain intact, and subsequent recovery can reuse the original marker without another revision command

### Requirement: Worker feedback compatibility

The existing `queue.pr_feedback` string-returning interface and optional `since` behavior for worker callers SHALL remain unchanged by specloop's ID-based selection.

#### Scenario: Worker timestamp cutoff remains
- **WHEN** a worker caller requests PR feedback with a `since` timestamp and comments exist before, at, and after it
- **THEN** it receives the same rendered strings strictly newer than the cutoff, with its existing trust, latest-PR, and best-effort read behavior

### Requirement: Revision health isolation

Revision health checks SHALL distinguish new spec drafting from admitted-run revision and retained-result delivery. A hold SHALL preserve eligible feedback and trigger state without consuming IDs or starting another job. Runtime edits SHALL remain in `nightshift/specloop_intake.py` and `nightshift/daemon.py` with corresponding tests.

#### Scenario: Spec drafting waits for repair
- **WHEN** an authorized unadmitted PR has new eligible feedback but its repository enrollment is unhealthy
- **THEN** no propose command or manifest-dependent git operation starts and the request and feedback remain eligible
- **THEN** after repair a later poll starts the normal spec revision once

### Requirement: Released revision selection

After explicit release, eligible revision intake SHALL select a new snapshot
using normal unconsumed-feedback rules, even under an unchanged trigger event.
It SHALL preserve consumed IDs and unknown receipt fields and SHALL NOT
acknowledge the stopped attempt's result.

#### Scenario: Same event permits replacement
- **WHEN** a stopped spec or build revision is released, its trusted revision label remains applied, and eligible unconsumed feedback exists
- **THEN** a later eligible poll starts one replacement with a new job ID using the normal feedback selection and stage rules without requiring relabeling
- **THEN** previously consumed IDs and unknown receipt fields survive, and stopped feedback is not consumed merely because release occurred

#### Scenario: Release does not replay consumed feedback
- **WHEN** a released revision is polled and only already-consumed feedback remains
- **THEN** normal empty-feedback handling explains that nothing remains, removes the corresponding label application and launches no revision

### Requirement: Immutable build revision input

Each build-revision invocation SHALL receive a distinct absolute UTF-8 JSON
feedback file, fully written and closed before dispatch. The file SHALL remain
unchanged and available with the historical request across release,
replacement and restart. Input preparation failure SHALL prevent dispatch.

#### Scenario: Replacement preserves stopped input
- **WHEN** a build revision stops, its feedback is edited or supplemented without changing the trigger event, and release permits a replacement invocation
- **THEN** the replacement request names a different input file containing its newly selected blocks
- **THEN** the stopped request still resolves to its exact original bytes after replacement and restart, including for a stopped attempt using the legacy event-named file

#### Scenario: Delayed reader retains complete blocks
- **WHEN** a build revision reads its input after the launching poll returns, including with a state path containing spaces and feedback containing Unicode and Markdown separators
- **THEN** its absolute JSON input contains the original complete blocks and remains available after daemon restart
- **THEN** synchronous injected runners also receive distinct files for separate invocations

#### Scenario: Feedback file preparation fails
- **WHEN** exclusive creation or writing of a build-revision feedback file fails
- **THEN** no revision command launches, no feedback is consumed, and the existing intake failure path reports the error without overwriting an earlier input

### Requirement: Fallback spec revision delivery

Spec-stage revision SHALL accept either exact proposal source-ref form under
issue-intake's Exact proposal source refs requirement and deliver its recorded
commit to the existing `spec/<change>` PR branch. It SHALL retain the public
PR-head input fetch, saved-head check, non-forced push, durable feedback
snapshot, acknowledgement order, notification reconciliation and eligibility
gates. It SHALL NOT change admitted-run revision behavior.

#### Scenario: Checked out spec revision updates same PR
- **WHEN** an eligible unadmitted PR revision returns a successful private-ref proposal while the public PR head still matches the saved revision head
- **THEN** the daemon pushes the returned commit to the same public spec branch without moving the checked-out local branch or creating another PR
- **THEN** after successful push it consumes only the saved feedback IDs, comments the revision result and removes the corresponding revision-label application

#### Scenario: Revision failure preserves feedback and result
- **WHEN** a fallback spec revision's push fails or its remote PR head has moved to a commit other than the saved head or the proposed commit
- **THEN** the existing delivery failure path prevents successful acknowledgement and label removal, consumes no selected feedback IDs, and retains the result
- **THEN** an eligible retry after a transient push failure reuses the original result without another drafting invocation or a force push

### Requirement: Stable build round identity

Each newly selected build snapshot SHALL save a fresh UUID round_id and revision_key_version before dispatch and pass that key to revise. Ordinary crash redispatch SHALL reuse its saved identity, feedback IDs and blocks before considering a newer trigger or newly available feedback. The key SHALL NOT be a job ID. Every invocation SHALL retain the existing distinct immutable JSON input-file contract.

#### Scenario: Ordinary retry keeps the snapshot
- **WHEN** an ordinary failed or crashed keyed attempt has been detached and normal eligibility permits retry, including after restart and after a newer authorized label application or feedback edit
- **THEN** intake launches a new job with the original saved key, IDs and blocks and a distinct immutable input file, without reselecting feedback
- **THEN** the earlier event is completed before later feedback is selected and its delivery does not remove the newer label application

#### Scenario: Prepared snapshot survives launch failure
- **WHEN** input preparation, a synchronous injected command or background launch fails after saving a keyed snapshot but before a valid result is saved
- **THEN** the next eligible retry keeps that snapshot and key without consuming feedback or inventing another logical round
- **THEN** no invocation is dispatched before its full input file is written and closed
- **WHEN** preparing the first input for a new selection fails before its snapshot is saved
- **THEN** no command is dispatched and the prior receipt and historical inputs remain byte-for-byte unchanged

#### Scenario: Replacement selection creates a new identity
- **WHEN** a delivered refusal is resolved or a confirmed stopped attempt is explicitly released, even under an unchanged trigger event
- **THEN** normal unconsumed-feedback selection creates a fresh build-round key and input file while preserving consumed IDs and unknown fields
- **THEN** the stopped attempt stays cancelled, its result is never acknowledged, and an unreleased stop remains held

#### Scenario: New event selects an independent round
- **WHEN** a newer authorized revision event remains after the original round completes and unconsumed feedback is selected for a build revision
- **THEN** the new snapshot keeps the newer event ID separately from its fresh UUID round_id, which differs from the completed round's ID, and its completion comment uses exactly that saved round marker
- **THEN** the original snapshot and comment stay unchanged, only newly selected IDs are consumed, and further retries add no dispatch or comment for the completed round
- **THEN** spec-stage snapshots continue to use their event-derived round IDs and markers

#### Scenario: Legacy rounds keep existing semantics
- **WHEN** a pre-upgrade receipt or request lacks keyed-round metadata, or an empty or spec-stage revision is processed
- **THEN** existing selection, marker, consumption and control behavior remains compatible without inferring a key from old task text or applying build-key replay to propose
- **THEN** malformed known metadata in a receipt declaring keyed support causes an error without overwriting its history

### Requirement: Keyed result acknowledgement

Intake SHALL validate keyed success against the saved round before durably consuming only that snapshot's selected IDs. It SHALL retain existing authorized, unpaused delivery, label-event binding and comment reconciliation. New build completion markers SHALL identify the saved logical round; successful replay wording SHALL accurately report the run state observed in the result.

#### Scenario: Result identity gates consumption
- **WHEN** a keyed result has the wrong or missing key, missing commit, invalid replay flag or invalid status/pause, or instead contains a complete matching success
- **THEN** invalid success is rejected without consuming IDs or posting success, and valid success durably consumes only saved IDs before notification
- **THEN** legacy unkeyed results remain valid under their existing schema

#### Scenario: Refusal and replacement have distinct comments
- **WHEN** a build round is refused, the human resolves it, and a replacement round succeeds under the same event
- **THEN** the refusal and success have two distinct exact saved-round markers and one comment each, with the successful commit in the success comment
- **THEN** repeated polling, restart and ambiguous comment-delivery retries add no comment within either round and preserve all label and feedback-consumption checks

#### Scenario: Replay reports observed control state
- **WHEN** successful keyed replay acknowledges a run observed running, paused, aborted or done
- **THEN** the completion message describes that observed state, including a pause reason when paused, without claiming resumed for paused, aborted or finished work
- **THEN** replay changes no run-control state for delivery and legacy unkeyed success wording stays compatible

# issue-intake Specification

## Purpose
The Nightshift daemon as the GitHub front end for specloop repositories:
engine routing, drafting from issues, approval and publication.

## Requirements

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

## Purpose

Enforce explicit issue dependencies before the specloop front door starts a
drafting round, while preserving running work and delivery of completed work.

## ADDED Requirements

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

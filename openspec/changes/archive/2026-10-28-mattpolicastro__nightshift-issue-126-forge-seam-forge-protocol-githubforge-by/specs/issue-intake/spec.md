## Purpose

Route the existing issue front door through Forge without changing eligibility,
authorization, dependency gating, drafting, recovery or delivery.

## ADDED Requirements

### Requirement: Forge mediated intake

Intake SHALL obtain GitHub facts and perform GitHub mutations through named
Forge operations, retaining its existing runner injection and all current
workflow requirements. Git pushes and engine commands SHALL retain their
existing runners. Existing runner-form argv assertions SHALL pass unchanged.

#### Scenario: Existing workflows retain transcripts
- **WHEN** drafting, approval, revision, publication delivery and their recovery or failure-reporting paths run through GitHubForge
- **THEN** existing runner-form tests pass without assertion or fixture edits, with the same GitHub calls, git operations, outputs and receipt effects
- **THEN** backend construction causes no read and no operation bypasses the forge to construct GitHub argv in intake

## MODIFIED Requirements

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

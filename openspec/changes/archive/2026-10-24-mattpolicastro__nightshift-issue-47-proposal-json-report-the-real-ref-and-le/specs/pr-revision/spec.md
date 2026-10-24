## Purpose

Deliver spec revisions retained at a private proposal ref to the same public
PR while preserving reviewed-head checks and feedback acknowledgement.

## ADDED Requirements

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

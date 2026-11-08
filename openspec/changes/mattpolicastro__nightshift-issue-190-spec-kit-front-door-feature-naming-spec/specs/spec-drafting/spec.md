## Purpose

Preserve ordinary draft-base resolution while allowing a discovery-time commit pin and serializing replacement of shared proposal workspaces.

## ADDED Requirements

### Requirement: Exclusive draft workspace

Every upgraded daemon and direct CLI proposal SHALL share the nonblocking workspace lock in specloop/propose.py from replacement through local publication, including spec revisions. Stable lock inodes, safe paths, lock ordering and validated borrowed/inherited ownership SHALL follow the design. Busy or unsafe ownership SHALL prevent replacement; closing a parent descriptor SHALL NOT unlock a live inherited owner. No receipt or state kind SHALL be added.

#### Scenario: Concurrent proposals preserve active work
- **WHEN** another proposal holds the lock for the same output, including a direct CLI caller competing with a daemon or two repositories producing the same native name
- **THEN** the contender reports retryable busy before deleting files, cloning, invoking agents or updating proposal refs, and the active workspace remains intact
- **THEN** an unrelated output can proceed and after the owner exits an eligible retry can acquire the same stable lock inode

#### Scenario: Unsafe locks refuse before replacement
- **WHEN** the lock or managed output parent is a symlink/nonregular object, ownership names another output, or legacy workspace evidence requires migration
- **THEN** the proposal refuses without following unsafe links, deleting evidence or acquiring a replacement lock inode
- **THEN** ordinary migrated compatibility links and existing daemon receipt/job evidence remain intact

#### Scenario: Background owner survives wrapper exit
- **WHEN** an upgraded daemon hands ownership through a background wrapper to the proposal CLI, then the wrapper exits while the CLI remains active
- **THEN** a competing CLI or daemon proposal stays busy until the surviving CLI closes ownership, with no premature explicit unlock by the parent
- **THEN** direct CLI proposals without inherited ownership acquire the same helper, and legacy saved requests remain executable without new request fields

#### Scenario: Failed dispatch releases ownership
- **WHEN** launch fails or pause prevents the next dispatch operation after lock acquisition
- **THEN** newly owned descriptors close, no replacement job is left falsely live, triggers and feedback remain eligible under existing recovery rules, and a later authorized unpaused attempt can retry
- **THEN** retained completed work is delivered without rerunning a proposal solely to acquire this lock

## MODIFIED Requirements

### Requirement: Draft base

A draft SHALL branch from the base as resolved in the source checkout, independent of the branch that checkout currently has out. Before resolving, drafting SHALL fetch the base from the source's `origin` when there is one (best effort, bounded) and prefer that copy, so a blocker merged upstream moments earlier is included. Without an origin, or when the fetch fails, the local base is used. A reviewed `from_branch` tip is used as given.

The front door MAY pass the exact base commit it resolved for existing-feature discovery before admission. In that case drafting SHALL use that commit without a second base resolution. The pinned commit SHALL be the commit ordinary resolution produced at discovery time, so discovery and drafting inspect the same tree. An unpinned invocation SHALL behave exactly as before. A pinned input SHALL be a full existing commit object, not a moving ref, and SHALL NOT be combined with a reviewed from_branch. This qualifies the fetch-before-drafting rule only when that fetch/resolution already occurred for discovery.

#### Scenario: A just-merged blocker is in the base
- **WHEN** a blocker's PR merges upstream and its dependent is drafted before the source checkout pulls
- **THEN** the draft starts from the upstream base that includes the blocker

#### Scenario: The source checkout branch does not matter
- **WHEN** the source checkout has a feature branch checked out, or has no origin
- **THEN** the draft still starts from the base (upstream if available, otherwise local), not from the checked-out branch

#### Scenario: Reviewed tip remains authoritative
- **WHEN** a spec revision supplies a reviewed from_branch tip without a base pin
- **THEN** drafting uses that tip as given without substituting the upstream or local base

#### Scenario: Existing feature on stale local base
- **WHEN** origin's base contains specs/042-add-a-gate/ and the local base does not, and issue 42 explicitly names that directory
- **THEN** discovery performs ordinary upstream-first resolution, pins that exact commit and finds the existing feature there
- **THEN** drafting uses the pinned commit without resolving the base again and revises that directory rather than creating a second feature, even if origin advances after discovery

#### Scenario: Invalid or conflicting pin refuses
- **WHEN** a proposal supplies a non-commit, abbreviated or missing base object, or combines a base pin with from_branch
- **THEN** drafting refuses before replacing the workspace or invoking an agent and does not fall back to a different tree

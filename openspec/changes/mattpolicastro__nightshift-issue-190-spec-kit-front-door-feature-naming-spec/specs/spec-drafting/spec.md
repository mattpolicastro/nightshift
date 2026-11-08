## Purpose

Preserve ordinary draft-base resolution while allowing a discovery-time commit pin and isolating native proposal workspaces by repository and format.

## ADDED Requirements

### Requirement: Repository qualified native draft workspaces

Managed native proposals SHALL use `<state_root>/drafts/@speckit/<owner>__<repo>/<change>`, selecting the repository from the trusted manifest through the shared proposal CLI path. The unchanged `SAFE_CHANGE` predicate SHALL reject the namespace. OpenSpec proposals SHALL retain `<state_root>/drafts/<change>` exactly. Replacement SHALL affect only the selected leaf and retain path-safety checks for all managed parents.

#### Scenario: Native workspaces are isolated
- **WHEN** native manifests for a/x and b/x propose 007-add-a-gate through daemon or direct CLI entry points
- **THEN** their managed outputs are drafts/@speckit/a__x/007-add-a-gate and drafts/@speckit/b__x/007-add-a-gate, and replacing either leaves the other's files intact
- **THEN** namespace selection uses the trusted repository; missing/unsafe repository components or linked/non-directory managed parents refuse before replacement, and explicit isolated Python output paths retain their supported behavior
- **THEN** the change requires no workspace migration, lock, wrapper handoff or new receipt/state kind; existing daemon one-job-per-item receipts and stopped-job holds remain authoritative

#### Scenario: OpenSpec workspaces remain flat and intact
- **WHEN** native a/x issue 7 and OpenSpec 1-a/1-x__007 issue 7, both titled Add a gate, are drafted or redrafted in either order
- **THEN** their directories are drafts/@speckit/a__x/007-add-a-gate and drafts/1-a__1-x__007-issue-7-add-a-gate, and drafting either leaves sentinel files in the other's workspace intact
- **THEN** SAFE_CHANGE.fullmatch("@speckit") is None, no valid OpenSpec or explicit CLI change can occupy the namespace root, and existing flat OpenSpec paths, legacy migration refusals and compatibility links remain unchanged

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

## Purpose

Identify the actual local source ref of a successful proposal without changing
the versioned command envelope or the requested public branch identity.

## ADDED Requirements

### Requirement: Proposal source ref JSON

Successful `specloop propose --json` output SHALL set `branch` to the exact ref
returned by local proposal publication and `commit` to the drafted commit
retained there. It SHALL retain version 1, one stdout JSON document, existing
fields and exit codes. Publication SHALL continue preserving checked-out
source worktrees. Non-success outcomes SHALL retain their existing contract.

#### Scenario: Checked out branch reports private ref
- **WHEN** a successful proposal targets a branch checked out in the main source worktree or a linked worktree
- **THEN** JSON reports `branch` as `refs/specloop/proposals/<change>` and that ref resolves to the reported commit in the manifest's source repository
- **THEN** the checked-out branch tip, HEAD, index, staged and unstaged edits and untracked files remain unchanged

#### Scenario: Ordinary branch retains its spelling
- **WHEN** a successful proposal can publish to its effective branch without the checked-out-branch fallback
- **THEN** JSON reports that branch without `refs/heads/`, including an explicitly configured custom CLI branch, and the branch resolves to the reported commit
- **THEN** stdout contains exactly one version-1 document with the existing fields and exit status 0

#### Scenario: Unsuccessful proposal retains its outcome
- **WHEN** drafting needs input, is rejected, or local publication fails
- **THEN** JSON and exit status retain their existing needs-input or rejection behavior, with questions or failure details as applicable
- **THEN** the command does not report a successful fallback publication

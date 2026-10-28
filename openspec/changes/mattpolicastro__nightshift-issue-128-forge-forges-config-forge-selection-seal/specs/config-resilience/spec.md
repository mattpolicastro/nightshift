## Purpose

Preserve retired-worker recovery on GitHub and prevent its journals from causing
writes or workflow delivery for a Gitea enrollment.

## ADDED Requirements

### Requirement: Foreign recovery journal hold

A Gitea-enrolled repository SHALL never enter worker switch recovery. Any direct
engine-switch JSON journal at its existing local repository key, or an unreadable
journal location, SHALL hold all its daemon stages and escalate through repository
health without remote writes. Evidence SHALL remain intact and other repositories
SHALL continue.

#### Scenario: Gitea journal holds the repository
- **WHEN** a Gitea repository has a pending, completed or malformed direct engine-switch JSON journal at startup or on a later poll
- **THEN** recovery, drafting, admission, revision, publication and failure-report label writes are skipped for that repository without querying either forge
- **THEN** journals, claims, receipts and worktrees remain unchanged across restart, a nontransient repository health escalation identifies the inspection location, and healthy repositories continue

#### Scenario: Journal location is uncertain or clear
- **WHEN** a Gitea repository's journal location cannot be inspected, or is absent or contains no direct JSON journal
- **THEN** uncertainty holds and escalates without guessing; a clear location permits ordinary forge-selected stages without ever running retired-worker recovery
- **THEN** pause still prevents external work, and later confirmed removal of the active foreign journal by an operator permits a fresh normal eligibility check without automatic deletion or migration

## MODIFIED Requirements

### Requirement: Worker switch reconciliation

On unpaused startup/polls for every GitHub-kind enrolled repository, the daemon
SHALL reconcile leftover working issues before new intake even when its manifest
is unhealthy. It SHALL release work with no surviving worktree to ready, hold
surviving work for inspection, preserve branches/worktrees and never start a
worker. Unknown state SHALL fail locally without guessing; legacy resume, ship
and destructive reconciliation SHALL NOT run.

#### Scenario: Switch recovers working issues
- **WHEN** a formerly worker GitHub repository starts as specloop with orphaned working issues, claims with missing worktrees and claims with surviving worktrees
- **THEN** the first two cases transition to ready and surviving-work cases transition to needs-human with a phase/branch/location explanation
- **THEN** outcomes record recovery, surviving work remains intact, no worker starts for the switched repository and independent repositories still receive normal specloop polling and safe leftover-claim recovery

#### Scenario: Recovery does not need a manifest
- **WHEN** the switched GitHub repository's manifest is missing or invalid
- **THEN** leftover worker recovery still runs while new specloop drafting and admission remain held by repository health

#### Scenario: Unknown recovery state is retained
- **WHEN** a claim is unreadable or a required GitHub read fails
- **THEN** evidence is preserved, repository health reports failure and no uncertain release or new intake occurs
- **THEN** independent repositories and eligible admitted-run stages continue

### Requirement: Durable switch recovery

GitHub switch recovery SHALL save intent and a claim snapshot before remote
changes, reconcile labels and marked comments on later polls, and complete
durable outcome bookkeeping before claim cleanup. Pending recovery SHALL survive
restart even without working. Pause SHALL guard external operations. Existing
GitHub transport retries SHALL remain unchanged; this contract SHALL NOT be
extended to Gitea journals.

#### Scenario: Lost transition response recovers
- **WHEN** a recovery label transition succeeds remotely but its response is lost and the daemon restarts before recording completion
- **THEN** the pending journal is processed even without the working label, local outcome bookkeeping completes and the claim is cleared only after durable bookkeeping
- **THEN** poll-level recovery does not repeat a completed label transition or create another explanatory comment

#### Scenario: Partial recovery retries safely
- **WHEN** a comment, label write or local journal/outcome write fails, or pause begins before the next external operation
- **THEN** recovery retains intent and work evidence, withholds unsafe further operations and resumes from reconciled progress on an eligible later poll
- **THEN** a conflicting human label change is preserved and reported instead of blindly rearming the issue

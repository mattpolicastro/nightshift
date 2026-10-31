## Purpose

Preserve advisory readiness with Gitea-specific CI reads and explicit create-recovery assumptions.

## ADDED Requirements



## MODIFIED Requirements

### Requirement: Advisory CI snapshot

Readiness SHALL use Forge.pr_checks once per assessment: GitHub SHALL retain `gh pr checks <pr> --json name,bucket,state` with an explicit repository, and Gitea SHALL use its current PR-head read followed by one logical exact-SHA combined-status snapshot with existing pagination. It SHALL accept green only when at least one check is `pass` and all checks are `pass` or `skipping`. It SHALL NOT inspect current-base or merge-ref provenance and SHALL describe the label as advisory.

#### Scenario: Green checks qualify
- **WHEN** valid check results contain at least one pass and only pass or skipping buckets
- **THEN** the CI condition passes for the observed PR-head snapshot

#### Scenario: CI not established blocks
- **WHEN** checks are absent, skipped-only, pending, failed, cancelled, malformed, unknown, or unavailable
- **THEN** readiness blocks with the CI reason and never converts a failed query into green

#### Scenario: Base provenance is delegated to landing
- **WHEN** readiness assesses a PR with green checks and a base that may have moved
- **THEN** it makes no base, Actions-log, checkout, or merge-parent query and does not infer a base-movement blocker
- **THEN** its comment requires fresh CI and current-main checks when landing and does not authorize merge

### Requirement: Head bound polling

Readiness SHALL require PR head equality with the run head, read the PR once in core per eligible poll (Gitea pr_checks may read it once more when CI is queried), and query checks only when unlabelled or the stored assessment no longer matches the observed head or publication identity. A complete matching assessment with consistent labels SHALL be cached for both outcomes, except that an assessment blocked on CI SHALL be reassessed each poll until CI no longer blocks. Label removal SHALL permit explicit reassessment.

#### Scenario: Matching assessment is cached
- **WHEN** a ready or blocked assessment is delivered and repeated eligible polls, including after restart, observe the same head, identity, and labels
- **THEN** each poll performs one PR read and no checks query, evidence read, comment lookup, or readiness mutation
- **THEN** same-head CI or checklist changes alone do not refresh a cached assessment that CI does not block

#### Scenario: New head removes ready
- **WHEN** the next eligible poll observes a different head from a previously ready assessment
- **THEN** it removes `merge:ready` before assessing a replacement and does not replay the stale ready intention
- **THEN** a head different from the run produces `merge:blocked` with a mismatch reason, even if CI is green

#### Scenario: Unlabelled head is reassessed
- **WHEN** readiness labels are absent or inconsistent, or a new matching publication identity is observed
- **THEN** the poll makes at most one checks query and a fresh assessment using its one core PR read, with Gitea permitted one additional PR read inside pr_checks
- **THEN** a head matching the run can become ready only if all conditions pass

### Requirement: Recoverable readiness delivery

Readiness SHALL reconcile mutually exclusive `merge:ready` and `merge:blocked` labels and one distinct readiness comment per PR/change. It SHALL persist delivery intent separately from publication, explain every determinable failed condition with the full assessed head, reconcile ambiguous writes, preserve unrelated labels, and obey daemon pause guards. It SHALL use the existing recognized approval-marker namespace without changing queue retry timing or attempts; candidate selection SHALL follow Canonical daemon markers. Gitea no-duplicate recovery SHALL assume read-after-write-consistent complete listings and no concurrent creator of the same marker. The deployed single-node assumption SHALL be documented: accepted creates are visible to subsequent listings. If a successfully accepted ambiguous create is invisible on later polls, duplicate comments or PRs remain possible across polls and restart; replicated deployments require revisiting the assumption. This accepted limitation SHALL NOT introduce persistent create reservations or other new recovery state.

#### Scenario: Ready PR is labelled once
- **WHEN** every readiness condition passes and delivery succeeds across repeated polls and restart
- **THEN** the PR receives `merge:ready`, loses `merge:blocked` if present, and has one readiness comment distinct from its publication comment
- **THEN** subsequent cached polls neither reapply the label nor create or edit the comment

#### Scenario: Blocked comment lists conditions
- **WHEN** one or more readiness conditions fail or cannot be established
- **THEN** the PR receives `merge:blocked`, loses `merge:ready`, and the one readiness comment lists all determinable reasons in stable order
- **THEN** later reassessment updates that comment rather than creating another or changing publication history

#### Scenario: Ambiguous writes recover
- **WHEN** a readiness label or comment succeeds remotely but its response or local acknowledgement is lost
- **THEN** a subsequent eligible attempt reconciles observed labels and the stable comment marker before retrying, without a duplicate comment or another publication under the stated Gitea consistency and single-creator assumptions
- **THEN** failed comment reconciliation prevents a blind POST and a newly observed head prevents stale ready delivery

#### Scenario: Pause or write failure retains pending delivery
- **WHEN** pause interrupts readiness operations or a label/comment write fails
- **THEN** no later external operation begins while paused and readiness remains incomplete until eligible recovery
- **THEN** publication stays delivered, run and revision state remain unchanged, and no label is created automatically

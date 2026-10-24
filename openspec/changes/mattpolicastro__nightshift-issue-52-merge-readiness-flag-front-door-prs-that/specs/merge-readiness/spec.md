## Purpose

Give published front-door PRs a head-bound, advisory readiness label with a recoverable explanation, without changing publication, human review, or landing gates.

## ADDED Requirements

### Requirement: Published run eligibility

Readiness SHALL assess only daemon-admitted runs paused at `human_checks` with a matching successfully delivered publication receipt and an open, same-repository PR at the recorded URL and spec branch. It SHALL run independently after publication delivery and SHALL NOT publish, finish, update a branch, merge, or import or invoke landing.

#### Scenario: Delivered publication is assessed
- **WHEN** a matching publication was delivered and the next eligible poll observes the run at `human_checks`
- **THEN** readiness assesses the PR independently while leaving the publication receipt, marker, and run state unchanged
- **THEN** publication is not invoked again and its human-check comment remains exactly one per publication round

#### Scenario: Ineligible run is ignored
- **WHEN** the run is unmanaged, running, at another pause, missing a matching delivered successful publication, or linked to a closed, forked, or wrong-identity PR
- **THEN** readiness makes no label or comment changes and preserves existing publication behavior

### Requirement: Local evidence assessment

Readiness SHALL enumerate current task sections from local run state and committed tasks, select each section's latest evidence event in append order, and require a measured pass with zero timeouts and saved gate completion. It SHALL distinguish the documented no-mutable-lines skip and positively proven test-only diffs from missing evidence, and SHALL NOT parse per-attempt receipts or alter evidence scoring.

#### Scenario: Measured sections pass
- **WHEN** every expected section has a latest passing event with a valid numeric score, zero timeouts, no uncovered scenarios, and saved evidence completion
- **THEN** evidence permits readiness, including when older attempts failed or timed out

#### Scenario: Unmeasured evidence blocks
- **WHEN** a section's latest evidence is unmeasured, failed, uncovered, or has a null score without an allowed skip
- **THEN** readiness blocks and identifies that section and its failed measurement condition
- **THEN** a test-only path classification does not override the recorded failure

#### Scenario: Any timeout blocks
- **WHEN** a latest event reports a positive timeout count even though its pass flag and score otherwise qualify
- **THEN** readiness blocks with the section and timeout count

#### Scenario: No mutable lines need no measurement
- **WHEN** a section's latest passing event has a null score, zero timeouts, saved completion, and the exact saved no-mutable-lines summary
- **THEN** readiness treats that section as measured-not-required without looking for execution receipts

#### Scenario: Test only missing event has positive proof
- **WHEN** a section has no evidence event and a valid bounded section diff contains test paths only, apart from its harness-owned tasks file
- **THEN** readiness accepts the test-only exception
- **THEN** missing boundaries, tasks-only diffs, production paths including integrated changes, and cross-boundary renames do not qualify

#### Scenario: Unknown evidence blocks
- **WHEN** required local inputs are missing, malformed, inconsistent with run identity, or omit a required section or timeout count without the test-only exception
- **THEN** readiness blocks with actionable evidence reasons rather than treating missing data as success

### Requirement: Human and revision conditions

Readiness SHALL require no pending build revision and an empty human-check section or only items explicitly annotated as post-deploy in their referenced committed spec scenarios. It SHALL NOT authorize revision triggers, mutate revision history, or tick human checks.

#### Scenario: Post deploy checks permit readiness
- **WHEN** the body section is empty or every listed check references one known scenario containing `<!-- nightshift:post-deploy -->`
- **THEN** the human-check condition passes without modifying the body or claiming those checks are complete

#### Scenario: Pre merge or unknown checks block
- **WHEN** the section contains an unmarked, unmapped, or malformed check, or its structure cannot establish the checklist
- **THEN** readiness blocks with the outstanding or unknown human-check reason, even if a listed pre-merge check is ticked

#### Scenario: Pending revision blocks
- **WHEN** assessment observes the configured revision trigger or local build-revision state indicating unfinished, stopped, undelivered, or unknown work
- **THEN** readiness blocks without collecting a job, consuming feedback, changing authorization, or delivering that revision

### Requirement: Advisory CI snapshot

Readiness SHALL query `gh pr checks <pr> --json name,bucket,state` with an explicit repository once per assessment. It SHALL accept green only when at least one check is `pass` and all checks are `pass` or `skipping`. It SHALL NOT inspect current-base or merge-ref provenance and SHALL describe the label as advisory.

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

Readiness SHALL require PR head equality with the run head, read the PR once per eligible poll, and query checks only when unlabelled or the stored assessment no longer matches the observed head or publication identity. A complete matching assessment with consistent labels SHALL be cached for both outcomes. Label removal SHALL permit explicit reassessment.

#### Scenario: Matching assessment is cached
- **WHEN** a ready or blocked assessment is delivered and repeated eligible polls, including after restart, observe the same head, identity, and labels
- **THEN** each poll performs one PR read and no checks query, evidence read, comment lookup, or readiness mutation
- **THEN** same-head CI or checklist changes alone do not refresh the cached assessment

#### Scenario: New head removes ready
- **WHEN** the next eligible poll observes a different head from a previously ready assessment
- **THEN** it removes `merge:ready` before assessing a replacement and does not replay the stale ready intention
- **THEN** a head different from the run produces `merge:blocked` with a mismatch reason, even if CI is green

#### Scenario: Unlabelled head is reassessed
- **WHEN** readiness labels are absent or inconsistent, or a new matching publication identity is observed
- **THEN** the poll makes at most one checks query and a fresh assessment using its one PR read
- **THEN** a head matching the run can become ready only if all conditions pass

### Requirement: Recoverable readiness delivery

Readiness SHALL reconcile mutually exclusive `merge:ready` and `merge:blocked` labels and one distinct readiness comment per PR/change. It SHALL persist delivery intent separately from publication, explain every determinable failed condition with the full assessed head, reconcile ambiguous writes, preserve unrelated labels, and obey daemon pause guards. It SHALL use the existing recognized approval-marker namespace without changing queue retry behavior.

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
- **THEN** a subsequent eligible attempt reconciles observed labels and the stable comment marker before retrying, without a duplicate comment or another publication
- **THEN** failed comment reconciliation prevents a blind POST and a newly observed head prevents stale ready delivery

#### Scenario: Pause or write failure retains pending delivery
- **WHEN** pause interrupts readiness operations or a label/comment write fails
- **THEN** no later external operation begins while paused and readiness remains incomplete until eligible recovery
- **THEN** publication stays delivered, run and revision state remain unchanged, and no label is created automatically

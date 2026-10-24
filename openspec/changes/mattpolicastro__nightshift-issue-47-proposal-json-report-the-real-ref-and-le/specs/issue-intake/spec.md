## Purpose

Accept the proposal's real local source ref while keeping public spec branch
identity, exact-commit delivery and existing intake recovery guarantees.

## ADDED Requirements

### Requirement: Exact proposal source refs

For a successful proposal with a nonempty commit, Nightshift SHALL accept
exactly `spec/<change>` or `refs/specloop/proposals/<change>`, where `change`
is the receipt's recorded identity. It SHALL apply this validation to direct
results and collected background results, retain the reported value in the
receipt, and reject all other source-ref values before successful delivery.

#### Scenario: Both supported forms are accepted
- **WHEN** direct invocation or background collection returns a drafted result with a commit and either exact supported ref for the recorded change
- **THEN** validation succeeds and the saved result retains the supplied branch and commit, including for a recorded legacy change name

#### Scenario: Unrelated or incomplete results are rejected
- **WHEN** a drafted result lacks a commit or has a missing, non-string, unrelated, differently namespaced or extended ref instead of either exact supported value
- **THEN** proposal validation fails through the existing error path without pushing, creating a PR or delivering success

### Requirement: Proposal source and destination separation

Nightshift SHALL deliver the recorded proposal commit from the manifest's
source repository to `refs/heads/spec/<change>` for either supported ref.
Proposal-output fetches SHALL use the reported local ref unchanged for private
refs; output already present SHALL need no additional fetch. Delivery SHALL
pin the saved commit and preserve public PR identity, existing lifecycle
effects, eligibility gates, replacement leases and retries.

#### Scenario: Fallback proposal opens public spec PR
- **WHEN** an eligible issue's successful proposal is retained under its private ref while the local spec branch remains checked out at an older commit
- **THEN** the reported ref can be fetched from the source repository and the daemon pushes the reported proposal commit to the public `spec/<change>` branch
- **THEN** it opens the normal draft PR with its issue link, risks and review label, comments the link, removes intake labels and leaves the review checkout unchanged
- **THEN** it does not publish a private ref or use one as the PR head

#### Scenario: Saved fallback result retries delivery
- **WHEN** delivery of a successful fallback result fails during push or GitHub notification and a later eligible poll resumes after restart
- **THEN** the retained result delivers its original commit without redrafting or substituting the current local spec branch tip
- **THEN** existing PR/comment reconciliation prevents duplicate delivery artifacts and completion is recorded only through the existing success path

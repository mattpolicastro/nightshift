## Purpose

Preserve forge-specific readiness and safety while completing native features
in place instead of archiving them.

## ADDED Requirements

### Requirement: Native finish recovery on both forges

Native finish SHALL reuse a durable Complete Status commit after external
failure. GitHub ready/read-back argv and Gitea title verification SHALL retain
their existing behavior, and completion SHALL never merge the PR.

#### Scenario: Native push and ready retries
- **WHEN** a native completion push fails or times out, or a GitHub ready/Gitea title-readiness operation fails after push
- **THEN** the run remains at human_checks with its live tasks and durable completion commit, and a fresh invocation reuses that head without another completion commit
- **THEN** push failure prevents readiness calls; successful retry marks done only after push and verified readiness, with existing GitHub lost-response read-back and Gitea already-unprefixed-title reconciliation

## MODIFIED Requirements

### Requirement: Verified Gitea finish

Finish SHALL preserve expected-head, completed-task, format completion and push
checks. Format completion SHALL archive OpenSpec changes and mark native
spec-kit features Complete without archiving. After pushing, finish SHALL read
the Gitea PR title, remove one exact leading WIP prefix with PATCH when present,
then GET and verify the intended title before marking done. An already
unprefixed title SHALL require no PATCH. Failed verification SHALL leave the
run retryable at human_checks. This qualifies archive-only wording; the
existing OpenSpec execution and assertions remain unchanged.

#### Scenario: Prefix removal is verified
- **WHEN** a Gitea PR has the configured leading WIP prefix and finish passes its gates
- **THEN** finish archives OpenSpec or completes native Status, pushes, reads the title, PATCHes only its title without that prefix and reads back the exact intended title before saving done
- **THEN** custom prefixes are literal and case-sensitive, interior occurrences remain and finish does not merge

#### Scenario: Retry observes current title
- **WHEN** PATCH loses its response, is silently ignored or verification fails
- **THEN** finish reads back after an ambiguous write and succeeds only on the intended title; otherwise it leaves human_checks without replaying PATCH in that invocation
- **THEN** retry reuses the OpenSpec archive or native completion commit, rereads current title and skips PATCH for an already unprefixed title

#### Scenario: Existing finish gates remain
- **WHEN** expected head mismatches, tasks remain unfinished, origin is invalid, the recorded PR URL is foreign or format completion/push fails
- **THEN** finish does not PATCH a title or mark done, retaining existing recovery behavior and GitHub ready/read-back argv

## Purpose

Supply attributed open-PR file context from the manifest-selected forge,
preserving partial evidence and accurately describing backend limits.

## MODIFIED Requirements

### Requirement: Concurrent file context

The critic SHALL receive the complete existing in-flight file map without a test-file filter and a separately attributed map of open PR changed files for the manifest-selected forge. The harness SHALL collect metadata through read-only paginated requests, include production and root-level paths, let each backend decide completeness, and reuse the collected snapshot for the critic recheck.

#### Scenario: Both sources reach the critic
- **WHEN** other unfinished runs and open PRs name different test and production files
- **THEN** the actual critic prompt contains all supplied paths with their change names or PR number/URL attribution, with no test-only filtering
- **THEN** a critic-triggered recheck receives the same snapshot without repeating the PR lookup

#### Scenario: Pagination renames and own branch
- **WHEN** open PRs or their files span multiple pages, a file is renamed, and both a same-repository PR and a fork PR use the effective proposal branch name
- **THEN** lookup includes all pages, both old and new renamed paths, draft PRs, and PRs against different base branches, with sorted deduplicated attribution
- **THEN** it excludes the same-repository proposal PR but includes the fork PR; explicit `github.com` keeps its existing hostname argv, while other hosts use configured Gitea

#### Scenario: Empty and unconfigured context
- **WHEN** a configured repository has no open PRs, or the manifest has no repository in `github`
- **THEN** the prompt explicitly distinguishes a successful empty lookup from unconfigured PR context
- **THEN** unconfigured context makes no forge request and neither case adds an unavailable-context risk

#### Scenario: Partial lookup remains visible
- **WHEN** lookup fails, returns malformed data, reaches a backend completeness bound or exhausts its request scheduling budget
- **THEN** available paths remain in context, the critic is told which context is unavailable or possibly incomplete, and drafting may continue
- **THEN** each distinct limitation appears once in returned risks with a `Critic: ` prefix even if the agent omits it
- **THEN** GitHub retains its 120-second scheduling budget, subprocess timeout of at most 30 seconds clamped to remaining time, and 3000-file-per-PR warning; Gitea uses the socket-timeout contract below

## ADDED Requirements

### Requirement: Gitea context pagination

The Gitea context backend SHALL read open PRs and their files through GiteaForge with independent page/limit requests until an empty page. Short pages SHALL NOT prove completion. File records SHALL use filename identities without requiring numeric IDs. It SHALL preserve valid partial paths and report malformed, repeated, conflicting or incomplete pagination rather than claim complete evidence.

#### Scenario: Short pages retain renamed paths
- **WHEN** Gitea returns short nonterminal PR and file pages, including file rows without numeric IDs and renamed files
- **THEN** the backend reads through empty pages using limit 50, includes old and new filenames with PR attribution and never applies GitHub's 3000-file ceiling
- **THEN** missing head-repository identity never excludes a possibly foreign PR and adds an incompleteness note when malformed

#### Scenario: Pagination evidence cannot hide gaps
- **WHEN** a later page fails, repeats, conflicts with a prior identity, contradicts total/next metadata, or reaches 1000 pages without empty termination
- **THEN** already collected valid paths remain, affected context is marked incomplete and no response Link URL is followed
- **THEN** one PR's file failure permits reading other PRs while time remains; a PR-list failure retains previously collected PR context

### Requirement: Gitea context request limits

Gitea critic requests SHALL use a 30-second socket timeout per attempt and a monotonic guard that starts no new request, including transport retries, once 120 seconds have passed. These are socket and request-start limits, not elapsed-time bounds: a slowly arriving response can overrun both. Received valid paths SHALL be retained and overruns reported as incomplete context.

#### Scenario: Budget prevents later dispatch
- **WHEN** the 120-second boundary is reached before a page request or a transport retry after backoff
- **THEN** the transport guard prevents that dispatch, existing evidence remains and one budget-exhausted note reaches critic risks
- **THEN** an attempt started just before the boundary still receives a 30-second socket timeout, with no new request after the boundary

#### Scenario: Slowly arriving response overruns
- **WHEN** an already-started response keeps arriving and its body read completes after 30 or 120 elapsed seconds
- **THEN** the collector accepts valid paths from the response, reports budget exhaustion if 120 seconds have passed, and starts no later request
- **THEN** neither the spec nor operator guidance claims a hard wall-clock deadline, and no transport read-deadline implementation is added

# spec-critic Specification

## Purpose
Review a linted, feasible OpenSpec draft against the human spec-review checklist before PR delivery, using concurrent-file context, one bounded correction, and existing contradiction/risk outcomes.

## Requirements

### Requirement: Independent checklist review

The critic SHALL use a separate read-only agent call on the exact candidate and effective change, with `feasibility.SCHEMA`. Its checklist SHALL cover over-engineering, file collisions and hidden instructions, invalidated protected assertions, conflicts with unowned behavior, tests broken by pending changes, unplanned migrations, retry and idempotency gaps, and stale operator docs and manual steps. Contradictions SHALL carry concrete evidence; uncertain and non-blocking concerns SHALL be risks.

#### Scenario: Complete checklist and read contract
- **WHEN** the critic reviews a candidate whose effective change differs from the manifest template
- **THEN** its call uses access `read`, the shared feasibility schema, a fresh extraction of that commit, and a prompt naming the effective change with all eight checklist items and the allowed/frozen paths
- **THEN** its profile selection and time limits match the feasibility review defaults, and its transcript and result are retained separately from the drafting and feasibility artifacts

#### Scenario: Review failure is not a pass
- **WHEN** the critic agent fails, omits its verdict, or returns a malformed result
- **THEN** the review returns an error, retains a diagnostic result, and removes its temporary extraction
- **THEN** proposal handling reports a critic review problem and does not deliver a successful draft or spend another drafting attempt

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

### Requirement: Ordered proposal gates

`propose` SHALL invoke the critic only after lint and feasibility pass for the candidate. It SHALL preserve existing pre-critic retries, question handling, manifest overrides, and branch behavior. Admission and the build loop SHALL retain their existing reviews without acquiring the proposal critic.

#### Scenario: Clean draft receives the extra review
- **WHEN** an initial draft or a spec-stage revision passes lint and feasibility without contradictions
- **THEN** the next review is the critic against that candidate, before the proposal reports success
- **THEN** an empty critic result permits the usual successful result on the effective branch, retaining existing feasibility risks

#### Scenario: Earlier gate prevents critic
- **WHEN** drafting requests human input, lint fails, or feasibility fails or reports contradictions
- **THEN** the current attempt makes no critic call or PR-file lookup and follows its existing questions, problems, or feasibility-feedback behavior

### Requirement: Single critic correction

Blocking critic findings SHALL occupy `Draft.contradictions` and feed all task/problem/evidence fields to at most one subsequent drafting attempt within the existing `rounds` limit. That correction SHALL pass lint, feasibility and a second critic review before success. A failed or unchanged correction SHALL never clear unresolved findings or trigger another critic correction.

#### Scenario: Blocking finding triggers one redraft
- **WHEN** the first critic returns blocking findings, another drafting attempt remains, and the corrected candidate passes lint, feasibility and the critic
- **THEN** every finding reaches the next drafting prompt, exactly one correction write occurs, and the critic reviews the new commit once
- **THEN** the draft succeeds with no contradictions, the corrected commit, and only that candidate's review notes

#### Scenario: Persistent findings reject
- **WHEN** the critic still returns contradictions after the one correction, even with general drafting attempts remaining
- **THEN** those contradictions remain in the result, no third draft or critic correction occurs, and the existing CLI reports rejection rather than publishing a successful proposal

#### Scenario: No remaining attempt rejects
- **WHEN** the first critic finds a blocker on the last allowed drafting attempt
- **THEN** its contradictions remain in the rejected result and the total number of writes does not exceed `rounds`

#### Scenario: Unchanged correction keeps findings
- **WHEN** the drafter makes no change in response to critic feedback
- **THEN** the previous commit and blocking findings remain, no duplicate feasibility or critic review runs, and the proposal is rejected

#### Scenario: Correction fails an earlier gate
- **WHEN** the one critic correction fails lint or feasibility, asks questions, or its drafting session fails
- **THEN** the corresponding existing problems, contradictions, or questions are returned and no further automatic correction is attempted
- **THEN** no critic call occurs for that unsuccessful correction

### Requirement: Notes through existing risks

The accepted candidate's critic risks SHALL be appended to the existing draft risks with the exact prefix `Critic: `. Feasibility and residual overlap risks SHALL retain their existing text and behavior. Identical critic notes and context limitations SHALL appear once in their original order. Serialization and delivery SHALL use existing fields and the existing PR-body renderer without a new CLI field or a daemon change.

#### Scenario: Notes reach the PR body
- **WHEN** a successful proposal contains feasibility risks and non-blocking critic notes and is delivered through the existing intake path
- **THEN** its version-1 CLI result contains both in `risks`, and the captured PR body contains the unchanged feasibility risk and each `Critic: ` note under the existing risk heading
- **THEN** non-blocking notes cause no redraft and introduce no additional result field

#### Scenario: Final notes replace superseded notes
- **WHEN** a critic correction succeeds with different feasibility risks and critic notes from the first candidate, including a note duplicated in the context limitations
- **THEN** the result and draft artifact contain the final candidate's feasibility risks and once-prefixed deduplicated critic notes/limitations, excluding superseded candidate notes
- **THEN** existing residual overlap risks are still appended unchanged

### Requirement: Advisory task count findings

The critic's over-engineering item SHALL flag plans with more than eight tasks,
or documentation-only plans with more than two tasks, as non-blocking risks.
It SHALL name task labels that could merge and their shared concern, or explain
why necessary boundaries prevent a safe merge. Task count alone SHALL never
be a contradiction or require a correction. Independent concrete defects
SHALL retain their existing blocking criteria.

#### Scenario: Excessive plans receive merge advice
- **WHEN** the critic reviews a plan with nine or more tasks, or a documentation-only plan with three or more tasks
- **THEN** its checklist instructs it to report a non-blocking risk identifying tasks that could merge and why, without inventing a merge when boundaries are necessary

#### Scenario: Threshold boundaries are advisory
- **WHEN** a general plan has eight tasks or a documentation-only plan has two tasks
- **THEN** the checklist does not flag it solely for task count, and the drafter's usual six-task target does not become a critic rejection threshold

#### Scenario: Count risk preserves successful drafting
- **WHEN** lint and feasibility pass and the critic's only finding is a task-count risk naming possible merges
- **THEN** the draft retains no contradictions, carries the finding through existing risks with one `Critic: ` prefix, and spends no correction round

### Requirement: Proportionate retry remedies

The critic's retry and idempotency item SHALL scale remedies to blast radius.
Safe re-runs that re-check real state SHALL satisfy the item for an
operator-attended local command. Durable journals, transaction records and
reconciliation of interrupted runs SHALL be treated as over-engineering for
that case. Unattended daemon operations and GitHub writes SHALL retain strict
review of lost responses, retries and duplicate or dropped effects, citing
the write and retry path.

#### Scenario: Safe local reruns suffice
- **WHEN** an operator-attended local command can be safely re-run by re-checking actual current state
- **THEN** the checklist accepts that remedy and does not demand durable journals, transaction records or interrupted-run reconciliation
- **THEN** operator attendance alone does not excuse a re-run that can cause unsafe effects

#### Scenario: Unattended and GitHub effects retain scrutiny
- **WHEN** a change adds or alters an unattended daemon operation or a GitHub comment, label, push or PR edit, even when invoked from a local CLI
- **THEN** the checklist retains strict scrutiny of lost-response and retry paths that can duplicate or drop effects and requires evidence naming the write and retry path

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

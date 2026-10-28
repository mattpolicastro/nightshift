# forge-interface Specification

## Purpose
Isolate intake and merge-readiness GitHub operations behind a factual forge
interface while preserving existing behavior, exact runner argv and transport
controls, and prevent new direct GitHub commands outside the staged boundary.

## Requirements

### Requirement: Factual forge contract

`nightshift.forge` SHALL expose a structural `Forge` Protocol for current
intake, readiness and daemon recovery operations. Its `types` module SHALL
provide frozen factual records while permitting existing result shapes without
new reads or normalization. Backends SHALL NOT implement authorization or
lifecycle policy.

#### Scenario: Frozen records and structural implementation
- **WHEN** a recording-runner GitHubForge or a structural fake supplies the declared operations and factual records are instantiated
- **THEN** callers can type-check against Forge without concrete inheritance, record field assignment is refused, and construction performs no GitHub call
- **THEN** records contain facts rather than trusted, authorized, actionable or ready decisions, and existing dictionary/helper result contracts remain usable
- **THEN** `nightshift.forge.types` provides frozen issue, PR, comment, label-event and CI-check records, with frozen response-text records where the design retains parsing in core

### Requirement: Forge failure compatibility

`nightshift.forge` SHALL expose `ForgeError(RuntimeError)` compatible with
`queue.GhError`. Direct-operation failures and exceptions escaping delegated
helpers SHALL propagate unchanged. Delegation SHALL preserve each helper's
existing error handling and fallback results without adding backend retries.

#### Scenario: Transport failure compatibility
- **WHEN** the selected runner raises a queue.GhError or a pause exception during a direct forge operation, or that exception escapes a delegated queue helper
- **THEN** the same exception object propagates from GitHubForge with unchanged message and transient classification where present, and without a backend retry
- **THEN** queue.GhError remains catchable by its original name and as both ForgeError and RuntimeError, and pause remains separately identifiable

#### Scenario: Delegated helper error fallbacks
- **WHEN** a selected runner raises RuntimeError, including queue.GhError or Paused, within a delegated helper's existing RuntimeError handler
- **THEN** issue_labels preserves labels_of's empty-set result, open_blockers retains unreadable numbers as blockers in input order, and latest_pr_number with strict=False preserves the helper's None result
- **THEN** strict=True latest PR lookup propagates the original exception, and GitHubForge neither reconstructs suppressed exceptions nor adds retries or reads
- **THEN** daemon recovery retains its separate captured-error re-raise after issue_labels, preventing cleanup on an unreadable label lookup

### Requirement: Exact GitHub operations

`GitHubForge(runner)` SHALL preserve existing runner-form argv byte for byte
and retain call order, delegating queue-owned operations to their helpers
with the selected runner. The backend SHALL add no reads, cache, retries or
policy and SHALL NOT expose a generic command escape hatch.

#### Scenario: Direct operation transcripts
- **WHEN** each direct operation in the design inventory is invoked with representative identifiers, labels, heads and bodies
- **THEN** the recording runner observes exactly the existing full argv sequence, including separate fixed-field PR views and unchanged membership URL encoding
- **THEN** the callable returns stdout text and receives argv without a leading `gh`, preserving JSON field order, pagination flags, limits, target spelling and body bytes
- **THEN** issue and PR comments, comment PATCH, draft PR creation and ordered label changes retain exact payloads, and malformed or failed reads do not become empty success

#### Scenario: Helper delegation
- **WHEN** issue listing, label reads, issue relabeling, latest PR lookup, feedback records, feedback items or blocker lookup is requested
- **THEN** GitHubForge calls the corresponding queue helper with its bound runner, preserving strict lookup behavior, helper return values, trust filtering and error semantics
- **THEN** the transcript retains all three paginated feedback sources in order, and an empty blocker list performs no request

### Requirement: Late bound runners

Migrated functions SHALL retain existing runner injection and resolve an
omitted runner through `GitHubForge(runner or queue._gh)` inside the function.
The backend SHALL bind its callable at construction, and every delegated queue
helper SHALL receive that callable explicitly. No module-level forge instance
or function default SHALL freeze the transport for migrated entry points or
their helpers. Internal helpers MAY accept a Forge directly while retaining
existing runner-based calls.

#### Scenario: Default runner remains patchable
- **WHEN** modules are imported before queue._gh is patched, and successive intake, readiness or daemon recovery invocations use different patches without explicit runners
- **THEN** each invocation dispatches through the then-current patched callable, including helper-delegated operations, without invoking the original subprocess transport

#### Scenario: Explicit runner wins
- **WHEN** a caller supplies a recording runner while queue._gh is patched to fail if called
- **THEN** all operations and delegated helpers use that supplied runner with no leading gh and no fallback to queue._gh
- **THEN** a protocol fake supplied to an internal helper is used without constructing another backend or making unrelated calls

### Requirement: Core policy preservation

Trust checks, label-actor authorization, latest-event selection, marker rules,
comment formatting, feedback consumption and workflow decisions SHALL remain
in core code. Existing queue policy helpers SHALL remain the implementation of
their delegated behavior. Extraction SHALL preserve validation, error handling,
receipt writes and operation ordering without introducing rich-text parsing.

#### Scenario: Authorization and comments unchanged
- **WHEN** intake resolves authorized, unauthorized, malformed or unavailable label evidence and delivers or reconciles an existing marked comment
- **THEN** core selects the same latest event and relationship evidence, reaches the same authorization result and preserves the current error or rejection behavior
- **THEN** rejection comments stay once-per-event, ordinary delivery stays an upsert, failed lookups prevent blind creates, and trusted text and marker checks are unchanged

#### Scenario: Revision and proposal delivery unchanged
- **WHEN** approval, proposal recovery, legacy feedback initialization or either revision stage runs through the forge
- **THEN** approved-head binding, source refs, public branches, git argv, saved feedback IDs, receipt identity and acknowledgement order remain unchanged
- **THEN** no extra commit-date, feedback or recovery reads occur, successful work reuses its existing PR and markers, and a newer revision label event is not removed by old delivery

### Requirement: Readiness request budget

Readiness SHALL retain its existing eligibility, one-PR-read polling contract,
conditional single CI snapshot, saved-assessment reuse and CI-blocked
reassessment through named Forge operations. Core SHALL retain CI parsing and
classification, local evidence/human-check/revision decisions, head binding and
advisory wording. The backend SHALL perform no base-provenance or extra check
query and SHALL add no cache.

#### Scenario: Cached and CI blocked polls
- **WHEN** eligible polls observe a matching delivered assessment with consistent labels, or that same assessment is blocked on CI
- **THEN** each poll reads the PR exactly once; a matching assessment not blocked on CI makes no checks or comment query, while a CI-blocked reassessment makes exactly the existing single checks query
- **THEN** checks retain the exact name,bucket,state field order and unavailable, malformed, absent or non-green results retain their existing blocking reasons

#### Scenario: Readiness delivery unchanged
- **WHEN** a new head, inconsistent labels, incomplete delivery or a fresh assessment requires readiness reconciliation
- **THEN** stale merge:ready removal, mutually exclusive readiness labels and the single approval-namespace readiness comment occur in the existing order
- **THEN** publication receipts and comments remain separate, ambiguous writes recover under existing rules, and no publication, finish or landing operation is introduced

### Requirement: Guarded daemon calls

Daemon guarded runners SHALL continue to check pause before and after logical
operations and install queue.gh_guard for transport attempts and reconciliation.
GitHubForge SHALL receive those wrapped runners. queue._gh SHALL remain the
transport with unchanged retry/backoff and kill-switch behavior. Engine-switch
recovery SHALL retain its captured-error and label-validation protections.

#### Scenario: Pause during dispatch and retry
- **WHEN** pause appears before dispatch, after an accepted write or before a transport retry or reconciliation read
- **THEN** existing guards prevent later operations and preserve pending delivery exactly as before extraction
- **THEN** GitHubForge neither bypasses the guard nor adds an attempt, and an already-dispatched write may complete without falsely completing its receipt

#### Scenario: Engine switch recovery unchanged
- **WHEN** daemon recovery observes orphaned work, surviving work, malformed labels or a failed label lookup
- **THEN** its forge-backed reads and writes preserve the same journals, comments, relabeling and claim/outcome ordering
- **THEN** labels_of error suppression cannot convert an unreadable recovery item into empty labels or authorize cleanup, and no worker resumes

### Requirement: AST source boundary

`test_no_gh_argv_outside_backends` SHALL parse all Python sources under
`nightshift/` and `specloop/` with ast and reject every literal list whose first
element is the string gh, issue, pr or api outside `nightshift/forge/github.py`
and the exact temporary allowlist in the design. Parsing or reading failures
SHALL fail validation. The allowlist SHALL include nightshift/factory_land.py
without migrating it; #130 owns that later migration.

#### Scenario: Scan rejects new argv sites
- **WHEN** an unallowlisted source contains a prefixed list, including an assigned list, multiline list or starred-tail list, or cannot be parsed
- **THEN** validation fails with the source path and available line information
- **THEN** comments and string contents alone do not count as argv, and tests exercise each forbidden literal prefix without invoking live services

#### Scenario: Deferred modules remain allowed
- **WHEN** the scan encounters existing argv in queue.py, land.py, factory_land.py, arm.py, outcomes.py, preflight.py, specloop/cli.py or specloop/critic.py at the exact paths in the design
- **THEN** it permits those deferred sites and the GitHub backend, while rejecting near-miss paths and direct builders in intake or daemon
- **THEN** no directory-wide exception, new allowlisted module or command-construction disguise is introduced

### Requirement: Module layout guidance

The root README's module layout SHALL name the Forge contract, factual records,
GitHub backend, standalone Gitea backend and retained queue transport, describe
the staged boundary and avoid claiming Gitea enrollment is available. It SHALL
describe `forge/gitea.py` as implemented over `forge/http.py`, replacing the
obsolete future-backend claim while keeping Gitea enrollment and daemon
integration as future work. This change SHALL leave the concurrent engine
guide and unrelated documentation intact.

#### Scenario: Layout describes staged boundary
- **WHEN** a contributor reads the updated root module-layout table and adjacent explanation
- **THEN** the named modules resolve to implemented files and distinguish backend operations from queue transport and core policy
- **THEN** the scope remains intake and readiness, with later landing migration and Gitea enrollment/integration described as future work; the standalone backend does not imply enrollment support
- **THEN** the table names forge/gitea.py and the explanation describes the backend as implemented over forge/http.py, without retaining the claim that the backend remains future work

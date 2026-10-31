## Purpose

Read Gitea 28.2.0 label actions and complete comment listings through the
existing factual adapter, preserving trust, validation and transport boundaries.

## ADDED Requirements

### Requirement: Unpaginated comment listings

The adapter SHALL read `/repos/{o}/{r}/issues/{n}/comments` and
`/repos/{o}/{r}/pulls/{n}/reviews/{id}/comments` with one GET per listing, without
page/limit or a terminal-page request. Complete reads SHALL preserve the existing
page-shaped interface, record validation, normalization, transport retries and
guards, and SHALL refuse malformed or duplicate-ID evidence. FakeGitea SHALL
model both endpoints as unpaginated.

#### Scenario: Issue comments read once
- **WHEN** comment_pages reads an issue or PR with zero, one or more than fifty comments
- **THEN** one successful GET without query returns every validated record in the existing page-shaped interface, including human and bot records for factual consumers
- **THEN** an empty listing returns `[]`; a nonempty listing returns one page containing all records, with existing validation and normalization
- **THEN** drafting, marker lookup and reconciliation each use a fresh complete listing when required, without an empty terminal request
- **THEN** FakeGitea returns the same complete list even when a direct test request includes page/limit or uses a small configured page_size

#### Scenario: Review comments read once per review
- **WHEN** feedback reads multiple pages of reviews with empty and nonempty inline-comment lists
- **THEN** reviews still paginate to an empty page and each review's comments use one successful GET without query
- **THEN** each complete reader result is `[]` for an empty listing or one page containing all records otherwise
- **THEN** all inline records remain available for existing trust filtering, context rendering and cross-review identity checks
- **THEN** FakeGitea ignores page/limit on each per-review comments endpoint

#### Scenario: Invalid complete comment response refuses
- **WHEN** either comment endpoint returns a non-list, a non-object row, a missing ID, an ID that is not an exact positive integer (including booleans), duplicate IDs (including identical rows), or invalid record fields
- **THEN** the operation raises a nontransient ForgeError without returning partial data or authorizing a mutation
- **THEN** transport failures and guard exceptions retain their existing classification and identity behavior; the one-GET rule permits existing guarded transport retries on transient failures

#### Scenario: Contradictory comment metadata refuses
- **WHEN** a complete comment response claims a malformed or mismatching total, a nonterminal next-page value, or a next relation in any Link header
- **THEN** the read refuses without a continuation request or following a response URL
- **THEN** an optional X-Total-Count must contain decimal digits and equal the complete row count, and X-Next-Page may only be absent, empty or `0`

#### Scenario: Private listing callers share endpoint rules
- **WHEN** a caller such as unchanged contract provisioning invokes _pages directly for either exact comment endpoint, including with page/limit query values
- **THEN** it receives complete validated evidence through one GET without query and the same empty/nonempty return shape
- **THEN** other endpoint paths continue through the existing pagination loop without changed query, termination or refusal rules

## MODIFIED Requirements

### Requirement: Complete endpoint pagination

The backend SHALL use independent page/limit loops for paginated list endpoints,
including repository labels, issues, pulls, timeline, reviews and combined-status
arrays. For these endpoints it SHALL continue after every nonempty page until
explicit empty-page termination, never infer completion from a short page, and
never follow response URLs. It SHALL refuse malformed or conflicting evidence,
repeated nonempty pages, contradictory termination metadata and exhaustion of
the 1,000-page bound without returning partial success.

Issue-label reads SHALL use a single GET without page/limit parameters and
validate the complete response as a list of unique, valid labels. Issue comments
and nested review comments SHALL follow Unpaginated comment listings because
Gitea 28.2.0 returns complete arrays for those endpoints.

#### Scenario: Short intermediate page
- **WHEN** a paginated list endpoint returns a short nonempty page followed by more records and then an empty page
- **THEN** the adapter reads all records using page numbers starting at one and limit 50, without mutating another endpoint's query or following Link URLs

#### Scenario: Incomplete listing refused
- **WHEN** a later page of a paginated endpoint fails, is malformed, conflicts on an existing ID, repeats a nonempty page, contradicts completion metadata or fails to terminate within the bound
- **THEN** the operation raises and returns no partial list, absence conclusion or mutation authorization

### Requirement: Label timeline evidence

Label events SHALL come from `/repos/{o}/{r}/issues/{n}/timeline` rows of type
`label`. Action SHALL derive solely from the string body: `"1"` means labeled
and `""` means unlabeled. Missing, non-string or any other body SHALL refuse;
removed SHALL neither be required nor influence the action. This replaces the
fake boolean-removed assumption with the Gitea 28.2.0 wire encoding.

The adapter SHALL preserve positive event ID, apply/remove action, label name,
actor identity and timestamp in the existing event shape, with association
derived solely from the allowlist. It SHALL present chronological ID order and
refuse unknown or incomplete label-action evidence. FakeGitea's normal timeline
responses SHALL emit body-based label rows without removed, including when
serializing legacy boolean-removed fixture input as described in the design.

#### Scenario: Label actions and actors
- **WHEN** paginated timeline rows contain non-label activity and label apply/remove/reapply events with user and body fields but no removed field
- **THEN** only label rows produce labeled/unlabeled events from the exact body values, preserving IDs, actors, names and timestamps in increasing ID order
- **THEN** an extraneous contradictory removed field cannot override body, and no issue-events endpoint, free-text comment inference or membership lookup is used
- **THEN** the fake serializes valid legacy label fixture actions into that wire shape without altering an explicitly supplied body

#### Scenario: Unknown label evidence
- **WHEN** a label timeline row lacks a valid ID, actor, name, timestamp or exact body action, or conflicts with another row of the same ID
- **THEN** the read is refused rather than manufacturing an actor or successful application, even if a removed field would otherwise suggest an action

### Requirement: Issue and feedback records

Issue reads SHALL preserve the existing title/body/state/label/comment shapes
and omit bot comments from drafting input. Feedback SHALL read all conversation,
review and nested inline sources, preserve stable source-qualified IDs,
timestamps and rendered context, and exclude untrusted/bot text. Reviews SHALL
paginate; conversation and inline comment lists SHALL follow Unpaginated comment
listings. Identical duplicates across review lists MAY be deduplicated during
feedback aggregation; duplicate IDs within a complete comment response SHALL
refuse. Conflicting or malformed eligible records and partial reads SHALL fail.
Consumption and lifecycle decisions SHALL stay in core.

#### Scenario: Draft facts and blockers
- **WHEN** issue detail, issue author, repository facts, drafting comments or blockers are requested
- **THEN** normalized facts retain required data, bot comments are excluded from drafting input and only positively closed blockers are satisfied
- **THEN** an empty blocker list performs no request and no repository owner fact overrides allowlist association

#### Scenario: Complete identified feedback
- **WHEN** trusted and untrusted conversation and inline records occur in complete listings and reviews span multiple pages, with overlapping numeric IDs across sources
- **THEN** trusted non-bot records retain source-qualified identities, timestamps, review states and file/line context, including blank records needed for legacy baselines
- **THEN** actionable items preserve the existing instruction filter and formatting; conflicting duplicate identities, missing eligible IDs, failed comment reads and later review-page failures raise without partial results
- **THEN** identical inline records repeated across distinct reviews retain existing aggregation behavior while conflicts still refuse

### Requirement: Offline fake and optional live contracts

FakeGitea SHALL run in process through the real transport opener interface with
no sockets and exercise silent writes, ignored filters, paginated and explicitly
unpaginated endpoints, and lost responses. Reusable normal-operation contracts
SHALL run offline by default and provide live cases gated by GITEA_CONTRACT_URL.
Missing URL SHALL skip before networking or credential validation; configured
but incomplete live setup SHALL fail.

Existing tests and assertions SHALL remain intact except the exact obsolete
Gitea shape expectations enumerated in this change's design, which SHALL be
replaced by assertions of the body-action and complete-comment requirements.
This exception includes only issue-comment pagination fixtures/assertions in
the approved landing test files. All GitHub assertions and `tests/contract/`
SHALL remain unchanged. The exception is necessary because those fixtures
previously asserted server behavior that Gitea 28.2.0 does not implement.

#### Scenario: Fake exercises transport behavior
- **WHEN** adapter tests and shared contracts run with FakeGitea
- **THEN** the real request encoding, guarded dispatch, response parsing and adapter behavior execute without sockets or service processes, including ignored filters, real label/comment shapes and injected write failures
- **THEN** the full pytest suite passes and type checking introduces no new baseline errors

#### Scenario: Live suite skips without URL
- **WHEN** GITEA_CONTRACT_URL is absent during live contract selection
- **THEN** collected live cases explicitly skip before any opener dispatch, even without credentials, and offline contracts still execute

#### Scenario: Configured live setup is strict
- **WHEN** live contract selection has a URL but missing credentials, identity, repository or required fixture evidence
- **THEN** setup fails rather than skipping assertions or claiming a live pass
- **THEN** tests can verify this failure path offline with networking forbidden, and run-owned cleanup never deletes preexisting fixture resources

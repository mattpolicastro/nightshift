## Purpose

Read Gitea 28.2.0 timeline and combined-status pages as served while preserving
complete evidence, factual return shapes, and endpoint-specific refusal rules.

## ADDED Requirements

### Requirement: Timeline wire pages

For the exact `/repos/{o}/{r}/issues/{n}/timeline` endpoint, the adapter SHALL
treat JSON null as an empty terminal page and SHALL NOT interpret
`X-Total-Count` as a listing total. A present count SHALL retain its existing
decimal/integer syntax validation but SHALL NOT determine completion or be
compared across pages or with accumulated IDs. All other record and pagination
safeguards SHALL remain in force, including #165's label-action semantics.

#### Scenario: Null terminal timeline
- **WHEN** a timeline is initially null or nonempty timeline pages end with null and `X-Total-Count: 0`
- **THEN** the read terminates with respectively no events or all validated label events in the existing chronological shape, without an extra request after null
- **THEN** a null page claiming continuation through X-Next-Page or any Link next relation refuses without partial success

#### Scenario: Per page timeline counts
- **WHEN** distinct timeline pages each contain one row and report `X-Total-Count: 1`, followed by null with count zero
- **THEN** every page is read, counts do not bound or invalidate the accumulated listing, and all valid label events are retained
- **THEN** page numbers start at one with limit 50, short pages do not stop the read, and response URLs are never followed

#### Scenario: Timeline safeguards remain
- **WHEN** timeline evidence includes malformed rows or count syntax, conflicting IDs, repeated nonempty pages, contradictory termination hints, a later read failure, or no terminal page within 1,000 pages
- **THEN** the operation refuses without returning partial evidence, preserving transport and guard behavior
- **THEN** identical overlapping rows may still deduplicate, while label-action, actor, timestamp and ordering checks remain unchanged

#### Scenario: Other endpoint pagination remains strict
- **WHEN** a different paginated endpoint returns a null list payload or inconsistent listing totals
- **THEN** its existing null and total refusals remain unchanged; the timeline exception does not apply to suffix lookalikes or other paths
- **THEN** #165's unpaginated issue-label and comment reads retain their existing behavior

### Requirement: Combined status wire pages

`checks_for_sha` SHALL decode row results exclusively from `status` and derive
the combined check from a consistent nonempty-page `(state, total_count)`
snapshot. Valid null-statuses terminal envelopes SHALL contribute no rows and
SHALL NOT establish, replace or participate in snapshot comparison. Existing
validation and empty-list consistency rules SHALL remain; no rows SHALL produce
the existing empty check array.

#### Scenario: Rows use status
- **WHEN** combined-status rows use supported `status` values without row `state`, or include a contradictory row `state`
- **THEN** validation and success contradiction checks use only `status`, while the normalized combined check retains the envelope state and existing bucket mapping
- **THEN** row status must be a string in success, pending, failure, error, warning or skipped; missing, non-string or unknown status refuses instead of falling back to row state

#### Scenario: Null terminal status envelope
- **WHEN** consistent nonempty pages end with an envelope for the requested SHA containing state pending, total_count zero and statuses null
- **THEN** the terminal envelope contributes no rows and does not change the saved state or count; complete evidence produces the same check as the nonempty snapshot
- **THEN** every envelope explicitly contains sha, state, total_count and statuses, matches the exact requested SHA, and has a supported string state and a nonnegative exact-integer count; missing statuses is not explicit null
- **THEN** null statuses with a positive count, a terminal next-page claim, wrong SHA, missing field, invalid state/count or incomplete collected row set still refuses
- **THEN** final completeness and success contradiction checks use the nonempty snapshot and validated, deduplicated rows

#### Scenario: No status rows
- **WHEN** the first page is a valid zero-count null-statuses envelope or a valid zero-count empty-list envelope
- **THEN** checks_for_sha returns JSON `[]` in ResponseText without requiring a nonempty snapshot, claiming a passing check or requesting another page
- **THEN** neither empty shape establishes a nonempty snapshot, and a first-page empty list with a positive count still refuses

#### Scenario: Nonempty status disagreement refuses
- **WHEN** two nonempty status pages disagree on envelope state or total_count, or collected row results contradict a success envelope
- **THEN** the read refuses even if a later null terminal envelope would otherwise be valid
- **THEN** explicit empty-list envelopes must still agree with an existing nonempty snapshot; only validated null statuses receives the changed-envelope exception
- **THEN** conflicting IDs, repeated pages, malformed records, incomplete unique-row counts and the 1,000-page cap retain their existing refusals; identical overlaps retain existing deduplication

## MODIFIED Requirements

### Requirement: Complete endpoint pagination

The backend SHALL use independent page/limit loops for paginated list endpoints,
including repository labels, issues, pulls, timeline, reviews and combined-status
arrays. For these endpoints it SHALL continue after every nonempty page until
explicit empty-page termination, never infer completion from a short page, and
never follow response URLs. It SHALL refuse malformed or conflicting evidence,
repeated nonempty pages, contradictory termination metadata and exhaustion of
the 1,000-page bound without returning partial success. Timeline payloads and
counts SHALL follow Timeline wire pages; combined-status envelopes SHALL follow
Combined status wire pages. Other paginated endpoints SHALL retain existing null
refusal and total checks, including status HTTP-header total checks.

Issue-label reads SHALL use a single GET without page/limit parameters and
validate the complete response as a list of unique, valid labels. Issue comments
and nested review comments SHALL follow Unpaginated comment listings because
Gitea 28.2.0 returns complete arrays for those endpoints.

#### Scenario: Short intermediate page
- **WHEN** a paginated list endpoint returns a short nonempty page followed by more records and then its valid empty terminal page
- **THEN** the adapter reads all records using page numbers starting at one and limit 50, without mutating another endpoint's query or following Link URLs

#### Scenario: Incomplete listing refused
- **WHEN** a later page of a paginated endpoint fails, is malformed under its endpoint rules, conflicts on an existing ID, repeats a nonempty page, contradicts completion metadata or fails to terminate within the bound
- **THEN** the operation raises and returns no partial list, absence conclusion or mutation authorization

### Requirement: Exact SHA combined status

`checks_for_sha` SHALL query combined status for a full exact SHA and validate
complete, consistent status evidence under Combined status wire pages.
`pr_checks` SHALL obtain the current PR head once and use that SHA. A proven
empty status set SHALL mean not registered and return an empty check array.
Registered combined states SHALL map success to pass, pending to pending,
failure/error/warning to fail and skipped to skipping. All other states or
malformed/mismatching evidence SHALL be refused. Success SHALL require at least
one successful row and no row result outside success/skipped, using `status`.

#### Scenario: Status mapping
- **WHEN** an exact SHA has no statuses or has each supported combined state
- **THEN** the result is respectively an empty JSON array or one named combined check with the specified bucket and original state in ResponseText
- **THEN** no-status and skipped-only results cannot imply a passing check, and a valid null terminal envelope does not replace the nonempty snapshot

#### Scenario: SHA and status refusal
- **WHEN** the caller supplies a branch or abbreviated SHA, the response names another SHA, nonempty status pages disagree, or the combined state/count/shape is unknown or malformed under Combined status wire pages
- **THEN** the adapter raises rather than returning absence or green; the valid null terminal envelope is the sole exception to cross-page envelope comparison
- **THEN** a PR head changing after lookup does not redirect the request away from the captured exact SHA, and no merge ref or Actions endpoint is queried

### Requirement: Offline fake and optional live contracts

FakeGitea SHALL run in process through the real transport opener interface with
no sockets and exercise silent writes, ignored filters, paginated and explicitly
unpaginated endpoints, and lost responses. It SHALL emit timeline per-page
counts and null empty pages, and status rows with `status` and pending/zero/null
terminal envelopes. State-backed legacy status fixture conversion SHALL occur
only on copied rows at serialization, never overwrite explicit status values,
and never affect raw route replies or production decoding. Existing body-action
and complete-comment wire behavior SHALL remain intact.

Reusable normal-operation contracts SHALL run offline by default and provide
live cases gated by GITEA_CONTRACT_URL. Missing URL SHALL skip before networking
or credential validation; configured but incomplete live setup SHALL fail.
Existing tests and assertions SHALL remain intact except the exact obsolete
shape fixtures inventoried in this change's design, which SHALL be replaced
under Timeline wire pages and Combined status wire pages. Previously established
#165 body-action and complete-comment corrections SHALL remain intact. All
GitHub assertions and `tests/contract/` SHALL remain unchanged. These narrowly
enumerated fixture corrections SHALL NOT weaken refusal or landing assertions.

#### Scenario: Fake exercises transport behavior
- **WHEN** adapter tests and shared contracts run with FakeGitea
- **THEN** real request encoding, guarded dispatch, response parsing and adapter behavior execute without sockets or service processes, including the timeline/status wire shapes, unchanged label/comment shapes, ignored filters and injected write failures
- **THEN** the full pytest suite passes and type checking introduces no new baseline errors

#### Scenario: Fake serialization preserves test evidence
- **WHEN** state-backed legacy status input, explicitly supplied status fields, raw route replies and timeline slices are serialized
- **THEN** normal wire bodies/headers match the endpoint shapes, stored inputs remain unchanged, explicit invalid status values survive, and raw malformed replies remain malformed
- **THEN** the unchanged contract oracle continues to exercise the real adapter through this wire boundary

#### Scenario: Live suite skips without URL
- **WHEN** GITEA_CONTRACT_URL is absent during live contract selection
- **THEN** collected live cases explicitly skip before any opener dispatch, even without credentials, and offline contracts still execute

#### Scenario: Configured live setup is strict
- **WHEN** live contract selection has a URL but missing credentials, identity, repository or required fixture evidence
- **THEN** setup fails rather than skipping assertions or claiming a live pass
- **THEN** tests can verify this failure path offline with networking forbidden, and run-owned cleanup never deletes preexisting fixture resources

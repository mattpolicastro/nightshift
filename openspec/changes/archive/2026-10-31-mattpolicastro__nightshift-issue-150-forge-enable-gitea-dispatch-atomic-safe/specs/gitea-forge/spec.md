## Purpose

Provide verified Gitea transitions and trustworthy daemon marker evidence while retaining factual reads and existing wire contracts.

## ADDED Requirements



## MODIFIED Requirements

### Requirement: Verified label mutations

Issue and PR label changes SHALL use resolved IDs, preserve unrelated labels,
reject overlapping add/remove requests and read labels back after every
successful write. All requested additions SHALL be verified present before any
requested removal, retaining sorted unique ordering within each group.
For a transition whose source was present, interrupted writes SHALL leave at
least source or destination present, absent concurrent external removal.
An accepted needs-human addition with a lost response SHALL remain a human hold,
even with its source trigger retained; existing early skips and pending-receipt
logic SHALL NOT be bypassed for automatic recovery. A silently ignored mutation or failed read-back SHALL raise
and prevent later writes. The adapter SHALL NOT replay a failed write.

#### Scenario: Write is read back
- **WHEN** additions or removals are requested for an issue or PR
- **THEN** each dispatched write is followed by a complete label read verifying that change before any later write
- **THEN** unrelated labels survive, and already satisfied changes may skip writing after a current read

#### Scenario: Silent write failure
- **WHEN** Gitea acknowledges but ignores a label change, or verification cannot complete
- **THEN** the operation raises rather than reporting success and dispatches no remaining label writes

#### Scenario: Interrupted transition retains a label
- **WHEN** an issue or PR transition from a present source to a destination loses a write response or fails read-back before or after any addition or removal
- **THEN** no removal occurs before every addition is confirmed, no failed write is replayed in that call, and labels contain the source, destination or both, never neither absent concurrent external edits
- **THEN** later eligible retries read actual state, skip satisfied changes and preserve unrelated labels

#### Scenario: Needs human remains a safe hold
- **WHEN** adding needs-human is accepted but its response or verification is lost while the source trigger remains
- **THEN** later polls and restart retain the existing needs-human hold without automatic refusal delivery, receipt bypass or rerunning completed work
- **THEN** after human inspection and resolution, removing needs-human permits the retained authorized trigger to re-enter ordinary eligibility and receipt handling; a fresh round still follows existing input or trusted remove-and-reapply rules


### Requirement: Bot owned marked comments

Comment creation SHALL recognize markers with the deterministic final-line
rule in the design, refuse ambiguous marker structure and search complete
comment listings for bot-owned matches using exact canonical final-marker selection. Marker upserts SHALL edit only bot-authored
comments. Direct updates SHALL verify bot ownership before PATCH. Human copies
SHALL neither authorize edits nor count as successful create reconciliation.
Bot ownership SHALL be tested before marker syntax: malformed human marker
strings SHALL be ignored, while malformed bot marker evidence SHALL refuse.
A separate bot_comment_pages view SHALL supply daemon marker consumers without
filtering factual comment_pages, drafting input or human feedback. Duplicate
canonical bot markers SHALL refuse. Canonical selection SHALL use the shared
final-line predicate without relaxing existing outgoing marker validation.

#### Scenario: Deterministic marker matching
- **WHEN** comment bodies contain valid final-line markers, misplaced or multiple marker tokens, indentation, CRLF, fences or HTML-like content
- **THEN** recognition follows only the design's exact line/token rules without semantic Markdown or HTML parsing
- **THEN** malformed marker-bearing input is refused before writes and original body bytes are retained

#### Scenario: Bot ownership gates upserts
- **WHEN** a marker lookup sees human copies, one bot match or multiple bot matches, or a direct update names a human comment
- **THEN** human comments remain unchanged, one bot match may be updated, multiple bot matches refuse and a direct human update dispatches no PATCH
- **THEN** comment_pages retains human records for factual reads and every edit verifies current bot authorship

#### Scenario: Human copies cannot satisfy daemon lookup
- **WHEN** rejection or delivery lookup encounters human copies, including malformed marker strings, and zero or one valid bot-owned canonical match
- **THEN** it ignores the human marker strings, creates when no bot match exists, and only considers the bot match for suppression or upsert
- **THEN** human comments remain byte-for-byte unchanged and factual comment_pages and drafting retain their existing human-record behavior

#### Scenario: Ambiguous bot evidence refuses
- **WHEN** daemon marker lookup or adapter upsert encounters malformed bot marker structure or duplicate canonical bot markers
- **THEN** it refuses without POST or PATCH; a fresh direct-update ownership check also refuses if authorship no longer matches bot_login


### Requirement: Failure and guard boundaries

The adapter SHALL preserve transport retry policy and transient classification,
translate genuine transport failures into ForgeError at public boundaries, and
refuse malformed evidence. It SHALL preserve guard exceptions by identity
without reconciliation, suppression, conversion or further dispatch, even when
their class is GiteaHTTPError. Only genuine GiteaHTTPError SHALL be caught for
write reconciliation. Diagnostics SHALL exclude credentials and remote bodies.

#### Scenario: Transport failure classification
- **WHEN** an operation exhausts transport GET retries or a write fails
- **THEN** the adapter adds no retries, retains transient classification in ForgeError, and does not expose response bodies or tokens
- **THEN** unreadable labels raise, strict PR lookup raises, non-strict lookup may suppress only genuine transport failures, and unreadable blockers remain blocking in input order

#### Scenario: Guard exception identity
- **WHEN** an injected guard raises before a read, write, GET retry or reconciliation, including a GiteaHTTPError, ForgeError or OSError
- **THEN** the exact same exception object escapes, no later request occurs, and no empty fallback or successful reconciliation result is returned
- **THEN** request-local guard-origin tracking cannot misclassify a later transport failure as a prior guard failure

#### Scenario: Optional completion guard preserves identity
- **WHEN** daemon-injected after_request raises after a successful transport request, including after an accepted final write and including a GiteaHTTPError exception object
- **THEN** that same object escapes outside transport reconciliation, no further request or successful receipt acknowledgement occurs, and accepted remote effects remain available for recovery
- **THEN** omitting after_request preserves every existing pre-request guard count, exception identity, GET retry and standalone adapter assertion


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
GitHub assertions and `tests/contract/` SHALL remain unchanged except the single
GitHub inline-marker fixture migration below. These narrowly
enumerated fixture corrections SHALL NOT weaken refusal or landing assertions.

The following additional assertion migrations SHALL be permitted, and no others:
- `test_gitea_label_write_is_read_back` SHALL expect verified POST additions before DELETE removals; `test_label_failure_stops_remaining_writes` SHALL inject removal faults after verified additions and retain its no-later-write and error-classification checks (Verified label mutations).
- Human (`Alice`) cases of `test_malformed_existing_marker_is_not_absence` SHALL expect malformed human marker strings to be ignored rather than blocking a bot create; bot cases SHALL still refuse (Bot owned marked comments).
- `test_gitea_daemon_stages_are_disabled` and its blanket `held`/`check_hold` expectations SHALL be replaced by eligible dispatch coverage; only nested-only and post-removal blanket-hold expectations in `test_gitea_repo_with_engine_switch_journal_is_held` SHALL change. Direct-journal and unreadable-location holds and every mixed GitHub assertion SHALL remain (Eligible Gitea dispatch; Foreign recovery journal hold).
- The sole GitHub exception SHALL be `test_comment_fake_preserves_literal_marker_and_raw_body`: its inline-only `"prefix " + marker + " suffix"` case SHALL expect create_comment, and an added canonical-final-line case SHALL expect update_comment while preserving raw body bytes (Core policy preservation; Canonical daemon markers).

All other GitHub and unrelated assertions SHALL remain intact. Existing #165
and #170 exceptions above SHALL remain in force; tests/contract/ SHALL remain
unchanged.

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

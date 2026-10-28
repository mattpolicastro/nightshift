# gitea-forge Specification

## Purpose
Provide standalone Gitea operations for the front-door Forge interface using
guarded HTTP, with conservative label, identity, status and comment behavior
verified offline and reusable contracts for explicit live validation.

## Requirements

### Requirement: Standalone adapter

`nightshift.forge.gitea.GiteaForge` SHALL implement the existing structural
`Forge` Protocol using `GiteaTransport`, with constructor-injected
`trusted_logins`, `bot_login` and `wip_prefix` defaulting to `"WIP: "`.
Construction SHALL make no request. The adapter SHALL preserve existing return
shapes and SHALL NOT add configuration, daemon wiring, membership lookups,
GitHub command dispatch or lifecycle/authorization decisions.

#### Scenario: Construction and interface
- **WHEN** GiteaForge is constructed with valid arguments and an injected fake opener
- **THEN** no request occurs, the allowlist is copied, and the completed adapter satisfies the Forge method signatures and return contracts
- **THEN** invalid constructor inputs are refused without disclosing their values

#### Scenario: Membership is unsupported
- **WHEN** either membership-response method is called
- **THEN** it raises a nontransient unsupported-operation ForgeError without making a request or fabricating relationship evidence

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

### Requirement: Complete endpoint pagination

The backend SHALL use independent page/limit loops for all list endpoints,
including nested review comments and combined-status arrays. It SHALL continue
after every nonempty page until explicit empty-page termination, never infer
completion from a short page, and never follow response URLs. It SHALL refuse
malformed or conflicting evidence, repeated nonempty pages, contradictory
termination metadata and exhaustion of the 1,000-page bound without returning
partial success.

#### Scenario: Short intermediate page
- **WHEN** any list endpoint returns a short nonempty page followed by more records and then an empty page
- **THEN** the adapter reads all records using page numbers starting at one and limit 50, without mutating another endpoint's query or following Link URLs

#### Scenario: Incomplete listing refused
- **WHEN** a later page fails, is malformed, conflicts on an existing ID, repeats a nonempty page, contradicts completion metadata or fails to terminate within the bound
- **THEN** the operation raises and returns no partial list, absence conclusion or mutation authorization

### Requirement: Fresh label resolution

Each label-filtered poll operation and each label mutation SHALL read all
repository labels afresh and resolve names to unique IDs. Missing requested
names, duplicate names or malformed label evidence SHALL be refused before
candidate selection or mutation. Issue and PR filtering SHALL occur locally,
without any label filter query parameter or automatic label creation.

#### Scenario: Client side filtering
- **WHEN** issue and open-PR polling receive mixed labels and the server ignores unknown-name filters
- **THEN** requests contain neither label nor labels query parameters, only matching IDs are selected locally, and issue results exclude PR rows

#### Scenario: Missing or duplicate label
- **WHEN** a requested label is missing or a name appears twice, including on different pages
- **THEN** the operation refuses without returning candidates or dispatching label writes

#### Scenario: Poll refreshes label map
- **WHEN** a repository label is renamed or recreated between two poll operations on the same adapter
- **THEN** the second operation reads the repository map again and uses the new evidence rather than cached IDs

### Requirement: Verified label mutations

Issue and PR label changes SHALL use resolved IDs, preserve unrelated labels,
reject overlapping add/remove requests and read labels back after every
successful write. A silently ignored mutation or failed read-back SHALL raise
and prevent later writes. The adapter SHALL NOT replay a failed write.

#### Scenario: Write is read back
- **WHEN** additions or removals are requested for an issue or PR
- **THEN** each dispatched write is followed by a complete label read verifying that change before any later write
- **THEN** unrelated labels survive, and already satisfied changes may skip writing after a current read

#### Scenario: Silent write failure
- **WHEN** Gitea acknowledges but ignores a label change, or verification cannot complete
- **THEN** the operation raises rather than reporting success and dispatches no remaining label writes

### Requirement: Label timeline evidence

Label events SHALL come from `/repos/{o}/{r}/issues/{n}/timeline` rows of type
`label`. The adapter SHALL preserve positive event ID, apply/remove action,
label name, actor identity and timestamp in the existing event shape, with
association derived solely from the allowlist. It SHALL present chronological
ID order and refuse unknown or incomplete label-action evidence.

#### Scenario: Label actions and actors
- **WHEN** paginated timeline rows contain non-label activity and label apply/remove/reapply events with user and removed fields
- **THEN** only label rows produce labeled/unlabeled events, preserving IDs, actors, names and timestamps in increasing ID order
- **THEN** no issue-events endpoint, comment-body inference or membership lookup is used

#### Scenario: Unknown label evidence
- **WHEN** a label timeline row lacks a valid ID, actor, name or boolean removed action, or conflicts with another row of the same ID
- **THEN** the read is refused rather than manufacturing an actor or successful application

### Requirement: Allowlist identity projection

The adapter SHALL report association OWNER only for case-insensitively
allowlisted logins other than `bot_login`, and NONE otherwise, ignoring server
association and permission claims. This projection SHALL apply to authors,
comments, reviews, inline feedback and event actors. Bot comments SHALL never
be idea text, even if the bot appears in the allowlist.

#### Scenario: Allowlist alone determines trust
- **WHEN** records include an allowlisted author, an unlisted server owner, a missing author and an allowlisted bot
- **THEN** only the allowlisted non-bot receives OWNER; all others receive NONE, consistently across camelCase and REST-shaped outputs
- **THEN** bot exclusion uses the same case-insensitive comparison and does not depend on body prefixes

### Requirement: Issue and feedback records

Issue reads SHALL preserve the existing title/body/state/label/comment shapes
and omit bot comments from drafting input. Feedback SHALL read all conversation,
review and nested inline sources, preserve stable source-qualified IDs,
timestamps and rendered context, and exclude untrusted/bot text. Identical
duplicates MAY be deduplicated; conflicting or malformed eligible records and
partial reads SHALL fail. Consumption and lifecycle decisions SHALL stay in core.

#### Scenario: Draft facts and blockers
- **WHEN** issue detail, issue author, repository facts, drafting comments or blockers are requested
- **THEN** normalized facts retain required data, bot comments are excluded from drafting input and only positively closed blockers are satisfied
- **THEN** an empty blocker list performs no request and no repository owner fact overrides allowlist association

#### Scenario: Complete identified feedback
- **WHEN** trusted and untrusted conversation, review and inline records span multiple pages with overlapping numeric IDs across sources
- **THEN** trusted non-bot records retain source-qualified identities, timestamps, review states and file/line context, including blank records needed for legacy baselines
- **THEN** actionable items preserve the existing instruction filter and formatting; conflicting duplicate identities, missing eligible IDs and later-page failures raise without partial results

### Requirement: PR identity and drafts

PR branch lookup SHALL enumerate all states without a head filter, match
`head.ref` exactly and select the highest PR number across complete pages.
PR views SHALL preserve the existing field contracts. Draft state SHALL mean
the case-sensitive configured title prefix; creating a draft SHALL prepend it
at most once. Target URLs SHALL be resolved only within the configured origin
and repository and SHALL never be dispatched as request destinations.

#### Scenario: Highest branch PR
- **WHEN** unsorted open, closed and merged PRs across pages include several exact branch matches and the server ignores head queries
- **THEN** the request uses state=all with no head query and the greatest matching number is returned, or None only after a complete unmatched listing

#### Scenario: PR views and targets
- **WHEN** front-door PR views or number, branch and same-repository URL targets are requested
- **THEN** the adapter supplies required normalized state, labels, head SHA/ref, repository identity, URL and text fields through confined API paths
- **THEN** foreign or ambiguous URLs and missing cross-repository evidence are refused without requesting the supplied URL

#### Scenario: Draft title prefix
- **WHEN** a PR is read or created with the default or a custom WIP prefix
- **THEN** isDraft reflects a case-sensitive prefix test and creation prepends the prefix only when absent, preserving title/body and returning the created URL
- **THEN** no GitHub draft flag or second create attempt is sent

### Requirement: Exact SHA combined status

`checks_for_sha` SHALL query combined status for a full exact SHA and validate
complete, consistent status evidence. `pr_checks` SHALL obtain the current PR
head once and use that SHA. A proven empty status set SHALL mean not registered
and return an empty check array. Registered combined states SHALL map success
to pass, pending to pending, failure/error/warning to fail and skipped to
skipping. All other states or malformed/mismatching evidence SHALL be refused.

#### Scenario: Status mapping
- **WHEN** an exact SHA has no statuses or has each supported combined state
- **THEN** the result is respectively an empty JSON array or one named combined check with the specified bucket and original state in ResponseText
- **THEN** no-status and skipped-only results cannot imply a passing check

#### Scenario: SHA and status refusal
- **WHEN** the caller supplies a branch or abbreviated SHA, the response names another SHA, status pages disagree, or the combined state/count/shape is unknown or malformed
- **THEN** the adapter raises rather than returning absence or green
- **THEN** a PR head changing after lookup does not redirect the request away from the captured exact SHA, and no merge ref or Actions endpoint is queried

### Requirement: Commit parent facts

Commit reads SHALL require and verify the exact requested full SHA and preserve
all parent SHAs and available nested author/committer dates for provenance and
legacy feedback consumers. Missing or malformed parents SHALL be refused,
not interpreted as a root commit. The adapter SHALL NOT decide provenance.

#### Scenario: Complete commit parents
- **WHEN** a root, ordinary or merge commit is read by its full SHA
- **THEN** zero, one or all merge parents respectively are returned with commit metadata
- **THEN** missing parents, malformed parent SHAs and a mismatching commit SHA raise

### Requirement: Bot owned marked comments

Comment creation SHALL recognize markers with the deterministic final-line
rule in the design, refuse ambiguous marker structure and search complete
comment pages for bot-owned matches. Marker upserts SHALL edit only bot-authored
comments. Direct updates SHALL verify bot ownership before PATCH. Human copies
SHALL neither authorize edits nor count as successful create reconciliation.

#### Scenario: Deterministic marker matching
- **WHEN** comment bodies contain valid final-line markers, misplaced or multiple marker tokens, indentation, CRLF, fences or HTML-like content
- **THEN** recognition follows only the design's exact line/token rules without semantic Markdown or HTML parsing
- **THEN** malformed marker-bearing input is refused before writes and original body bytes are retained

#### Scenario: Bot ownership gates upserts
- **WHEN** a marker lookup sees human copies, one bot match or multiple bot matches, or a direct update names a human comment
- **THEN** human comments remain unchanged, one bot match may be updated, multiple bot matches refuse and a direct human update dispatches no PATCH
- **THEN** comment_pages retains human records for factual reads and every edit verifies current bot authorship

### Requirement: Ambiguous comment reconciliation

A marked create SHALL POST at most once per call. After a genuine ambiguous
GiteaHTTPError it SHALL re-list all comments and reconcile only an exact
bot-owned marker, returning success only when the intended body is established.
Absent matches SHALL re-raise the original failure, and failed reconciliation
reads SHALL propagate without another POST. Guard errors SHALL bypass this
handler unchanged. Definite rejection and unmarked failures SHALL not reconcile.

#### Scenario: Accepted create loses response
- **WHEN** the server stores a marked comment but its response is lost or its successful response is undecodable
- **THEN** a complete re-list locates the bot-owned marker, verifies or updates the intended body and returns without a second POST
- **THEN** a human marker copy cannot stand in for that accepted bot comment

#### Scenario: Reconciliation cannot confirm success
- **WHEN** the ambiguous create is not found, lookup fails, a guard raises, or matching bot comments are ambiguous
- **THEN** the appropriate error propagates and no second POST occurs in the call
- **THEN** a definite rejection or unmarked create failure causes no reconciliation lookup

### Requirement: Offline fake and optional live contracts

FakeGitea SHALL run in process through the real transport opener interface with
no sockets and exercise silent writes, ignored filters, pagination and lost
responses. Reusable normal-operation contracts SHALL run offline by default
and provide live cases gated by GITEA_CONTRACT_URL. Missing URL SHALL skip
before networking or credential validation; configured but incomplete live
setup SHALL fail. Existing tests and assertions SHALL remain intact.

#### Scenario: Fake exercises transport behavior
- **WHEN** adapter tests and shared contracts run with FakeGitea
- **THEN** the real request encoding, guarded dispatch, response parsing and adapter behavior execute without sockets or service processes, including ignored filters and injected write failures
- **THEN** the full pytest suite passes and type checking introduces no new baseline errors

#### Scenario: Live suite skips without URL
- **WHEN** GITEA_CONTRACT_URL is absent during live contract selection
- **THEN** collected live cases explicitly skip before any opener dispatch, even without credentials, and offline contracts still execute

#### Scenario: Configured live setup is strict
- **WHEN** live contract selection has a URL but missing credentials, identity, repository or required fixture evidence
- **THEN** setup fails rather than skipping assertions or claiming a live pass
- **THEN** tests can verify this failure path offline with networking forbidden, and run-owned cleanup never deletes preexisting fixture resources

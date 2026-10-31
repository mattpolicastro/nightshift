## Purpose

Enable selected Gitea daemon operations while preserving GitHub transcripts, factual boundaries and explicit compatibility exceptions.

## ADDED Requirements

### Requirement: Canonical daemon markers

Rejection lookup, delivery upsert and GitHub transport reconciliation SHALL
share queue._has_final_marker: normalize CRLF, split on LF, discard only trailing
empty strings and compare the last line exactly. They SHALL NOT trim whitespace
or interpret Markdown/HTML. Gitea SHALL use bot-only evidence and retain its
stricter malformed-marker refusals.

#### Scenario: Inline references do not match
- **WHEN** a valid comment ending with marker B contains inline marker A and a rejection or delivery requests A, with or without another canonical A comment
- **THEN** B neither suppresses rejection nor receives a PATCH; canonical A is selected if present, otherwise a new A comment is created
- **THEN** this holds on both forges without changing B or the daemon's outgoing raw body

#### Scenario: Lost response uses canonical selection
- **WHEN** the production GitHub transport loses a response after POST A and its retry lists inline-A/final-B before the newly stored canonical A
- **THEN** reconciliation PATCHes A only, preserves B byte-for-byte and sends no second POST when A is visible
- **THEN** failed listing prevents a blind retry create and guard/retry behavior remains unchanged

#### Scenario: Physical line matching is deterministic
- **WHEN** candidate bodies vary in CRLF, trailing empty lines, trailing whitespace-only lines, indentation, fences or HTML
- **THEN** only the specified exact last non-empty line matches, without semantic parsing or trimming; malformed Gitea bot marker evidence still refuses under its existing validator

### Requirement: Eligible Gitea dispatch

The daemon SHALL dispatch eligible Gitea repositories through one selected bot
adapter per repository/poll, retaining enrollment preflight, health, authorization,
pause, stopped-job and foreign-journal gates. It SHALL forward that adapter to
all four stages, readiness and failure reporting, isolate repository failures
and keep worker recovery GitHub-only. It SHALL NOT add per-poll preflight,
permission flags or persistent create reservations.

#### Scenario: Healthy Gitea front door runs
- **WHEN** an enrolled Gitea repository has passed preflight, has bot credentials, a healthy manifest, a clear journal location and an eligible allowlisted human's spec:draft issue
- **THEN** the real adapter and normal intake path draft, open the WIP-prefix spec PR, add spec:review, link the issue and remove its trigger without GitHub calls or worker recovery
- **THEN** stage forwarding also reaches admission, both revision stages, publication and readiness using that selected adapter and normal receipt/head/trust rules

#### Scenario: Pending delivery retains existing recovery
- **WHEN** an eligible Gitea item has saved successful work or an already-visible PR/comment after interrupted delivery and the daemon polls again or restarts
- **THEN** existing lookup and receipts reuse the work and recover delivery without rerunning a completed job, subject to read-after-write consistency and no concurrent creator
- **THEN** needs-human and stopped-job holds still prevent delivery; a lost needs-human addition is recovered only through human re-arm under Verified label mutations

#### Scenario: Repository failures stay local
- **WHEN** a Gitea bot credential is missing, its manifest prevents new work or a stage fails alongside healthy GitHub or Gitea repositories
- **THEN** sanitized repository health records the failure and independent repositories continue; GitHub receives its unchanged runner arguments without a forge keyword
- **THEN** eligible admitted work retains its copied-manifest health bypass, permanent item failures report through the selected adapter, and no Gitea operation falls back to gh

#### Scenario: Transient outages never become item holds
- **WHEN** Gitea timeline reads repeatedly raise ForgeError with transient=True for more than the escalation threshold while comment and label endpoints remain writable
- **THEN** repository health reports the outage without a failure comment, needs-human label or triggered work, retaining labels and receipts for a later healthy poll
- **THEN** transient GhError has the same existing exemption, and DeliveryError, permanent errors and thresholds retain their existing treatment

## MODIFIED Requirements

### Requirement: Late bound runners

Migrated functions SHALL retain existing runner injection and resolve an
omitted runner through `GitHubForge(runner or queue._gh)` inside the function.
The backend SHALL bind its callable at construction, and every delegated queue
helper SHALL receive that callable explicitly. No module-level forge instance
or function default SHALL freeze the transport for migrated entry points or
their helpers. Internal helpers MAY accept a Forge directly while retaining
existing runner-based calls. The four stage entry points propose_pending,
admit_pending, revise_pending and publish_pending SHALL accept an optional
keyword-only forge=None and use a supplied adapter unchanged; when None, their
existing construction and timing SHALL remain GitHubForge(runner or queue._gh).

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
- **THEN** core preserves latest-event selection and uses unchanged GitHub relationship evidence or Gitea allowlist projection under Actor relationship resolution, preserving the applicable error or rejection behavior
- **THEN** rejection comments stay once-per-event, ordinary delivery stays an upsert and failed lookups prevent blind creates; trusted text remains unchanged and marker selection follows Canonical daemon markers, with bot-only Gitea evidence
- **THEN** the single GitHub assertion exception is test_comment_fake_preserves_literal_marker_and_raw_body: inline-only existing text expects create_comment and an added canonical-final-line case expects update_comment with unchanged raw outgoing bytes; every other GitHub assertion remains unedited

#### Scenario: Revision and proposal delivery unchanged
- **WHEN** approval, proposal recovery, legacy feedback initialization or either revision stage runs through the forge
- **THEN** approved-head binding, source refs, public branches, git argv, saved feedback IDs, receipt identity and acknowledgement order remain unchanged
- **THEN** no extra commit-date, feedback or recovery reads occur, successful work reuses its existing PR and markers, and a newer revision label event is not removed by old delivery

### Requirement: Readiness request budget

Readiness SHALL retain its existing eligibility, one-core-PR-read polling contract,
conditional single CI snapshot, saved-assessment reuse and CI-blocked
reassessment through named Forge operations. Core SHALL retain CI parsing and
classification, local evidence/human-check/revision decisions, head binding and
advisory wording. The backend SHALL perform no base-provenance or extra check
query and SHALL add no cache. GitHub SHALL retain exactly one PR read; Gitea
MAY perform a second PR read inside pr_checks when CI is queried, followed by
one logical exact-SHA combined-status snapshot with existing pagination.

#### Scenario: Cached and CI blocked polls
- **WHEN** eligible polls observe a matching delivered assessment with consistent labels, or that same assessment is blocked on CI
- **THEN** each poll reads the PR once in core (and Gitea pr_checks reads it once more only when CI is queried); a matching assessment not blocked on CI makes no checks or comment query, while a CI-blocked reassessment makes exactly the existing single checks query
- **THEN** checks retain the exact name,bucket,state field order and unavailable, malformed, absent or non-green results retain their existing blocking reasons

#### Scenario: Readiness delivery unchanged
- **WHEN** a new head, inconsistent labels, incomplete delivery or a fresh assessment requires readiness reconciliation
- **THEN** stale merge:ready removal, mutually exclusive readiness labels and the single approval-namespace readiness comment occur in the existing order
- **THEN** publication receipts and comments remain separate, ambiguous writes recover under existing rules, and no publication, finish or landing operation is introduced

#### Scenario: Gitea adapter read budget
- **WHEN** readiness assesses, reuses or CI-reassesses a Gitea publication through the real adapter
- **THEN** cached no-CI polls read the PR once with no status/comment query, while CI assessments read it twice and obtain one logical combined-status snapshot for the SHA captured by pr_checks
- **THEN** status pagination and non-green/absent/malformed blocking remain intact, with no base-provenance query, cache or change to GitHub read-count assertions


### Requirement: Guarded daemon calls

Daemon guarded runners SHALL continue to check pause before and after logical
operations and install queue.gh_guard for transport attempts and reconciliation.
GitHubForge SHALL receive those wrapped runners. queue._gh SHALL remain the
transport with unchanged retry/backoff and kill-switch behavior. Engine-switch
recovery SHALL retain its captured-error and label-validation protections.
Gitea daemon adapters SHALL receive check_running before transport attempts,
GET retries and reconciliation, plus an optional after_request completion guard
so an accepted final write cannot acknowledge delivery while paused. Default
adapter guards and all GitHub guard behavior SHALL remain unchanged.

#### Scenario: Pause during dispatch and retry
- **WHEN** pause appears before dispatch, after an accepted write or before a transport retry or reconciliation read
- **THEN** existing guards prevent later operations and preserve pending delivery exactly as before extraction
- **THEN** GitHubForge neither bypasses the guard nor adds an attempt, and an already-dispatched write may complete without falsely completing its receipt

#### Scenario: Engine switch recovery unchanged
- **WHEN** daemon recovery observes orphaned work, surviving work, malformed labels or a failed label lookup
- **THEN** its forge-backed reads and writes preserve the same journals, comments, relabeling and claim/outcome ordering
- **THEN** labels_of error suppression cannot convert an unreadable recovery item into empty labels or authorize cleanup, and no worker resumes

### Requirement: Selected forge construction

The daemon's forge_for SHALL return GitHubForge for GitHub and the existing
GiteaForge over its GiteaTransport for Gitea, using the configured bot token
variable. Construction SHALL make no request. GitHub intake calls and local
construction SHALL remain unchanged; only Gitea daemon paths SHALL forward
the selected adapter through forge= to the four stages and report_failure.

#### Scenario: GitHub retains runner construction
- **WHEN** an implicit or explicitly GitHub-kind repository is polled
- **THEN** the daemon passes its existing runner arguments without a forge keyword, intake constructs GitHubForge itself, and existing constructor-spy assertions and fixtures remain unchanged
- **THEN** directly invoking forge_for for GitHub returns GitHubForge without a request, retaining late-bound or explicit runner selection

#### Scenario: Gitea uses its configured adapter
- **WHEN** forge_for is directly invoked or an eligible configured Gitea repository is polled
- **THEN** the existing adapter receives its configured instance URL, bot token, trusted_logins, bot_login and wip_prefix
- **THEN** construction makes no request, missing/empty bot credentials raise a sanitized configuration error, and no operation falls back to GitHub
- **THEN** the Gitea factory result is passed into daemon intake, publication/readiness and failure reporting; standalone construction still dispatches nothing and GitHub stage calls omit forge=

### Requirement: Human token exclusion

Daemon entry SHALL refuse before startup side effects when any declared
land_token_env variable is present in its environment, regardless of value,
forge use, pause or one-shot mode. Direct startup and polling entry points SHALL
enforce the same boundary. Diagnostics SHALL name only the forge and variable.

#### Scenario: Landing environment refuses startup
- **WHEN** a declared landing variable is present, including empty or for an unused forge
- **THEN** service, one-shot and direct entry paths refuse before heartbeat, recovery, notification or child launch, without revealing its value
- **THEN** removing that variable permits otherwise eligible startup; Gitea dispatch still requires its independent normal gates

### Requirement: Module layout guidance

The root README's module layout SHALL retain the Forge contract, factual records, GitHub backend, Gitea backend and retained queue transport. Its staged migration description SHALL be updated for eligible Gitea daemon dispatch; `docs/landing.md` SHALL describe the current #130 terminal Gitea landing boundary. Gitea configuration, adapter construction and read-only preflight SHALL remain distinct from per-item execution authorization and GitHub-only worker recovery. README.md and the affected shared guides SHALL correct obsolete disabled-dispatch claims while preserving unrelated content.

#### Scenario: Layout describes staged boundary
- **WHEN** a contributor reads the root module-layout table and the current landing guide
- **THEN** the named modules resolve to implemented files and distinguish backend operations from queue transport and core policy
- **THEN** the table names forge/gitea.py over forge/http.py, configuration and preflight alone do not authorize individual Gitea workflows, and worker recovery remains GitHub-only
- **THEN** docs/landing.md explains terminal Gitea landing, unchanged Factory UI eligibility and gated Gitea daemon dispatch, superseding historical future-work and blanket disabled-dispatch wording

## REMOVED Requirements

### Requirement: Gitea dispatch disabled

**Reason:** Add-first verified transitions, bot-owned canonical markers and allowlist actor resolution supply the missing contracts that justified the blanket hold.

**Migration:** Replace the unconditional hold with Eligible Gitea dispatch and preserve Foreign recovery journal hold, all normal eligibility gates and GitHub-only worker recovery. Only inventoried blanket-hold assertions change.

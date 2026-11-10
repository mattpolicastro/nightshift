## Purpose

Surface front-door escalations and compact references to human-paused builds,
preserving bounded reads, durable stale evidence and version-1 compatibility.

## ADDED Requirements

### Requirement: Front door escalations

Attention SHALL enumerate open issues and PRs with the configured `needs_human`
label for each enrolled GitHub repository independently of worker history and
trigger labels. Each entry SHALL expose repository, number, title, kind and its
validated issue or PR URL. Discovery SHALL paginate to completion or report the
repository stale; it SHALL NOT silently publish a limited result as complete.

#### Scenario: Labelled issues and PRs listed
- **WHEN** enrolled repositories have labelled open issues and PRs, including a custom configured label, with no worker outcomes
- **THEN** text, version-1 JSON and the page list each matching item once with its title, Issue/PR kind, repository, number and correct link
- **THEN** unlabelled, closed, merged and unenrolled items are absent, and no trigger label is required

#### Scenario: Complete listing removes resolved entries
- **WHEN** a successful refresh spans multiple result pages and previously listed items have closed or lost the configured label
- **THEN** all remaining matching items are retained and the removed items disappear, including when the complete result is empty
- **THEN** the read makes no remote writes, receipt changes or worker reconciliation calls

#### Scenario: Unsupported enrollment is visible
- **WHEN** a configured repository selects a non-GitHub forge
- **THEN** attention includes a stale repository status with an unsupported-forge diagnostic and does not query a same-named GitHub repository

### Requirement: Escalation comment summary

Each entry SHALL show the first physical line of its latest recognized escalation
comment, or null. Recognition SHALL follow the design's exact headline and final
marker line rules without interpreting rich text; ambiguity SHALL refuse summary
evidence. Recency SHALL use updated time, creation time when updated time is absent,
then numeric ID. Incomplete comment reads SHALL make the repository stale.

#### Scenario: Most recent escalation supplies summary
- **WHEN** a labelled item has multiple recognized drafting, admission, revision, publication or stage-error comments across pages, an older comment updated later, and newer ordinary discussion or success comments
- **THEN** the most recently updated qualifying escalation supplies its first physical line, with numeric comment ID breaking timestamp ties
- **THEN** later non-escalation comments do not replace the summary and the body is never rendered as Markdown or HTML
- **THEN** summary text is limited to 400 characters with an ellipsis on truncation

#### Scenario: Ambiguous comment does not invent a summary
- **WHEN** comments have copied inline markers, leading indentation, mismatched action/headline, trailing whitespace-only lines, fenced examples or no recognized escalation
- **THEN** only bodies satisfying the exact physical first/final-line rule qualify, and an item with no qualifying comment remains visible with null summary
- **THEN** LF splitting removes one terminal CR per line and only trailing empty lines, without trimming whitespace or semantically parsing fences, HTML or indentation

### Requirement: Independent repository snapshots

Attention SHALL retain a complete last successful snapshot and timestamp per
repository, forge and label. Any incomplete or failed read SHALL preserve it marked
stale with a safe error, including a visible cold-failure status without entries.
Healthy repositories SHALL update independently. Offline reads SHALL make no
remote reads or advance timestamps. Different enrollment selections SHALL NOT
share snapshots.

#### Scenario: Unreadable repository is stale not empty
- **WHEN** a repository read fails either before its first success or after cached entries exist while another repository succeeds
- **THEN** it appears with a stale diagnostic and null or retained last-success timestamp, any previous entries remain, and the healthy repository updates normally
- **THEN** text and JSON exit 1 and the page does not claim everything is clear

#### Scenario: Incomplete read preserves complete snapshot
- **WHEN** a later listing page, comment request or qualifying comment's recency metadata is unreadable or malformed
- **THEN** none of that repository's partial replacement is committed and its complete previous entries and timestamp remain marked stale with the error

#### Scenario: Offline recovery and selection isolation
- **WHEN** attention reads offline, then a complete online read recovers a previously failed repository
- **THEN** offline uses persisted entries and errors without launching GitHub or status, and recovery replaces entries and clears that repository's stale error
- **WHEN** a repository is removed, its forge or label changes, or an enrolled repository has never been checked
- **THEN** removed identities are absent, changed selections do not reuse old entries, and uncached enrollments show `not_checked` rather than a confirmed empty result

#### Scenario: Older refresh cannot replace newer result
- **WHEN** two CLI refreshes overlap and an earlier-started attempt finishes after a newer-started attempt for the same repository selection
- **THEN** the earlier attempt cannot overwrite the newer attempt's entries, timestamp or error

### Requirement: Bounded attention reads

GitHub reads SHALL share a 40-second monotonic budget, with four repository jobs
at most and each request limited to eight seconds or remaining time. Refresh SHALL
select at most 32 repositories, oldest attempted first with name breaking ties;
failures SHALL advance ordering and deferred repositories SHALL be stale.
Specloop's eight-second limit and plugin timeout/output limits SHALL remain unchanged.

#### Scenario: Deadline includes pagination and comments
- **WHEN** list pages, comment pages or stalled requests consume the refresh budget
- **THEN** no further request starts after the shared deadline, active requests are bounded by remaining time, and unfinished repositories retain snapshots marked stale
- **THEN** specloop still receives its bounded status read and the plugin's 60-second online timeout is not increased

#### Scenario: Large enrollment rotates failed attempts
- **WHEN** more than 32 repositories are enrolled and some selected repositories repeatedly fail
- **THEN** at most 32 repositories are attempted with at most four jobs in flight, unattempted repositories show deferral, and later refreshes select least-recently-attempted repositories without starvation by failed peers

### Requirement: Additive attention JSON

`attention --json` SHALL retain version 1, existing keys/types and specloop fields,
adding `repositories` with identity, label, entries, checked time and stale/error
metadata. Legacy `tasks`/`stale` SHALL be empty; worker history SHALL NOT feed
attention rows, counts or errors. Success SHALL exit 0 even with waits; current
repository or specloop failure SHALL exit 1. Usage/configuration handling SHALL
remain unchanged.

#### Scenario: Worker history no longer renders
- **WHEN** historical worker outcomes include awaiting-merge, needs-decision and stale records and attention runs online or offline
- **THEN** no historical worker row, count or error appears in attention, `tasks` and `stale` are empty arrays, and version 1 and existing specloop fields remain present
- **THEN** only current repository/specloop read state determines the attention result, and legacy outcome data remains readable through its existing APIs
- **THEN** each repository includes `repo`, `label`, `entries`, nullable `checked_at`, boolean `stale`, and nullable safe `error` and `error_code`

### Requirement: Factory pause pointers

Specloop selection and JSON metadata SHALL remain unchanged. Text SHALL show each
human pause on one logical line with change, reason, Factory reference and optional
PR URL. The page SHALL show a compact, naturally wrapping change/reason reference
to its Factory row below. It SHALL NOT duplicate detail, progress or actions,
change Factory behavior, or add RPCs or timers for the pointer.

#### Scenario: Paused runs point to Factory
- **WHEN** status includes human-paused, automatically recovering, running and finished runs
- **THEN** only the human-paused runs appear as compact Factory references and existing allowance compatibility and stale metadata are retained
- **THEN** the page names the exact change to find using existing Factory search, without duplicating controls or altering the Factory list or actions

### Requirement: Safe plugin attention projection

The plugin SHALL project validated escalation, health and pause display fields,
ignore worker rows and preserve host execution/cache safeguards. Missing new data
SHALL show front-door unavailability; malformed data SHALL fail the read. Links
SHALL match repository, kind and number and use `openExternalUrl`. Raw subprocess
errors and private paths SHALL NOT cross RPC.

#### Scenario: Structured stale response reaches page
- **WHEN** the command exits 1 with valid repository errors and retained entries, including a cold failed repository with no entries
- **THEN** the plugin retains each repository's health and checked time and the page shows its safe error without discarding healthy entries
- **THEN** the clear empty-state message appears only for a complete, successfully checked empty snapshot

#### Scenario: Legacy and malformed producers remain visible failures
- **WHEN** a version-1 producer omits repository data or a refresh supplies malformed fields, unsafe URLs, invalid JSON or a transport failure
- **THEN** missing repository data shows an update/unavailable diagnostic without worker rows, while malformed or failed refreshes retain the previous good plugin snapshot with a safe error or show unavailable when cold
- **THEN** argv, host paths, offline-first loading, 30-second throttling, shared reads, timeout/output limits, disposal and existing refresh triggers remain unchanged
- **THEN** installation checks and safe transport-error handling remain unchanged, and arbitrary URL schemes or foreign destinations are rejected

#### Scenario: External escalation link opens browser
- **WHEN** an escalation's Open on GitHub control is pressed
- **THEN** its validated issue or PR URL is passed to `openExternalUrl`, an open failure remains visible, and no run action or label mutation occurs

## MODIFIED Requirements

### Requirement: Allowance compatibility and snapshots

Attention SHALL accept older pause objects without `auto_resumes_remaining`,
listing them unless their reason is `usage_limit`. A present allowance SHALL
be null or a nonnegative integer excluding booleans; invalid values SHALL
fail the whole read through existing stale-snapshot handling. Offline reads
SHALL use the persisted entries without fetching status. Existing specloop
snapshot timestamps, entry metadata and exit-code semantics SHALL be preserved.
Worker results SHALL remain readable through historical outcome APIs but SHALL
no longer feed attention, as specified by Additive attention JSON.

#### Scenario: Legacy pause without allowance
- **WHEN** status returns paused runs without the allowance field, including infra, requested, existing human reasons, an unknown reason, and usage-limit
- **THEN** attention lists every such run except usage-limit, while an explicit null allowance is treated as unlimited and omitted

#### Scenario: Invalid allowance preserves snapshot
- **WHEN** attention has successfully cached an exhausted infra or requested entry and a later document contains a boolean, negative number, float, string, array, or object allowance
- **THEN** the whole specloop read fails, attention retains the previous entries and checked-at time marked stale with an error, and text and JSON commands exit 1

#### Scenario: Cached entries and recovery
- **WHEN** exhausted infra and requested entries have been cached, an offline read occurs, and a later successful online read reports no human waits
- **THEN** the offline read returns the cached entries without launching status, and the successful online read clears those entries and any previous specloop stale error and exits 0 when repository reads are also healthy

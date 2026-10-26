# touchpoint-report Specification

## Purpose
Report observed human touchpoints for front-door Nightshift changes from local
intake history and run events, with deterministic output and explicit gaps.

## Requirements

### Requirement: Local report entry point

The system SHALL expose `python -m nightshift.touchpoints` using only local
records under `~/.nightshift/{proposals,admissions,revisions}` and
resolver-discovered `~/.nightshift/runs` with unmigrated `~/.specloop/runs` fallback and compatibility-link deduplication. It SHALL perform no state writes, subprocess commands,
credential reads or network calls. Missing roots SHALL represent empty history.

#### Scenario: Offline read leaves state unchanged
- **WHEN** the module reports fixture receipts and events with external calls forbidden
- **THEN** it succeeds without accessing GitHub or a host service, and source files and directory contents remain unchanged

#### Scenario: Empty local history
- **WHEN** the local roots are absent or empty
- **THEN** the report contains no changes, all scalar totals equal zero, reason maps and errors are empty, and the command exits 0 without creating directories

### Requirement: Front door attribution

The report SHALL identify each row by repository, source issue number and exact
change name, join PR receipts to that identity, and preserve separate rows
across repositories. It SHALL recover identity from proposal jobs or the exact
producer naming convention when possible and diagnose unsafe or ambiguous joins.

#### Scenario: Issues and PRs remain distinct
- **WHEN** two repositories both have issue 8 and their admission and revision receipts are keyed by PR numbers different from 8
- **THEN** separate rows retain each repository and issue 8, with only that row's matching change evidence attached

#### Scenario: Missing proposal and ambiguous run
- **WHEN** a proposal is missing but a PR receipt has a valid front-door change name, or a run name could belong to multiple rows, or a receipt uses a path-like change name
- **THEN** the fallback row has the inferred issue, unknown draft date and a warning; ambiguous run events and unsafe paths are diagnosed without being read or arbitrarily attributed

### Requirement: Intake round accounting

The report SHALL count completed intake rounds by source and outcome: question
rounds from `needs_input`, spec revisions from revision `propose` invocations,
build revisions from revision `revise` invocations, and admission refusals
from admissions only. Recognized terminal revision outcomes SHALL count even
when they request input or reject the revision.

#### Scenario: Each intake kind counted once
- **WHEN** history contains a proposal asking three questions, a spec revision asking two questions, a successful spec revision, a successful build revision with matching event, and a refused admission followed by a successful admission
- **THEN** the row reports two question rounds, two spec revisions, one build revision and one admission refusal, without counting each question string or the duplicate build event

### Requirement: Retained intake history

The report SHALL recover independent rounds from retained job requests/results
and current receipts without counting matching snapshots twice. Successful
build revisions SHALL be deduplicated across results and events by commit;
an unmatched successful build event SHALL count once.

#### Scenario: Retained jobs survive snapshot replacement
- **WHEN** old jobs retain repeated question or refusal outcomes, the current receipt repeats the latest job with or without its job ID, and another receipt has no retained job
- **THEN** independent completed jobs count individually, the compatible latest snapshot adds no duplicate, and the unmatched legacy snapshot counts once with a history warning
- **THEN** an ambiguous snapshot match adds a warning and no additional count beyond the observed jobs

#### Scenario: Build evidence counted once
- **WHEN** a successful build revision appears in a retained result, its current receipt and a `revised` event with the same commit, followed by a `revised` event for a different commit without a receipt
- **THEN** the report counts two build revisions, preserving the unmatched event and collapsing the three copies of the first revision

### Requirement: Incomplete intake rounds

The report SHALL exclude empty-feedback receipts, pending jobs and wrapper
errors from completed-round counts, warn about pending jobs, and list wrapper
errors as diagnostics. These records SHALL NOT count as admission refusals.

#### Scenario: Incomplete and empty rounds
- **WHEN** records include empty revision feedback, a request awaiting its result, a wrapper error, a refused build revision, and a successful build event with no receipt
- **THEN** only the refused build invocation and successful build event contribute build revisions, pending work is warned, the wrapper error is listed, and none contributes an admission refusal

### Requirement: Human event accounting

The report SHALL replay each event log in append order, count human pauses and
manual `resumed` events by reason, and count `human_accept` events. It SHALL
exclude automatic resumes, arbiter accepts and automatic publication handoffs
from those counters.

#### Scenario: Pauses resumes and accepts by reason
- **WHEN** a log contains requested and blocked pauses followed by manual resumes, a stuck pause followed by `human_accept`, a `human_checks` pause, a `ready_to_publish` pause, and an `arbiter_accept`
- **THEN** the row counts four human pauses, two manual resumes, and one human accept, with pause reasons requested, blocked, stuck and human_checks each equal to one and resume reasons requested and blocked each equal to one

### Requirement: Automatic pause classification

The report SHALL classify pauses using observed resolution and scheduling
metadata under the design's recovery rules. Unscheduled pauses SHALL count
as human waits except automatic publication handoffs or automatically resolved
pauses. Scheduled pauses SHALL count when manually resolved or when their
observed per-task recovery allowance is exhausted; uncertain cases SHALL warn.

#### Scenario: Automatic recovery and exhausted infra
- **WHEN** a task has two scheduled infra pauses resolved automatically, a third scheduled infra pause followed by a manual resume, a fourth unresolved scheduled infra pause, and a scheduled usage-limit pause on another task
- **THEN** the third and fourth infra pauses and the manual resume count, the manual resume preserves that task's exhausted two-resume allowance, and automatic recovery never increments manual resumes
- **THEN** an unscheduled previously unknown reason counts as a human pause, while an unresolved scheduled unknown reason produces a warning

#### Scenario: Manual resolution establishes a human wait
- **WHEN** scheduled usage-limit or unknown-reason pauses are manually resumed or accepted, a legacy unscheduled pause is automatically resumed, and a scheduled infra pause without a task remains unresolved
- **THEN** each manually resolved pause counts once as a human wait with its raw reason, while the automatically resolved legacy pause and taskless scheduled infra pause do not count
- **THEN** manual resumes and human accepts increment their respective counters independently of the pause count

### Requirement: Event reason attribution

The report SHALL attribute a manual resume to the preceding unresolved pause.
`revised`, `human_accept`, `resumed`, `auto_resumed`, `finished` and `aborted`
events SHALL clear that attribution.
Malformed lines SHALL invalidate attribution and recovery-budget certainty;
unpaired manual resumes SHALL count under `unknown` with a warning.

#### Scenario: Broken attribution stays explicit
- **WHEN** a manual resume has no unresolved pause, follows a pause cleared by revision or acceptance, or follows a malformed event line
- **THEN** the resume counts under `unknown` with a warning, and corrupt history cannot supply a stale reason or a certain recovery allowance

### Requirement: Draft date selection

The report SHALL accept `--since YYYY-MM-DD` as inclusive UTC midnight and
select whole changes by their earliest initial proposal request mtime, falling
back to proposal receipt mtime. It SHALL expose this approximate timestamp's
source and warn about its limitations. Unknown dates SHALL remain visible
without a filter and be excluded with an error when filtering. Invalid dates
SHALL be rejected before reading state.

#### Scenario: Inclusive cutoff selects whole changes
- **WHEN** proposal request mtimes lie before, exactly at and after the cutoff, with later revisions and replaced current receipts
- **THEN** only the changes at and after the cutoff are included, all their observed touchpoints contribute, and later receipt or revision times do not move an older change into the selection

#### Scenario: Approximate missing and invalid dates
- **WHEN** one change has only a proposal receipt, another has no proposal timestamp, and a caller separately supplies an invalid date
- **THEN** the first exposes its receipt-mtime estimate, the second is visible unfiltered but excluded with an error when filtered, and the invalid argument exits 2 without collecting records

### Requirement: Versioned report output

Text output SHALL show each repository, issue, change, the seven counters,
their sum, reason breakdowns, totals and diagnostics. `--json` SHALL emit one
version-1 document with `since`, `coverage`, `merge_boundary`, `limitations`,
`changes`, `totals` and `errors`, using the design's row and error fields.
Totals SHALL sum included rows and reason maps, without adding reason maps
again to scalar totals. Rows and diagnostics SHALL have deterministic ordering.

#### Scenario: Table and JSON agree
- **WHEN** the same unchanged multi-change history is rendered as a table and with `--json`
- **THEN** all per-change and aggregate counts agree, JSON has integer version 1 and all specified fields including zero counters, and repeated output is deterministic
- **THEN** JSON stdout contains only one document and reason maps sum to their corresponding pause and resume counters

#### Scenario: Observed history does not claim merge
- **WHEN** history contains active, finished and aborted changes, including later events after `finished`
- **THEN** their observed history remains eligible, coverage is `observed_local_history`, merge_boundary is `unknown`, and both formats explain missing history and approximate dates without asserting that finished means merged
- **THEN** a question during spec revision contributes to both categories and the total explicitly represents the sum of category observations

### Requirement: Partial failure isolation

Unreadable or malformed records SHALL be listed with path, line when applicable,
and a nonempty error message while healthy records and valid event lines remain
available. The report SHALL ignore unknown fields and valid unknown event kinds,
but validate known required structure. Completed reports SHALL exit 1 when
errors exist and 0 otherwise; warnings alone SHALL not fail the command.

#### Scenario: Unreadable records are not fatal
- **WHEN** healthy history is mixed with malformed receipts, invalid known fields, injected permission failures, missing admitted-run events, and malformed or truncated JSONL lines
- **THEN** both output formats retain healthy counts and valid lines on both sides of bad lines, list each encountered failure with its source location, and exit 1 without modifying records

#### Scenario: Discovery and compatibility
- **WHEN** one source directory cannot be enumerated while another is readable, and valid records contain new fields or unknown event kinds
- **THEN** the readable source remains represented, the discovery error is listed, and new fields or event kinds do not cause errors or extra touchpoints
- **THEN** KeyboardInterrupt and SystemExit propagate rather than becoming record errors

### Requirement: Producer naming compatibility

When proposal records are missing, the report SHALL infer issue identity from exact owner-qualified or legacy producer names in admission/revision receipts and retained jobs, matching the validated receipt repository and existing issue/slug grammar. It SHALL preserve Front door attribution guarantees, offline read-only operation, accounting, schema, diagnostics and date filtering. Only the fallback matcher and its tests SHALL change.

#### Scenario: Qualified fallback recovers without a proposal
- **WHEN** proposal records are absent and admission or revision evidence for owner/sample names owner__sample-issue-7-add-a-gate, including evidence retained only in a job request
- **THEN** the report retains a row for owner/sample, source issue 7 and that exact change, independently of the receipt's PR number, with the matching run event path
- **THEN** the draft date remains unknown with a warning, and a date-filtered report excludes the row with the existing unknown-date error

#### Scenario: Both conventions retain isolated attribution
- **WHEN** local history without proposals includes a__x-issue-7-add-a-gate for a/x, b__x-issue-7-add-a-gate for b/x, and legacy x-issue-8-add-a-gate receipts for both repositories
- **THEN** the qualified rows and their run events remain separate, the legacy rows infer issue 8 while their shared run events remain unread with an ambiguity diagnostic, and no records are renamed or rewritten
- **THEN** reporting works offline even if either repository's enrollment manifest is unusable, preserving existing counts and output fields

#### Scenario: Fallback requires exact repository and suffix
- **WHEN** fallback evidence includes a correctly qualified name for owner/widget.js_tools-x alongside another owner's prefix, regex-lookalike punctuation, a zero or leading-zero issue number, an invalid or overlong slug, a path-like change, a conflicting issue field or an ambiguous receipt directory without an explicit repository
- **THEN** the valid row is recovered with its exact repository spelling, invalid evidence retains diagnostics without speculative attribution or unsafe event reads, and healthy rows remain available
- **THEN** matching proposal/job identities continue to take precedence over name inference under the existing attribution rules

### Requirement: Compaction preserves history

Delivered-attempt compaction SHALL preserve the existing report's identities, round counts, deduplication and date selection through retained requests/results and original request mtimes. It SHALL require no change to reporting code, output schema or offline read-only behavior.

#### Scenario: Cleanup preserves touchpoint history
- **WHEN** multiple proposal question rounds, spec revisions, build revisions and refused admissions are reported before compaction, after compaction, and after current receipts are replaced by later rounds
- **THEN** each historical round retains its original attribution and count, current-result and build-event duplicates are still collapsed, and new rounds add only their normal observations
- **THEN** request mtimes remain unchanged, including across cleanup interruption/restart, and --since boundaries before, exactly at and after the original proposal date select the same historical changes
- **THEN** offline reporting succeeds without new cleanup-induced diagnostics, state writes, subprocesses or network calls

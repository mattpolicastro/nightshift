## Context

The implementation is limited to the new `nightshift/touchpoints.py` and
`tests/test_touchpoints.py`. The repository has no `AGENTS.md`; the draft was
grounded in `README.md`, `CLAUDE.md`, the relevant existing capability specs,
and the receipt/event producers and their tests.

`nightshift/specloop_intake.py` stores the latest proposal at
`proposals/<owner>__<repo>/<issue>.json`, and admission/revision receipts at
`{admissions,revisions}/<owner>__<repo>/<pr>.json`. PR numbers are not issue
numbers. Proposal receipts carry `repo`, `issue`, `change`, `round_id`, `job`
and `result`; revisions also carry `command` (`propose` or `revise`) and a
label `event`. Admission delivery rewrites its receipt without its job ID.
Receipts have no durable drafting timestamp and are replaced on later rounds.

`nightshift/specloop_job.py` retains `<number>.jobs/<job-id>/request.json`
with an `args` array and `result.json` with either `result` or `error`.
These records recover earlier production rounds. Requests identify changes
with `--change` for `propose`/`admit`, or the first positional argument for
`revise`. Payload text, logs and `*.feedback.json` are not receipts.

`specloop/state.py` appends JSON objects with numeric Unix `at` and string
`event` to `runs/<change>/events.jsonl`. Relevant kinds are `pause`, `resumed`,
`auto_resumed`, `human_accept`, and `revised`. `resumed` lacks a reason;
the preceding unresolved pause supplies it. `revised` carries a commit,
round number and task count. `finished` means archived and ready for review,
not merged. None of the allowed sources records GitHub's actual merge time.

## Goals / Non-Goals

Goals are useful observed counts, explicit counting units and provenance
limits, repeatable output, and isolation of damaged records. Everything can
be tested using temporary directories and fixture JSON without GitHub,
launchd, a running supervisor, agent sessions or a host application.

Non-goals are changing writers or their retention, measuring effort or elapsed
human time, counting individual question strings or feedback tasks, treating
automatic actions as human decisions, or proving a complete merge-bounded
audit. Do not edit `nightshift/cli.py`, `nightshift/daemon.py`,
`nightshift/specloop_intake.py`, `nightshift/specloop_job.py`, `specloop/**`,
`plugins/**`, `docs/validation.md`, `pyproject.toml`, or any existing test.
Use the module entry point; no new installed console script is needed.

## Decisions

### 1. A standalone standard-library reader

Expose `collect_report(nightshift_root: Path, runs_root: Path, *, since: date | None = None) -> dict`,
`render_table(report: dict) -> str`, and `main(argv: list[str] | None = None) -> int`
from `nightshift/touchpoints.py`. The module guard exits with `main()`.
CLI defaults resolve `Path.home() / '.nightshift'` and
`Path.home() / '.specloop' / 'runs'` at invocation time. Only `--json`,
`--since YYYY-MM-DD`, and normal argparse help are needed. Tests inject roots
through the collector or an isolated HOME for subprocess entry-point tests.

Read JSON directly. Do not import daemon/job machinery or specloop internals,
call their collection/status functions, acquire locks, inspect processes,
read credentials, use Git, or perform network operations. Do not create state
directories, caches or repair files. Missing root directories mean empty
history; directory permission/discovery failures are errors.

### 2. Join identities before counting

Discover only numeric `*.json` receipts and
`<number>.jobs/<job-id>/{request,result}.json` under the three intake roots.
Ignore ideas, feedback, logs, temporary files and unrelated JSON. Enumerate
jobs even when the current receipt has disappeared. Treat request argv as
data, never execute it. Permit unknown object fields.

Rows are keyed by `(repo, issue, change)`, not issue number alone. Proposal
receipts provide explicit identity; legacy proposals may use the encoded
repository directory and numeric issue filename. A proposal job provides
the same repository/issue location and an exact change from argv. Reject
conflicting explicit and path identities as record errors rather than
silently joining them. Decode the producer's repository directory convention;
an ambiguous directory without an explicit repository is an attribution error.

Join admissions/revisions to proposals by repository and exact change. If a
proposal is missing, accept the producer's exact front-door naming pattern
`<repository-basename>-issue-<positive-number>-<suffix>` as fallback identity,
with a warning and an unknown drafting date. Do not use the PR filename as an
issue number. Unattributable receipts become errors. Standalone runs with no
front-door identity are outside this report. Match `runs/<change>/events.jsonl`
only to a unique row; a name shared by multiple repositories is an attribution
error, and its events must not be assigned arbitrarily. Validate change names
as a single safe path component before using them in a path.

### 3. Recover rounds without counting receipt copies twice

Each retained job with a valid terminal result supplies one completed
invocation. Join a current receipt's `job` to that invocation, retaining any
extra receipt identity but counting only once. A request with no result is
pending, contributes no completed-round count, and adds a warning. A job
wrapper `error` is a diagnostic, not a refusal or a completed revision.

When a receipt has no job reference, collapse it into a unique compatible job:
same source kind, repository, issue/PR, change, command and outcome, plus the
same result commit when present and the same admission `--head`/receipt
`head` when present. This handles admission delivery stripping `job`.
If multiple jobs match, retain their independently observed invocations but
do not add another count for the ambiguous snapshot; issue a warning. With
no matching job, count the receipt once as a legacy snapshot and warn that
earlier rounds may have been overwritten. Never deduplicate distinct job IDs
merely because their questions or refusal text match.

Count successful build revisions by the union of result and `revised` event
commit IDs per change, so a receipt and event for the same commit count once.
A `revised` event without a receipt still contributes a build revision.
Non-successful completed revision invocations remain separate observed rounds.
An event claiming `revised` without a usable commit is malformed. Do not
deduplicate pause/resume occurrences by text or timestamp: distinct log lines
are distinct recorded events, even if their content is identical.

### 4. Define the counters and event replay

Each row's `counts` contains these integer fields and reason maps:

| Field | Counting unit |
| --- | --- |
| `drafter_questions` | One `needs_input` result from a proposal or spec-revision invocation, regardless of the number of question strings. |
| `spec_revisions` | One completed `propose` invocation under revisions, with outcome `drafted`, `needs_input` or `rejected`. |
| `build_revisions` | One completed `revise` invocation under revisions (`revised` or `refused`), or an unmatched successful `revised` event. |
| `human_pauses` | One human-waiting pause episode under the rules below. |
| `manual_resumes` | One `resumed` event, including when the note is absent or empty. |
| `human_accepts` | One `human_accept` event; `arbiter_accept` and verification `acceptance` never count. |
| `admission_refusals` | One `refused` result from admissions only. |
| `pauses_by_reason` | Counts contributing to `human_pauses`, grouped by raw reason. |
| `resumes_by_reason` | Counts contributing to `manual_resumes`, grouped by replayed pause reason or `unknown`. |
| `total` | Sum of the seven scalar categories above, excluding reason maps. |

A question during spec revision increments both relevant categories. A pause
and its resume are two observations. Thus `total` is a sum of these defined
touchpoints, not a count of unique people, sessions, or label applications.
Initial `spec:draft`/`spec:approved` labels, merge, publication, empty-feedback
receipts (`outcome: empty`), task attempts, autonomous gate reviews and
automatic resumes do not independently increment any counter.

Replay events in file order, not sorted by timestamps. Keep an unresolved
pause and the observed automatic infra-resume counts per task. A new `pause`
replaces the unresolved one. `resumed`, `auto_resumed`, `human_accept`,
`revised`, `finished`, and `aborted` clear it; unrelated events do not.
An unpaired manual resume still counts under `unknown` with a warning.
Malformed event lines clear unresolved attribution and make recovery-budget
knowledge uncertain, preventing a later resume from borrowing a stale reason.

Unscheduled pauses (`resume_at` absent or zero) count as human pauses, including
unknown reason strings, except `ready_to_publish`, which is the daemon's
automatic publication handoff. `human_checks` is a human pause. A pause
actually paired with `auto_resumed` does not count as human, even in legacy
logs without scheduling metadata. Scheduled `usage_limit` pauses do not count
unless manually resumed or accepted. Scheduled `infra` pauses count when
manually resolved, or when two observed `auto_resumed` events for that task
have consumed the current recovery allowance. A manual resume preserves that
task's consumed infra allowance: `specloop/cli.py::_resume` resets attempt
and infra-failure counters but does not reset `auto_resumes`. Scheduled
infra pauses without a task are treated as automatic, matching the supervisor.
If history is broken or a new scheduled reason has no known policy, an
unresolved scheduled pause is unclassified, omitted from human pauses and
explained by a warning. A manual resolution provides enough evidence to count
it, once. Use these local event rules without consulting current run state.

### 5. Dates and the observable lifecycle boundary

`drafted_at` means the earliest observable initial drafting attempt for the
change, including a first attempt returning `needs_input`; it does not mean
the most recent successful draft. Existing request records have no embedded
time: use the minimum proposal `request.json` mtime, converted to UTC, and
set `drafted_at_source` to `request_mtime`. If no proposal job survives, use
the proposal receipt mtime and `receipt_mtime`. Both are explicit estimates
and carry a row warning. Do not use admission time, latest revision time or
event timestamps to invent a drafting date. If no proposal timestamp is
available, both date fields are null and the row is warned.

`--since` accepts an exact, valid ISO calendar date, interpreted as inclusive
UTC midnight (`drafted_at >= cutoff`). Validate before reading state. Select
whole changes, then retain all their observed rounds/events; this is not an
event-date filter. Unknown dates remain visible without the flag; with the
flag exclude them and add an error explaining why the filter could not decide.
Retain discovered read errors even when a row is excluded. Tests set mtimes
explicitly so they never depend on the machine's clock or timezone.

The collection includes all currently available history, including active,
finished and aborted changes. Never stop at `published` or `finished`, and
never label a run merged based on those events. Each document declares
`coverage: "observed_local_history"` and `merge_boundary: "unknown"`.
Explain in both formats that counts may be lower bounds when records were
overwritten or removed, mtimes are approximate, and local records cannot
verify a merge cutoff. Do not claim to recover missing history.

### 6. Output and partial failure contract

The JSON document has exactly these initial top-level fields:

```json
{
  "version": 1,
  "since": null,
  "coverage": "observed_local_history",
  "merge_boundary": "unknown",
  "limitations": ["Draft dates use filesystem timestamps; retained local history may be incomplete; actual merge time is unavailable."],
  "changes": [],
  "totals": {},
  "errors": []
}
```

`since` is null or the normalized input date. Every change object contains
`repo`, positive integer `issue`, `change`, UTC ISO-8601 `drafted_at` or null,
`drafted_at_source`, `counts`, and `warnings` (an array of explanatory strings).
`totals` contains `changes` (row count) plus the same counter fields as a row;
sum only included rows, including reason-map values by key. Zero scalars and
empty maps must be present even when no records exist. Sort rows by repository,
numeric issue, then change; sort reason-map keys; sort diagnostics by path,
line and message. No generation timestamp is necessary: unchanged records
produce identical output.

Each error has `path`, `line` (one-based JSONL line or null), and nonempty
`message` including exception class/context where applicable. Do not echo
whole JSON payloads, prompts or notes. Catch ordinary per-record read,
decode, schema and attribution failures; keep valid siblings and valid lines
before and after bad JSONL, including a truncated final line. Required known
fields get type validation (booleans are not numeric counters/timestamps),
unknown fields and well-formed unknown event kinds are ignored. Missing
events for an observed admitted change are errors; an unadmitted proposal
without a run is normal. Missing results for retained requests are pending
warnings, not corrupt-record errors. Propagate `KeyboardInterrupt` and
`SystemExit`. Treat ordinary discovery failures as path errors while still
collecting other roots.

Text output has one row per change with columns `Repository`, `Issue`,
`Change`, `Questions`, `Spec revisions`, `Build revisions`, `Pauses`,
`Resumes`, `Accepts`, `Refusals`, `Total`, followed by a totals row. Show
per-change and aggregate pause/resume reason breakdowns below the table,
then limitations, row warnings and an Errors list. Do not truncate issue or
change identity. Empty input still prints headers and zero totals.

Exit 0 for a report with no errors (warnings alone are permitted), 1 for a
completed partial report containing errors, and argparse's 2 for invalid
arguments. `--json` emits exactly one document on stdout, including errors
and warnings, with no table or logging mixed in. Invalid arguments use
argparse stderr and produce no report. JSON is the same collected data the
table renders, not a separate counting implementation.

## Risks / Trade-offs

- Retained job IDs measure completed invocations, not provably distinct human
  gestures. An automatic retry recorded as another terminal job may inflate
  revision attempts; missing historical jobs may undercount. Avoid claiming
  this is a perfect behavioral audit. No writer changes are authorized here.
- Filesystem mtimes can move on copy/restore and current receipt mtimes can
  reflect delivery rather than initial drafting. Expose the date source and
  warnings; adding immutable timestamped writer history is separate work.
- Event replay describes recorded human waits and interventions. Recovery
  policy is interpreted from today's producer (infra cap two, usage uncapped);
  malformed or unfamiliar histories must expose uncertainty. New writer fields
  remain forward compatible because unknown fields are ignored.
- Reads are not transactional across a live run. A report can see a request
  before its result or an incomplete appended line. Emit diagnostics and let
  the next invocation observe a later snapshot; never lock or repair the run.
- Runtime behavior is fully fixture-testable. Whether observed counts match
  actual human actions, especially around merge, needs the following human
  PR checklist and is deliberately not an implementation task.

Human PR checklist: compare the implemented report against issue #8 and #14
and their PR histories from **2026-10-21 America/Chicago**, including the UTC
rollover. Verify each question/revision/refusal round, pause reason, resume
and accept against what the operator actually did, and record any missing
or overwritten evidence and the real merge boundary. This is a human review
of histories, not a reason to add GitHub calls to the command or CI tests.

A read-only inspection during drafting found this provisional local evidence
(runs may continue; these are not immutable expected final totals):

| Issue | PR | Questions | Spec revisions | Build revisions | Human pauses | Manual resumes | Accepts | Refusals |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| #8 | #28 | 0 | 0 | 1 | 5 | 4 | 0 | 0 |
| #14 | #45 | 0 | 1 | 0 | 0 | 0 | 0 | 1 |

#8's human pauses were `requested: 1`, `human_checks: 1`, `blocked: 2`,
`gate_evidence: 1`; resumes were `requested: 1`, `blocked: 2`,
`gate_evidence: 1`. Its `ready_to_publish` pause is excluded. Its single
build revision appears in both the retained revision result and a `revised`
event, demonstrating why they must collapse by commit. #14's refused
admission survives in an earlier job result although its current admission
receipt says `admitted`. Neither inspection establishes an actual merge.
Use synthetic equivalents in tests; never make CI depend on this host's data.

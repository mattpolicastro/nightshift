## Purpose

Report observed build phase timings, change lifecycle durations and profile
attempt outcomes from local specloop event logs without changing run state.

## ADDED Requirements

### Requirement: Local read only collection

The system SHALL expose `uv run python -m nightshift.timings` and a collector
with an injectable runs root defaulting to `~/.specloop/runs`. It SHALL read
only immediate run directories' `events.jsonl` files, use directory names as
change identities, and perform no state writes, repairs, subprocess commands,
credential reads or network calls.

#### Scenario: Offline collection preserves history
- **WHEN** a report reads temporary run logs with external calls forbidden
- **THEN** it collects each readable run without requiring state.json, manifests or a host application, and file contents and directory entries remain unchanged

#### Scenario: Missing history is empty
- **WHEN** the runs root is absent or empty
- **THEN** changes, profiles and errors are empty, every phase has count 0, total_seconds 0 and median_seconds null, and the command exits 0 without creating directories

### Requirement: Isolated history diagnostics

The report SHALL skip unreadable runs with path-bearing diagnostics and retain
healthy runs. It SHALL diagnose malformed lines individually, clear pending
pairs at those gaps, and retain other valid lines. Timestamps SHALL be finite
numeric epoch seconds excluding booleans. Unknown fields and well-formed
unknown event kinds SHALL be ignored. Diagnostics SHALL never repair history.

#### Scenario: Unreadable run does not hide healthy runs
- **WHEN** healthy logs surround a missing log, an injected permission failure or a log that fails partway through reading
- **THEN** healthy runs remain, the unreadable runs contribute no partial counters or samples, each failure has a nonempty diagnostic and the completed command exits 1 in either format

#### Scenario: Malformed lines break pairing
- **WHEN** valid events surround invalid JSON, a partial trailing line, non-object records, undecodable bytes, invalid required task keys or non-finite, boolean or string timestamps
- **THEN** each damaged line has a one-based line diagnostic, valid completed samples and counters remain, and no phase pair bridges a damaged line
- **THEN** valid unknown events and additional fields neither fail collection nor add measurements, and zero and fractional timestamps remain valid

#### Scenario: Discovery failures retain readable entries
- **WHEN** root enumeration fails after some entries are discovered or reading raises KeyboardInterrupt or SystemExit
- **THEN** enumeration failure is reported while discovered healthy entries remain eligible, and KeyboardInterrupt and SystemExit propagate

### Requirement: Phase duration aggregation

The report SHALL aggregate completed `implement` to `implemented`, `sealed`
to `verified`, `review` to `reviewed`, `landed` to `evidence`, `admitted` or
manual `resumed` to `implement`, and `pause` to manual `resumed` pairs using
the design's append-order matching rules. It SHALL expose all six phases with
sample count, total seconds and median seconds over individual samples.

#### Scenario: Six phases produce measured samples
- **WHEN** events at seconds 0 admitted, 10 implement, 30 implemented, 35 sealed, 45 verified, 50 review, 55 reviewed, 60 landed, 90 evidence, 100 pause, 160 resumed and 180 implement are collected with matching task keys
- **THEN** implement totals 20 seconds, verify 10, review 5, evidence_gate 30, paused_wait 60, and slot_wait has two samples totaling 30 with median 15
- **THEN** each other completed phase has count 1 and median equal to its total, while the final unfinished implement adds no sample

#### Scenario: Retries and task identities stay separate
- **WHEN** task keys interleave, a task repeats a start or retries after attempt_failed, and successful and failed phase completions appear across multiple runs
- **THEN** pairs never cross task or run boundaries, each completion consumes at most the latest eligible start, stale starts are cleared as specified, and failed outcomes still contribute elapsed samples
- **THEN** pooled samples of 10, 20 and 90 seconds yield count 3, total 120 and median 20 regardless of how runs distribute them

#### Scenario: Evidence and wait boundaries are explicit
- **WHEN** multiple tasks land before one evidence event, a pause is followed by auto_resumed, human_accept or revised, and a manual resume is followed by two implementation starts
- **THEN** evidence pairs only with the latest unconsumed landing, automatic resume and acceptance or revision produce no manual wait or slot samples, and manual resume produces only the first subsequent slot interval
- **THEN** pause cancels an outstanding slot marker, revised and terminal events clear all pending markers, and task or evidence pairs that span a pause retain endpoint wall time without subtracting the pause

#### Scenario: Incomplete and inverted pairs are visible
- **WHEN** history has orphan starts or completions, repeated completions, a negative or non-finite elapsed result, equal timestamps, or reverify between sealed and verified
- **THEN** unmatched endpoints warn without manufactured samples, invalid elapsed results are diagnosed and omitted, and equal timestamps contribute a zero sample
- **THEN** reverify does not create a new phase start and a valid verify pair still measures from sealed

### Requirement: Per change lifecycle accounting

Each selected readable run SHALL report distinct landed tasks, observed
`attempt_failed` records and lifecycle timestamps. Build hours SHALL run from
the first admission to the first subsequent publication; publication wait
hours SHALL run from that publication to the first subsequent finish. Missing
endpoints SHALL produce null, invalid arithmetic SHALL produce null and a
diagnostic, and finish SHALL NOT be described as merge.

#### Scenario: Build and publication wait use observed endpoints
- **WHEN** a run is admitted at 0, published at 7200 and finished at 18000 seconds, with two distinct landed tasks, a repeated landing and three failed-attempt records
- **THEN** it reports tasks_landed 2, failed_attempts 3, build_hours 2 and publication_wait_hours 3, with the observed timestamps
- **THEN** repeated numeric attempt values following manual resume are counted as separate failed-attempt records

#### Scenario: Revisions and missing endpoints retain meaning
- **WHEN** history includes publication at 7200, a revision and publication at 10800, and finish at 18000 after admission at 0, alongside active, aborted, empty and admission-missing runs
- **THEN** the revised run still reports build_hours 2 and publication_wait_hours 3, and its counters and phase samples include later revision history
- **THEN** missing lifecycle endpoints stay null without substituting current time or mtimes, admission-missing runs can still measure publication-to-finish, and early out-of-order markers warn while inverted durations are diagnosed

### Requirement: Profile attempt accounting

The report SHALL group completed implementation and review events by their
recorded profile, preserving separate role counts. Review failures SHALL count
only `reviewed` events with disposition `ok` and `output.verdict` equal to
`FAIL`. The review-failure rate SHALL divide those failures by all observed
review completions for that profile, or be null with zero review completions.
It SHALL describe this as the reported verdict rate, not harness decisions.

#### Scenario: Review profiles have independent failure rates
- **WHEN** profile alpha has four reviewed completions including one ok FAIL and one infra completion, profile beta has two ok FAIL reviews, and profile gamma only has implemented completions
- **THEN** alpha reports review_attempts 4, review_failures 1 and review_failure_rate 0.25, beta reports 2, 2 and 1, and gamma reports 0, 0 and null with its implementation_attempts retained
- **THEN** a reported FAIL later landed remains a reported failure, a reported PASS followed by attempt_failed adds none, and gate reviews, arbiter decisions and verification failures add no review attempts or failures

#### Scenario: Missing profiles and incomplete output stay explicit
- **WHEN** completion events lack usable profiles, contain malformed or absent review output, lack a matching start, or are preceded by admission with a different configured profile
- **THEN** the actual completion profile determines the group, unusable profiles use a null bucket with warnings, malformed output warns and adds no reported failure, and completed attempts still count without timed starts
- **THEN** starts without completions add no profile attempts and profiles are never inferred from configuration

### Requirement: Run selection

The report SHALL support an inclusive UTC `--since YYYY-MM-DD` admission-date
filter and exact-name `--change NAME`, composed before aggregation. It SHALL
select whole histories and SHALL NOT use draft dates, mtimes or event-level
cutoffs. Exact-name filtering SHALL occur before opening run logs.

#### Scenario: Date and name filters compose
- **WHEN** runs are first admitted before, exactly at and after UTC midnight on the selected date, one older run is later resumed, and --change selects one discovered name
- **THEN** only names satisfying both filters contribute to rows, phases and profiles, equality at midnight is included, and a later resume does not admit an older run
- **THEN** all history of included runs is retained, unselected names are not opened, and an unknown or path-like name matches no directory rather than reading outside the root

#### Scenario: Unknown admission and invalid dates are explicit
- **WHEN** a readable run has no usable admission timestamp, a selected name does not exist, or an invocation supplies an invalid date
- **THEN** the admission-missing run appears unfiltered but is excluded with a diagnostic under --since, and an unmatched name yields an empty report
- **THEN** invalid dates exit 2 with argparse stderr before any history read, including when --json is present

### Requirement: Deterministic report output

Text SHALL display phase, change and profile tables with units and explicit
unknown values. `--json` SHALL emit exactly one version-1 document containing
`since`, `change`, `coverage`, `limitations`, `phases`, `changes`, `profiles`
and `errors`, using the design's fields and deterministic ordering. Both
formats SHALL expose diagnostics and observed-history limitations. Completed
reports SHALL exit 1 for errors and 0 otherwise; warnings alone SHALL not fail.

#### Scenario: JSON and text describe the same measurements
- **WHEN** an unchanged fixture containing multiple runs, profiles, unknown values and diagnostics is rendered repeatedly in both formats
- **THEN** all measurements agree before documented text rounding, JSON has integer version 1 and every defined field, every phase appears including zeros, and stdout in JSON mode parses as one document with no NaN or Infinity
- **THEN** rows, profiles, warnings and diagnostics follow the specified order, unknown rates and medians are null, and exit codes distinguish errors from warnings

#### Scenario: Limitations do not imply a time partition
- **WHEN** either format describes completed, active or revised runs
- **THEN** coverage is observed_local_history and limitations explain missing history, evidence and slot estimates, manual-only pause pairing, potential overlapping durations, later revisions, the review-rate denominator and the distinction between finish and merge
- **THEN** no claim or percentage column asserts that the six phases partition initial build time

### Requirement: Operator guidance

README SHALL add one short build-timing subsection beside its existing
touchpoint-report paragraph, preserving existing guidance and links. It SHALL
document the module command, all three options, admission-date selection,
read-only local coverage, diagnostics and the recorded-verdict rate's meaning.

#### Scenario: Timing command is discoverable beside touchpoints
- **WHEN** a reader follows the README's local reporting guidance
- **THEN** it shows `uv run python -m nightshift.timings` and --since YYYY-MM-DD, --change NAME and --json with their selection meanings and reporting limits, while the touchpoint command and concurrent arming guidance remain intact

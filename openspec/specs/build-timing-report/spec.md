# build-timing-report Specification

## Purpose
Compare build speed and recorded profile outcomes using local run event logs.

## Requirements

### Requirement: Read only collection

The report SHALL read local run `events.jsonl` files with an overridable runs
root, defaulting to resolver discovery in `~/.nightshift/runs` with unmigrated `~/.specloop/runs` fallback and compatibility-link deduplication, without writing run state or requiring
external services. It SHALL skip malformed lines and skip an unreadable run
with one diagnostic line, continuing with readable runs.

#### Scenario: Readable history survives bad input
- **WHEN** temporary runs contain valid events around malformed lines and one run raises a file-read error
- **THEN** valid events contribute, the unreadable run is omitted with one path-bearing stderr diagnostic, the command completes with exit 0, and source files remain unchanged

#### Scenario: Empty history
- **WHEN** the runs root is absent or empty
- **THEN** changes, profiles and errors are empty, all six phases have zero count and total with null median, and no directory is created

### Requirement: Phase durations

The report SHALL summarize count, total seconds and median seconds for
`implement` → `implemented`, `sealed` → `verified`, `review` → `reviewed`,
`landed` → `evidence`, `admitted` or `resumed` → `implement`, and `pause` →
`resumed`. Pairs SHALL match by task for implementation, verification and
review, and by run for evidence and waits.

#### Scenario: Six phase summaries
- **WHEN** events occur at seconds 0 admitted, 10 implement, 30 implemented, 35 sealed, 45 verified, 50 review, 55 reviewed, 60 landed, 90 evidence, 100 pause, 160 resumed and 180 implement, with matching task keys
- **THEN** implement totals 20 seconds, verify 10, review 5, evidence_gate 30 and paused_wait 60, each with count 1 and median equal to its total
- **THEN** slot_wait has count 2, total 30 and median 15, and the unfinished implementation adds no sample

#### Scenario: Retries and separate tasks
- **WHEN** task events interleave, one task starts again before completing and then retries after a completed pair, and multiple tasks land before evidence
- **THEN** in append order, the next matching end consumes the pending start, a new start replaces an unfinished start for the same key, and each completed retry adds a sample
- **THEN** task pairs remain separate, evidence uses the most recent landing, malformed intervening lines do not invalidate pending starts, and unmatched endpoints or negative durations add no samples
- **THEN** samples of 10, 20 and 90 seconds across selected runs produce count 3, total 120 and median 20

### Requirement: Change summaries

Each selected readable run SHALL report distinct landed tasks, the count of
`attempt_failed` events, first-admission-to-first-publication hours and
first-publication-to-following-finish hours. Missing or inverted intervals
SHALL be null; later publications SHALL NOT reset these clocks.

#### Scenario: Build and wait hours
- **WHEN** a run is admitted at 0, published at 7200, published again at 10800 and finished at 18000 seconds, with two distinct landed tasks, one repeated landing and three failed-attempt events
- **THEN** it reports tasks_landed 2, failed_attempts 3, build_hours 2 and publication_wait_hours 3
- **THEN** a run missing publication or finish has null for each interval lacking an endpoint

### Requirement: Profile summaries

The report SHALL group `implemented` and `reviewed` completions by their
recorded profile, counting implementation and review attempts separately.
It SHALL count recorded ok FAIL reviews and divide by all review completions
for that profile, with null for no reviews and an unknown-profile bucket.

#### Scenario: Review failure rates
- **WHEN** alpha has four reviewed completions including one ok FAIL and one infra completion, beta has two ok FAIL reviews, and gamma has only implementation completions
- **THEN** alpha reports 4 review attempts, 1 failure and rate 0.25; beta reports 2, 2 and 1; gamma retains implementation attempts with null review rate
- **THEN** missing profiles use unknown and missing output adds no failure, while completions without matching starts still count as attempts

### Requirement: Filtered report output

`uv run python -m nightshift.timings` SHALL accept `--since YYYY-MM-DD`, exact
`--change NAME` and `--json`. Date selection SHALL include whole runs first
admitted at or after UTC midnight, combined with the name filter before
aggregation. Text SHALL contain one measurement table; JSON SHALL contain
`phases`, `changes`, `profiles` and `errors` from the same report data.

#### Scenario: Date and change filters
- **WHEN** runs are admitted before, exactly at and after the cutoff, including an older run later resumed, and --change selects one name
- **THEN** only runs meeting both filters contribute to any measurements, the midnight boundary is inclusive, and all history of included runs is retained
- **THEN** missing admission excludes a run only when --since is used, an unmatched name yields empty history, and an invalid date exits 2

#### Scenario: Text and JSON agree
- **WHEN** a fixture report is rendered in both formats
- **THEN** JSON stdout parses as one document with the specified phase, change, profile and error fields, preserving numeric values and nulls
- **THEN** one text table shows the same measurements with units and unknown markers, while each unreadable-run diagnostic appears once on stderr in either mode

### Requirement: Operator guidance

README SHALL add a short timing-report subsection beside touchpoints,
retaining existing guidance and describing the command, three options,
admission-date selection and a brief note on measurement limitations.

#### Scenario: Discover the timing command
- **WHEN** a reader reaches the README touchpoint-report paragraph
- **THEN** an adjacent subsection shows the timing command and --since, --change and --json, explains read-only local coverage and estimates, and leaves touchpoint and concurrent arming guidance intact

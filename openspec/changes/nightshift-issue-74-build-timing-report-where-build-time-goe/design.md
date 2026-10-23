## Context

`nightshift.touchpoints` provides the standalone module precedent; its receipt
joins and recovery logic are unnecessary here. `specloop/state.py:Run.event`
writes numeric epoch-seconds `at` and an `event` name. `specloop/loop.py`
emits task keys, completion profiles and `attempt_failed`; `specloop/cli.py`
emits admission, manual resume, publication and finish. No producer changes
are needed. Issue #67's documentation change is archived in this checkout.

## Goals / Non-Goals

Provide useful before/after comparisons with a small standard-library report.
Target about 400 implementation lines: roughly 220 module lines, 160 test
lines and 20 README lines, in three tasks. Tests use temporary event logs
without a running daemon, GitHub or other host application.

No state repair, partial-enumeration recovery, mid-read interruption handling,
damage-invalidation rules, warning taxonomy, CPU profiling, scheduler
attribution, merge detection or report framework.

## Decisions

### Files and interfaces

In new `nightshift/timings.py`, expose
`collect_report(runs_root: Path, *, since: date | None = None, change: str | None = None) -> dict`,
`render_table(report: dict) -> str` and
`main(argv: list[str] | None = None, *, runs_root: Path | None = None) -> int`.
Default the CLI root to `Path.home() / ".specloop" / "runs"`; the keyword
override is the test seam. Use a normal module guard; no entry-point changes.

Add tests only in new `tests/test_timings.py`. Insert `### Build timing report`
after the README paragraph about the operations guide and touchpoints, before
dependency preparation. This small insertion is the sole necessary overlap
with #76; preserve its arming/enrollment additions and existing commands/links.

Explicitly leave these concurrent files untouched: `KNOWN-LIMITATIONS.md`,
`docs/arming.md`, `docs/deploy.md`, `docs/landing.md`, `docs/specloop/README.md`,
`nightshift/arm.py`, `nightshift/cli.py`, `nightshift/deploy.py`,
`nightshift/land.py`, `pyproject.toml`, `specloop/cli.py`, `specloop/critic.py`,
`tests/test_arm.py`, `tests/test_deploy.py` and `tests/test_land.py`.
Also leave `nightshift/touchpoints.py`, `tests/test_touchpoints.py` and every
existing assertion unchanged. Only the three scoped files may be implemented.

### Reading and selection

Enumerate immediate run directories in name order, using directory names as
change names. Read each selected `events.jsonl` as a whole text file, then
parse lines in append order. A missing root yields empty history. On an
ordinary file-read error, omit the run and retain one path-bearing error
string. Skip malformed JSON/non-object lines and events without a string kind
or finite numeric `at` (excluding booleans). Ignore unknown event kinds.
Skipped lines neither reset pairing state nor produce diagnostics.

Apply exact `--change` matching before opening logs. `--since` selects whole
runs whose first `admitted.at` is at or after UTC midnight on that date.
Runs without admission remain eligible unfiltered but are omitted with
`--since`. Combine both filters before aggregation. Invalid dates exit 2 via
argparse. Do not use mtimes or cut individual events.

### Pairing and summaries

Keep one pending start per phase and matching key. The next matching end
consumes it; a new start for that key replaces an unfinished start, so a retry
starts a fresh pair. Drop unmatched endpoints and negative durations.
There are no additional cancellation or recovery transitions.

| Phase key | Start → end | Key within each run |
| --- | --- | --- |
| `implement` | `implement` → `implemented` | task |
| `verify` | `sealed` → `verified` | task |
| `review` | `review` → `reviewed` | task |
| `evidence_gate` | `landed` → `evidence` | run |
| `slot_wait` | `admitted` or `resumed` → `implement` | run |
| `paused_wait` | `pause` → `resumed` | run |

Task phases require a nonempty string task key. Multiple landings before
evidence use the most recent landing. `resumed` closes pause wait and opens
slot wait; `auto_resumed` is not an alias. Measured pairs count regardless of
outcome. Pool samples across selected runs for `count`, `total_seconds` and
`median_seconds`; empty phases have 0, 0 and null respectively.

Per change, count distinct landed task keys and `attempt_failed` records.
Use first admission, first subsequent publication, and first finish after
that publication for `build_hours` and `publication_wait_hours`, dividing
seconds by 3600. Without admission, first publication can still supply the
publication-to-finish interval. Missing endpoints or negative intervals yield
null; never substitute now. Later revisions contribute counts and phase
samples without resetting lifecycle clocks.

Group `implemented` and `reviewed` completions by their own `profile`, using
`unknown` for missing/empty profiles. Count `implementation_attempts` and
`review_attempts` separately, including completions without timed starts.
`review_failures` counts reviewed events with `disposition == "ok"` and
`output.verdict == "FAIL"`. Divide by all review completions for that profile;
use null for no reviews. Missing output adds no failure. This is the recorded
verdict rate: the harness can decide differently using findings and waivers.
Do not reconstruct decisions or read attempt artifacts.

### Output and acceptance

Return one dictionary with `phases` (map keyed by the six phase names),
`changes` (rows: `change`, `tasks_landed`, `failed_attempts`, `build_hours`,
`publication_wait_hours`), `profiles` (rows: `profile`,
`implementation_attempts`, `review_attempts`, `review_failures`,
`review_failure_rate`), and `errors` (diagnostic strings).
Sort change/profile rows by name. No versioned envelope or warning structures.

`--json` prints that dictionary as one document. Text flattens measurements
into one table with `Kind`, `Name`, `Metric`, `Value`, `Unit` columns.
Use seconds for phases, hours for lifecycle intervals, counts for tasks and
attempts, percentages for rates, and `-` for unknowns. Both modes print each
error once on stderr; JSON also retains it in `errors`. Completed reports
exit 0 even when some runs were skipped. Add a short note to text and README;
no limitations schema or exhaustive output contract.

Keep these six tests with appropriate `pytest.mark.scenario` IDs:
`test_phase_durations_from_event_pairs`, `test_per_change_build_and_wait_hours`,
`test_review_failure_rate_by_profile`, `test_unreadable_run_is_reported_not_fatal`,
`test_since_and_change_filters`, and `test_json_output_shape`. Include retries,
malformed lines, missing endpoints, empty history, an injected read failure
and unchanged source logs within those fixtures. Acceptance is green
`uv run pytest` and `uv run python scripts/check_types.py`, without weakening
existing assertions or changing the type baseline.

## Risks / Trade-offs

The short reporting note should explain that these are local-history
estimates: missing pairs are dropped, evidence/slot waits are endpoint proxies,
intervals may overlap, recorded FAIL rates can differ from harness decisions,
and finish is not merge. The six phases need not sum to build hours.

Human PR checklist: on the real runs directory, run
`uv run python -m nightshift.timings --json` once and compare the same cohort
and denominator with the 2026-10-23 analysis (about 32% evidence, 21% implement,
18% slot wait, 11% verify, 4% review). Record discrepancies or unavailable
historical inputs in the PR; do not tune results to force those percentages.
CI fixtures cannot establish historical agreement. This host-only check is
not an implementation task.

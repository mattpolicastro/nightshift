## 1. Collect and summarize event history

- [ ] 1.1 Add the simple reader and `collect_report` aggregation in new `nightshift/timings.py`, plus temporary-log fixtures and `test_phase_durations_from_event_pairs`, `test_per_change_build_and_wait_hours`, `test_review_failure_rate_by_profile` and collector coverage for `test_unreadable_run_is_reported_not_fatal` in new `tests/test_timings.py`. Include empty history, skipped malformed lines, retries, missing endpoints and unchanged source logs. Budget about 250 changed lines; use scenario markers. Covers `build-timing-report/read-only-collection/readable-history-survives-bad-input`, `build-timing-report/read-only-collection/empty-history`, `build-timing-report/phase-durations/six-phase-summaries`, `build-timing-report/phase-durations/retries-and-separate-tasks`, `build-timing-report/change-summaries/build-and-wait-hours` and `build-timing-report/profile-summaries/review-failure-rates`.

## 2. Expose the filtered command and output

- [ ] 2.1 Extend `nightshift/timings.py` with whole-run filters, `render_table`, `main` and the module guard, using one report dictionary for the single text table and JSON. Add `test_since_and_change_filters` and `test_json_output_shape` to `tests/test_timings.py`; complete `test_unreadable_run_is_reported_not_fatal` with stderr and exit-code assertions. Budget about 130 changed lines, with no CLI registration or dependency changes. Covers `build-timing-report/filtered-report-output/date-and-change-filters`, `build-timing-report/filtered-report-output/text-and-json-agree`, `build-timing-report/read-only-collection/readable-history-survives-bad-input` and `build-timing-report/read-only-collection/empty-history`.

## 3. Document the report and finish validation

- [ ] 3.1 Insert only the short build-timing subsection beside the touchpoint paragraph in `README.md`, preserving existing content and concurrent arming guidance; budget about 20 lines. Honor all file exclusions in design.md. Run `uv run pytest` and `uv run python scripts/check_types.py`, keeping existing assertions and the type baseline unchanged. Covers `build-timing-report/operator-guidance/discover-the-timing-command`.

## 1. Publication

- [x] 1.1 Update publication in `docs/specloop/README.md` and the specloop flow in `README.md` from the landed base-sync behavior: driver preparation, publish handoff, pauses and recovery, same-PR delivery, and PR #59 citation. Keep this session below 150 changed lines. Covers `specloop-documentation/publication-guidance/base-moves-before-publication`, `specloop-documentation/publication-guidance/base-preparation-pauses`.
- [ ] 1.2 Add tagged tests covering `specloop-documentation/publication-guidance/base-moves-before-publication`, `specloop-documentation/publication-guidance/base-preparation-pauses`, fixing the code where a scenario does not hold

## 2. Spec stage

- [ ] 2.1 Update drafting guidance in `docs/specloop/README.md` for the in-flight map, normalized paths, overlap feedback, four-round default, human-task lint and preserved spec guarantees; add the source-map PR citations and name the files an avoidance instruction must identify. Keep this session below 150 changed lines. Covers `specloop-documentation/spec-drafting-guidance/concurrent-files-inform-a-draft`, `specloop-documentation/spec-drafting-guidance/drafting-rules-preserve-feasible-tasks`.
- [ ] 2.2 Extend the same guide's proposal sequence with the independent critic, all eight checklist items, both concurrent-file sources, single correction and contradiction/risk outcomes, citing PRs #57 and #62. Keep this session below 100 changed lines. Covers `specloop-documentation/spec-critic-guidance/critic-checklist-is-complete`, `specloop-documentation/spec-critic-guidance/critic-outcome-is-actionable`.

## 3. Front door

- [ ] 3.1 Reconcile front-door sections in `docs/specloop/README.md` and `README.md` with dependencies, label authorization, consumed feedback IDs and legacy initialization, idea files and isolated status errors. Retain correct root README revision details and keep worker instructions unchanged. Add adjacent citations from the design and stay below 250 changed lines. Covers `specloop-documentation/front-door-guidance/dependencies-and-label-actors-gate-work`, `specloop-documentation/front-door-guidance/revision-feedback-survives-a-long-round`, `specloop-documentation/front-door-guidance/input-and-status-remain-usable`.

## 4. Evidence

- [ ] 4.1 Update evidence guidance in `docs/specloop/README.md` for concurrency, timeout scoring, prose/comment exclusions, `mutation_skip`, measured versus skipped evidence, and both guard-clause operators. Add small examples and PR citations; reconcile only any conflicting specloop evidence summary in `README.md`. Keep this session below 200 changed lines. Covers `specloop-documentation/mutation-evidence-guidance/parallel-and-timed-out-mutants-are-interpreted`, `specloop-documentation/mutation-evidence-guidance/exclusions-do-not-fabricate-evidence`, `specloop-documentation/mutation-evidence-guidance/guard-clauses-produce-candidates`.

## 5. Operations

- [ ] 5.1 Add concise operations guidance in `docs/specloop/README.md` and links or summaries in the specloop portions of `README.md`: job controls and supervisor boundaries, touchpoint commands and counting limits, `uv run pytest -n 4` and xdist-safe scenario reporting. Reuse existing root README recovery guidance and cite PRs #28, #53 and #54. Keep this session below 200 changed lines. Covers `specloop-documentation/operations-guidance/operator-distinguishes-pause-and-stop`, `specloop-documentation/operations-guidance/touchpoints-describe-observed-history`, `specloop-documentation/operations-guidance/parallel-tests-retain-scenario-evidence`.

## 6. Limitations

- [ ] 6.1 Reconcile `KNOWN-LIMITATIONS.md` with the preceding documentation and the design's fix/remaining-gap map. Remove only demonstrated fixes, split partially fixed bullets, retain unresolved gaps, and add the five required ledger limitations with issue #17 and #34 links. Do not infer a crash-boundary fix from consumed-ID tracking. Keep this session below 180 changed lines. Covers `specloop-documentation/current-limitations/fixed-limitations-leave-the-open-list`, `specloop-documentation/current-limitations/remaining-ledger-gaps-are-explicit`.

## 7. Documentation acceptance

- [ ] 7.1 Reconcile wording and local links across `docs/specloop/README.md`, `KNOWN-LIMITATIONS.md` and the scoped parts of `README.md`; check PR citations against local merge history and the design's source map, inspect the diff for scope and unchanged assertions, and run `uv run pytest`. Record the result and carry the design's human checklist into the implementation PR. Limit final prose corrections to 100 changed lines; do not edit tests, code, CI or unrelated docs to clear a failure. Covers `specloop-documentation/documentation-integrity/scope-and-regression-checks-remain-intact`, `specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable`.

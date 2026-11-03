## Context

`nightshift/specloop_intake.py::_readiness_evidence(row, *, git_runner=None) -> list[str]` validates run state, loads `run.manifest_path`, checks manifest change/branch identity, then reads committed tasks and `events.jsonl`. It never consults `Manifest.evidence`, whose existing default in `specloop/manifest.py` is false.

The shared `tests/merge_readiness_fixtures.py::LocalRun` manifest omits the gate setting. Existing evidence and assessment tests therefore exercise evidence rules using an implicitly disabled manifest. They need an explicit enabled fixture to remain meaningful after the fix. No `AGENTS.md` is present in this checkout or its ancestor directories; `README.md`, repository instructions in `CLAUDE.md`, and the current merge-readiness requirements inform this draft.

The `publication` fixture in `tests/test_specloop_base_publication.py` also writes a manifest without `gates.evidence`, while its poll helper asserts `merge:blocked`. That fixture must explicitly require evidence so those assertions continue to exercise the intended blocked publication flow.

## Goals / Non-Goals

Honor the admitted run's trusted evidence setting, preserve all enabled-evidence behavior and diagnostics, and keep the implementation within one session and about 100 changed lines. An empty evidence-reason list allows the existing head, CI, revision and human-check conditions to determine readiness.

Do not change evidence generation/scoring, manifest parsing or defaults, event formats, public interfaces, label/comment delivery, cache invalidation, or the `tests/` prefix rule. No new Markdown recognition or parsing is required.

## Decisions

1. Insert `if not manifest.evidence: return []` immediately after the existing manifest identity check in `_readiness_evidence`. Keep preceding state validation and its error handling in their existing order. Missing/unreadable/invalid state or manifest and identity mismatches still return the existing source-specific reasons; inability to load a manifest never implies disabled evidence. Consult the run's saved manifest, not the current enrollment template.
2. A validated disabled manifest returns before committed-task reads, event reads, diff proof, or section measurement checks. Missing or malformed events cannot create evidence reasons in this branch. Existing parser defaults still apply when the evidence setting is omitted. The enabled branch remains byte-for-byte unchanged apart from the inserted guard, including all current errors and the bounded `tests/` exception.
3. Add an explicit top-level `gates = {evidence = true}` entry to the manifest in `tests/merge_readiness_fixtures.py`; retain its existing events, boundaries and assertions. In `tests/test_specloop_base_publication.py::publication`, add `[gates]` with `evidence = true` to the written TOML manifest, before `[coverage.exempt]`. This is the only additional file permitted by the review revision; change no assertions or other fixture behavior. In `tests/test_merge_readiness_evidence.py`, add:
   - `test_readiness_evidence_disabled_manifest_reports_no_evidence_reasons`: use a real saved false manifest, clear saved completion and section boundaries, and remove section events. Assert exactly `[]`. Parameterize empty, absent and malformed event files; use a rejecting `git_runner` to prove no task/diff reads are attempted. Include the omitted-setting default as a compact parameter case.
   - `test_readiness_evidence_enabled_manifest_still_requires_events`: use an explicit true manifest, empty events and no bounded diff proof, and assert the exact existing missing-event reasons for both fixture sections. Parameterize missing state, manifest and event files to retain the existing source-specific failure diagnostics. Preserve the existing malformed-input, identity, timeout, completion, skip and test-only assertions.
   These tests use temporary local runs and Git and need neither a live forge nor the host application.
4. Keep the matching requirement delta in this change's `specs/merge-readiness/spec.md`, preserving existing scenarios and unrelated requirements. Canonical synchronization to `openspec/specs/merge-readiness/spec.md` belongs to the normal harness-controlled OpenSpec archive step, outside task 1.1. All `openspec/**` files remain frozen to the implementing agent; the task neither applies the delta nor invokes archive. Add one short entry under `## Open gaps` in `KNOWN-LIMITATIONS.md`: with evidence enabled, the missing-event fallback recognizes only repository-root `tests/` paths after excluding the harness-owned tasks file; layouts such as `packages/*/test/` still block without evidence. This explicitly requested documentation edit remains in scope alongside the publication fixture adjustment.
5. Verify with `uv run pytest` and `uv run python scripts/check_types.py`; existing assertions must pass and there must be zero new type errors without growing `mypy-baseline.txt`. Keep code, fixture, regressions and limitation note together in the single implementation task, targeting about 100 changed lines. Spec synchronization is lifecycle work, not an additional implementation task or a prerequisite for these local checks.

## Risks / Trade-offs

A guard before validation could turn unreadable or mismatched trusted inputs into success; the selected insertion point avoids that. Merely adding the guard without correcting the fixture would bypass existing negative tests. Returning no evidence reasons is not proof of measured evidence or permission to merge: the label remains advisory and other conditions still apply.

The language-specific fallback remains a known gap by design. Existing same-head assessments may remain cached; this change adds no automatic reassessment or historical migration. The next fresh publication exercises the new policy.

CI proves local behavior, not a deployed daemon's real Gitea label delivery. The following is a human check after deployment, not an implementation task:

- [ ] On the next published run in an evidence-disabled enrolled repository, confirm the saved manifest disables evidence, the PR head matches the run, CI is green, no build revision is pending and there are no human checks; observe `merge:ready` and record the PR URL/head and readiness comment. Do not treat the advisory label as authorization to merge.

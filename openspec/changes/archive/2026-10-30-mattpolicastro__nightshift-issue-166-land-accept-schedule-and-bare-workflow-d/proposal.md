## Why

Issue #159 (`e47fd27`) added a daily `schedule` to `.github/workflows/ci.yml`. The physical-line parser in `nightshift.land.ignored_paths` accepts only `push` and `pull_request`, so the unchanged repository-workflow regression now fails and docs-only landing exclusion cannot be established. The Gitea contract job will also need a bare `workflow_dispatch` in that trigger block.

## What Changes

- Accept one block-form `schedule` containing one or more quoted cron items, plus blank/comment lines, and one bare `workflow_dispatch` with no nested lines. Neither event contributes an ignore list; only `push` and `pull_request` are checked against changed paths.
- Preserve conservative refusal of unknown events, duplicate events, unsupported syntax and all existing ambiguous-trigger cases. Keep the repository-workflow test unedited and all existing assertions intact; add the three issue-named regression tests.
- Record a MODIFIED delta for only the `Base workflow exclusion proof` requirement in this change's `specs/reviewed-pr-landing/spec.md`, describing the accepted non-path events and retaining its other guarantees. Canonical spec synchronization belongs to the existing trusted finish/archive flow, not the implementing agent.
- Update the supported-trigger example and grammar in the docs-only CI exclusion section of `docs/landing.md` to name `schedule` and bare `workflow_dispatch`, explain that neither contributes an ignore list, and limit path matching to `push` and `pull_request`. This narrow scope expansion resolves the feasibility finding while preserving the existing `Docs only operator guidance` requirement unchanged.
- Implement in one session/task targeting about 150 changed lines across `nightshift/land.py` (`ignored_paths` only), `tests/test_land_ci_exclusion.py`, and that section of `docs/landing.md`. All `openspec/**` files remain read-only during implementation; the task reads this approved change but does not edit or apply its delta.

Out of scope: `.github/workflows/` edits, workflow selection (`ci_workflow`, including refusal when a second workflow exists), glob rules, every other part of `land.py`, unrelated guide edits, new dependencies, and changes to CI grace, deadlines, attestations, merge gates or other specifications. This draft writes only this change directory.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `reviewed-pr-landing`: extend `Base workflow exclusion proof` to recognize narrowly specified `schedule` and bare `workflow_dispatch` events without treating them as path-filtered events. No other requirement changes.

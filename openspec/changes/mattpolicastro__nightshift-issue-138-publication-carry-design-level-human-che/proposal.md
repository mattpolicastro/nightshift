## Why

Issue #80 exposed a publication gap: #113's design contained a human rollout checklist, but neither the publication receipt nor the `ready_to_publish` detail carried it. Merge readiness consequently saw no outstanding checks. Design-only checks must survive the same publication path as existing task/scenario checks.

blocked-by: #132

## What Changes

- Collect unchecked physical checklist lines from the change's committed `design.md` beneath `## Human checks` and `### Verification and human rollout checklist`.
- Append these items after existing checks, preserving source order and suppressing exact-text duplicates without interpreting Markdown semantics.
- Include the additional items in readiness detail and newly generated PR bodies; the existing daemon receipt and comment delivery then carry them unchanged.
- Add local regression coverage, including `test_design_human_checks_reach_publication` and `test_task_checks_keep_order_before_design_checks`, while preserving existing assertions.

Necessary scope correction: this checkout constructs readiness checks in `specloop/loop.py:finals`, whereas `specloop/cli.py:_pr_body` constructs a newly created PR's checks. The daemon snapshots readiness detail before invoking publish. A CLI-only change cannot repair the receipt; implementation therefore also needs a small collector and readiness integration in `specloop/loop.py`. Other implementation edits are limited to `specloop/cli.py`, new test files, and specs.

Out of scope: parsing implementation tasks as human checks, semantic Markdown parsing, changing merge-readiness or landing policy, ticking checks, migrating old receipts or already-published PRs, new CLI/schema fields, forge work, and unrelated documentation updates. No existing requirement is removed or relaxed; the delta adds to `issue-intake` publication behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `issue-intake`: Add design checklist collection and preservation through readiness, publication receipts, and human-check comments.

## Why

On 2026-10-24, `nightshift jobs` shows 112 attempts, mostly history from already delivered work. Every finished attempt says delivery may be pending, obscuring the jobs needing attention. Issue #9 compacts newly delivered attempts but provides no explicit pass over older history.

Dependency: blocked-by #7. This checkout contains #7's keyed revision provenance and #9's identity-checked compactor; preserve both contracts.

## What Changes

- Default inventory shows starting/running (including overdue), stopping, stopped and finished-undelivered work. Keep diagnostic rows visible; move ordinary failed attempts and delivered finished attempts to `--all`. Print a one-line count when history is hidden.
- Render finished jobs from their item's receipt: `delivered` when `delivered` is exactly true, otherwise `finished (delivery pending)`. Inventory remains offline and read-only.
- Add `nightshift jobs compact`: one explicit local pass applying the existing identity-checked compactor to historical attempts whose current delivered receipt still matches. Report compacted and skipped attempts with reasons; safely support repetition and interruption.
- Update affected passages of `README.md`, `KNOWN-LIMITATIONS.md` and `docs/specloop/README.md`, plus implementation tests. Preserve every existing assertion except exact inventory-output expectations invalidated by the new default, footer and delivery wording; migrate those with equivalent default/`--all` coverage.
- Explicitly out of scope: daemon delivery, scheduling, authorization and automatic cleanup eligibility; changing pause, stop or release; deleting attempt directories; receipt migration or inferred historical identities; automatic retention policies; worker/supervisor behavior; changes to touchpoint reporting.

## Capabilities

### Modified Capabilities

- `background-job-control`: modify Local job inventory and Job command syntax for the new view and command. Modify Delivered attempt compaction and Recoverable cleanup solely to allow an explicit operator pass outside daemon eligibility gates. Add receipt-aware display and historical-pass requirements. Automatic cleanup still requires an authorized eligible unpaused item visit.
- `specloop-documentation`: modify Active job status guidance to point full-history readers to `--all`, and Delivered retention guidance to describe the explicit historical pass alongside unchanged daemon recovery constraints. Add inventory-view guidance.

No capability is removed. Unmentioned requirements remain in force, including keyed revision provenance, pause/delivery isolation and touchpoint history preservation.

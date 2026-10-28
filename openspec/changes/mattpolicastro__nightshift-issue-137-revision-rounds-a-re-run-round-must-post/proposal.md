## Why

A revision can run again under the same `agent:revise` label event. Its new
result then shares the previous completion-comment marker. The reported
failure on #118 left new contradictions only in the local receipt; the current
checkout's `_comment` upserts the old comment instead of preserving a separate
result. Both behaviors give different result attempts the same delivery identity.

This change follows #80 and is blocked by #126's Forge seam, which is present
in this checkout. It fixes result delivery without changing revision triggers.

## What Changes

- Identify revision comments by logical round and result attempt. Preserve the
  existing marker as the implicit first attempt; give each replacement snapshot
  with the same round a fresh, durable attempt suffix. Delivery retries reuse
  the saved identity and reconcile the same comment.
- Keep legacy unqualified and round-qualified markers readable, with no receipt
  migration that changes a pending result's marker.
- Add `test_rerun_round_posts_new_result` and
  `test_old_round_markers_still_recognised`, plus focused retry coverage. Preserve
  existing assertions except the explicitly superseded one-comment/overwritten
  body expectations in two spec-revision tests; replace those with stronger
  assertions for two independent results and unchanged earlier comments.
- Retain the operator workaround in `KNOWN-LIMITATIONS.md`: resolve the refusal,
  remove `agent:revise`, clear `needs-human`, then have a trusted actor re-add
  `agent:revise` to create a fresh label event. This checkout needs that guidance
  added to its existing revision-recovery limitations.

Explicitly out of scope: removing `agent:revise` after `rejected` or
`needs_input`; new label writes or retry/reconciliation policy; compare-and-remove
emulation; round selection or build replay changes; Forge/backend changes;
CLI/result schemas; and changes to pause, stop, authorization or consumption.
GitHub and Gitea have no atomic compare-and-remove for this label operation, so
automatic rejection cleanup is deliberately excluded.

Implementation surfaces are `nightshift/specloop_intake.py`, focused tests and
`KNOWN-LIMITATIONS.md`. Implementing agents must not edit `openspec/**`; the
harness archives these specification deltas. This drafting operation writes only
this change directory.

## Capabilities

- `pr-revision`: add attempt-scoped result delivery and legacy compatibility;
  modify spec-stage comment behavior and extend retry reconciliation to the
  attempt-qualified format without changing execution or label semantics.
- `specloop-documentation`: document the remaining manual fresh-round workaround
  and the boundary between automated tests and a live re-revision check.

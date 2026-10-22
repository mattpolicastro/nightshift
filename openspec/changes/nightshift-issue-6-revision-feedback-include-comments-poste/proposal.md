## Why

Specloop revision intake treats feedback as new when its creation time is later than the PR branch tip's commit time. A revision can run for hours: comments posted during that session disappear behind the next commit's timestamp. Conversely, relabelling an admitted PR before publication feeds the previous round's comments into another round because the remote tip has not advanced.

Dependency: **blocked-by: #5**. Its label-actor authorization is already present in this checkout and must remain ahead of revision processing.

## What Changes

- Select trusted, actionable PR conversation comments, review bodies, and inline comments by stable source-qualified GitHub IDs, excluding IDs already consumed for that repository and PR.
- Save each round's exact IDs and rendered feedback before dispatch. Consume that snapshot after a successful spec push or a validated build revision result, independently of later notification delivery and build publication.
- Preserve consumption across label applications, daemon restarts, and the transition from spec review to an admitted build. Comments arriving during a round remain eligible for the next authorized request.
- Define same-ID edits, read failures, and safe handling of legacy receipts without ID history. An edit to consumed feedback does not request another revision; a new comment does.
- Add offline regressions, including `test_comments_during_revision_feed_the_next_round` and `test_relabel_does_not_requeue_consumed_feedback`, and keep `uv run pytest` green.

Out of scope: worker-engine timestamp selection; automatic revisions without a label; reprocessing edited consumed comments; changing specloop's CLI or task-generation protocol; solving a CLI crash after committing but before writing a durable result; publication/base synchronization; UI, job cancellation, and label-authorization policy changes.

## Capabilities

- **Modified: `pr-revision`** — replace timestamp eligibility with durable per-PR consumption, add snapshot/recovery rules, and explicitly modify the existing stage, empty-feedback, and actor-gate requirements where they describe timestamp filtering. Preserve their other behavior.
- No new capability or changes to `issue-intake`, `specloop-interface`, `paused-job-delivery`, or `background-job-control` requirements are needed. Their authorization, durable payload, CLI, pause, and cancellation contracts remain in force.

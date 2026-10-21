## Why

Worker issues already enforce explicit `blocked-by:` dependencies, but the
specloop front door starts drafting without checking them. Armed issues that
touch the same files can therefore draft and build concurrently, producing
conflicting changes before their dependencies land.

## What Changes

- In `nightshift/specloop_intake.py`, check the current issue body's blockers
  immediately before `propose_pending` starts a new drafting round, using
  `queue.blockers` and `queue.open_blockers` with the injected GitHub runner.
- Skip a blocked issue for this poll, logging `#N is blocked by #M — not
  drafting`. Preserve labels, comments and receipts. Recheck on subsequent
  polls; unreadable blockers remain blocking under the existing helper policy.
- Keep running jobs and delivery of completed drafts independent of this gate.
  Apply the gate to every new round, including a re-armed issue.
- Add regression tests in `tests/test_specloop_intake.py`, leaving all existing
  tests unchanged. Acceptance requires a green `uv run pytest`.

Explicitly out of scope: the worker path, changes to `nightshift/queue.py`,
dependency syntax or helper semantics, and the admit, publish and revise stages.
No new scheduling service, receipt fields, labels or configuration are needed.

## Capabilities

- `issue-intake`: add requirements for dependency checks at the start of a
  drafting round and preservation of existing draft lifecycle handling. This
  qualifies when the existing drafting-from-an-issue behavior may start; it
  does not introduce a separate capability.

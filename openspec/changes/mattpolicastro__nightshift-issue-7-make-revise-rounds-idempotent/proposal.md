## Why

A build revision can commit its review tasks and die before its background job writes `result.json`. Retrying currently selects another snapshot and appends the same feedback again. Issue #9 (PR #98) is now on this base; its deadline, crash collection and compaction interfaces are available.

Use the saved revision round's identity as the idempotency key. Job IDs identify execution attempts and change on retry; they must not identify the logical revision. This follows the clarified issue #7 intent rather than the original request-ID suggestion.

## What Changes

- Add optional `specloop revise --round-id` and durable, run-local evidence that binds a key to normalized feedback and its original commit. A matching retry returns that commit without adding tasks again.
- Recover interruptions around branch advancement and run-state saving, preserving later pauses, aborts and completion. Reject conflicting feedback and unverifiable evidence without appending another round.
- Give each newly selected build snapshot a UUID `round_id`, saved before dispatch. Preserve the key and saved feedback across ordinary crash retries; use a fresh identity after a resolved refusal or explicit stopped-job release.
- Record attempt provenance separately from the key, so intake can distinguish crash detachment from stopped-job release without changing either operation. Extend #9's request schema and compactor to preserve round identity.
- Report the run's observed state in keyed CLI results and completion comments. Keep unkeyed command output compatible, including its success wording containing `running`.
- Add focused tests in new files, with narrowly authorized migrations of both exact-request assertions in `tests/test_specloop_jobs.py` and the refusal-recovery comment assertions in `tests/test_specloop_build_revision.py`.
- Update operator and CLI documentation to distinguish protected keyed retries from legacy/unkeyed calls, explicit new rounds, and unproven power-loss durability.

Out of scope: implementing or reverting #9; retaining expired crashed jobs or changing deadline/escalation policy; changing stop/release behavior; making spec-stage `propose` idempotent; worker revision changes; general job transactions; changing the production client executable lookup; rollback of already committed effects; automatic deduplication of pre-upgrade unkeyed revisions; and claiming CI proves machine-crash recovery.

Runtime scope is `specloop/revise.py`, a small `specloop/revision_store.py` helper, `specloop/cli.py`, `nightshift/specloop_job.py`, and `nightshift/specloop_intake.py`. Tests and the four named operator documents in the design are included. This draft itself changes only this OpenSpec change directory.

## Capabilities

- **specloop-interface** (modified): optional keyed revisions, durable replay, conflict handling, accurate keyed JSON/human output; narrow the existing Revision round guarantee to distinguish new append from replay.
- **background-job-control** (modified): round identity and provenance with the landed timing schema and compaction, preserving crash detachment and all cancellation rules.
- **pr-revision** (extended): stable build snapshot identity across crashed attempts, fresh identity for replacement snapshots, and accurate, per-round completion delivery.
- **specloop-documentation** (modified): keyed recovery guidance and qualified crash-boundary limitations.

No `issue-intake` delta is required: an idle job lacking a result still follows the existing error, job-reference removal and repository-health path, even after its deadline. Intake's subsequent eligible dispatch reuses the logical snapshot, not the detached attempt.

## Why

Published front-door PRs pause at `human_checks` without a concise indication of whether the recorded automated evidence and remaining checklist permit review for merge. Add an advisory readiness label and an explanation, while preserving human or explicitly delegated landing.

Dependency: #47 (now represented by the archived proposal-ref change). Preserve its exact ref validation and recorded run/PR identity.

On 2026-10-22, #50 and #21 each passed CI against an older merge ref; after both merged, `main` stayed red until #63. The redraft answers explicitly supersede the original proposal to inspect CI merge parents: readiness uses ordinary PR checks, and `nightshift.land` retains its current-base checks at merge time. The label alone does not authorize merging.

## What Changes

- Assess daemon-published runs at `human_checks` for green hosted checks, measured section evidence without timeouts (with the specified test-only/no-mutable-lines exceptions), no pending build revision, an empty or explicitly post-deploy human checklist, and a PR head matching the run.
- Reconcile mutually exclusive `merge:ready` and `merge:blocked` labels and one separate readiness comment explaining the assessed head and every failed condition.
- Remove stale readiness on an observed head change. Cache a delivered assessment per head: one PR read per subsequent poll, with one checks read only when an assessment is needed.
- Read local `events.jsonl` and saved run state; do not inspect per-attempt receipts.
- Keep executable edits in `nightshift/specloop_intake.py` and put new tests in new files. Permit the three explicitly authorized base-publication assertion migrations and necessary fixture/fake setup changes, preserving exact publication guarantees and explaining each in the implementation PR.

Out of scope: merging, invoking or changing `nightshift.land`, finishing runs, updating branches, CI base/checkout/merge-ref provenance, Actions logs, evidence production or scoring changes, new configuration, worker behavior, and changes to other in-flight work. Labels are advisory snapshots, not continuously refreshed merge gates.

## Capabilities

### New Capabilities

- `merge-readiness`: head-bound advisory readiness assessment and recoverable label/comment delivery for published front-door PRs.

### Modified Capabilities

None. Existing `issue-intake`, `publish-base-sync`, `pr-revision`, `paused-job-delivery`, `parallel-evidence-mutants`, and `reviewed-pr-landing` requirements remain intact. Readiness runs after publication delivery and has independent state and comments; publication itself still has exactly its existing effects.

## Why

A run verifies against the base it inherited at admission, while hosted CI tests its branch merged with the current PR base. On 2026-10-21, #8's PR (#28) passed local verification and then failed eight hosted tests after #12 added files to a directory its tests counted. Base drift also caused three spec-stage test-file collisions that day.

Merge the current base before declaring readiness and recheck it on publication attempts. Preserve evidence attribution: an upstream merge must not make the run's mutation gate measure upstream code.

Dependency: blocked-by #14; build on its landed CLI and runtime scenario-reference behavior.

## What Changes

- Fetch the manifest's `pr_base` from the source clone's `origin` into the run repository before `ready_to_publish` and on every eligible `publish` attempt, including retries.
- If the fetched base is not already incorporated, create a harness-authored merge commit preserving the run head as first parent, then run the trusted prepare/verify pipeline on the merged head.
- Pause as `base_conflict` with conflicting paths without moving the run branch, or as `base_drift` with retained verification receipts after a clean merge fails checks. Fetch failures also prevent readiness/publication and retain a diagnostic.
- Persist enough integration and verification state to recover safely after interruption. An unchanged, already checked base adds one fetch and no merge or additional verification.
- Exclude base-only lines from section mutation evidence while retaining the section's own changes and existing coverage, selection, scoring and timeout rules.
- Add regressions in new test files. Existing tests may change **fixture setup only** to supply a fetchable local base or a fetch-boundary stub where `_remote` is faked. Preserve every assertion and explain each changed fixture in the implementation PR.

Implementation scope is `specloop/loop.py`, `specloop/cli.py`, additive fields/loading in `specloop/state.py`, and tests. Explicitly out of scope: daemon/intake changes, spec-stage synchronization, verify sandbox changes, review rules, mutation-engine/filter/manifest changes, `finish` synchronization, automatic conflict resolution, automatic merging of PRs, and UI changes. Human repair and existing explicit resume/revision commands provide recovery.

## Capabilities

### New Capabilities

- `publish-base-sync`: current-base integration and trusted verification before readiness/publication, retry recovery, and exclusion of integrated base lines from evidence.

### Modified Capabilities

None. This adds a prerequisite to successful publication and corrects evidence attribution without replacing the existing `specloop-interface`, `run-actions`, `verify-preparation`, `parallel-evidence-mutants`, `pr-revision`, or `issue-intake` requirements. Existing repository-target refusals, adopted PR reuse, JSON contracts, preparation isolation, section scope and review rules remain in force.

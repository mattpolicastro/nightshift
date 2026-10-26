## Why

Runs waiting at `human_checks` already appear in Factory, often on a phone. After checking the PR, the operator should be able to start the reviewed landing workflow there and keep its progress and refusal reason in view instead of opening a terminal. This depends on #78's landing implementation and #40's unified Nightshift CLI, both present in this checkout.

## What Changes

- Add a separate **Land** control only for a run paused at `human_checks` with a verifiable published PR, head and pause timestamp. Preserve the existing status verbs, controls and accessibility contracts.
- Confirm completion of the PR's human checks against an immutable `(change, PR, pause_id, head, since)` offer. Revalidate at admission and worker startup; invoke existing `land --checks-done` through a runner bound to the confirmed initial head.
- Add a narrow CLI gateway and detached landing job adapter with durable request identity, retained outcomes, local inventory, verified stop and explicit stopped-hold release. Never schedule landing through daemon intake or the supervisor.
- Display progress and retained per-attempt results in the originating row, or above the list when the row is folded or absent. Keep complete evidence locally while limiting history responses to the newest 20 attempts by default, plus a total and a separate current-attempt lookup.
- Extend plugin TypeScript tests and root Python tests without weakening assertions. Migrate both rendered harness RPC routers explicitly. Use existing offline prepared plugin verification; add no pytest-to-Node bridge.
- Update the plugin README, main README, engine guide and `KNOWN-LIMITATIONS.md` for the separate action and landing-specific job behavior.

Implementation scope is `plugins/nightshift/`, `nightshift/cli.py`, new landing gateway/worker helpers under `nightshift/`, narrow job-kind adapters in `nightshift/specloop_job.py`, root `tests/`, and the four named operator documents. The Python extension is necessary because the existing job wrapper only executes intake commands and cannot safely own a detached landing invocation without an adapter.

Out of scope: changes to `nightshift/land.py` or its checklist, attestation, finish, CI, branch-update and merge logic; autonomous merging; new `act` verbs; changed build status schemas; deployment; frozen CI, packaging, service wrappers or trusted manifests; dependency installation during verification. Matt checks desktop and phone behavior after deploy.

## Capabilities

### New Capabilities

- `factory-landing-jobs`: confirmed landing admission, durable launch and guarded invocation, retry identity, and bounded read-only history.

### Modified Capabilities

- `paseo-runs-page`: explicit separate-Land exception to status-only buttons; confirmation, RPC boundaries, refreshes and visible retained outcomes.
- `background-job-control`: landing inventory and timing, job-specific ownership verification, per-attempt release receipts and retained evidence. Existing intake behavior remains intact.
- `specloop-documentation`: concise separate requirements for Factory landing, recovery, limitations and structural documentation checks.

`reviewed-pr-landing`, `run-actions`, `specloop-interface`, `factory-finished-runs`, `nightshift-cli` and `verify-preparation` remain unchanged. Their existing gates and contracts apply; the new CLI gateway is an operator-requested adapter, not a daemon or supervisor landing path.

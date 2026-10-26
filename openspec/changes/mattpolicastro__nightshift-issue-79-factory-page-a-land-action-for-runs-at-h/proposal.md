## Why

Runs waiting at `human_checks` already appear in Factory, often on a phone. After checking the PR, the operator should be able to start the reviewed landing workflow there and keep its progress and refusal reason in view instead of opening a terminal. This depends on #78's landing implementation and #40's unified Nightshift CLI, both present in this checkout.

## What Changes

- Add a separate **Land** control only for a run paused at `human_checks` with a verifiable published PR, head and pause timestamp. Preserve the existing status verbs, controls and accessibility contracts.
- Confirm completion of the PR's human checks against an immutable `(change, PR, pause_id, head, since)` offer. Revalidate at admission and worker startup; invoke existing `land --checks-done` through a runner bound to the confirmed initial head.
- Add a standalone operator gateway and detached plugin-owned landing worker with durable request identity, retained outcomes, local inventory, verified stop and explicit stopped-hold release. Never schedule landing through daemon intake or the supervisor.
- Display progress and retained per-attempt results in the originating row, or above the list when the row is folded or absent. Keep complete evidence locally while limiting history responses to the newest 20 attempts by default, plus a total and a separate current-attempt lookup.
- Extend plugin TypeScript tests and root Python tests without weakening assertions. Migrate both rendered harness RPC routers explicitly. Use existing offline prepared plugin verification; add no pytest-to-Node bridge.
- Update the plugin README, main README, engine guide and `KNOWN-LIMITATIONS.md` for the separate action and landing-specific job behavior.

Implementation scope is `plugins/nightshift/`, a standalone `nightshift/factory_land.py` gateway, a control-only `nightshift/factory_land_records.py` helper, narrow job-kind adapters in `nightshift/specloop_job.py`, root `tests/`, and the four named operator documents. The worker entry script lives at `plugins/nightshift/server/factory_land_job.py`. Shared job code reads and controls landing evidence but never imports or launches the gateway or worker. No registration is added to `nightshift/cli.py`: it is already reachable from daemon and supervisor paths, whose existing no-landing dependency assertion must remain intact.

Out of scope: changes to `nightshift/land.py` or its checklist, attestation, finish, CI, branch-update and merge logic; autonomous merging; canonical CLI registration or forwarding for landing; new `act` verbs; changed build status schemas; deployment; frozen CI, packaging, service wrappers or trusted manifests; dependency installation during verification. Matt checks desktop and phone behavior after deploy.

## Capabilities

### New Capabilities

- `factory-landing-jobs`: confirmed landing admission, durable launch and guarded invocation, retry identity, and bounded read-only history.

### Modified Capabilities

- `paseo-runs-page`: explicit separate-Land exception to status-only buttons; confirmation, RPC boundaries, refreshes and visible retained outcomes.
- `background-job-control`: landing inventory and timing, job-specific ownership verification, per-attempt release receipts and retained evidence. Existing intake behavior remains intact.
- `specloop-documentation`: concise separate requirements for Factory landing, recovery, limitations and structural documentation checks.

`reviewed-pr-landing`, `run-actions`, `specloop-interface`, `factory-finished-runs`, `nightshift-cli`, `host-paths` and `verify-preparation` remain unchanged. Their existing gates and contracts apply. Factory invokes the standalone gateway directly; neither the canonical CLI nor any daemon/supervisor dependency gains a path to landing execution. No exception or weakening of the existing landing dependency checker is authorized.

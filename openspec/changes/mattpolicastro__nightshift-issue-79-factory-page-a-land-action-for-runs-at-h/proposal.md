## Why

Factory is where an operator sees a run waiting at `human_checks`, often from a phone. Confirmed landing there saves a terminal session and keeps progress and refusals beside the run. Dependencies #78 (reviewed landing) and #40 (unified CLI) are present in this checkout.

## What Changes

- Add separate Land only at a verifiable `human_checks` pause, preserving existing status verbs and accessibility. Bind confirmation to change, PR, `pause_id`, published head and pause `since`; revalidate admission and worker startup.
- Invoke unchanged `nightshift.land` with `--checks-done` through its injectable runner, bound to the confirmed identity. Landing remains the sole merge path and retains its head-conditioned merge and existing gates.
- Add private plugin transport and detached worker in `nightshift/factory_land.py`. Keep a log and small JSON records per attempt; hold one per-PR flock for the worker's lifetime. Stop verifies worker PID/start time before SIGTERM to its process group.
- Read newest 20 attempts plus total and separate current progress; retain results and refusal reasons in the originating row or above the list when hidden.
- Preserve existing assertions and migrate both rendered RPC harnesses. Python tests belong under root `tests/`; plugin TypeScript tests under `plugins/nightshift/tests/`.
- Update the plugin README, engine guide, main README, `KNOWN-LIMITATIONS.md` and `docs/landing.md`, including duplicate-marker refusal/recovery and the worker-death limitation. Test structure rather than prose wording.

Implementation scope is `plugins/nightshift/`, new `nightshift/factory_land.py`, root `tests/` and those five documents. Six tasks group implementation and its tests by concern.

Out of scope: changes to `nightshift/land.py`; duplicated checklist, attestation, finish, CI, update or merge gates; autonomous landing; canonical CLI registration or new act verbs; background-job-control inventory, compaction, ownership or escalation adapters; request-UUID replay; hold receipts, release or an uncertain state; inherited lock descriptors; automatic retries/pruning; changes to CI, trusted manifests or read-only verification source. Matt reviews desktop and phone behavior after deploy.

## Capabilities

### New Capabilities

- `factory-landing-jobs`: isolated operator-triggered attempts, bound identity, lifetime lock, verified Stop and bounded status. This names detached attempts, not a background-job-control job kind.

### Modified Capabilities

- `paseo-runs-page`: separate-Land exception to status-only verbs, confirmation, independent RPC state and retained notices.
- `specloop-documentation`: focused workflow, recovery and evidence guidance with structural checks.

Remove the previous draft's background-job-control delta: landing no longer participates in that system. Existing `background-job-control`, `reviewed-pr-landing`, `run-actions`, `specloop-interface`, `factory-finished-runs`, `nightshift-cli`, `host-paths` and `verify-preparation` contracts remain unchanged.

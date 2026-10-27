## Why

Factory is where an operator sees a run waiting at `human_checks`, often from a phone. Confirmed landing there saves a terminal session and keeps progress and refusals beside the run. Dependencies #78 (reviewed landing) and #40 (unified CLI) are present in this checkout.

## What Changes

- Add separate Land only at a verifiable `human_checks` pause, preserving existing status verbs and accessibility. Bind confirmation to change, PR, `pause_id`, published head and pause `since`; revalidate admission and worker startup.
- Invoke `nightshift.land` with `--checks-done` through its injectable runner, bound to the confirmed identity. Preserve its attestation implementation, including within-invocation reconciliation and duplicate-marker refusal/recovery. Explicitly narrow the owning Stable check attestation requirement to one invocation: cross-invocation uniqueness is not guaranteed after a late POST. Add no persisted create intent, unresolved-key evidence or cross-attempt POST guard.
- Add optional `finish --expect-head SHA` in `specloop/cli.py`: compare the run branch head after locked reload and before archiving or other finish side effects. Refuse a mismatch or unreadable head without archiving. The sole change to `nightshift/land.py` passes its validated PR head on every finish call, protecting Factory and terminal invocations while preserving validated successor-head handling. This intentionally changes partial-finish recovery: if an archive commit exists locally but its push failed, inspect and complete direct Finish without the optional guard before rerunning terminal landing.
- Add private plugin transport and detached worker in `nightshift/factory_land.py`. Keep logs and small records per attempt; hold a per-PR flock for the worker's lifetime. Before invoking land, prove earlier recorded execution groups drained with `os.killpg(pgid, 0)`; only `ESRCH` proves absence. Stop verifies worker PID/start time before SIGTERM to its group.
- Read newest 20 attempts plus total and separate current progress; retain results and refusal reasons in the originating row or above the list when hidden.
- Preserve existing assertions and migrate both rendered RPC harnesses. Python tests belong under root `tests/`; plugin TypeScript tests under `plugins/nightshift/tests/`.
- Update the plugin README, engine guide, main README, `KNOWN-LIMITATIONS.md` and `docs/landing.md`, including duplicate-comment recovery, failed-push finish recovery and worker-death limitations. Describe the private transport in prose without new command examples or documentation-parser routing.

Implementation scope is `plugins/nightshift/`, new `nightshift/factory_land.py`, the narrow finish changes in `specloop/cli.py` and `nightshift/land.py`, root `tests/` and those five documents. Seven sessions use the approved split of the previous worker task: the locked finish handoff and its tests land before process-control integration. There is no attestation-reconciliation task.

The reviewed private-worker design supersedes the original proposal to integrate with `nightshift jobs`: landing uses separate records, a worker-held lock and verified Stop, without intake holds, release or escalation adapters. Existing job-control behavior remains intact.

Out of scope: other changes to land's gates or attestation logic; cross-attempt create guards; duplicated checklist parsing, attestation selection, CI, update or merge logic; autonomous landing; canonical CLI registration of landing or new act verbs; request-UUID replay; inherited lock descriptors; automatic retries/pruning; changes to CI, trusted manifests or read-only verification source. Matt reviews desktop and phone behavior after deploy.

## Capabilities

### New Capabilities

- `factory-landing-jobs`: isolated operator-triggered attempts, bound identity, lifetime lock, verified Stop and bounded status. This names detached attempts, not a background-job-control job kind.

### Modified Capabilities

- `paseo-runs-page`: separate-Land exception to status-only verbs, confirmation, independent RPC state and retained notices.
- `run-actions`: extend finish preconditions with an optional head guard under the existing run lock, retaining repository and unfinished-task protections.
- `reviewed-pr-landing`: pass the validated pre-finish PR head to finish, explicitly migrate failed-push recovery and scope attestation reconciliation to one invocation with the accepted late-POST duplicate exception; retain other landing gates and successor-head validation.
- `specloop-documentation`: focused workflow, duplicate recovery and evidence guidance with structural checks.

Existing `background-job-control`, `specloop-interface`, `factory-finished-runs`, `nightshift-cli`, `host-paths` and `verify-preparation` contracts remain in force. The new optional finish argument uses the existing shared build parser; no command dispatch or JSON schema migration is required.

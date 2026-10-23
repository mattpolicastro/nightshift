## Why

After each merge, the operator manually pulls the live checkout, runs `uv
sync`, redeploys changed plugin files, and restarts services with changed code.
Missed steps are silent: on 2026-10-23 verification failed with `No module
named mypy` after PR #69 because sandboxed verification uses the live `.venv`.

## What Changes

- Add the local command `uv run python -m nightshift.deploy`. Default check
  reports checkout, environment, plugin, and process-age drift. Document the
  `uv run --no-sync` form so the launcher does not repair dependencies before
  the command observes them.
- Add conditional `--apply`, in order: fast-forward pull, frozen sync, staged
  plugin swap, reload, daemon restart, supervisor restart. Each step rechecks
  real state before acting; a successful second run says `Nothing to do.`.
- Keep at most three plugin rollback copies. One presence-only pending-reload
  marker is the only persisted control state. Write it before swapping, retry
  reload while it exists, and remove it only after successful reload. Failures
  print an exact manual restore command; there is no automatic rollback.
- Guard restarts on idle specloop background jobs / supervisor activity unless
  `--force`. Confirm a new PID within 30 seconds; if absent, issue one
  non-killing kickstart and wait another 30 seconds. Report remaining failures
  with a manual command. There are no restart records or ownership probes.
- Add `tests/test_deploy.py`, including the eight requested named tests and
  interrupted/failed reload reruns, and `docs/deploy.md` with operator guidance.
  Plan six implementation tasks, about 1,000 changed lines in total.

Implementation creates only `nightshift/deploy.py`, `tests/test_deploy.py`, and
`docs/deploy.md`. Every existing file is out of scope, including `launchd/`,
`config.toml`, CLI modules, packaging, existing tests, and existing docs.
Also out of scope: transaction journals, interrupted-run reconciliation,
automatic rollback, service installation, worker-engine task guards, automatic
deployment, UI changes, and CLI consolidation (issue #40).

## Capabilities

### New Capabilities

- `deployment-drift`: check and repair local post-merge drift through an
  ordered operator command with safe reruns and guarded restarts.

### Modified Capabilities

None. Existing `background-job-control`, `paused-job-delivery`,
`specloop-interface`, `verify-preparation`, `paseo-runs-page`, and `run-actions`
requirements remain unchanged. Deployment consumes their existing interfaces;
it does not change scheduling, job delivery, verification, status, or the UI.

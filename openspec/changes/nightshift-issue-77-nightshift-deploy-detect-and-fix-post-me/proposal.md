## Why

After merging, the operator manually pulls the live checkout, synchronizes its
Python environment, redeploys the Paseo plugin, and restarts affected services.
These steps can be missed without any drift report. On 2026-10-23, verification
failed with `No module named mypy` because the live `.venv` had not been synced
after PR #69. Sandboxed verification depends on that environment.

## What Changes

- Add `nightshift/deploy.py`, invoked as `uv run python -m nightshift.deploy`.
  Default check reports checkout, environment, plugin, and process-age drift.
  Document `uv run --no-sync python -m nightshift.deploy` for checks that must
  observe the environment before uv implicitly repairs it.
- Add ordered, conditional `--apply`: fast-forward pull, frozen dependency
  sync, staged plugin replacement with up to three rollback copies, plugin
  reload, and idle-only daemon/supervisor restarts. `--force` overrides busy
  restart guards, not checkout refusals. Repeated successful application is a
  no-op when nothing changed.
- Add isolated command-runner tests in `tests/test_deploy.py` and an operator
  guide in `docs/deploy.md`, including the post-merge host checklist.
- Implementation may create only those three files. Every existing file is
  out of scope, including `launchd/`, `config.toml`, `pyproject.toml`, existing
  tests, plugin sources, CLI modules, and repository documentation. No CLI
  consolidation (issue #40), installation of services, automatic scheduling,
  manifest changes, verification sandbox changes, or Factory UI changes.

## Capabilities

### New Capabilities

- `deployment-drift`: detect post-merge deployment drift and repair it through
  an ordered, repeatable command with rollback copies and restart guards.

### Modified Capabilities

None. The command consumes existing job inventory and version-1 specloop
status without changing them. Requirements in `background-job-control`,
`paused-job-delivery`, `specloop-interface`, `verify-preparation`,
`paseo-runs-page`, and `run-actions` remain in force.

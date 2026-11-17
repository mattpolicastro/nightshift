## Purpose

Deploy the nightshift Paseo plugin through Paseo's Git-sourced install, pinned
by commit. Drift is the revision Paseo reports compared with the last commit
touching `plugins/nightshift`. Repair is `paseo plugin update --ref`, and
rollback is the same command with the previous revision.

## ADDED Requirements

### Requirement: Git-pinned plugin drift

With a configured plugin ID, the command SHALL compare the
`installation.currentRevision` that `paseo plugin ls <id> --json` reports on
stdout with the target, `git log -1 --format=%H HEAD -- plugins/nightshift`
after the checkout step. The plugin counts only as one Git install of
`plugins/nightshift` with a hex revision. Any difference SHALL be drift. Anything
it cannot confirm SHALL be inconclusive and fail the invocation.

#### Scenario: Revision differs from plugin commit
- **WHEN** check finds a Git install of `plugins/nightshift` whose reported revision differs from the target
- **THEN** it prints `plugin: installed revision <current> differs from <target>` and exits 1
- **THEN** its only `paseo` command is `paseo plugin ls <id> --json`

#### Scenario: Revision matches plugin commit
- **WHEN** the reported revision equals the target and every other component is current
- **THEN** check prints `plugin: current (<target>)` and exits 0

#### Scenario: Plugin cannot be confirmed as a Git install
- **WHEN** `paseo` is missing, times out or exits nonzero, its stdout is not a JSON array, it lists zero or several items with the ID, the item's `installation.identity.kind` is not `git` or its `pluginPath` is not `plugins/nightshift`, its `currentRevision` is missing or not `^[0-9a-f]{40,64}$`, or the target is empty or malformed
- **THEN** check reports `plugin failed:` with the reason, keeps reporting the other components, and exits 1 without `paseo plugin update`

### Requirement: Pinned plugin update

For plugin drift only, apply SHALL first print the previous revision and its
rollback command, then run `paseo plugin update <id> --ref <target> --json`, and
then confirm through `paseo plugin ls` that the revision equals the target. An
unconfirmed update SHALL be a hard failure, with no automatic rollback. The
command SHALL write no plugin files or plugin deployment state.

#### Scenario: Apply updates plugin to the head commit
- **WHEN** apply observes plugin drift and the update is confirmed
- **THEN** it runs exactly `paseo plugin update <id> --ref <target> --json` once, confirms with `paseo plugin ls <id> --json`, prints `plugin: updated to <target>`, and continues to the service steps
- **THEN** after a pull, the target is the plugin commit at the post-pull HEAD

#### Scenario: Previous revision printed before update
- **WHEN** apply is about to update the plugin
- **THEN** `plugin: previous revision <previous>` and `plugin rollback: paseo plugin update <id> --ref <previous>` are printed before the update command runs

#### Scenario: Failed or unconfirmed update stops apply
- **WHEN** the update exits nonzero, raises, or the confirming query fails or reports a revision other than the target
- **THEN** apply prints `plugin failed:`, exits 1, sends no service restart, and runs no further `paseo` command
- **THEN** a later apply observes again and retries the update while drift remains

#### Scenario: Current or inconclusive plugin is not updated
- **WHEN** apply finds the plugin current, or cannot confirm it as a Git install
- **THEN** it runs no `paseo plugin update`; an inconclusive plugin exits 1 before the service steps

## MODIFIED Requirements

### Requirement: Standalone deployment command

`uv run python -m nightshift.deploy` SHALL default to checking. It SHALL accept
these options, and no `--plugin-dir` or `--reload-cmd`:
- `--apply`
- `--force`
- `--base-branch` (default `main`)
- `--plugin-id`
- `--daemon-label`
- `--supervisor-label`

It SHALL route every external command, including every `paseo` command, through
an injectable runner.

Check SHALL report every independently observable drift. It SHALL perform no
repairs and no control-state writes, and SHALL run no `paseo plugin update`. It
SHALL exit 1 for drift or inconclusive observations, otherwise 0. Fetch MAY
update Git metadata. Invalid arguments SHALL exit 2.

#### Scenario: Check reports independent results
- **WHEN** check observes multiple drifts or a failed probe alongside other observable components
- **THEN** it reports each result and exits 1 without pull, sync, plugin update, or restart
- **THEN** a check with all configured components current exits 0

#### Scenario: Flags and plugin location
- **WHEN** options are parsed with `--plugin-id`, only `NIGHTSHIFT_PLUGIN_ID`, or neither
- **THEN** the flag takes precedence, otherwise the environment supplies the ID
- **THEN** with neither set and no `NIGHTSHIFT_PLUGIN_DIR`, the plugin step prints `plugin check skipped: no plugin ID configured`, runs no `paseo` command, and does not by itself fail
- **THEN** force without apply, an explicitly empty plugin ID or one not matching `^[a-z][a-z0-9-]*$`, the retired `--plugin-dir` and `--reload-cmd` options, and malformed arguments exit 2 before any command runs

#### Scenario: Retired plugin directory setting
- **WHEN** no plugin ID is configured but `NIGHTSHIFT_PLUGIN_DIR` is non-empty
- **THEN** the command prints `plugin inconclusive: NIGHTSHIFT_PLUGIN_DIR is no longer used; install the plugin from Git and set NIGHTSHIFT_PLUGIN_ID`, runs no `paseo` command, and exits 1
- **THEN** apply stops before the service steps

### Requirement: Ordered repeatable application

Apply SHALL recheck each component before acting, in this order:
1. checkout
2. environment
3. plugin update
4. daemon
5. supervisor

Hard failures SHALL stop later repair effects. Activity deferrals SHALL stay
pending while independent service repairs go ahead.

Re-running SHALL observe real state and retry only the remaining work, with no
transaction, restart or plugin deployment records. With nothing to do, apply
SHALL print `Nothing to do.` and exit 0.

#### Scenario: Failure and rerun preserve completed work
- **WHEN** an ordered repair fails and apply is run again
- **THEN** the failed invocation reports completed steps and stops later effects, apart from its specified bounded restart fallback
- **THEN** the rerun freshly checks each component, skips completed current work, and retries only remaining drift

#### Scenario: Second application is a no op
- **WHEN** a successful apply is followed by another with no drift
- **THEN** the second run prints `Nothing to do.`, exits 0, and performs no pull, sync, plugin update, restart, or control-state write, while still fetching and probing, including `paseo plugin ls`

## REMOVED Requirements

### Requirement: Committed plugin drift

**Reason:** The deployed plugin is now the commit Paseo reports for its Git
install. Comparing files in a directory Paseo might not even load is what let
the 2026-11-15 deploy update the wrong copy.

**Migration:** Replaced by Git-pinned plugin drift. The plugin-tree tests
(`test_check_reports_plugin_drift` and the `plugin_recording` fixture) are
deleted.

### Requirement: Staged plugin replacement

**Reason:** Paseo stages, verifies and places the pinned checkout itself. A
rollback is `paseo plugin update --ref <previous>`, so deploy keeps no staging
directory or rollback copies.

**Migration:** Replaced by Pinned plugin update, which prints the rollback
command before updating. `test_apply_swaps_plugin_keeping_rollback` and
`test_rename_gap_stages_current_head_afresh` are deleted. Existing
`.nightshift-deploy` directories are left for a human to remove.

### Requirement: Pending plugin reload

**Reason:** `paseo plugin update` restarts the plugin itself. Deploy keeps no
`pending-reload` marker and has no `--reload-cmd`.

**Migration:** Confirmation that the update took effect is the second
`paseo plugin ls` query. `test_pending_reload_rerun` and
`test_reload_override_and_skip` are deleted. The reload-skip case of
`test_pending_work_allows_independent_service_repairs` is dropped, and its
unconfigured-plugin case kept.

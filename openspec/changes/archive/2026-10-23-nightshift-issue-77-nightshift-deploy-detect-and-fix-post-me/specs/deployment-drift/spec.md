## Purpose

Detect and repair local post-merge checkout, environment, plugin, and process
drift through an operator-run command whose steps recheck real state.

## ADDED Requirements

### Requirement: Standalone deployment command

`uv run python -m nightshift.deploy` SHALL default to checking, accept
`--apply`, `--force`, `--base-branch` (default `main`), `--plugin-dir`,
`--reload-cmd`, `--daemon-label`, and `--supervisor-label`, and route every
external command through an injectable runner. Check SHALL report every
independently observable drift, perform no repairs or control-state writes,
and exit 1 for drift or inconclusive observations, otherwise 0. Fetch MAY
update Git metadata. Invalid arguments SHALL exit 2.

#### Scenario: Check reports independent results
- **WHEN** check observes multiple drifts or a failed probe alongside other observable components
- **THEN** it reports each result and exits 1 without pull, sync, swap, reload, restart, or marker writes
- **THEN** a check with all configured components current exits 0

#### Scenario: Flags and plugin location
- **WHEN** options are parsed with a plugin flag, only `NIGHTSHIFT_PLUGIN_DIR`, or neither
- **THEN** the flag takes precedence, otherwise the environment supplies the location, otherwise plugin checking/deployment/reload are skipped with an explicit note
- **THEN** force without apply, an explicitly empty plugin path, and malformed arguments exit 2 before repair effects

### Requirement: Checkout drift and refusal

The command SHALL fetch the configured upstream remote and report dirty,
wrong/detached branch, behind, ahead-only, diverged, and unavailable checkout
state. Apply SHALL refuse dirty, wrong/detached, ahead-only, diverged, or
unresolved state even with force. It SHALL pull only a clean behind base
branch using `git pull --ff-only`, rechecking before repair and using the new
HEAD for downstream observations.

#### Scenario: Fetch reveals behind base
- **WHEN** fetching shows the clean base branch is behind its upstream
- **THEN** check reports drift without pulling and apply fast-forwards before checking downstream drift against the resulting HEAD

#### Scenario: Unsafe checkout refuses application
- **WHEN** staged, unstaged, or nonignored untracked files exist, the branch is wrong/detached, local commits are ahead or diverged, or upstream/fetch cannot be resolved
- **THEN** apply exits 1 without pull or later repairs, including when forced

### Requirement: Environment synchronization

The command SHALL check the live checkout's `.venv` against `uv.lock` and
default dependency groups using `uv sync --check --frozen --offline`, with the
environment path explicitly selected. Apply SHALL use `uv sync --frozen` only
for observed dependency drift and recheck afterwards, without changing the
lockfile. An inconclusive probe SHALL fail visibly rather than imply health.

#### Scenario: Missing dependency is drift
- **WHEN** mypy is absent despite an unchanged lockfile, or the environment is missing
- **THEN** check reports environment drift and exits 1 without synchronizing

#### Scenario: Conditional frozen sync
- **WHEN** apply observes a stale, current, or inconclusively checked environment
- **THEN** it respectively syncs and verifies it, skips sync, or fails before later repairs with the probe diagnostic

### Requirement: Committed plugin drift

With a configured plugin location, the command SHALL compare deployed files
to `plugins/nightshift` at HEAD, including contents, executable bits, symlink
targets, missing paths, and extra nonignored paths. Tracked paths SHALL always
be compared; ignored generated extras SHALL not cause drift. A missing
deployed directory SHALL count as drift.

#### Scenario: Plugin differs from committed tree
- **WHEN** the deployed directory is missing or a committed path is missing/changed, its mode or link differs, or a nonignored obsolete file remains
- **THEN** check reports plugin drift against HEAD, while ignored generated extras alone do not cause drift

### Requirement: Staged plugin replacement

For plugin drift, apply SHALL stage and verify the committed tree on the same
filesystem, mark reload pending, rename the current copy to a rollback copy,
then rename staging into place. It SHALL keep at most three newest rollback
copies, leave unrelated paths untouched, and never automatically roll back.
Reruns SHALL detect real state and stage afresh as needed without transaction
records or interrupted-run reconciliation.

#### Scenario: Swap retains rollback copies
- **WHEN** apply replaces a differing plugin, including successive deployments beyond three previous copies
- **THEN** the installed files match committed HEAD, marker creation precedes both renames, the previous copy is preserved, and only the three newest rollback copies remain
- **THEN** first installation works without a previous copy and unrelated directories remain untouched

#### Scenario: Missing destination after interruption
- **WHEN** an invocation is interrupted between the two renames and apply runs again
- **THEN** the missing destination is drift, apply stages current HEAD afresh and installs it, retains existing rollback copies within the limit, and proceeds to reload without consulting a transaction record

#### Scenario: Unsafe destination or failed swap
- **WHEN** the destination is a symlink, overlaps the checkout, export entries escape staging, or staging, marker creation, or a rename fails
- **THEN** apply reports failure and stops later repairs, does not automatically restore a copy, and does not start a swap if staging or marker creation failed

### Requirement: Pending plugin reload

The empty `pending-reload` marker SHALL be the only persisted deployment
control state. Check SHALL report it as drift: `plugin installed but not
reloaded`. Apply SHALL reload after a swap or whenever marked even if files
match, defaulting to `paseo plugin reload nightshift`, and remove the marker
only after success. Failure SHALL retain files, marker, and backups and print
exact manual restore/reload commands without automatic rollback.

#### Scenario: Interrupted reload succeeds on rerun
- **WHEN** execution stops after installing the plugin but before reload and a fresh check and apply follow
- **THEN** check reports pending reload, apply reloads the matching installed copy without another swap or backup, and success removes the marker
- **THEN** a subsequent unchanged apply performs no reload or marker write

#### Scenario: Failed reload succeeds on rerun
- **WHEN** reload fails after a swap and a fresh apply later receives a successful reload result
- **THEN** the failed run retains the installed files and marker, stops later repairs, and prints shell-quoted commands with concrete paths to restore the newest rollback copy and reload it
- **THEN** the rerun reloads without another swap or backup and removes the marker only after success; first-install failure reports no rollback available and prints the reload retry command

#### Scenario: Reload override and explicit skip
- **WHEN** a pending reload uses a custom command or an exactly empty `--reload-cmd`
- **THEN** the custom command executes as parsed argv without a shell, or the empty command skips with a note while retaining the marker and exit 1
- **THEN** a later nonempty successful reload clears the marker; skip never acknowledges successful reload

### Requirement: Process age drift

The command SHALL compare the actual start time of the daemon PID from
`~/.nightshift/heartbeat.json` and the supervisor PID from version-1
`specloop status --json` with the newest reachable commit touching
`nightshift/` and `specloop/` respectively. A strictly earlier start SHALL be
drift. Missing or unreadable identity/history SHALL be an inconclusive
nonzero result, not permission to guess a restart target.

#### Scenario: Process predates code
- **WHEN** either service's process start is before, equal to, or after its code commit time
- **THEN** only the earlier start is reported stale; commits touching unrelated paths do not change its threshold

#### Scenario: Process identity is unavailable
- **WHEN** heartbeat/status is missing, malformed, dead, or unsupported, or process start/history cannot be read
- **THEN** check exits 1 with the diagnostic and apply does not guess a target even with force
- **THEN** an unavailable service is given a concrete manual start command without creating restart state

### Requirement: Guarded service restart

Apply SHALL restart only stale identified services after fresh identity, age,
and activity checks. The daemon guard SHALL cover specloop background jobs
only; the supervisor guard SHALL require confirmed idle driving activity.
Busy or uncertain activity SHALL block restart unless forced. Force SHALL
bypass activity guards only. No job, pause, receipt, or run state SHALL change.

#### Scenario: Background jobs defer daemon restart
- **WHEN** the stale daemon has starting, running, stopping, or unknown specloop background jobs or inventory diagnostics without force
- **THEN** apply prints `daemon restart pending`, exits 1, and sends no daemon kickstart
- **THEN** terminal jobs alone do not block, regardless of pending delivery

#### Scenario: Driving supervisor defers restart
- **WHEN** the stale supervisor is driving a run, activity is missing/contradictory, or status has running or error run rows without force
- **THEN** apply prints `supervisor restart pending`, exits 1, and sends no supervisor kickstart
- **THEN** status error rows are not treated as proof of idle activity

#### Scenario: Fresh idle or forced restart
- **WHEN** fresh observations show an idle stale service, or force overrides its activity blocker
- **THEN** apply restarts with the current UID and selected label, defaulting to `com.mattpolicastro.nightshift` and `com.mattpolicastro.specloop`
- **THEN** newly busy services defer unless forced, newly current services are skipped, and daemon deferral does not prevent an idle supervisor restart

### Requirement: Bounded restart confirmation

Apply SHALL send one `launchctl kickstart -k gui/<uid>/<label>` and wait up to
30 seconds for a new live, current PID from heartbeat/status. Without one, it
SHALL send one `launchctl kickstart gui/<uid>/<label>` without `-k` and wait
another 30 seconds maximum. It SHALL report success or failure with a manual
command on failure. It SHALL use no ownership/ancestry probes, restart
records, or further recovery attempts in that invocation.

#### Scenario: First wait confirms replacement
- **WHEN** a new current PID appears during the first wait after a successful kickstart
- **THEN** apply reports success without the non-killing fallback or any further restart

#### Scenario: Second wait or final failure
- **WHEN** no confirmed replacement appears within the first 30 seconds
- **THEN** apply sends exactly one non-killing kickstart and waits up to another 30 seconds
- **THEN** it reports success for a confirmed replacement or failure with exit 1 and the concrete manual command, with no additional commands or persisted recovery budget
- **THEN** command errors are reported and make the invocation fail even if bounded confirmation/fallback restores availability

### Requirement: Ordered repeatable application

Apply SHALL recheck each component before acting in order: checkout,
environment, plugin swap, reload, daemon, supervisor. Hard failures SHALL
stop later repair effects; activity deferrals and explicit reload skip SHALL
remain pending while allowing independent service repairs. Re-running SHALL
observe real state and retry remaining work, without transaction/restart
records. With nothing to do, apply SHALL print `Nothing to do.` and exit 0.

#### Scenario: Failure and rerun preserve completed work
- **WHEN** an ordered repair fails and apply is run again
- **THEN** the failed invocation reports completed steps and stops later effects, apart from its specified bounded restart fallback
- **THEN** the rerun freshly checks each component, skips completed current work, and retries only remaining drift or pending reload

#### Scenario: Second application is a no op
- **WHEN** a successful apply is followed by another with no drift or pending marker
- **THEN** the second run prints `Nothing to do.`, exits 0, and performs no pull, sync, swap, backup creation/pruning, reload, restart, or control-state write, while still fetching and probing

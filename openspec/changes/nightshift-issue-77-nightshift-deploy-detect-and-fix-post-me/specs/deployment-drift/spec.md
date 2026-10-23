## Purpose

Detect drift between the live Nightshift checkout and its deployed environment,
plugin, and running services, and repair it explicitly after merges.

## ADDED Requirements

### Requirement: Standalone deployment command

The system SHALL expose `python -m nightshift.deploy` with check as the default,
`--apply` for repair, and the options and defaults in the design. It SHALL use
the caller's Git checkout and an injectable runner for every external command.
It SHALL preserve all existing CLI and service interfaces.

#### Scenario: Default check and isolated commands
- **WHEN** the module is invoked without `--apply` in a checkout
- **THEN** it checks that checkout, defaults the base branch to `main`, and sends every external command through the supplied runner without executing repair commands

#### Scenario: Invalid invocation
- **WHEN** arguments are invalid, an explicit plugin directory is empty, or `--force` is used without `--apply`
- **THEN** it exits 2 with an explanation before repair effects

### Requirement: Complete drift reporting

Check SHALL report checkout, environment, plugin, daemon, and supervisor results
independently. It SHALL exit 1 for any drift or inconclusive result and 0 when
all configured checks pass. An unconfigured plugin SHALL be an explicit skip.

#### Scenario: Multiple drifts remain visible
- **WHEN** checkout fetch fails and environment, plugin, or process drift can still be observed locally
- **THEN** the report includes the fetch failure and every independently observable drift and exits 1 without repairs

#### Scenario: Healthy configured components
- **WHEN** all configured components are current and the plugin directory is unconfigured
- **THEN** the report identifies the plugin skip, reports the other components current, and exits 0

### Requirement: Checkout drift and refusal

The command SHALL check cleanliness, the selected base branch, and its upstream
after fetching the configured remote. It SHALL report dirty, wrong/detached,
behind, ahead-only, diverged, or unresolved checkout state. Apply SHALL refuse
all but clean base-branch state that is equal to or only behind upstream,
regardless of `--force`.

#### Scenario: Fetch reveals behind base
- **WHEN** a clean base checkout is behind its upstream after fetch
- **THEN** check reports the behind count and exits 1 without pulling

#### Scenario: Unsafe checkout refuses application
- **WHEN** apply encounters staged, unstaged, or nonignored untracked changes, a wrong/detached branch, ahead-only or diverged history, missing upstream, or failed fetch
- **THEN** it exits 1 before pull, sync, plugin mutation, reload, or restart, including with `--force`

#### Scenario: Pull refreshes downstream checks
- **WHEN** apply finds a clean base checkout only behind upstream
- **THEN** it runs `git pull --ff-only` and evaluates environment, plugin, and process drift against the resulting HEAD before performing later repairs

### Requirement: Environment synchronization

The command SHALL check the live `.venv` against `uv.lock` with uv's frozen
offline synchronization check, including default development dependencies.
It SHALL detect missing packages even when lockfile timestamps are unchanged.
Apply SHALL use `uv sync --frozen` only when synchronization is needed and
SHALL verify the result without rewriting the lockfile.

#### Scenario: Missing dependency is drift
- **WHEN** `.venv` is absent or uv reports it out of sync, including a missing mypy installation with an unchanged lockfile
- **THEN** check reports environment drift and exits 1 without modifying the environment or lockfile

#### Scenario: Apply synchronizes stale environment
- **WHEN** checkout preflight or pull succeeds and the environment needs synchronization
- **THEN** apply runs frozen sync in the live checkout, rechecks the environment, and advances only after synchronization is confirmed

### Requirement: Committed plugin drift

The command SHALL select `--plugin-dir` ahead of `NIGHTSHIFT_PLUGIN_DIR` and
compare deployed files to `plugins/nightshift` at HEAD. It SHALL compare names,
contents, executable bits, and symlink targets, detect stale extra nonignored
files, and ignore extra Git-ignored generated files. With no configured path,
it SHALL note the skip and perform no plugin actions.

#### Scenario: Plugin differs from committed tree
- **WHEN** the deployed directory is missing or contains missing, changed, wrong-mode, wrong-link, or stale extra nonignored source files relative to HEAD
- **THEN** check reports plugin drift and exits 1; ignored generated extras alone do not count as drift

#### Scenario: Plugin option overrides environment
- **WHEN** both a plugin directory option and environment setting are present
- **THEN** only the option's directory is checked or repaired

### Requirement: Staged plugin replacement

Apply SHALL deploy only the committed tree through a verified sibling staging
directory when drift exists, keeping the live path unchanged and retaining up
to three previous copies for rollback after success. It SHALL refuse unsafe
destinations and escaping export paths, restore the previous copy on a handled
swap failure, and never prune unrelated directories.

#### Scenario: Swap and retain rollback copies
- **WHEN** plugin drift is applied successfully repeatedly with different committed trees
- **THEN** each new live copy contains the committed export, the immediately previous copy remains recoverable, and only the three newest owned previous copies remain after successful reload or explicit reload skip

#### Scenario: Unsafe destination or failed swap
- **WHEN** the destination overlaps the checkout, is a symlink, the export escapes its root, or staging or replacement fails
- **THEN** apply exits 1 without later restarts, preserves or restores the old live copy when one existed, and leaves unrelated directories untouched

### Requirement: Plugin reload and recovery

After a changed plugin is installed, apply SHALL run `paseo plugin reload
nightshift`, or the shell-free argv override from `--reload-cmd`. An empty
override SHALL skip reload explicitly. Reload failure SHALL stop later repairs,
restore the previous copy when available, and attempt to reload that copy once.

#### Scenario: Reload default override and skip
- **WHEN** apply installs a changed plugin with the default reload setting, a quoted override, or an empty override
- **THEN** it respectively invokes the default argv, invokes the parsed literal override argv without a shell, or prints a reload-skip note without a reload command

#### Scenario: Reload failure restores previous plugin
- **WHEN** reload of the new copy fails
- **THEN** apply restores and attempts one reload of the previous copy, reports both outcomes, exits 1, and does not restart services; if no previous copy exists it removes the failed live candidate and reports that rollback was unavailable

### Requirement: Process age drift

Check SHALL read the daemon PID from its heartbeat and the supervisor PID from
version-1 `specloop status --json`, query actual process start through the
runner, and compare it with the newest HEAD commit touching `nightshift/` and
`specloop/` respectively. It SHALL report a strictly older process as drift,
and missing, malformed, dead, or unreadable process identity as inconclusive.

#### Scenario: Process predates its code
- **WHEN** either process started before its corresponding newest code commit
- **THEN** check reports that service as stale and exits 1; an equal or newer process start is not age drift and unrelated path changes do not trigger it

#### Scenario: Process identity cannot be established
- **WHEN** a heartbeat is missing or malformed, a PID is dead, status is unsupported, or process start/history cannot be read
- **THEN** the component is reported unavailable or inconclusive, exit is 1, and apply does not guess a restart target even with `--force`

### Requirement: Guarded daemon restart

Apply SHALL restart a stale daemon using `launchctl kickstart -k
gui/<uid>/<label>`, defaulting to `com.mattpolicastro.nightshift`, only after
fresh local inventory proves no background attempt is executing or uncertain,
unless `--force` overrides the activity guard. It SHALL not stop jobs or modify
pause or delivery state.

#### Scenario: Background job defers daemon restart
- **WHEN** a stale daemon has a starting, running, stopping, or unknown job, or an inventory diagnostic, without `--force`
- **THEN** apply reports `daemon restart pending`, exits 1, and sends no daemon kickstart while preserving job and pause records

#### Scenario: Idle or forced daemon restart
- **WHEN** a stale daemon has only terminal jobs or none, or `--force` overrides an activity blocker
- **THEN** apply invokes kickstart with the current UID and default or overridden daemon label; a current daemon is not restarted

### Requirement: Guarded supervisor restart

Apply SHALL restart a stale supervisor using the same launchctl argv with the
default label `com.mattpolicastro.specloop` or `--supervisor-label`. It SHALL
require fresh matching-PID activity metadata, no driven or potentially running
run, and no activity uncertainty unless `--force` overrides this guard.

#### Scenario: Driving supervisor defers restart
- **WHEN** a stale supervisor is driving a run, status contains a running or error run, or activity is missing, malformed, or has a mismatched PID, without `--force`
- **THEN** apply reports `supervisor restart pending`, exits 1, and sends no supervisor kickstart or run-state mutation

#### Scenario: Idle or forced supervisor restart
- **WHEN** a stale supervisor has confirmed idle activity or `--force` overrides its activity blocker
- **THEN** apply invokes kickstart with the current UID and selected supervisor label, independently of a deferred daemon restart, and leaves run state unchanged

### Requirement: Restart confirmation

Apply SHALL re-read identity and activity immediately before each restart and
SHALL wait at most 30 seconds for a new live process start no older than its
code commit. Command acceptance alone SHALL NOT prove restart success.

#### Scenario: Activity changes before restart
- **WHEN** a service appeared idle during the first check but becomes busy before kickstart
- **THEN** apply defers that restart unless forced, using the fresh activity observation

#### Scenario: Restart becomes current or times out
- **WHEN** kickstart succeeds and subsequent observations show a new current process, or the 30-second deadline expires first
- **THEN** apply respectively reports a confirmed restart or pending restart with exit 1, without a second kickstart in that invocation

### Requirement: Ordered repeatable application

Apply SHALL repair checkout, environment, plugin, reload, daemon, and supervisor
in that order, skipping current components. Hard repair failures SHALL stop
later effects; busy-service deferral SHALL allow the other service's independent
repair. A subsequent invocation SHALL observe actual state rather than repeat
completed work. With no work required, it SHALL say `Nothing to do.` and exit 0.

#### Scenario: Failure stops later repair effects
- **WHEN** pull, sync, export, reload, or a kickstart command fails
- **THEN** apply exits 1, reports completed and failed steps, and performs no later repair effects; retry observes any earlier successful repairs and does not repeat them unnecessarily

#### Scenario: Second application is a no op
- **WHEN** a successful apply is followed by another with no new commits, dependency drift, plugin changes, or stale processes
- **THEN** the second run prints `Nothing to do.`, exits 0, and performs no pull, sync, directory swap, backup creation/pruning, reload, restart, or deployment-state write, while still fetching and probing

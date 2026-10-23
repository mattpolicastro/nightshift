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
Existing pending deployment records SHALL be reported without being changed;
matching files or process age alone SHALL NOT hide unfinished work.

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

Apply SHALL persist reload intent before replacement and reconcile it before
skipping matching files. It SHALL reload using `paseo plugin reload nightshift`
or shell-free `--reload-cmd` argv; empty overrides SHALL explicitly skip reload.
It SHALL clear intent only after reload/skip and finalization or completed
rollback. Reload failure SHALL restore and reload the previous copy when
available, stop later repairs, and retain unresolved recovery evidence.

#### Scenario: Reload default override and skip
- **WHEN** apply installs a changed plugin with the default reload setting, a quoted override, or an empty override
- **THEN** it respectively invokes the default argv, invokes the parsed literal override argv without a shell, or prints a reload-skip note without a reload command

#### Scenario: Reload failure restores previous plugin
- **WHEN** reload of the new copy fails
- **THEN** apply restores and attempts one reload of the previous copy, reports both outcomes, exits 1, and does not restart services; if no previous copy exists it removes the failed live candidate and reports that rollback was unavailable

#### Scenario: Interrupted swap retains reload intent
- **WHEN** application is interrupted after the candidate becomes live but before reload, and deployed files already match HEAD
- **THEN** check reports `plugin reload pending` and exits 1 without changing the record, and a fresh apply reloads the installed candidate using its recorded transaction without another export, swap, or backup
- **THEN** successful reload and finalization clear the record, and a further apply with no drift is a no-op

#### Scenario: Reload acknowledgement survives retry
- **WHEN** interruption occurs after reload returns success but before durable confirmation, or after confirmation but before finalization
- **THEN** retry respectively repeats reload at least once or only finishes retention and record removal, never creates another rollback generation, and cannot report success while unfinished work remains

#### Scenario: Pending transaction reconciliation
- **WHEN** intent persistence fails, a pending record is malformed or conflicts with filesystem state, or HEAD advances while a valid transaction remains pending
- **THEN** failed persistence prevents any swap, inconsistent state preserves evidence and exits 1, and a valid pending transaction completes against its pinned manifest before new HEAD drift is repaired

#### Scenario: Interrupted rollback remains recoverable
- **WHEN** application is interrupted during restoration or rollback reload after a candidate reload fails
- **THEN** retry reconciles the saved old manifest and rollback intent without installing a new candidate, retains evidence on recovery failure, and stops later repairs with exit 1 for the failed deployment

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
- **THEN** the component is reported unavailable or inconclusive and check exits 1; apply does not guess a restart target even with `--force`, and only the bounded recovery requirement may start a stopped service bound to an existing restart record

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
code commit. It SHALL establish launchd ownership, persist restart intent
before `kickstart -k`, and confirm the replacement belongs to the same loaded
service. Command acceptance alone SHALL NOT prove restart success.

#### Scenario: Activity changes before restart
- **WHEN** a service appeared idle during the first check but becomes busy before kickstart
- **THEN** apply defers that restart unless forced, using the fresh activity observation

#### Scenario: Restart becomes current or times out
- **WHEN** subsequent observations show a new current process owned by the same service, or the 30-second deadline expires first
- **THEN** apply respectively confirms restart and clears its record, or reports pending recovery with exit 1 and retains the record; it never repeats the destructive `kickstart -k` within that transaction

#### Scenario: Restart target is unverified
- **WHEN** launchd ownership cannot be linked to the stale heartbeat/status process, the target is unloaded, or restart intent cannot be persisted
- **THEN** apply exits 1 without signalling a service, even with `--force`, and does not create an actionable recovery record for an unverified target

### Requirement: Bounded service recovery

Apply SHALL recover a recorded restart's confirmed stopped, loaded service
with at most one `launchctl kickstart gui/<uid>/<label>` without `-k` per
transaction, persisting the budget before dispatch and confirming within 30
seconds. Retries SHALL reconcile intent without killing replacements or
starting unrelated, unloaded, or previously stopped services. Recovery SHALL
NOT edit plists, install services, or change job/pause/run state.

#### Scenario: Clean exit receives explicit start
- **WHEN** the recorded stale daemon exits cleanly after `kickstart -k` and launchd reports the same loaded service stopped with no running PID
- **THEN** apply sends one non-killing kickstart, confirms the new owned process and heartbeat before clearing the record, and does not depend on KeepAlive to restart it
- **THEN** the same recovery sequence is available for the supervisor, and a normal automatic replacement receives no extra start

#### Scenario: Retry recovers service stopped by deployment
- **WHEN** a fresh apply finds a valid pending restart record, the old process is gone, the same loaded service is stopped, and the recovery-start budget is unused
- **THEN** it sends only the non-killing start, polls for at most 30 seconds, and clears the record only after confirming the owned replacement; an already current replacement is reconciled without signalling it

#### Scenario: Recovery is bounded and target specific
- **WHEN** recovery fails or times out, its persisted start budget is already spent, or service ownership or record identity is unavailable or mismatched
- **THEN** apply exits 1 with retained evidence and recovery guidance, performs no repeated kill or extra recovery start, and never starts an unrelated or unloaded service
- **THEN** an initially stopped service without a matching pending record receives no start, and check never performs recovery or modifies records

#### Scenario: Failed restart still reconciles availability
- **WHEN** destructive kickstart fails or times out after stopping its previously identified service
- **THEN** apply attempts the same bounded recovery before returning exit 1 for the command failure, reports both outcomes, and performs no later repair stages

### Requirement: Ordered repeatable application

Apply SHALL repair checkout, environment, plugin, reload, daemon, and supervisor
in that order, skipping current components. Hard repair failures SHALL stop
later effects; busy-service deferral SHALL allow the other service's independent
repair. A subsequent invocation SHALL observe actual state rather than repeat
completed work, and SHALL reconcile pending plugin/restart records before
declaring a component current. With no work required, it SHALL say
`Nothing to do.` and exit 0.

#### Scenario: Failure stops later repair effects
- **WHEN** pull, sync, export, reload, or a kickstart command fails
- **THEN** apply exits 1, reports completed and failed steps, performs only the affected component's specified rollback or bounded recovery, and performs no later repair stages; retry observes successful repairs and reconciles pending records

#### Scenario: Second application is a no op
- **WHEN** a successful apply is followed by another with no new commits, dependency drift, plugin changes, or stale processes
- **THEN** the second run prints `Nothing to do.`, exits 0, and performs no pull, sync, directory swap, backup creation/pruning, reload, restart, or deployment-state write, while still fetching and probing

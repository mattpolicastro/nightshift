# Deploying Nightshift

Run from the intended live checkout on the macOS host, with its launchd services already installed. Arrange a quiet window **before pull/sync as well as restarts**: idle checks can race new work, and pausing the daemon does not stop the independent supervisor. Choose the correct plugin directory and service labels; concurrent applies and checkout edits during deployment are unsupported.

```sh
export NIGHTSHIFT_PLUGIN_DIR='/absolute/path/to/deployed/nightshift'
uv run --no-sync python -m nightshift.deploy          # check
uv run --no-sync python -m nightshift.deploy --apply  # repair
```

`uv run python -m nightshift.deploy` is supported, but the outer uv may synchronize before the command can observe dependency drift. Prefer `--no-sync` in both modes to preserve observation and repair order. If `.venv` is absent, use an available Python 3.12+ from the checkout: `python3 -m nightshift.deploy` (or add `--apply`). The module needs no third-party dependencies; uv must still be available for environment probes and repair.

## Options and results

| Option | Default and behavior |
| --- | --- |
| `--apply` | Off: check only. Enable conditional repairs. |
| `--force` | Off; requires `--apply`. Bypass only busy/unknown activity guards for an identified stale process. |
| `--base-branch BRANCH` | `main`; expected deployment branch. |
| `--plugin-dir PATH` | Overrides `NIGHTSHIFT_PLUGIN_DIR`; otherwise use that environment variable. Expands `~`; relative paths start at invocation cwd. Explicitly empty paths are invalid. |
| `--reload-cmd COMMAND` | `paseo plugin reload nightshift`; parsed into argv with shell-style quoting, executed without a shell. Exactly empty text explicitly skips reload. |
| `--daemon-label LABEL` | `com.mattpolicastro.nightshift` |
| `--supervisor-label LABEL` | `com.mattpolicastro.specloop` |
| `-h`, `--help` | Show usage. |

Without either plugin path setting, the command prints `plugin check skipped: no plugin directory configured` and skips plugin checking, deployment, and reload; this alone does not fail. Use a dedicated destination outside the checkout, neither a symlink nor an ancestor of the checkout.

Exit **0** means all configured components are current/completed; **1** means drift, inconclusive observation, refusal, failure, or pending work; **2** means invalid arguments, before repair effects. Check reports independently observable results without repairs or control-state writes. Both modes fetch the upstream remote, which can update Git metadata.

## Repair order and restart guards

Apply freshly checks each stage: fast-forward pull → frozen environment sync → committed plugin swap → reload → daemon restart → supervisor restart. Dirty, wrong/detached, ahead-only, diverged, or unresolved checkout state refuses application even with force. There is no stash, reset, or branch switch. Environment checks use `uv sync --check --frozen --offline` against the checkout's `.venv`, including default dev dependencies; only observed drift authorizes `uv sync --frozen`, followed by another check. An inconclusive probe stops repair.

Plugin deployment exports committed `plugins/nightshift` at post-pull HEAD, including modes and symlinks, without npm/build steps. Hard failures stop later repairs; earlier completed changes stand. Busy restart deferrals and explicit reload skip remain pending but allow independent service repairs. Git and environment repairs have no rollback.

The daemon restart guard covers **specloop background jobs only**, not worker-engine tasks. Starting, running, stopping, unknown jobs or inventory diagnostics block restart; terminal jobs do not block merely because delivery is pending. The supervisor needs confirmed idle driving activity; running/error run rows and missing or contradictory activity block restart. No job, pause, receipt, or run state is changed. Operators own the quiet window; there is no automatic drain or concurrency lock.

Process age compares actual PID start time with the latest reachable commit touching the service's code. Only strictly older processes are stale; this heuristic cannot prove loaded code or service health. Before restarting, identity, age, and activity are checked again. Newly current processes are skipped. Unknown identity is a failure even with force; an unavailable service requires manual start.

Each eligible restart sends one `launchctl kickstart -k gui/<uid>/<label>` (kills and starts), then waits up to 30 seconds for a different live PID whose start is current. Without confirmation, it sends exactly one non-killing `launchctl kickstart gui/<uid>/<label>` and waits another 30 seconds. Final failure exits 1 and prints the concrete manual command. Command errors still fail the invocation and stop later repairs even if bounded confirmation/fallback restores availability. There are no further attempts or persisted restart budgets in that invocation.

For manual start/retry, use the printed command or these defaults on the intended host (substitute overridden labels):

```sh
launchctl kickstart "gui/$(id -u)/com.mattpolicastro.nightshift"
launchctl kickstart "gui/$(id -u)/com.mattpolicastro.specloop"
```

## Pending reload, rollback, and reruns

For destination `<parent>/<name>`, artifacts live in `<parent>/.<name>.nightshift-deploy/`: an empty `pending-reload` marker and at most three newest `rollback-<timestamp>-<unique-id>/` copies. Verified staging is a temporary sibling. The marker is written before either swap rename; failure to create it prevents the swap. Retention pruning happens only after a swap and leaves unrelated siblings alone. There is no automatic rollback.

Marker **presence alone** means pending, even if installed files match HEAD. Check prints `plugin installed but not reloaded`; apply retries this invocation's reload command and removes the marker only after exit 0. A crash after reload success but before marker removal can repeat reload. The marker has no power-loss durability guarantee.

```sh
uv run --no-sync python -m nightshift.deploy --apply --reload-cmd ''
uv run --no-sync python -m nightshift.deploy --apply --reload-cmd 'paseo plugin reload nightshift'
```

The first command installs changed files but retains the marker, notes skipped reload, and exits 1 while pending. The second completes reload and clears it. Empty reload with no file drift or marker is a no-op; skip never acknowledges a successful reload. Custom commands execute as parsed argv: pipes, redirects, and command substitutions are not shell operations.

Reload failure preserves installed files, marker, and backups, stops later repairs, and prints shell-quoted restore/reload commands with concrete paths and the actual reload argv. First-install failure may have no backup; the command then prints a reload retry. Prefer the printed recovery command. For a manual restore example, set an absolute destination and replace `rollback-<timestamp>-<unique-id>` with a retained copy's actual name:

```sh
plugin="$NIGHTSHIFT_PLUGIN_DIR"
state="$(dirname "$plugin")/.$(basename "$plugin").nightshift-deploy"
backup="$state/rollback-<timestamp>-<unique-id>"
test -d "$backup" && aside=$(mktemp -d "$plugin.failed.XXXXXX") &&
  mv "$plugin" "$aside/plugin" && mv "$backup" "$plugin" &&
  paseo plugin reload nightshift
```

Use your custom reload argv if applicable. If the destination is already absent, omit moving it aside and restore the selected backup directly. Manual restore leaves the marker. Only after successful manual reload, an operator deliberately keeping the old copy can run `rm "$state/pending-reload"`; future checks still report file drift and apply may reinstall current HEAD.

Rerun apply after resolving errors or waiting for idle activity. Matching installed files with a marker reload without another swap/backup. An interruption between renames leaves a missing destination: the next apply stages current HEAD afresh, installs, and reloads. Abandoned staging directories may need manual cleanup; no transaction reconciliation occurs. If a service is no longer discoverable, use its manual start command first.

A successful unchanged second apply prints `Nothing to do.` and exits 0: no pull, environment sync, swap, backup creation/pruning, reload, restart, or control-state write. Fetch and read-only probes still run. A later invocation observes service PID/age afresh; it can retry a still-stale process or skip a current replacement.

## Human host checklist

- After merge, run apply in the live checkout with the plugin path configured during a quiet window.
- Confirm the daemon heartbeat advances, the supervisor is live with expected runs, and deferred restarts complete when retried idle. Observe actual launchd restart/fallback behavior; CI cannot prove host timing or service health.
- Open the Factory page on the real Paseo host and confirm current runs and controls load. CI cannot prove plugin reload or host presentation.
- Run apply again; confirm `Nothing to do.`, unchanged service PIDs, and no new rollback copies. Record live results in the PR checklist.

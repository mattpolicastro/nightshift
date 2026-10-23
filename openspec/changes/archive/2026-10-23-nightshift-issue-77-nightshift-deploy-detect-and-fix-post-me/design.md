## Context

The live checkout supplies Nightshift/specloop code and the `.venv` used by
sandboxed verification. Paseo loads a separate copy of `plugins/nightshift`.
The plugin README describes exporting a committed tree and reloading it; the
plugin has no build/install hooks. This is an operator command run while
watching its output, with existing launchd services already installed.

No `AGENTS.md` exists in this checkout or its ancestor directories. This design
uses README/CLAUDE guidance and the current interfaces:

- `nightshift/daemon.py` writes `pid`, `started_at`, and `last_poll` to
  `~/.nightshift/heartbeat.json`.
- `nightshift.specloop_job.list_jobs(state_dir)` returns job rows and diagnostics
  from local state without subprocesses, state creation, or configuration.
- `specloop status --json` returns version 1, `supervisor.state`, and `runs`.
  The PID is inside prose such as `running (pid 123, last poll 2s ago, driving
  nothing)` or `WEDGED (pid 123 alive, but no poll for 301s)`; there is no
  `supervisor.pid` field. `~/.specloop/serve.json` supplies a PID and `driving`
  list when the status text cannot establish activity.
- A status document can exit 0 with per-run error rows. Those rows do not prove
  idle activity. Existing status and pause/job-control semantics stay intact.

## Goals / Non-Goals

Detect the four drift categories and repair them in the requested order. Make
re-running the command the recovery mechanism, with one reload marker as the
only persisted control state. Target roughly 1,000 implementation lines across
six sessions, each below approximately 300 changed lines including its tests.

Create only `nightshift/deploy.py`, `tests/test_deploy.py`, and `docs/deploy.md`.
Do not edit any existing file, including `README.md`, `KNOWN-LIMITATIONS.md`,
`docs/specloop/README.md`, `nightshift/cli.py`, `specloop/cli.py`,
`specloop/critic.py`, `pyproject.toml`, `uv.lock`, `tests/conftest.py`,
`launchd/`, `config.toml`, or plugin sources/tests. Do not add or modify the
other changes' `nightshift/arm.py`, `nightshift/land.py`, `docs/arming.md`,
`docs/landing.md`, `tests/test_arm.py`, or `tests/test_land.py`.

No transaction/restart records, phase machine, interrupted-run reconciliation,
launchd ownership or ancestry inspection, automatic rollback, service
installation, worker task handling, concurrency lock, scheduler changes,
unified CLI, or UI changes. #40 owns CLI consolidation; #16 retires the worker
engine, which currently has no enrolled repositories.

## Decisions

### Command boundary and output

Implement `main(argv=None, *, runner=...) -> int` and small helpers in
`nightshift/deploy.py`, with an executable module entry point. All external
commands, including Git, uv, ps, specloop, reload, and launchctl, go through
one injectable runner accepting argv, cwd, env, timeout, and text/binary mode.
Do not invoke a shell or import a helper that bypasses that boundary. Inject
state roots, UID, monotonic clock/sleep, and job inventory for tests through a
small context; these are not public flags.

Find the checkout with `git rev-parse --show-toplevel` from the caller's cwd.
Flags: `--apply`, `--force`, `--base-branch` (default `main`), `--plugin-dir`,
`--reload-cmd` (default `paseo plugin reload nightshift`), `--daemon-label`
(default `com.mattpolicastro.nightshift`), and `--supervisor-label` (default
`com.mattpolicastro.specloop`). Reject `--force` without `--apply` and malformed
arguments with exit 2. Parse reload overrides with `shlex.split`; shell syntax
is not executed. Exactly empty reload text means skip with a note.

`--plugin-dir` wins over `NIGHTSHIFT_PLUGIN_DIR`; expand `~`, resolve relative
paths from invocation cwd, and reject an explicitly empty path. If neither is
set, print `plugin check skipped: no plugin directory configured` and skip
plugin/reload steps without failing solely for that skip.

Check prints all independently observable results, even after a probe failure.
Exit 0 means all configured components are current; exit 1 means drift,
inconclusive observation, refusal, failure, or pending work. Check performs no
repairs or state writes; fetch may update Git metadata. Apply reports progress
and completed steps, with the same nonzero treatment of pending work.

The requested `uv run python -m nightshift.deploy` is supported, but its parent
uv may sync before Python starts. The guide recommends `uv run --no-sync
python -m nightshift.deploy` for both modes to preserve observation and order.
If `.venv` is absent, an available Python 3.12+ can run the dependency-free
module directly from the checkout. No packaging/entry-point change is needed.

### Checkout and environment

Read staged, unstaged, and nonignored untracked changes using `git status
--porcelain=v1 --untracked-files=all`. Resolve the symbolic branch, its upstream
and remote, fetch that remote, and use `git rev-list --left-right --count
HEAD...@{upstream}`. Report dirty, wrong/detached branch, behind, ahead-only,
diverged, or unavailable upstream/fetch separately. Ahead-only also counts as
drift: unpublished commits are not the intended deployment base.

Apply refuses dirty, wrong/detached, ahead-only, diverged, or unresolved state,
even with force. Recheck before repair, pull with `git pull --ff-only` only when
behind, then resolve HEAD again. All later comparisons use the post-pull HEAD;
no stash, reset, branch switch, or push is performed.

Probe `.venv` using `uv sync --check --frozen --offline`, explicitly setting
`UV_PROJECT_ENVIRONMENT` to the checkout's `.venv` and removing conflicting
active-project overrides. Use uv's default groups, including dev dependencies.
Missing environment or an out-of-sync result is drift; tool/lockfile errors
are inconclusive failures, with their diagnostics retained. Do not use a
lockfile timestamp/digest receipt: removal of mypy with an unchanged lockfile
must still be detected. Apply runs `uv sync --frozen` only when needed, then
rechecks. An inconclusive probe cannot authorize blind synchronization.

### Plugin comparison and simple swap

Use runner-mediated `git ls-tree -rz HEAD -- plugins/nightshift` and blob reads
for a committed manifest, comparing relative paths, contents, executable bits,
and symlink targets with the deployed copy. Missing deployment counts as
drift. Extra nonignored files count too, detecting source deletions; ignore
untracked generated artifacts using checkout Git ignore rules, while always
comparing tracked paths. Export only committed files, without npm/build steps.

Require a dedicated destination outside the checkout, neither a symlink nor an
ancestor of the checkout. Reject unsupported tree entries or paths/links that
escape staging. Stage in a unique sibling directory and verify it before
renaming. Runtime artifacts use a destination-specific sibling directory,
`<parent>/.<name>.nightshift-deploy/`, containing only `pending-reload` and
`rollback-<timestamp>-<unique-id>/` copies; staging is a temporary sibling.
There are no manifests or backup ownership records in this directory.

For file drift: stage the current committed tree; create the empty marker if
absent; rename the current copy to a uniquely named rollback copy if present;
rename staging into place; retain only the three newest rollback copies in
that dedicated directory. Never prune unrelated siblings, and never prune on
a no-op. Do not restore automatically on a rename or reload failure. Report
filesystem errors and stop later steps. Temporary stages may be cleaned on
handled errors; abandoned stages after a crash are not reconciled or reused.

An interruption between renames leaves the destination absent. Next apply
compares real state, exports current HEAD afresh, installs it, and reloads.
It does not inspect a transaction or restore an old tree first. Retained copies
are recovery artifacts, not control state.

### Pending reload marker

Write `pending-reload` before the first swap rename, including when reload is
explicitly skipped. Marker creation failure prevents the swap. Presence alone
means pending; do not store phases, SHAs, paths, commands, budgets, or attempts.
Check reports `plugin installed but not reloaded` whenever it exists, in
addition to file drift if the destination is missing or different.

At the plugin stage each apply rechecks files, swaps only if different, and
reloads whenever the marker exists, even when files already match. Use this
invocation's reload command. Remove the marker only after exit 0. Empty
`--reload-cmd` skips the command but retains a pending marker and exit 1;
without file drift or a marker it is simply a no-op. Skipping cannot certify a
successful reload. A later invocation with a nonempty command completes it.

If reload fails, leave installed files and marker, stop later repairs, report
the error, and print exact shell-quoted commands to move the current copy to a
unique aside path, rename the newest rollback copy to the destination, and
reload it. Print concrete absolute paths and the actual reload argv, not
placeholders; if no backup exists, say so and give the reload retry command.
These commands are advice only. A manual restore leaves the marker for the
next apply; apply always targets current HEAD, so it may reinstall that tree.
Document how an operator deliberately keeping the old copy can remove the
marker after a successful manual reload. No automatic rollback is attempted.

A crash after reload succeeds but before marker removal can cause another
reload on retry. This small at-least-once window is acceptable. Tests simulate
interruption after install/before reload and failed reload, each followed by a
fresh-context successful apply without another swap or backup.

### Process age, guards, and bounded restart

Read the daemon PID from its heartbeat and supervisor PID from version-1
`supervisor.state`, accepting current running/WEDGED forms and ignoring unknown
JSON fields. Invoke `uv run --no-sync specloop status --json` in the checkout.
Obtain actual process start with `/bin/ps -p <pid> -o lstart=` via the runner,
using `LC_ALL=C` and `TZ=UTC`. Compare at second precision with
`git log -1 --format=%ct HEAD -- nightshift/` or `specloop/`. Strictly older
means stale; equal/newer is current. Missing history, malformed/dead/missing
PID, or unsupported output is inconclusive, exit 1. Initially unavailable
services are reported for manual start; force does not invent a restart target.

Immediately before restarting, reread PID/age and activity; skip a now-current
process. For the daemon, `list_jobs` states `starting`, `running`, `stopping`,
`unknown`, or any diagnostic block restart. Terminal jobs do not block just
because delivery remains pending. This guard covers specloop background jobs
only, not worker-engine tasks. Do not modify job, pause, receipt, or run state.

For the supervisor, require fresh status showing `driving nothing`, or a valid
empty `driving` list in `serve.json` whose PID matches the status PID. Nonempty
driving activity blocks; contradictory/missing activity is unknown. Running
run rows and per-run errors also conservatively block restart. Unknown activity
blocks unless forced. `--force` bypasses only busy/unknown-activity guards for
an identified stale process. Deferral prints `daemon restart pending` or
`supervisor restart pending` and exits 1; daemon deferral does not prevent an
idle supervisor restart. Operators own the quiet window.

For each eligible stale service:

1. Send `launchctl kickstart -k gui/<uid>/<label>` exactly once. This explicitly
   kills and starts the service; do not model it as relying on KeepAlive or
   the throttle interval. Poll heartbeat/status for a different live PID up
   to 30 seconds, with one-second intervals and runner timeouts bounded by the
   remaining monotonic deadline. Confirm its start is current using ps.
2. If no confirmed replacement appears, send `launchctl kickstart
   gui/<uid>/<label>` once, without `-k`, and wait up to another 30 seconds.
3. Report success only for a confirmed replacement. Otherwise exit 1 and
   print the exact non-killing manual command for that UID/label. Command
   errors are reported; still perform bounded confirmation/fallback if a
   command may have taken effect. A command error makes the invocation fail
   and prevents later repair stages even if availability is restored.

There are no launchctl print/ownership/ancestry probes or restart records.
A new invocation simply observes PID and age again. If an interruption left
no discoverable process, report it and the manual start command; no persisted
attempt needs reconciliation. Correct labels are the operator's responsibility.

### Ordered application, tests, and documentation

Repair order is checkout, environment, plugin swap, reload, daemon, supervisor.
Each stage freshly observes its own drift before acting. Hard failures stop
later repair effects; busy deferrals allow the other service's independent
repair. Explicit reload skip is pending work, not a hard command failure.
Already completed repairs stand. With no needed action or pending marker,
print `Nothing to do.` and exit 0, performing no pull, sync, swap, backup
creation/pruning, reload, restart, or control-state write. Fetch and read-only
probes still run.

Keep all tests in new `tests/test_deploy.py`, with scenario markers, a recording
fake runner that fails unexpected calls, temporary state/plugin directories,
and fake time. No tests invoke launchctl, Paseo, the network, real ps, or live
state. Include the eight named tests from the request, failure ordering,
rename-gap rerun, and interrupted/failed reload followed by successful rerun.
Use parametrization and a shared restart helper to keep this near 1,000 lines.
Acceptance requires `uv run pytest` and `uv run python scripts/check_types.py`
to pass without weakening assertions or editing the type baseline.

`docs/deploy.md` documents all flags/defaults, exit codes, no-sync bootstrap,
quiet-window responsibility, worker exclusion, marker/skip semantics, backup
retention, manual restore/restart commands, and recovery by rerunning. The
implementation PR's human checklist (not agent tasks) is:

- After merge, run apply in the live checkout with the plugin path configured,
  during a quiet window.
- Confirm the daemon heartbeat advances, the supervisor is live with expected
  runs, and deferred restarts complete when retried idle. Observe real launchd
  restart/fallback behavior; CI cannot prove host timing or service health.
- Open the Factory page on the real Paseo host and confirm current runs and
  controls load. CI cannot prove plugin reload or host presentation.
- Run apply again and confirm `Nothing to do.`, unchanged service PIDs, and no
  new rollback copies. Record live results in the PR checklist.

## Risks / Trade-offs

- A crash between the plugin renames leaves no plugin until the next apply.
  Failures preserve earlier changes; Git/uv have no rollback. Reload failure
  leaves installed files pending and requires retry or deliberate manual restore.
- The marker provides only pending-reload memory. It has no fsync/power-loss
  durability guarantee; abandoned staging directories may need manual cleanup.
  A crash before marker deletion can repeat reload. These are accepted limits.
- Process start versus commit time is a heuristic. Clock skew, historical
  cherry-picks, and loading old code after startup can hide drift. PID reuse
  and status prose parsing are also imperfect. No ownership attestation is added.
- A restart can remain down after both waits; no record tracks it. Manual
  start is then required. A later rerun may repeat a restart if the old PID is
  still stale, or skip it if a replacement has become current.
- Idle observations can race new work. The operator must arrange a quiet window
  before pull/sync as well as restarts; pause alone does not stop the independent
  supervisor. Worker tasks are unguarded because that engine has no enrolled
  repos and is retiring. No automatic drain or concurrency coordination exists.
- Concurrent applies, checkout edits during deployment, or mislabeled launchd
  targets are unsupported. The caller must choose the intended live checkout,
  plugin directory, and labels. Plain outer `uv run` can mask dependency drift.

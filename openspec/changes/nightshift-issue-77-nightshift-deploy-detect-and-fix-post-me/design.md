## Context

The live checkout supplies Python code and `.venv` to Nightshift, specloop, and
sandboxed verification. Paseo runs a separate exported copy of
`plugins/nightshift`. The plugin README already describes exporting committed
files, swapping directories at the configured path, and reloading the plugin;
it has no install/build hooks. The launchd agents already exist.

There is no `AGENTS.md` in this checkout or its ancestor directories. Repository
guidance in `README.md` and `CLAUDE.md`, the plugin README, the relevant modules,
and the existing capability specifications informed this design.

Available interfaces:

- `nightshift/daemon.py` writes `pid`, `started_at`, and `last_poll` to
  `~/.nightshift/heartbeat.json`.
- `nightshift.specloop_job.list_jobs(state_dir)` returns durable job rows and
  diagnostics without invoking external commands or creating state. It uses
  execution locks, rather than the daemon's in-memory process handles.
- `specloop status --json` returns version 1 with `supervisor.state` and `runs`.
  The current supervisor state is a string, for example
  `running (pid 123, last poll 2s ago, driving nothing)`. It has no separate
  PID field. `specloop/serve.py` also writes PID and a `driving` list to
  `~/.specloop/serve.json`.
- Existing status tolerates isolated run errors and unknown fields. Deployment
  must not reinterpret error rows as evidence that the supervisor is idle.

## Goals / Non-Goals

Provide an explicit check and an idempotent repair command that an operator can
run after merging. Report every independently observable drift, including
inconclusive checks. Keep subprocesses injectable and tests independent of
Paseo, launchd, live state, and network access.

Only `nightshift/deploy.py`, `tests/test_deploy.py`, and `docs/deploy.md` may be
created during implementation. Do not edit any existing file. In particular,
avoid `README.md`, `KNOWN-LIMITATIONS.md`, `docs/specloop/README.md`,
`specloop/cli.py`, `tests/test_status_pause_since.py`,
`plugins/nightshift/client/factory-model.ts`,
`plugins/nightshift/client/factory.tsx`, `plugins/nightshift/server/factory.ts`,
`plugins/nightshift/shared/factory.ts`, and all existing plugin test files,
including `factory-pause-projection.test.ts`, `factory-waiting-model.test.ts`,
`factory-waiting-view.test.ts`, and `factory.test.ts`. Other changes own these
files. Also leave `nightshift/cli.py`, `launchd/`, `config.toml`, `uv.lock`,
`pyproject.toml`, and `tests/conftest.py` unchanged.

No automatic deployment trigger, unified CLI, service installation, admission
or run-state mutation, job cancellation, dependency-group configuration,
plugin compilation, UI change, or rollback of Git/dependency changes is added.

## Decisions

### Command and dependency boundaries

Implement `main(argv=None, *, runner=...) -> int` and small typed observation
and action helpers in `nightshift/deploy.py`, with a module entry point. Pass a
single runner callable through every helper that executes a command, including
Git, uv, process inspection, specloop, reload, and launchctl. The production
adapter accepts argv, cwd, environment, timeout, and text/binary output mode
and returns a completed-process result; tests supply a recording fake. Never
use shell command strings or import helpers that secretly invoke subprocesses
outside this boundary. Allow tests to inject state roots, UID, clock, and sleep
through a context object without adding public host-configuration flags.

Resolve the live checkout with `git rev-parse --show-toplevel` from the caller's
working directory; require execution within the intended live checkout. Use
`--base-branch` with default `main`, rather than inferring the deployment base
from whichever branch happens to be checked out. No repository config load is
needed. Options are `--apply`, `--force`, `--base-branch`, `--plugin-dir`,
`--reload-cmd`, `--daemon-label`, and `--supervisor-label`. Reject `--force`
without `--apply`. Argument errors exit 2.

`--plugin-dir` takes precedence over `NIGHTSHIFT_PLUGIN_DIR`; expand `~` and
resolve a relative path against the invocation directory. An explicitly empty
path is invalid. With neither setting, print `plugin check skipped: no plugin
directory configured` and skip both deployment and reload. The reload command
defaults to `paseo plugin reload nightshift`. Parse overrides with
`shlex.split`; an exactly empty value skips reload with a note. Do not interpret
shell operators. Default labels are `com.mattpolicastro.nightshift` and
`com.mattpolicastro.specloop`.

Print one deterministic result per component (checkout, environment, plugin,
daemon, supervisor), with actionable details and skips/pending work identified.
Exit 0 when all configured checks pass, or all needed repairs are verified;
exit 1 for drift, pending restarts, refused application, command failure, or
inconclusive observations. Continue independent checks after a failed probe.
Check mode never pulls, syncs, copies, reloads, restarts, or writes application
state. Fetch may update Git metadata; observational commands may use tool
caches. No deploy receipt is needed merely to check.

The literal requested `uv run python -m nightshift.deploy` works, but outer
`uv run` may synchronize before Python starts. `docs/deploy.md` must recommend
`uv run --no-sync python -m nightshift.deploy` (also for `--apply` to preserve
ordering), and explain that an absent `.venv` requires an available Python
3.12+ interpreter to run the dependency-free module directly. The module
cannot prevent side effects performed by its parent uv process.

### Checkout and environment observations

Use `git status --porcelain=v1 --untracked-files=all` to detect staged,
unstaged, and nonignored untracked changes. Check the symbolic branch, resolve
its configured upstream and remote, fetch that remote, then measure ahead and
behind counts with `git rev-list --left-right --count HEAD...@{upstream}`.
Report wrong/detached branch, dirty checkout, behind, ahead-only, divergence,
missing upstream, or failed fetch distinctly. Ahead-only is drift too: local
unpublished commits are not an approved deployment base. Apply refuses dirty,
wrong/detached, ahead-only, diverged, or unresolved checkout state before any
repair, even with `--force`. Fetch is allowed during this preflight.

After successful preflight, run `git pull --ff-only` only when behind; recheck
branch/cleanliness immediately before pulling and abort on failure. Then
resolve the new HEAD and recompute downstream observations; never deploy from
the pre-pull snapshot. Do not reset, stash, change branches, or push.

Observe dependencies using `uv sync --check --frozen --offline`, with cwd set
to the checkout and `UV_PROJECT_ENVIRONMENT` explicitly set to its `.venv`
(remove conflicting active-project environment overrides). Default dev groups
are included, matching plain `uv sync`. Missing environment or uv's
out-of-sync result is drift; a tool/lockfile failure is an inconclusive error,
never success. Preserve diagnostic text rather than treating every nonzero as
proof of a particular missing package. Run `uv sync --frozen` only when this
observation requires synchronization; then recheck. Use no timestamp/hash-only
receipt: a missing package must be found even if `uv.lock` is unchanged.

### Committed plugin comparison and replacement

Get the authoritative file manifest with `git ls-tree -rz HEAD --
plugins/nightshift` and blob data through the runner. Compare relative names,
bytes, executable bits, and symlink targets against the deployed directory.
Missing deployment is drift. Extra nonignored files also count as drift so
deleted source files are detected, including an installation predating this
command. Extra paths ignored by the checkout's Git ignore rules (for example
`node_modules/`) do not create drift; tracked HEAD files are always compared
even if an ignore rule matches. These ignored artifacts are not carried into
the new export. Reject unsupported Git object types with a diagnostic.

Require a dedicated deployed directory outside the source checkout and its
`.git` directory; reject a symlink destination or a destination that contains
the source checkout. This prevents swapping a checkout or accidentally
deploying onto the in-tree plugin. Read symlinks without following them and
reject archive paths/links that escape the export. No npm install is needed.

Only when plugin drift exists, export the pinned post-pull HEAD to a unique
sibling staging directory on the same filesystem, verify it against the
manifest, move the old destination to a uniquely named sibling rollback, then
rename staging to the unchanged destination path. Export only tracked source
files, not the worktree. Two renames are not an atomic exchange: if installing
staging fails, restore the previous directory and report failure. Preserve the
old copy until reload succeeds. If reload fails, move the failed candidate
aside, restore the previous copy, attempt its reload once, report both results,
and stop before service restarts. A first installation with no previous copy
removes the failed candidate from the live path and reports no rollback was
available. Clean up transient staging/candidate directories on handled errors.

Keep backup ownership metadata in a sibling deployment-specific rollback
directory, outside the exported plugin tree. Never prune unrelated siblings.
After a successful swap and reload (or explicit reload skip), retain the three
most recent successful previous copies and prune only older owned copies.
No-op applications neither create backups nor prune them. Document how to
restore a named copy and reload; a separate rollback CLI is out of scope.

### Process age and restart guards

For each code tree, use `git log -1 --format=%ct HEAD -- nightshift/` or
`specloop/` as its newest reachable touching commit in Git history. Get the
daemon PID from `~/.nightshift/heartbeat.json`; get the supervisor PID from
the version-1 `supervisor.state` returned by
`uv run --no-sync specloop status --json`. Parse the currently emitted running
and WEDGED forms, tolerating additional JSON fields. Do not change status JSON
or invent a required `supervisor.pid` field. Query `/bin/ps -p <pid> -o lstart=`
through the runner with `LC_ALL=C` and `TZ=UTC`, and compare UTC process start
to commit time at one-second precision. Start strictly earlier means drift;
equal/newer is current. Missing history is inconclusive rather than zero.

Missing/malformed heartbeat, missing/dead PID, unsupported status, command
failure, or unreadable process start is reported as unavailable/inconclusive
and exits 1. Do not turn unknown process identity into a restart target.
Starting a stopped service is outside this command's automatic repairs.

Restart only a stale process, using argv
`launchctl kickstart -k gui/<uid>/<label>`. Re-read identity and activity just
before each restart. For daemon activity, call `list_jobs` against the injected
Nightshift state root: `starting`, `running`, `stopping`, `unknown`, or any
inventory diagnostic blocks restart. Finished/failed/stopped jobs do not block
just because delivery is pending. Print `daemon restart pending` and the
reason when blocked. Never stop jobs or touch pause/delivery records.

For supervisor activity, read `~/.specloop/serve.json` and require its PID to
match the fresh status PID and its `driving` list to be valid. A nonempty list
blocks restart. Also treat any running run or per-run status error in the
fresh JSON as potentially driving; this guards heartbeat lag between polls.
Absent activity metadata is unknown, not idle. Print exactly
`supervisor restart pending` with the reason when blocked. `--force` bypasses
busy/unknown-activity guards for a positively identified stale service only;
it cannot override checkout, staging, dependency, or identity failures.

Deferred daemon restart does not prevent checking/restarting an idle stale
supervisor; one busy service does not block the other's independent repair.
After each kickstart, poll through the same runner and state readers for up to
30 seconds at one-second intervals for a new live process start at least as
new as its code commit. Timeout remains pending and exits 1; do not repeatedly
kickstart within the same invocation or record success from command exit alone.

### Ordered application and repeatability

Apply orders repair effects as checkout, environment, plugin swap, reload,
daemon restart, supervisor restart. Skip each current component. Hard command
or filesystem failures stop later repair effects and exit 1; a busy restart
is a deferred action, not a hard failure. Preserve completed earlier repairs
and report them; the next invocation observes actual state and resumes only
remaining work. A clean second invocation prints `Nothing to do.` and does not
pull, sync, write deployment state, create/prune backups, reload, or kickstart.
Fetch and probes still run, so a no-op excludes normal observation metadata.

### Verification and operator documentation

Every implementation task adds tests alongside its helper in
`tests/test_deploy.py`; mark tests with the exact OpenSpec scenario IDs.
Use temporary checkout/export/state fixtures, fake runner responses (including
binary Git data), and fake time for restart polling. Unexpected runner calls
fail tests. Do not invoke launchctl, Paseo, network, real process inspection,
or real host state from these tests. Cover the eight specifically requested
test names, refusals, failure ordering, and unchanged filesystem snapshots on
the second application. Run `uv run pytest` and
`uv run python scripts/check_types.py`; preserve every existing assertion and
do not update the baseline. Test success is an implementation acceptance
condition, not a claim that this proposal has implemented the command.

`docs/deploy.md` documents invocation directory, flags, exit codes, uv bootstrap,
plugin path and rollback layout, pending restarts, force limitations, failure
recovery, and the distinction between a verified command result and real host
behavior. It contains this human checklist for the implementation PR, not
agent tasks:

- After merge, run `uv run --no-sync python -m nightshift.deploy --apply` in
  the live checkout with the deployed plugin directory configured.
- Confirm the daemon heartbeat advances and its PID/start reflects current
  code; confirm the supervisor is live and expected runs remain visible. If
  either restart was deferred, retry when idle and confirm it completes.
- Open the Factory page on the real Paseo host and confirm it loads current
  runs and existing controls. CI cannot establish host reload or presentation.
- Run apply again and confirm `Nothing to do.` with no new service restart or
  rollback directory. Record the live results in the PR checklist.

## Risks / Trade-offs

- Commit time is a requested heuristic, not proof of loaded code. Clock skew,
  historical cherry-picks, or pulling an old commit after process startup can
  hide stale code. Git ancestry/hash attestation requires future service
  changes and is outside this scope.
- Status exposes process identity as prose. Pin parsing with fixtures of
  current forms; unsupported forms fail visibly. Reading `serve.json` for
  activity avoids requiring changes to the status schema.
- Idle checks are snapshots. Rechecking immediately before kickstart and
  treating running runs conservatively reduces but cannot eliminate a new
  launch racing the check. Atomic drain/lease support requires service changes;
  operators should use a quiet window. The command makes no claim to drain
  worker-engine tasks or provide cross-process deployment locking.
- Pulling code and syncing the shared `.venv` can affect ongoing verification;
  restart guards do not make the whole deployment transaction isolated. A
  maintenance window remains the recommended host practice.
- Plugin replacement has a brief gap between renames. Handled failures restore
  the old path; process death/power loss in that gap may need manual restoration
  from the preserved backup. Backup pruning happens only after success.
- This is a sequential operator command. Concurrent apply invocations and
  edits to the live checkout during deployment are unsupported; the guide must
  state this. No durable global transaction or rollback for Git/uv is promised.
- A plain uv launcher can hide dependency drift before the module runs. The
  documented `--no-sync` form makes the check meaningful without modifying
  packaging or the existing CLI.

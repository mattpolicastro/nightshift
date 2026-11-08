# Deploying Nightshift

Run from the intended live checkout on the macOS host, with its launchd services already installed. Arrange a quiet window **before pull/sync as well as restarts**: idle checks can race new work, and pausing the daemon does not stop the independent supervisor. Choose the correct plugin directory and service labels; concurrent applies and checkout edits during deployment are unsupported.

```sh
export NIGHTSHIFT_PLUGIN_DIR='/absolute/path/to/deployed/nightshift'
uv run --no-sync python -m nightshift.deploy          # check
uv run --no-sync python -m nightshift.deploy --apply  # repair after migration/holds below
```

`uv run python -m nightshift.deploy` is supported, but the outer uv may synchronize before the command can observe dependency drift. Prefer `--no-sync` in both modes to preserve observation and repair order. If `.venv` is absent, use an available Python 3.12+ from the checkout: `python3 -m nightshift.deploy` (or add `--apply`). The module needs no third-party dependencies; uv must still be available for environment probes and repair.

## Install OpenSpec

Install the official standalone **OpenSpec 1.16.1** from the
[v1.16.1 release](https://github.com/Fission-AI/OpenSpec/releases/tag/v1.16.1).
Only `(Darwin, arm64)` → `darwin-arm64` is mapped. Intel macOS, Linux, Windows
and every other host fail preflight as unknown; upstream archives alone do not
establish Nightshift support. Additional targets require reviewed executable
provenance and a host-map update.

The trusted process setting `NIGHTSHIFT_OPENSPEC_BIN` overrides
`<NIGHTSHIFT_BIN_DIR>/openspec`, default `/opt/homebrew/bin/openspec`.
Use the [literal host path rules](../KNOWN-LIMITATIONS.md#platform-assumptions):
absolute or leading `~/`, no shell expansion or PATH search. Supply applicable
settings separately to Python services and the Paseo host. The daemon env file
wins over inherited downstream settings; the supervisor does not source it.

During the quiet window, with writers stopped as described below, run this from
the intended checkout on the mapped host. It uses the same resolver as runtime
and preflight. Set an override first if needed, for example
`export NIGHTSHIFT_OPENSPEC_BIN='/absolute/tool directory/openspec'`.
The destination parent must already exist and be writable by the operator.
These are operator download commands; neither runtime nor preflight downloads,
repairs an installation or falls back to `npx`.

```sh
(
set -eu
checkout="$PWD"
openspec_bin=$(uv run --no-sync python -c 'from specloop.openspec import resolve_binary; print(resolve_binary())')
release='https://github.com/Fission-AI/OpenSpec/releases/download/v1.16.1'
stage=$(mktemp -d "${TMPDIR:-/tmp}/openspec.XXXXXXXX")
cd "$stage"
curl --fail --location --proto '=https' --proto-redir '=https' "$release/openspec-darwin-arm64.tar.gz" -o openspec-darwin-arm64.tar.gz
curl --fail --location --proto '=https' --proto-redir '=https' "$release/SHA256SUMS" -o SHA256SUMS
awk '$2 == "openspec-darwin-arm64.tar.gz"' SHA256SUMS > archive.sha256
printf '%s\n' '30e9f4b0a6ca4d9025a3c78a85343e15f60fa03ce042c358d6667d177a03ee19  openspec-darwin-arm64.tar.gz' > expected.sha256
cmp expected.sha256 archive.sha256
shasum -a 256 -c archive.sha256
tar -xzf openspec-darwin-arm64.tar.gz
printf '%s\n' 'bf0fbb541e79c3990906254be7d1b075c95efceb41e9e28899e72357ba4268a0  openspec' > binary.sha256
shasum -a 256 -c binary.sha256
install -m 755 openspec "$openspec_bin"
"$openspec_bin" --version
cd "$checkout"
uv run --no-sync nightshift preflight
)
```

Both checksum checks must pass; `--version` must print exactly `1.16.1` and exit
0. The archive hash verifies the compressed release **before extraction**; the
different executable hash verifies the extracted/installed file. Both come from
the supplied operator evidence in [the committed checksum manifest](../specloop/openspec-SHA256SUMS),
verified on 2026-11-08 for the release published 2026-11-07T00:44:30Z.
Preflight freshly checks the executable hash before executing its version probe,
independently of runtime's successful-version cache. Unknown targets, missing or
unusable files, invalid checksum data, hash/version mismatch and probe failures
all block readiness. Runtime performs only the version gate; launch/version and
non-JSON errors are ordinary refusals, while only subprocess timeouts use
[transient recovery](specloop/command-json.md). Each subprocess has 120 seconds;
first use can need separate version and operation budgets.

Restart affected consumers after replacement, even at the same path: successful
runtime version checks are cached per path per process. Preserve the existing
quiet-window/hold order below: plugin reload (restart Paseo if its environment
changed), daemon restart, then supervisor restart, inspect before resuming.
Rollback requires matching code and binary pins plus fresh preflight/restarts.
All `bin/**` and launchd plists remain frozen without exceptions, including
comments. The supervisor keeps `~/n/bin` and its stale OpenSpec/Node comment;
exact PATH/comment cleanup remains deferred on
[issue #184](https://github.com/mattpolicastro/nightshift/issues/184)'s ledger.

## OpenSpec rollout checks

Exactly three checks for [issue #191](https://github.com/mattpolicastro/nightshift/issues/191)
remain pending, with results linked from [issue #184](https://github.com/mattpolicastro/nightshift/issues/184):

- [ ] Before merge, run verified `openspec validate --all --strict` on a throwaway
  clone of Nightshift `main` and the Scheming Vizier checkout. In a separate
  disposable fixture root, verify failures for missing WHEN/GIVEN, missing THEN
  and an unrecognized requirements header; verify a valid four-hash scenario
  with closing heading hashes passes. Record revisions, executable version/digest,
  exits, example outcomes and every failing path/diagnostic. Active failures
  need review; archived history is informational. Do not repair those specs here.
- [ ] Before deploy, repeat `openspec validate --all --strict` on both enrolled
  repositories' intended deployment checkouts; record revisions and failures.
- [ ] In a quiet window on the Mac Studio, install the verified executable, run
  preflight, restart affected consumers, admit one real OpenSpec change and
  finish one through the deployed service path, preserving normal human gates.
  Confirm the plugin resolves its own Node tools; record executable/version/digest,
  service PATH, run identities and outcomes, explicitly noting the unchanged
  bundled wrapper. Absolute executable selection plus this cut-over establish
  dependency removal; no separate supervisor-without-`~/n/bin` experiment is needed.

Offline tests do not prove the upstream strict parser or live deployment success.
The [retained four-hash lint](specloop/README.md#how-the-engine-works), duplicate-ID
checks and unfinished-task archive gate remain. Existing portability and legacy-run
resume checks retain their separate tracking; these three checks cover this cut-over.

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

## Migration before apply or restart

For the CLI/state upgrade, first follow the human checklist below. Keep daemon
intake/delivery paused, stop all independent writers, and stop the supervisor
with launchd KeepAlive disabled before checkout sync. With the new code available
and the supervisor still stopped, migrate and inspect without scheduler work:

```sh
uv run --no-sync nightshift migrate
uv run --no-sync nightshift status --json
uv run --no-sync nightshift daemon status
```

Resolve migration refusals before proceeding. Inspect all repositories, revisions,
Git/worktree references, permissions, credential links and the completion marker.
Legacy build `proposals/<change>` now lives in `drafts/<change>`; daemon receipts
and jobs remain under `proposals/<owner__repo>`. Keep due pauses unchanged during
inspection. Preserve original evidence in the backup, then replace each automatic
recovery pause with a requested hold while the supervisor is still stopped:

```sh
uv run --no-sync nightshift resume <change> --no-run
uv run --no-sync nightshift pause <change>
uv run --no-sync nightshift status <change> --json
```

Confirm each requested hold has no recovery deadline before allowing supervisor
startup. Apply/restart while holds and daemon pause remain in place, retaining
pause across daemon reload. Restore the intended service configuration and start
the supervisor only after inspection and holds; confirm code/dispatch and
heartbeats, then explicitly resume selected work:

```sh
uv run --no-sync nightshift resume <change> --no-run
uv run --no-sync nightshift daemon resume
```

Apply does not perform migration or set holds. An unavailable service needs manual
start after these checks; automatic startup migration is only a fallback. For
later applies/restarts, retain the same quiet-window, inspection and hold ordering.

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

- Schedule a shared quiet window across every repository, including Phreak and
  Compact Mixer. Use `nightshift daemon pause` and pause every run at a task
  boundary with `nightshift pause <change>`. Verify detached drafting, revision,
  admission and home users have stopped, including other sessions. Back up both
  state roots and record repository/run identities; daemon pause alone is insufficient.
- Stop the supervisor before checkout sync and prevent launchd KeepAlive restart
  during migration/inspection. Inspect the unchanged wrapper diff and inventory,
  actual service configuration, bootstrap paths, env-file and credential/tool guards.
  Confirm daemon startup uses `nightshift daemon run` or frozen `nightshift run`,
  and the installed alias supports frozen `specloop serve`. Wrapper modernization
  is a follow-up, not a prerequisite edit.
- With new code available, perform the stopped-supervisor migrate/inspect/hold
  sequence above across all repositories. Resolve refusals; inspect drafts, daemon
  receipts/jobs, revisions, references and credentials before enabling startup.
- Follow the [disposable unload/reload procedure](validation.md#manual-host-checks).
  Retain daemon pause through reload, verify requested holds without deadlines,
  and run apply with the configured plugin path while still paused. Manually
  start unavailable services only after these migration checks; confirm intended
  code/dispatch and advancing heartbeats, then explicitly resume selected runs
  and intake. Observe actual launchd restart/fallback behavior and retry deferred
  restarts while idle.
- Check the [landing prerequisites](landing.md#prerequisites-and-flags): interpreter
  imports and applicable run environment, `uv`/`gh` on PATH, operator authentication
  and repository permissions, and exactly one case-insensitive specloop enrollment.
  Confirm omitted/explicit specloop enrollment; migrate retired worker enrollment.
- Open Factory on the actual Paseo host; check current runs and controls for every
  repository. Run apply again and confirm `Nothing to do.`, unchanged service PIDs
  and no new rollback copies. Review readability/live references. Record actual
  sandbox probes separately from generated profiles and leave unperformed host
  checks pending in the publish receipt; CI cannot establish these host results.

## Context

`nightshift/deploy.py` checks and repairs post-merge drift in this order:
checkout, environment, plugin, daemon, supervisor. Its plugin step
(`plugin_manifest`, `plugin_differs`, `rollback_copies`, `reload_plugin`,
`plugin`, about l.139-275) mirrors `plugins/nightshift` at HEAD into a directory
that Paseo is configured to load. It needs a staging directory, rollback copies,
a `pending-reload` marker and a separate reload command. Paseo never says which
directory it actually loaded. On 2026-11-15 that gap let a deploy update one
copy while Paseo ran another.

### What Paseo 0.16.1 supports (checked 2026-11-17)

Sources:
- `paseo plugin {add,update,ls} --help`, run against the installed
  `/Applications/Paseo.app` (CLI 0.16.1);
- a read of the CLI and daemon JavaScript bundled in that app's `app.asar`
  (`commands/plugin/*`, `managed-source.js`, `plugin-source-reference.js`, the
  protocol schemas).

The drafting sandbox could not reach the local daemon (`uv_uptime returned
EPERM`), so none of the following ran live. That is the first item on the human
checklist.

- **Source syntax.** `paseo plugin add <source>` accepts an explicit
  `git:owner/repo`, a `github:owner/repo` or a Git URL. A `:plugin/path` suffix
  selects a subdirectory and must be a portable relative path with no `:` in it.
  `--path` is the legacy form of the suffix. With a `git:` or `github:` prefix,
  `owner/repo` expands to `https://github.com/owner/repo.git`. For this plugin
  the source is therefore `git:mattpolicastro/nightshift:plugins/nightshift`.
- **Full SHA as `--ref`.**
  - `add --ref` clones the repository and resolves the ref with
    `git rev-parse <ref>^{commit}` after trying `origin/<ref>` and tags, so a full
    commit SHA works.
  - `update --ref` turns into a Git update target. When the ref is
    `^[0-9a-f]{40,64}$`, `resolveRemoteCommit` returns it verbatim, and the
    target schema accepts only that form. An explicit `--ref` also skips the
    confirmation prompt (`reviewPluginUpdates`: `!options.yes && !target`).
  - The "installed-newer, keep installed" refusal only applies to npm sources.
    An explicit older Git commit is a normal update, so rollback works.
- **Revision reporting.**
  - `paseo plugin ls [id] --json` prints a JSON array of plugin items, with no
    custom serializer. For a Git install, each item has
    `installation.identity = {kind: "git", remote, pluginPath}` and
    `installation.currentRevision`. That revision is `git rev-parse HEAD` of the
    pinned checkout, so it is the full SHA of the `--ref` commit.
  - When the revision can't be read, `currentRevision` is omitted.
  - A directory install reports `identity.kind == "directory"` and no revision.
  - Asking for an unknown ID fails with `Plugin is not configured: <id>`.
- **npm.** A Git install clones the repository, checks out the commit, reads
  `paseo-plugin.json`, and runs only the commands in its optional `build` array.
  It never runs `npm install` on its own. `plugins/nightshift/paseo-plugin.json`
  declares no `build`, and today's `git archive` export runs no npm either,
  because Paseo provides the SDK, React and Zod at runtime (plugin README,
  "Development"). So no `build` entry is needed and `paseo-plugin.json` stays
  unchanged.
- **Reload.** When the plugin is enabled and plugins are on,
  `paseo plugin update` stops the running plugin and starts the new checkout.
  No separate `paseo plugin reload` is needed (from the bundle; unverified live).

None of this contradicts the issue's plan, so the change goes ahead.

## Goals / Non-Goals

**Goals**
- Make the commit Paseo reports the definition of the deployed plugin.
- Deploy and roll back by commit with one Paseo command that the injectable
  runner records exactly.
- Drop all deploy-side plugin bookkeeping.

**Non-Goals**
- Keep the directory-copy mode as a second option.
- Change the plugin's code, its manifest, or the service and checkout steps.
- Automate the one-time switch on the Mac Studio, or roll back automatically.

## Decisions

1. **The Git source replaces the directory mode; it does not sit beside it.**
   - The directory mode was the cause of the 2026-11-15 incident.
   - Keeping it would mean keeping every requirement this change removes, and
     testing two plugin paths.
   - Only one host deploys the plugin, and a human switches it right after merge.

   Between merge and that switch, check reports the plugin as inconclusive
   (Decision 3) rather than deploying it the old way.

2. **Options** (`parse_options`):
   - `--plugin-id ID` overrides `NIGHTSHIFT_PLUGIN_ID`, and there is no default.
     The ID must match Paseo's `^[a-z][a-z0-9-]*$`. An explicitly empty or
     non-matching flag or environment value exits 2 before any command runs.
   - `--plugin-dir` and `--reload-cmd` are removed. argparse rejects them as
     unrecognised arguments (exit 2), which is the behaviour existing tests
     already assert for their malformed forms.
   - `args.plugin_id` is `str | None`.

3. **Unconfigured and un-migrated hosts** (`main`):
   - With no plugin ID and `NIGHTSHIFT_PLUGIN_DIR` unset or empty, print
     `plugin check skipped: no plugin ID configured`. Run no `paseo` command,
     and don't fail.
   - With no plugin ID and a non-empty `NIGHTSHIFT_PLUGIN_DIR`, print
     `plugin inconclusive: NIGHTSHIFT_PLUGIN_DIR is no longer used; install the
     plugin from Git and set NIGHTSHIFT_PLUGIN_ID` and treat it as a plugin
     failure:
     - check exits 1;
     - apply exits 1 before the service steps, as any plugin failure already
       does.
   - With a plugin ID set, `NIGHTSHIFT_PLUGIN_DIR` is ignored.

4. **Target commit.** `target = git log -1 --format=%H HEAD -- plugins/nightshift`,
   read after the checkout step, so apply uses post-pull HEAD.
   - Empty output, or output that isn't `^[0-9a-f]{40,64}$`, is inconclusive.
   - The comparison is exact string equality with Paseo's revision. It is not
     a tree comparison.
   - Pinning to HEAD instead (a later commit with the same plugin tree) shows
     as drift. Apply then moves the pin to the plugin commit. That is harmless
     and keeps the rule simple.

5. **Observation** (new helper, e.g. `git_plugin_revision(context, runner, plugin_id)`):
   - Run `["paseo", "plugin", "ls", plugin_id, "--json"]` through
     `context.run` with `timeout=60`, and parse **stdout only**. Paseo's
     Electron wrapper writes code-signing noise to stderr.
   - The plugin is a usable Git install only when all of these hold:
     - stdout is a JSON array with exactly one object whose `id` equals
       `plugin_id`;
     - its `installation.identity.kind` is `"git"`;
     - `installation.identity.pluginPath` is `"plugins/nightshift"`;
     - `installation.currentRevision` matches `^[0-9a-f]{40,64}$`.
   - Anything else raises a `ValueError` or `RuntimeError` naming what was
     wrong. That covers:
     - runner exceptions (`FileNotFoundError`, `TimeoutExpired`);
     - a nonzero exit;
     - unparseable JSON, a non-array, or zero or several matches;
     - a `directory` identity;
     - another `pluginPath`;
     - a missing or malformed revision.
   - The remote URL is not checked, because forks and URL spellings vary.
     `update` fails on its own if the commit isn't on that remote.

6. **Check output** (new `plugin(context, runner, plugin_id, *, apply)`, which
   replaces the old function):
   - `plugin: current (<target>)` when the revisions match;
   - `plugin: installed revision <current> differs from <target>` when they
     differ;
   - for inconclusive cases, `main`'s existing handler prints
     `plugin failed: <reason>`.

   It returns whether drift is still pending. Check never runs
   `paseo plugin update`.

7. **Apply**, for drift only:
   1. Print `plugin: previous revision <current>`.
   2. Print `plugin rollback: paseo plugin update <id> --ref <current>`.
   3. Run `["paseo", "plugin", "update", plugin_id, "--ref", target, "--json"]`
      with `timeout=360`. Paseo's apply request alone allows 300 s, and the
      preview may clone first.
   4. Re-run step 5's observation and require the revision to equal `target`.
   5. On success, print `plugin: updated to <target>` and set `context.changed`.

   The update's JSON body is not interpreted: the second observation decides.
   - A nonzero update exit, a runner exception, a failed observation or a
     mismatch is a hard failure. `main` prints `plugin failed: …`, returns 1
     and skips the services.
   - The rollback line has already been printed, and nothing is rolled back
     automatically.
   - Deploy writes no files for the plugin, so a rerun simply observes again.

8. **Removed code.** `plugin_manifest`, `plugin_differs`, `rollback_copies`,
   `reload_plugin` and the old `plugin` go, along with the imports only they
   used (`shutil`, `stat`, `tempfile`, `uuid`). Each import is removed only if
   nothing else in the module uses it.

9. **Tests.** New tests go in a new file, `tests/test_deploy_git_plugin.py`.
   - It reuses `recording` and the command constants from `tests/test_deploy.py`
     by import (`from test_deploy import recording, ...`). The fake runner keys
     replies by the space-joined argv, so the tests assert exact `paseo` argv.
   - Required test names:
     - `test_check_reports_git_plugin_revision_drift`
     - `test_check_git_plugin_current`
     - `test_check_git_plugin_unqueryable_is_inconclusive`
     - `test_apply_updates_git_plugin_to_head_ref`
     - `test_apply_prints_previous_revision_before_update`
     - `test_plugin_skipped_with_note_when_unconfigured`
   - `tests/test_deploy.py` loses the tests that belong only to removed
     requirements:
     - `plugin_recording`
     - `test_check_reports_plugin_drift`
     - `test_apply_swaps_plugin_keeping_rollback`
     - `fresh_plugin_run`
     - `test_pending_reload_rerun`
     - `test_rename_gap_stages_current_head_afresh`
     - `test_reload_override_and_skip`
   - Its cross-component tests are moved onto a faked Git plugin:
     - the `deployment` fixture
     - `test_apply_twice_is_a_no_op`
     - `test_ordered_failure_and_fresh_rerun`
     - `test_pending_work_allows_independent_service_repairs`
     - `test_check_aggregates_independent_results`
     - `test_options`
     - `test_invalid_options_have_no_effects`
     - `test_path_expansion_and_empty_environment`

     Their assertions about order, idempotence, exit codes and service
     independence are preserved. Assertions about a swap, backup, reload,
     marker, `--reload-cmd` or plugin-path expansion are dropped, and only
     those.

10. **Docs.** `docs/deploy.md` gets new option rows for `--plugin-id`, a rewritten
    "Pending reload, rollback, and reruns" section (renamed "Plugin update and
    rollback"), and the one-time switch.
    - In `plugins/nightshift/README.md`, only the deployment sentences of
      "Layout" (from "This directory is the plugin's source of truth" to "needed
      no daemon restart") and the install/reload block under "Installation"
      change.
    - Another in-flight change (issue 117) also edits this README, so other
      sections stay as they are.
    - `manifest.sha256` is described as retired.

## Risks / Trade-offs

- **Paseo behaviour was read from the bundle, not run.** A live mismatch would
  surface as an inconclusive check or a failed update, never a silent pass,
  because apply confirms the revision afterwards. The human checks it below.
- **A pin on a later commit with the same plugin tree is reported as drift.**
  This is accepted to keep the comparison exact (Decision 4).
- **Between merge and the one-time switch, the plugin is inconclusive on the
  Mac Studio,** and apply exits before restarting services. This is deliberate:
  it forces the switch rather than letting the old export go stale unnoticed.
- **No local rollback copy.** Rollback now depends on the old commit being
  fetchable from GitHub. Merged commits on `main` are.
- **Paseo stores the clone under `~/.paseo/plugins/`.** Deploy no longer owns
  any plugin files.

### Human checklist (what CI cannot prove)

CI fakes the `paseo` CLI, so a person has to check these on the Mac Studio after
merge:

1. **Source and ref syntax.** Paseo 0.16.1 accepts
   `git:mattpolicastro/nightshift:plugins/nightshift` with a full-SHA `--ref` on
   both `add` and `update`. `paseo plugin ls nightshift --json` reports
   `installation.identity.kind == "git"`, `pluginPath == "plugins/nightshift"`
   and a 40-hex `currentRevision`.
2. **The one-time switch.**
   1. `paseo plugin remove nightshift`
   2. `paseo plugin add git:mattpolicastro/nightshift:plugins/nightshift --ref <sha>`,
      where `<sha>` is `git log -1 --format=%H origin/main -- plugins/nightshift`
   3. In `~/.config/nightshift/env`, replace `NIGHTSHIFT_PLUGIN_DIR=…` with
      `NIGHTSHIFT_PLUGIN_ID=nightshift`.
3. **After the switch.** The Factory section loads in the app, and
   `factory.runs`, `factory.landing` and `attention.read` answer.
   `uv run --no-sync python -m nightshift.deploy` reports `plugin: current`.
4. **Updates reload Factory.** A later `paseo plugin update nightshift --ref <sha>`,
   or `nightshift.deploy --apply`, reloads Factory without a daemon restart.
5. **Old copy.** Decide whether the old `~/.local/share/paseo-plugins/nightshift/`
   copy, its `.nightshift-deploy` state and `manifest.sha256` can be deleted.
   Delete them only after item 3 passes.

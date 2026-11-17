## Why

Since 2026-11-17 the Mac Studio runs the Paseo daemon bundled in Paseo.app
0.16.1. Paseo loads the nightshift plugin from a hand-made `git archive` export
at `~/.local/share/paseo-plugins/nightshift/`, and `NIGHTSHIFT_PLUGIN_DIR` points
`nightshift.deploy` at that directory. `nightshift.deploy` then deploys the plugin
by copying files: it compares a directory against the committed tree, stages a
copy, swaps it in, keeps rollback copies and a `pending-reload` marker, and
reloads. Before the move, the export sat in a custom build's release folder. On
2026-11-15 a deploy updated a stale copy of that folder while Paseo loaded a
different one. Nothing in the copy flow can detect that, because Paseo never
reports which files it loaded.

Paseo 0.16.1 can install a plugin from Git and pin it to a commit:
`paseo plugin add git:owner/repo:<path> --ref <sha>`. `paseo plugin ls --json`
reports the installed commit, and `paseo plugin update <id> --ref <sha>` moves
the plugin to another reviewed commit. Using this for our own plugin turns
shipping into "update to the commit that last touched `plugins/nightshift`".
Rolling back is the same command with the previous commit. The deployed
revision becomes a value Paseo reports, not one we infer, and the staging,
rollback-copy, marker and `manifest.sha256` bookkeeping can go.

## What Changes

- **Plugin selection.** `nightshift.deploy` selects the plugin by Paseo plugin
  ID: `--plugin-id ID`, or `NIGHTSHIFT_PLUGIN_ID` when the flag is absent. These
  replace `--plugin-dir` / `NIGHTSHIFT_PLUGIN_DIR`, and `--reload-cmd` is
  retired. When neither is set, the plugin is skipped with a note, as now. A
  leftover `NIGHTSHIFT_PLUGIN_DIR` with no plugin ID is reported as
  inconclusive, so an un-migrated host is never skipped silently.
- **Check.** Runs `paseo plugin ls <id> --json` and compares the reported Git
  revision with `git log -1 --format=%H HEAD -- plugins/nightshift`. The
  result is drift if they differ, current if they match, and inconclusive if
  Paseo can't be queried or the plugin isn't a Git install of
  `plugins/nightshift`.
- **Apply.** When there is drift, apply prints the previous revision and the
  exact rollback command, then runs
  `paseo plugin update <id> --ref <target> --json`. It then queries
  `paseo plugin ls` again and requires the reported revision to equal the
  target. There is no automatic rollback.
- **Removed.** The requirements Committed plugin drift, Staged plugin
  replacement and Pending plugin reload go, along with their code in
  `nightshift/deploy.py` and their tests:
  - the committed-tree comparison;
  - staging and the swap;
  - the three retained rollback copies;
  - the `pending-reload` marker;
  - `--reload-cmd`.
- **Modified.** Standalone deployment command (flags, check side effects) and
  Ordered repeatable application (the plugin step is now one update).
- **Docs.** `docs/deploy.md` and the deployment paragraphs of
  `plugins/nightshift/README.md` describe the Git-pinned install, update and
  rollback flow, and the one-time switch.

Out of scope:
- the plugin's own code under `plugins/nightshift/{client,server,shared}`;
- `plugins/nightshift/paseo-plugin.json`. No `build` entry is needed (see
  design).
- checkout, environment, process-age and service-restart logic;
- `KNOWN-LIMITATIONS.md`, `README.md` and every other file;
- machine changes on the Mac Studio. A human makes those after merge:
  `~/.paseo/config.json` through `paseo plugin remove` / `add`, and
  `~/.config/nightshift/env`.

## Capabilities

- `deployment-drift`: modified. Plugin drift and repair move from a file copy to
  Paseo's Git-pinned plugin install. Three file-copy requirements are removed,
  and two requirements are modified.

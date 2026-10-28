# Preparing dependencies for verification

Trusted manifests may declare `[[prepare]]` steps using the same fields as
`[[verify]]`: `id`, an argv list in `run`, `expect` (default `0`), and `timeout`
in seconds (default `300`). Preparation runs in declaration order after a
fresh candidate extraction, before the offline checks.

```toml
[vars]
node_bin = "~/n/bin"

[sandbox]
prepare_read = ["~/n"]   # the node toolchain

[[prepare]]
id = "dependencies"
run = ["/bin/sh", "-c", "export PATH=\"{node_bin}:$PATH\" && cp -R \"{src}/.\" \"{build}/project\" && chmod -R u+w \"{build}/project\" && cd \"{build}/project\" && pnpm install --frozen-lockfile"]
timeout = 600

[[verify]]
id = "tests"
run = ["/usr/bin/env", "PATH={node_bin}:/usr/bin:/bin", "pnpm", "--dir", "{build}/project", "test"]
```

Steps use `NIGHTSHIFT_BIN_DIR` (default `/opt/homebrew/bin`) followed by
`/usr/bin:/bin:/usr/sbin:/sbin`, without appending the inherited PATH. Supply
this operator setting in the Python process environment and restart the
affected supervisor or daemon; candidate files and manifest env files cannot
configure the harness resolver. Absolute paths and leading `~/` are supported;
home expansion uses the host user's home before the isolated `HOME` is set.
Spaces stay literal, with no `$VAR`, glob or shell-substitution expansion.
Blank/relative paths, unsupported tilde forms, NUL/line breaks and colons in
this PATH directory setting are invalid. See
[host setup](../../README.md#host-path-setup) for direct CLI examples,
service inheritance and the frozen wrappers' fixed bootstrap paths, and
[platform limits](../../KNOWN-LIMITATIONS.md#platform-assumptions) for all
defaults and pending second-install checks.

`NIGHTSHIFT_NODE_BIN` affects plugin Factory only; it does not add Node to
verification. Tools elsewhere, such as `node` and `pnpm` under `~/n/bin` (from
the `n` version manager), can use a manifest variable and explicit `PATH`, as
above, or a suitable `NIGHTSHIFT_BIN_DIR`. PATH selection grants no sandbox
read access: home-local tools still need manifest variables or
`[sandbox].prepare_read`, and credential denials remain. pnpm's
`#!/usr/bin/env node` line needs `node` on PATH too. With `n`, `~/n/bin/pnpm` is a Corepack shim that downloads pnpm on first use; prepare
steps have network and share the build's `HOME` with verify steps, so running pnpm in a prepare step
leaves it cached for the offline verify steps.

Choose commands appropriate to the project's package manager.
`{src}`, `{build}`, `{sha}`, and manifest variables expand in both kinds of
step. The working directory is `{build}`; `HOME` and `TMPDIR` also point inside
it. Copy source into the build directory when installation needs to modify a
project tree. The extracted candidate remains read-only.

The prepare sandbox allows network access and writes inside the build directory.
Because prepare steps run candidate-controlled code (package lifecycle scripts)
with network, reads under `$HOME` are an allowlist: this verification's own
directory, the paths in `[vars]`, and `[sandbox] prepare_read`. Reads of the
user's `.codex*`, `.claude`, `.nightshift/homes`, `.specloop/homes`, `.ssh`,
`.config`, `.aws`, and `Library/Keychains` directories, plus `[sandbox].deny_read`,
stay denied in preparation and offline verification, even
inside an allowed path. Every step's process group is killed when the step
ends, so nothing a prepare step starts keeps network into the verify steps.
Use `deny_read` for any additional credential locations. Preparation does not
inherit `[sandbox].writable` exceptions. Verify steps retain their offline
sandbox. An enclosing sandbox cannot be relaxed or nested.

Prepare receipts precede check receipts in `receipts.json`, with a separate log
for every step. Prepare IDs must be unique and cannot collide with verify or
acceptance IDs. An unexpected exit, signal, or timeout stops the run before any
remaining step. Preparation also runs for fresh coverage, acceptance, drill,
and mutation-baseline extracts. Each mutant reruns preparation in a fresh build
directory so copied or generated source includes that mutation. Its logs and
receipts are kept under `mutant-<number>/`; a failed prepare step makes that
mutant invalid rather than counting it as caught by tests.

Both modes also deny resolved agent-home and authentication symlink targets,
including every manifest profile's auth target, even unused profiles. These
built-in denials do not depend on manifest deny lists or preparation allowlists.

For this repository, trusted preparation copies `plugins/nightshift/` into a
separate plugin build directory and installs its locked dependencies there with
`npm ci --ignore-scripts --no-audit --no-fund`. Verification runs offline from
the build directory against read-only extracted source, with scenario reporting,
Python/plugin tests, plugin typechecking, the type baseline and negative control.
Local `uv run pytest`, plugin `npm test` and `npm run typecheck` supplement those
harness gates; local results are not acceptance receipts. Record actual sandbox
probe execution or skips separately from generated-profile tests. Host rollout
and credential/bootstrap inspection remain human checks in the
[deployment checklist](../deploy.md#human-host-checklist).

## Trusted dependency snapshots

Opt in with `[[snapshot]]`: unique `id` matching `[A-Za-z0-9][A-Za-z0-9_-]*`, nonempty `lockfiles`, and argv-list `warm`.
Lockfiles are canonical repository-relative regular-file paths without symlinks. Identity hashes sorted paths and exact
bytes. Declare all dependency lockfiles; re-admit with an appropriate manifest when toolchain or unlisted inputs change.

Only driver base synchronization warms, including initial sync before tasks. `warm` gets fetched base `{src}`/`{sha}`,
disposable `{build}`, and payload output `{snapshot:<id>}`. Manifest variables are allowed; other snapshots and
`{mutant_path}` are not. Prepare's network/credential restrictions apply, with timeout 600 seconds and expected exit zero.
Only payload is published read-only; links/special files are rejected. Warm logs/receipts stay separate from candidates'.

This Dart recipe caches only `PUB_CACHE`. Set `dart` to your SDK; home-local SDKs may need `[sandbox].prepare_read`.
Both phases copy their own source and regenerate `.dart_tool` project paths.

<!-- snapshot-example:start -->
```toml
[vars]
dart = "/opt/homebrew/bin/dart"
[[snapshot]]
id = "pub"
lockfiles = ["pubspec.lock"]
warm = ["/bin/sh", "-ec", '''
cp -R "{src}/." "{build}/project"
chmod -R u+w "{build}/project"
cd "{build}/project"
rm -rf .dart_tool
export PUB_CACHE="{snapshot:pub}"
"{dart}" pub get
''']
[[prepare]]
id = "dependencies"
timeout = 600
run = ["/bin/sh", "-ec", '''
cp -R "{src}/." "{build}/project"
chmod -R u+w "{build}/project"
cd "{build}/project"
rm -rf .dart_tool
export PUB_CACHE="{snapshot:pub}"
"{dart}" pub get --offline || "{dart}" pub get
''']
```
<!-- snapshot-example:end -->

Candidate preparation always runs, including for mutants. Each fresh build gets a private writable APFS `clonefile`
copy at `{snapshot:pub}`, shared by its steps. Unsupported platforms/filesystems, cross-device clones and other misses
supply an empty directory, never byte-copy substitution. The recipe tries offline setup, then network fallback.
`snapshot_fallback` events beside receipts and in run events report `missing`, `stale`, `key_unavailable`, `invalid`,
`context_unavailable` or `clone_failed`. Candidates never warm/publish; ordinary prepare/check receipts decide results.

Each run retains three completed entries total across all IDs/keys by publication sequence; reuse does not promote them.
More than three IDs compete and can miss. Locked sync retries pruning; `snapshot_retention_failed` reports an unmet bound.
Diagnostics remain. No age/size/access-time/cross-run eviction occurs. Failed warming evicts nothing. No migration is needed.

See [#134](https://github.com/mattpolicastro/nightshift/issues/134). Pending human work: compare equivalent scheming-vizier
runs before/after: base SHA, keys, host/toolchain, mutation settings, warm/prepare/verify/evidence times, misses, APFS clones
and payload relocation against 25 seconds/565 MB. Inspect regenerated configuration. CI proves no speedup or savings.

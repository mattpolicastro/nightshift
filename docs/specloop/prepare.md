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

Steps run with a fixed `PATH` (`/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin`). Tools installed
elsewhere, such as `node` and `pnpm` under `~/n/bin` (from the `n` version manager), need a manifest variable and an
explicit `PATH`, as above; pnpm's `#!/usr/bin/env node` line needs `node` on it too. With `n`, `~/n/bin/pnpm` is a Corepack shim that downloads pnpm on first use; prepare
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
user's `.codex`, `.claude`, `.specloop/homes`, `.ssh`, `.config`, `.aws`, and
`Library/Keychains` directories, plus `[sandbox].deny_read`, stay denied even
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

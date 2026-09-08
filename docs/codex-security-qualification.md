# Native Codex security qualification

Status: **not qualified for real repository tasks**. Updated 2026-09-08.

The native adapter remains blocked while its permission boundary is qualified.
Protocol fixtures and configuration validation can proceed independently.
A successful protocol exchange does not establish worker-tool isolation.

## Scope

This bounded offline spike used Codex CLI 0.153.4 on macOS arm64, a fresh
synthetic configuration directory, and disposable sentinel files. It did not
start a model turn, use live credentials, or change an existing daemon.
No paid model calls were made. Private diagnostics are excluded from Git.

These findings describe incomplete Nightshift qualification, **not a confirmed
Codex vulnerability**. The actual model-tool policy path remains unverified.

## Established observations

- App-server accepts a named profile and lists it through
  `permissionProfile/list`.
- `config/read` showed the intended synthetic user configuration and an empty
  system layer. No unexpected project configuration appeared in this fixture.
- Disabled hooks, plugins, apps, shell snapshots, multi-agent delegation,
  browser use, and computer use appeared disabled in effective configuration.
  MCP and plugin maps were empty. Actual negative invocation tests remain due.
- An ephemeral thread reported the named active profile, intended writable
  root, network disabled, and excluded global temporary directories in its
  legacy sandbox projection.
- Restricted instruction-file helper startup initially failed with a permission
  denial. Expanding runtime read access allowed startup. The minimal runtime
  read set remains unqualified; broad runtime-path access used during the probe
  is not a production recommendation.
- A standalone command with explicit legacy `readOnly` sandbox denied fixture
  writes. Individual operation exit codes and denial messages were checked,
  rather than relying on the shell's aggregate exit code.

Generate schemas with `codex app-server generate-json-schema --experimental`
when qualifying named profiles. Default schema generation omits
`ThreadStartParams.permissions` and `CommandExecParams.permissionProfile`.
Each is a profile-name string, incompatible with its corresponding legacy
sandbox override. Check `activePermissionProfile` after thread creation.

## Unresolved policy behavior

The debug sandbox command and standalone app-server `command/exec` using the
named profile permitted synthetic outside-root reads/writes, symlink reads,
and Git-control writes that the fixture intended to prohibit. Explicit profile
selection did not change the standalone-command result. The explicit legacy
read-only control did deny writes.

The difference between profile resolution, helper execution, standalone commands,
and actual model-tool execution needs resolution. Accepted configuration and
restrictive thread metadata do not establish identical enforcement across these
paths. These probes do not establish behavior during a normal production turn.

Separate `CODEX_HOME` directories organize configuration; they do not establish
credential isolation. Environment filtering alone also does not prevent reads
from credential files or other process-inspection surfaces.

## Next concrete test

Inspect the experimental schema and resolved sandbox state for the actual
model-tool path. If a legacy sandbox supports restricted minimal reads, exercise
that exact state offline before a model turn. Do not substitute broad filesystem
reads for an unavailable control.

Include positive and negative controls:

1. Normal worktree edits succeed for implementation.
2. Outside-root and symlink-escape reads/writes are denied.
3. Git-control writes are denied, including the real Git directory behind a
   linked worktree. Reviewer source writes are denied.
4. Synthetic credentials cannot be read through files, tool environment, or
   process inspection.
5. Tool network is denied and provider transport remains a separate credential
   channel. Begin with synthetic local endpoints.
6. Repository configuration cannot enable inherited plugins, MCP, hooks, or
   broader permissions. Cancellation reaps all owned child operations.

Record executable version, effective profile, schema, policy fingerprint, and
per-operation results. Missing evidence or unexpected success fails qualification.
Repeat qualification for each supported platform and pinned runtime.

If native controls cannot enforce the boundary, use external isolation with a
host credential broker, or reject the platform. Do not weaken the gate or add an
operator switch that treats an assertion of qualification as evidence.

The [native worker spec](../SPEC-openai-workers.md) remains the acceptance
contract. Official [permissions documentation](https://learn.chatgpt.com/docs/permissions)
describes named filesystem/network profiles. Installed-runtime behavior needs
independent verification before Nightshift claims support.

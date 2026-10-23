## Why

The engine and Paseo plugin assume one machine's Homebrew, Node and checkout locations. These consumers should support another macOS filesystem layout through configuration. The worker worktree root already has a configuration key, but its loader does not expand `~` and the example does not document it. The launchd wrappers also hard-code paths, but the implementation rules freeze `bin/**`, so wrapper portability must remain deferred.

Blocked by #13. Its config-resilience implementation and specifications are present in this checkout; preserve repository-local manifest health and recovery behavior.

## What Changes

- Add operator-controlled environment configuration for the host tool directory, Node bin directory, `uv` executable and Nightshift checkout, retaining today's effective defaults and each consumer's PATH order.
- Use the configured tool directory in verification/preparation, agent subprocess environments and Codex quota reads. Keep sanitized environments and sandbox rules intact.
- Make both plugin command adapters use the host settings. Paths remain server-local configuration, never RPC inputs.
- Retain `[daemon].worktree_root`, expand leading `~/`, resolve relative values beside the selected config file, and reject malformed values. Keep the default `~/Projects/nightshift-wt` and existing worktrees intact.
- Document defaults, configuration examples, restart requirements and the real-second-install limitation in `KNOWN-LIMITATIONS.md`'s platform section and the relevant setup docs.

Implementation includes a small shared Python resolver, a plugin resolver, the named Python/plugin consumers, `nightshift/config.py`, new tests and the documentation named in the design. The only necessary overlap with provider-fallback work is the PATH construction in `specloop/agents/__init__.py`; no provider policy changes are planned.

Out of scope: Linux/Windows support; replacing `sandbox-exec` or launchd; relocating state, credentials, manifests or existing worktrees; changing provider routing/retries; automatic tool discovery or installation; modifying deployed launchd plists or Paseo installations; new RPC settings or UI; hot reload; changing verification permissions. CI does not establish that another machine works.

All changes under frozen `bin/**` are also out of scope, including edits to `bin/nightshift-daemon` and `bin/specloop-serve` or adding a shell helper. The wrappers retain their fixed bootstrap checkout, uv and PATH locations. Document this unresolved limit; wrapper portability requires separate work under rules permitting those edits. This change does not deliver full service-bootstrap portability.

## Capabilities

### New Capabilities

- `host-paths`: trusted host configuration for executable search paths, plugin checkout selection and worker worktree placement, with compatible defaults.

### Modified Capabilities

- `specloop-documentation`: add host portability guidance while retaining existing workflow and unresolved-platform requirements. This is an ADDED requirement; no existing requirement is removed or weakened.

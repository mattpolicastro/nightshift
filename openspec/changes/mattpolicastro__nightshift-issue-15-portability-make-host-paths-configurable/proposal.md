## Why

The engine, Paseo plugin and launchd wrappers assume one machine's Homebrew, Node and checkout locations. A second macOS installation should be able to select its own locations without patching code. The worker worktree root already has a configuration key, but its loader does not expand `~` and the example does not document it.

Blocked by #13. Its config-resilience implementation and specifications are present in this checkout; preserve repository-local manifest health and recovery behavior.

## What Changes

- Add operator-controlled environment configuration for the host tool directory, Node bin directory, `uv` executable and Nightshift checkout, retaining today's effective defaults and each consumer's PATH order.
- Use the configured tool directory in verification/preparation, agent subprocess environments and Codex quota reads. Keep sanitized environments and sandbox rules intact.
- Make both plugin command adapters and both `bin/` entry points use the same host-setting names. Paths remain server-local configuration, never RPC inputs.
- Retain `[daemon].worktree_root`, expand leading `~/`, resolve relative values beside the selected config file, and reject malformed values. Keep the default `~/Projects/nightshift-wt` and existing worktrees intact.
- Document defaults, configuration examples, restart requirements and the real-second-install limitation in `KNOWN-LIMITATIONS.md`'s platform section and the relevant setup docs.

Implementation includes a small shared Python resolver, a plugin resolver, a shell helper, the named consumers, `nightshift/config.py`, new tests and the documentation named in the design. The only necessary overlap with provider-fallback work is the PATH construction in `specloop/agents/__init__.py`; no provider policy changes are planned.

Out of scope: Linux/Windows support; replacing `sandbox-exec` or launchd; relocating state, credentials, manifests or existing worktrees; changing provider routing/retries; automatic tool discovery or installation; modifying deployed launchd plists or Paseo installations; new RPC settings or UI; hot reload; changing verification permissions. CI does not establish that another machine works.

## Capabilities

### New Capabilities

- `host-paths`: trusted host configuration for executable search paths, plugin/wrapper checkout selection and worker worktree placement, with compatible defaults.

### Modified Capabilities

- `specloop-documentation`: add host portability guidance while retaining existing workflow and unresolved-platform requirements. This is an ADDED requirement; no existing requirement is removed or weakened.

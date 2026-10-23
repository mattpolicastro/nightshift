## Context

`specloop/verify.py` and `specloop/agents/__init__.py` construct sanitized PATHs beginning with `/opt/homebrew/bin`; `specloop/quota.py` has the same prefix with a shorter system suffix. `plugins/nightshift/server/attention.ts` and `factory.ts` hard-code `uv` and the checkout, and Factory prepends `~/n/bin`. Both launchd wrappers repeat these assumptions with different existing PATH orders. `nightshift/config.py:load()` already reads `[daemon].worktree_root` but wraps it in `Path` without expanding a tilde or anchoring relative values.

There is no AGENTS.md in this checkout or the checked ancestor directories. README.md points to CLAUDE.md, whose sandbox, credential, compatibility and verification rules apply. The existing `verify-preparation`, `config-resilience`, `attention`, `paseo-runs-page`, `run-actions` and `specloop-documentation` requirements remain in force. In particular, path configuration must not change preparation permissions, stale-data behavior, action validation or #13's enrollment-health isolation. Historical documentation-only scope clauses apply to their named changes; this feature explicitly declares its own documentation scope and adds a portability requirement.

## Goals / Non-Goals

Provide enough host configuration to use another macOS filesystem layout without editing production source. Keep an unconfigured installation's effective paths, command arguments and process behavior unchanged. Exercise selection and propagation without Paseo, launchd, credentials, agent sessions or live services.

Do not generalize every filesystem location. System directories, credential locations, `~/.nightshift`, `~/.specloop`, the config-file selection API, repository `dir` settings and manifest variables remain as they are. The worker root is not the specloop run-state root. Do not migrate old worktrees or make portability claims for other operating systems.

## Decisions

### Host configuration is the process environment

Use these four environment settings, shared across Python, TypeScript and Bash:

| Setting | Default | Meaning |
| --- | --- | --- |
| `NIGHTSHIFT_BIN_DIR` | `/opt/homebrew/bin` | Replacement for the existing host-tool prefix in each consumer's PATH |
| `NIGHTSHIFT_NODE_BIN` | `~/n/bin` | Node/npx directory in Factory and the service wrappers |
| `NIGHTSHIFT_UV` | `<resolved NIGHTSHIFT_BIN_DIR>/uv` | Exact executable file used by plugin and wrappers |
| `NIGHTSHIFT_CHECKOUT` | `~/Projects/nightshift` | Plugin and wrapper working directory |

An explicit setting wins over its default; an unset `NIGHTSHIFT_UV` follows an overridden bin directory. This uses environment configuration because the wrappers need to find `uv` and the checkout before Python or checkout-local TOML can be loaded. Do not add a second TOML host table or have the plugin invoke Python to discover its executable. Keep the existing TOML key for worktree placement.

Settings accept absolute paths or a leading `~/`, expanded against the real host user's home before verification substitutes its isolated HOME. Reject empty/whitespace-only values, relative values, unsupported tilde forms, NUL/newline/carriage-return characters, and colons in the two PATH directory settings. Preserve spaces and other literal filename characters; do not expand `$VAR`, globs or shell substitutions. Reject an explicitly invalid value instead of falling back. Report the setting name without echoing arbitrary values. Validate only settings needed by that consumer, so an irrelevant plugin setting cannot break a quota read. Resolution does not require existence and does not create directories; existing launch/existence failures still handle missing installations.

Use `nightshift/host_paths.py` for small pure helpers such as `host_path(name, default, env=None, home=None)` and `tool_path(system_dirs, env=None, home=None)`, with runtime defaults rather than import-time environment snapshots. Mirror the small resolution contract in `plugins/nightshift/server/host-paths.ts` and `bin/host-paths.sh`; no new dependency is required. Export shell-helper functions, and source that helper relative to the wrapper's own location, before changing directory. Tests use explicit environment/home fixtures to keep the three implementations aligned.

Configuration is operator-owned. A candidate checkout, manifest env file or RPC request cannot supply these host settings to the harness resolver. Do not load dotenv files or source candidate scripts to obtain them. For normal use, set variables in the service's environment and restart that service. `bin/nightshift-daemon` continues sourcing its existing trusted credentials env file before resolution; settings exported there therefore win over inherited values, matching current file-wins behavior. `bin/specloop-serve` does not gain credential-file sourcing. Paseo must receive variables in its own host process environment; a terminal export alone cannot reconfigure an already-running host. There is no hot-reload promise.

### Preserve each consumer's PATH and environment boundaries

Let `B` be the resolved bin directory, `N` the resolved Node directory and `S` the literal `/usr/bin:/bin:/usr/sbin:/sbin` suffix:

| Consumer | Effective PATH |
| --- | --- |
| `specloop/verify.py:run_extracted()` preparation and verification | `B:S` |
| `specloop/agents/__init__.py:base_env()` | `B:S` |
| `specloop/quota.py:codex_limits()` | `B:/usr/bin:/bin` |
| Plugin attention | `B:S` |
| Plugin Factory, including finish | `N:B:S` |
| `bin/nightshift-daemon` | `B:N:<incoming PATH>` |
| `bin/specloop-serve` | `N:B:S` |

The engine and plugin do not append an arbitrary inherited PATH. Changing `N` does not add Node to verification automatically. An operator needing Node there can select a suitable tool directory or retain the trusted manifest's explicit PATH commands. Home-local tools still require appropriate `[sandbox].prepare_read`/manifest variables: choosing a PATH is not a grant of read access. Keep credential denials, offline verification, source protection, build-only preparation writes, receipts, process-group cleanup and existing explicit manifest command overrides unchanged. Do not copy the host environment into sanitized engine subprocesses.

Resolve the bin directory when constructing each environment. Only the literal PATH constant/import and its use in `base_env()` need change in `specloop/agents/__init__.py`; preserve any concurrently landed provider results, dispatch and retry handling. Quota lookup retains its read-only protocol, timeout and fallback behavior when an executable cannot be launched. A malformed path setting is a configuration error before spawn, not an unknown quota response.

### Plugin adapters use server-owned configuration

`plugins/nightshift/server/host-paths.ts` exposes a resolver taking an environment mapping and home directory, defaulting to `process.env` and `homedir()`. Its resolved checkout, executable and consumer-specific PATH feed the actual `execFile` calls in `attention.ts` and `factory.ts`; the attention installation check uses that same resolved checkout. The outcome-database location stays unchanged.

Keep command argv, `--no-sync`, `shell: false`, exit-code rules, timeouts, buffers, child cleanup, caching and display-only projection. Factory actions must still pass through cached-change/verb validation and the CLI's live checks. Resolve inside the execution/error-handling path so invalid host configuration produces the existing unavailable/stale/refused result, never a host path or raw error in RPC output. No changes to `reader.ts`, shared RPC schemas, `index.server.ts` or clients are needed.

Test both production execution adapters by intercepting `execFile` and filesystem existence checks, not just by testing a resolver. Include checkout and executable paths containing spaces and literal shell metacharacters; assert one executable string, literal argv and exact cwd. Isolate module caches/environment in the new test file so existing default-path assertions in `plugins/nightshift/tests/factory.test.ts` remain valid.

### Wrappers bootstrap from the same settings

Update `bin/nightshift-daemon` and `bin/specloop-serve` to source `bin/host-paths.sh`, resolve their settings and quote both `cd` and `exec`. Preserve the daemon's required env-file, OAuth/GitHub-token checks, secret-variable unsets, executable preflight and exit 78 failures. Preserve `uv run nightshift run` and `uv run --no-sync specloop serve` respectively. A malformed host setting exits 78 before command execution. The daemon retains its incoming PATH tail; the supervisor keeps its fixed system suffix. Use Bash parameter handling, never `eval`, and distinguish unset from explicitly empty values.

Automated wrapper tests run the scripts with a temporary HOME, fake credentials and stub tools/uv, capture cwd/argv/PATH, and never start a service or contact a provider. This verifies quoting, settings, defaults, file-wins precedence and credential protections without launchd. No deployed plist is edited: its ProgramArguments must still point to the installed wrapper, and relocating that path is an operator installation step.

### Reuse and normalize the worker root setting

Keep `Config.worktree_root` and `[daemon].worktree_root` in `nightshift/config.py`; add no parallel environment override. Omission remains `Path.home() / 'Projects' / 'nightshift-wt'`. For a supplied nonempty string, expand leading `~/`; anchor a relative path beside the selected config file, independent of daemon cwd. Reject wrong types, blank strings, unsupported tilde forms and NUL/line breaks with `ValueError` naming `daemon.worktree_root`. Spaces remain literal. Do not require the directory to exist during config loading, create it there, or rewrite existing claims/worktrees. Absolute paths retain their location. Relative paths previously followed cwd; this deliberate normalization makes configuration portable and must be called out in docs. Operators who need the old location should supply its absolute path before restart.

Tests in a new file cover omission, absolute, tilde, relative paths under a different cwd, invalid values and a config containing a healthy worker plus an unusable specloop manifest. The latter must still load under #13; path normalization must not eagerly validate enrollment manifests.

### Scope, task sizing and verification

Use new tests: `tests/test_host_paths.py`, `tests/test_worktree_root_config.py`, `tests/test_host_path_wrappers.py` and `plugins/nightshift/tests/host-paths.test.ts`. Put `test_verify_path_is_configurable` in `tests/test_host_paths.py`; exercise `run_extracted()` with an instrumented process boundary and both prepare/verify steps to observe the actual sanitized environments and unchanged profiles. Add a temporary stub executable test for search-path selection without requiring sandbox nesting. Plugin coverage must prove configured checkout reaches both command adapters and the attention installation check. Existing sandbox tests remain the evidence for OS enforcement; do not weaken them.

Do not edit `docs/specloop/README.md`, `specloop/agents/claude.py`, `specloop/agents/codex.py`, `specloop/loop.py`, `specloop/messages.py`, `tests/test_provider_cooldown.py`, `tests/test_provider_fallback.py`, `tests/test_provider_fallback_continuation.py`, `tests/test_provider_fallback_docs.py`, `tests/test_provider_retry_accounting.py` or `tests/test_provider_unavailable_adapters.py`. Provider-fallback work owns those files. The minimal `base_env()` integration is the one necessary shared-file exception, and must be applied on top of landed changes without replacing unrelated code.

Documentation edits are limited to `KNOWN-LIMITATIONS.md`'s Platform assumptions, the root `README.md`'s preparation/setup/platform passages, `docs/specloop/prepare.md`'s PATH explanation, `plugins/nightshift/README.md`'s installation/execution-path passages, and the `[daemon]` comments/example in `config.example.toml`. Preserve unrelated assertions and references. Link issue #15, without inventing an implementing PR number. The implementation acceptance commands are `uv run pytest`, `uv run python scripts/check_types.py`, and, from `plugins/nightshift`, `npm test` and `npm run typecheck`; also syntax-check all three shell files with `bash -n`. Record actual results; a host limitation is not a green result.

## Risks / Trade-offs

- Environment configuration is easy to consume before startup but must be supplied separately to Nightshift, specloop and Paseo. Documentation must show service inheritance and restarts, including the daemon env-file precedence.
- Python, TypeScript and shell repeat a small contract. Matching table-driven cases for defaults, expansion, invalid values and spaces limit drift without introducing a bootstrap dependency.
- Configured executable directories are trusted operator inputs. Preserve sanitized environments and permissions; a relocated home-local tool may still be denied by the prepare sandbox until its trusted manifest allows the necessary reads.
- Existing relative worktree settings change their anchor from process cwd to config-file directory. Document this explicitly; existing absolute paths and the default do not move. There is no migration or cleanup of recorded state.
- The shared agent initializer can conflict with #72. Restrict the edit to PATH construction, use separate tests and retain all landed provider behavior.

Human checklist for the implementation PR (not implementing-agent tasks):

- On a real second macOS installation with a different tool directory, Node directory, checkout and worktree root, set the documented values in the actual service environments, update installed launchd wrapper paths as needed, restart services, and record the layout and result. Merely moving a temporary test directory on the original machine does not satisfy this check.
- On that installation, run a disposable prepare/verify flow, confirm tool lookup for agent/quota calls, and confirm a worker worktree is placed under the configured root. Check real sandbox denials still hold and no existing worktree moved.
- In the actual Paseo host, read attention/Factory and exercise a suitable disposable run action that needs npx (finish only after its normal human checks). Confirm the configured checkout is used and existing confirmation/error behavior remains. Until completed, the docs/PR must mark second-install and host integration validation as pending; CI proves configuration propagation, not end-to-end portability.

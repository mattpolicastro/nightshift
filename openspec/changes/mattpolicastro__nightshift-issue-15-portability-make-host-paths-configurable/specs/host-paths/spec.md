## Purpose

Allow an operator to select host tool, checkout and worker worktree locations while preserving current defaults, command boundaries and macOS sandbox behavior.

## ADDED Requirements

### Requirement: Trusted host path settings

Host consumers SHALL read `NIGHTSHIFT_BIN_DIR`, `NIGHTSHIFT_NODE_BIN`, `NIGHTSHIFT_UV` and `NIGHTSHIFT_CHECKOUT` from their trusted process environment as applicable. Defaults SHALL be `/opt/homebrew/bin`, `~/n/bin`, the resolved bin directory plus `/uv`, and `~/Projects/nightshift`, respectively. Explicit values SHALL override defaults. These settings SHALL NOT be obtained from RPC requests, candidate files or candidate-controlled environment files.

#### Scenario: Unconfigured host retains defaults
- **WHEN** the four host settings are unset
- **THEN** consumers use the current Homebrew, Node, uv and checkout locations, with tilde paths expanded against the host user's home

#### Scenario: Explicit settings override defaults
- **WHEN** an operator selects a different bin directory, Node directory and checkout
- **THEN** each applicable consumer uses those values and an unset uv setting resolves to uv inside the selected bin directory
- **THEN** an explicit uv executable overrides that derived value independently

### Requirement: Literal path resolution

Host setting resolution SHALL accept absolute paths and leading `~/` paths, preserve spaces and literal filename characters, and avoid shell or variable evaluation. It SHALL reject blank, relative, unsupported-tilde and NUL/line-break values, and reject colons in PATH directory settings. It SHALL validate only settings used by the consumer, report invalid settings by name without echoing their values, and require no filesystem existence check or directory creation during resolution.

#### Scenario: Paths remain literal
- **WHEN** applicable settings contain leading `~/`, spaces or shell metacharacters in otherwise valid paths
- **THEN** resolution expands only the leading home marker and preserves all remaining characters without executing or interpolating them
- **THEN** a well-formed nonexistent location still resolves for the consumer's normal launch or installation check

#### Scenario: Invalid settings do not fall back
- **WHEN** a consumed setting is empty, whitespace-only, relative, uses an unsupported tilde form, contains NUL or line breaks, or a PATH directory contains a colon
- **THEN** the consumer reports a configuration failure naming the setting before spawning a command, without silently using a default or leaking the value
- **THEN** an invalid setting unused by that consumer does not prevent its operation

### Requirement: Engine executable search paths

Preparation, verification and agent environments SHALL use the configured bin directory followed by `/usr/bin:/bin:/usr/sbin:/sbin`. Codex quota reads SHALL use that directory followed by `/usr/bin:/bin`. Environment construction SHALL resolve configuration at call time, exclude arbitrary inherited PATH entries and preserve existing environment sanitization, sandbox permissions, preparation order, receipts, provider behavior and quota protocol/fallback behavior.

#### Scenario: Verify path is configurable
- **WHEN** `run_extracted()` runs prepare and verify steps with an overridden host bin directory
- **THEN** both actual child environments use that directory at the head of PATH with the unchanged system suffix and can select a tool from that directory
- **THEN** isolated HOME/TMPDIR, credential exclusions, sandbox profiles and step ordering remain unchanged, and unrelated inherited environment variables are not copied

#### Scenario: Agents and quota use configured tools
- **WHEN** agent environment construction and a Codex quota read occur with an overridden bin directory
- **THEN** their subprocess environments use that directory and their respective existing system suffixes without appending inherited PATH entries
- **THEN** adapter behavior and quota request/timeout/fallback semantics remain unchanged

### Requirement: Plugin host command configuration

Both plugin execution adapters and the attention installation check SHALL use server-owned host path configuration. Configuring paths SHALL preserve argv-based execution without a shell, existing RPC validation and display projection, and command lifecycle behavior.

#### Scenario: Checkout comes from configuration
- **WHEN** attention, Factory status and an allowed Factory action execute with a nondefault checkout and uv path
- **THEN** their production execFile calls receive the configured executable and exact cwd, and attention checks installation under that checkout
- **THEN** existing command arguments are retained; attention uses `<bin>:/usr/bin:/bin:/usr/sbin:/sbin` and Factory uses `<node>:<bin>:/usr/bin:/bin:/usr/sbin:/sbin` with the configured directories
- **THEN** existing timeouts, output limits, caches and child cleanup remain unchanged

#### Scenario: RPC cannot select host commands
- **WHEN** a request contains extra path-like fields or a note with shell metacharacters
- **THEN** it cannot override host settings, no shell is invoked and an allowed note reaches the CLI as one literal argument
- **THEN** unknown changes and unavailable verbs still fail without spawning an action
- **THEN** only validated display fields cross the RPC boundary, with existing home-directory redaction

#### Scenario: Configuration failures preserve plugin errors
- **WHEN** a consumed host setting is invalid or the configured checkout/executable is unavailable
- **THEN** the plugin uses its existing unavailable, stale-cache or refused-action response as applicable, without exposing raw host paths or errors or reporting a healthy empty list

### Requirement: Configurable service wrappers

Both service wrappers SHALL use the host settings for their checkout and uv executable, with quoted literal paths and no eval. The daemon SHALL prepend configured bin then Node directories to its incoming PATH; the supervisor SHALL use configured Node then bin directories followed by `/usr/bin:/bin:/usr/sbin:/sbin`. Existing credential loading/checks, secret unsets, executable preflight and uv argument lists SHALL remain unchanged. Invalid settings SHALL exit 78 before command execution.

#### Scenario: Wrappers use alternate locations
- **WHEN** either wrapper starts with configured tool, Node, checkout and uv locations, including paths with spaces
- **THEN** it invokes the selected uv executable once from the selected checkout with the existing command arguments and the specified PATH order
- **THEN** with settings unset it retains today's locations and PATH ordering

#### Scenario: Daemon credentials retain precedence and guards
- **WHEN** the daemon wrapper sources its trusted env file containing host settings and required credentials
- **THEN** file values override inherited values, required-token checks and preflight still run, and Anthropic API-key/base-url/auth-token variables are removed before uv execution
- **THEN** missing env files or required credentials still refuse startup with exit 78, while the supervisor does not start sourcing the daemon credential file

#### Scenario: Invalid wrapper paths refuse startup
- **WHEN** a wrapper consumes an invalid host setting, including an explicitly empty value
- **THEN** it exits 78 with a setting-name diagnostic and does not invoke uv or silently choose a default

### Requirement: Configured worker worktree root

`nightshift.config.load()` SHALL retain `[daemon].worktree_root` and its default `~/Projects/nightshift-wt`. It SHALL expand leading `~/`, preserve absolute locations and resolve relative values beside the selected config file independent of process cwd. It SHALL reject invalid types, blank values, unsupported tilde forms and NUL/line breaks with a `ValueError` naming `daemon.worktree_root`. Loading SHALL NOT create or migrate worktrees or eagerly validate repository manifests.

#### Scenario: Worker default and absolute roots remain compatible
- **WHEN** worktree_root is omitted or names an absolute path
- **THEN** Config exposes the existing home-based default or that absolute location respectively, without moving existing worktrees or creating directories during loading

#### Scenario: Worker root expansion is independent of cwd
- **WHEN** configuration supplies a leading-home or relative worktree_root and loads from a different working directory
- **THEN** the root expands under the host home or resolves beside the selected config file respectively, preserving spaces as literal path characters

#### Scenario: Worker root validation preserves enrollment isolation
- **WHEN** worktree_root has an invalid type or path value
- **THEN** config loading rejects it with a ValueError naming daemon.worktree_root
- **WHEN** the root is valid and a specloop enrollment manifest is absent or unusable alongside a healthy worker repository
- **THEN** configuration still loads and existing repository-local manifest-health behavior remains in force

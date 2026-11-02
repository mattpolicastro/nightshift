## Purpose

Extend existing run publication base synchronization with source-local credential helper transport for base fetch, publish push and finish push, without changing base preparation or publication recovery contracts.

## ADDED Requirements

### Requirement: Source local helper discovery

The harness SHALL read source-local `credential.helper` values as NUL-delimited bytes before each origin transport, preserving values and order, including empty resets. Unreadable or malformed configuration SHALL block transport without exposing config output.

#### Scenario: Ordered values survive discovery
- **WHEN** the source config contains multiple helpers, duplicate values, an empty reset, whitespace, newlines or non-UTF-8 bytes
- **THEN** discovery returns the exact ordered byte values from the local config without importing global or URL-scoped helpers, expanding include files, stripping values or modifying the source
- **THEN** no matches returns an empty collection, while one empty helper returns a collection containing that reset

#### Scenario: Discovery failure blocks transport
- **WHEN** reading the local helper list fails, times out or returns malformed NUL-delimited output
- **THEN** no network fetch or push begins, and a fixed sanitized error is returned without config output or chained subprocess diagnostics

### Requirement: Credential config environment

Forwarded helpers SHALL travel only in the child Git config environment, preserving their order and byte values. The harness SHALL append to valid inherited indexed config, preserve unrelated environment, and leave the parent environment unchanged.

#### Scenario: Helpers extend command config
- **WHEN** an origin operation forwards helpers with or without existing indexed config
- **THEN** it appends `GIT_CONFIG_KEY_i=credential.helper` and `GIT_CONFIG_VALUE_i` entries and updates `GIT_CONFIG_COUNT`, preserving inherited pairs, helper ordering and empty-reset semantics
- **THEN** helper values are absent from argv and no credentials are written to files; malformed active inherited indexed config blocks helper-bearing transport with a value-free error

### Requirement: Base fetch helper forwarding

Every run base fetch SHALL use the source-local helper environment when helpers exist, retaining SAFE_FLAGS, disabled terminal prompting, refspec, timeout and existing base-sync state transitions.

#### Scenario: Base fetch uses source helpers
- **WHEN** driver preparation or an eligible publication attempt fetches from a source origin whose clone has local helpers
- **THEN** the run fetch receives all helpers in its per-command environment and retains the existing safe fetch argv, `GIT_TERMINAL_PROMPT=0`, destination ref and caller timeout
- **THEN** retries reread local helpers and no stale ref substitutes for a failed fetch; driver failure pauses at base_fetch and publish failure remains retryable without changing readiness

### Requirement: Publish push helper forwarding

Publication SHALL forward the current source-local helpers on its validated origin push using SAFE_FLAGS, disabled terminal prompting and the existing push timeout. It SHALL preserve target, readiness, base and workflow checks before the push.

#### Scenario: Publish uses source helpers
- **WHEN** a ready run with source-local helpers passes publication checks
- **THEN** its push uses the validated destination, unchanged refspec and 300-second timeout with all helpers in the config environment, SAFE_FLAGS and `GIT_TERMINAL_PROMPT=0`
- **THEN** successful publication retains existing PR creation or reuse and human_checks behavior on GitHub and Gitea

### Requirement: Finish push helper forwarding

Finish SHALL forward the current source-local helpers on its validated origin push using the same credential transport as publish. Existing expected-head, task, archive and PR-readiness gates SHALL remain in force.

#### Scenario: Finish and retry use source helpers
- **WHEN** finish reaches its external push, including a retry after a failed or timed-out push
- **THEN** it rereads source-local helpers and uses their config environment, SAFE_FLAGS, `GIT_TERMINAL_PROMPT=0` and the existing 300-second timeout
- **THEN** a failed push retains human_checks and the durable archive without PR readiness writes or done status; retry reuses that archive and can succeed after helper correction without merging

### Requirement: Ephemeral helper configuration

Forwarding SHALL NOT write helpers into repo.git/config, the source config, manifests, run state or logs. Helper environments SHALL be limited to origin fetch/push subprocesses and SHALL NOT be supplied to seal.git or internal archive operations.

#### Scenario: Run config stays credential free
- **WHEN** base fetch, publish push or finish push succeeds, fails, times out or is retried with source-local helpers
- **THEN** a run config initially containing no credential.helper entries still has none, the source config and parent environment are unchanged, and state and event artifacts contain no forwarded helper values
- **THEN** internal seal and archive operations do not receive the newly constructed helper environment

### Requirement: Credential failure boundary

Helper-bearing transports SHALL capture child output and catch CalledProcessError and TimeoutExpired at the subprocess boundary. They SHALL expose only the operation, exit status or timeout duration, and a sanitized stderr tail, without unsafe exception chaining.

#### Scenario: Failed transport diagnostics are safe
- **WHEN** either exception type occurs during a helper-bearing fetch or push with a sentinel helper echoed by the child
- **THEN** driver pause diagnostics, direct publish JSON, act publish, direct finish, act finish, displayed tracebacks and logs omit the sentinel and raw exception text
- **THEN** error diagnostics retain operation/ref, exit status or timeout duration and at most the last 400 characters of sanitized stderr, never falling back to stdout
- **THEN** publish remains retryable without PR writes or readiness loss; finish retains its archived retry state without marking the PR ready

### Requirement: Byte level diagnostic redaction

The harness SHALL replace nonempty raw helper values and their nonempty byte lines in raw stderr with a fixed marker before decoding or truncating diagnostics. Empty reset entries SHALL NOT be redaction patterns.

#### Scenario: Invalid UTF8 bytes are redacted
- **WHEN** either subprocess exception contains raw stderr echoing the helper `b"!printf 'synthetic-\xff-credential'"`
- **THEN** redaction removes its raw bytes before replacement decoding, and neither the helper bytes, its U+FFFD-substituted text nor surrogate-escaped rendering appears in any diagnostic, output or saved log

#### Scenario: Redaction precedes tail selection
- **WHEN** raw stderr contains repeated or multiline helpers or a helper spanning the eventual diagnostic tail boundary
- **THEN** full values and their nonempty lines are redacted in the complete byte buffer before decoding and tail selection, without disclosing a truncated helper fragment

### Requirement: No helper compatibility

Without local helpers, fetch and push SHALL retain existing transport arguments, subprocess options, inherited configuration, error formatting and exception causes. No-helper calls SHALL NOT add or normalize indexed Git config environment entries.

#### Scenario: Fetch remains unchanged
- **WHEN** source-local helper discovery finds no values
- **THEN** fetch keeps its existing argv, text capture, SAFE_FLAGS, prompt override and caller timeout, as well as exact first-400-character stderr/stdout diagnostics and original exception-cause identity

#### Scenario: Push remains unchanged
- **WHEN** publish or finish has no source-local helpers, or a low-level push caller omits the optional source
- **THEN** push retains its legacy argv, `check=True`, 300-second timeout, inherited streams/environment and original exception behavior
- **THEN** existing no-helper publication, locking, PR reconciliation and finish-retry assertions remain valid

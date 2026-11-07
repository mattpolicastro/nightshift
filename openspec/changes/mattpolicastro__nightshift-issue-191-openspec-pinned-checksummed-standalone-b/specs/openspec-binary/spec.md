## Purpose

Run the pinned standalone OpenSpec validator without runtime package acquisition and establish installed-binary integrity before unattended use.

## ADDED Requirements

### Requirement: Trusted executable selection

The backend and preflight SHALL resolve the executable from trusted `NIGHTSHIFT_OPENSPEC_BIN`, otherwise from `NIGHTSHIFT_BIN_DIR` (default `/opt/homebrew/bin`) plus `/openspec`. Resolution SHALL follow existing literal host-path rules, validate only consumed settings, avoid PATH search and shell evaluation, and never use candidate or RPC settings.

#### Scenario: Default and overridden binary
- **WHEN** the executable override is absent, the bin directory is overridden, or an explicit executable with spaces and leading `~/` is supplied
- **THEN** runtime and preflight select the same default, derived or explicitly expanded literal path
- **THEN** an explicit executable does not consume an invalid unused bin-directory setting

#### Scenario: Invalid binary configuration refuses
- **WHEN** a consumed executable setting is blank, relative, has an unsupported tilde form or contains a NUL or line break
- **THEN** runtime raises plain `SpecError` and preflight reports failure before spawning, naming the setting without echoing its value or falling back

### Requirement: Pinned direct invocation

The backend SHALL define `VERSION = "1.16.1"` as the single executable version constant, successfully check `[OPENSPEC_BIN, "--version"]` once per resolved path per process, then run `[OPENSPEC_BIN, *args, "--json"]`. Both probes and operations SHALL use the existing merged environment, capture settings, cwd and 120-second timeout. Runtime SHALL NOT download, checksum, retry or fall back to npx.

#### Scenario: Validation uses the pinned binary
- **WHEN** strict validation runs with a successful version gate
- **THEN** operation argv is exactly `[OPENSPEC_BIN, "validate", CHANGE, "--strict", "--json"]` and existing environment flags and timeout remain intact

#### Scenario: Version probe is reused
- **WHEN** validate, show and archive use the same selected path in one process, including concurrent first use
- **THEN** exactly one successful version probe precedes operations and later operations run without another probe
- **THEN** a different resolved path requires its own successful probe, failed probes are not cached, and no operation follows a failed probe within that invocation

#### Scenario: Version mismatch refuses
- **WHEN** the version probe exits nonzero or stripped stdout differs from `VERSION`, including malformed or banner output
- **THEN** the backend raises plain `SpecError` naming the required version and deployment install step without invoking the requested operation
- **THEN** admission reports a refusal and saves no admitted state

#### Scenario: Missing binary is not transient
- **WHEN** the selected executable is missing or a probe or operation cannot be launched
- **THEN** the backend raises plain `SpecError`, never `ValidatorUnavailable`, with installation guidance
- **THEN** admission exits 1 with a version-1 refused outcome containing the reason, no agent invocation and no admitted state

### Requirement: Installed binary preflight

Preflight SHALL independently verify a readable executable file, its SHA-256 against committed `specloop/openspec-SHA256SUMS` for the host target, and its exact pinned version. It SHALL verify bytes before execution, check afresh on every invocation, preserve other checks and fail with installation instructions on unavailable or invalid evidence. Release archive hashes and installed-executable hashes SHALL be distinguished and have recorded official-release provenance.

#### Scenario: Verified executable passes
- **WHEN** the selected file is executable, its bytes match the host binary digest and its version is exactly `VERSION`
- **THEN** preflight reports a successful OpenSpec check without modifying files, downloading anything or relying on the runtime version cache
- **THEN** existing forge, credential, alert and deep-probe checks retain their outcomes

#### Scenario: Wrong checksum blocks readiness
- **WHEN** an executable reports the expected version but its bytes have the wrong checksum
- **THEN** preflight fails the OpenSpec check with the deployment install instruction and does not execute that file
- **THEN** other checks remain reportable and CLI preflight cannot report readiness

#### Scenario: Unavailable prerequisite fails closed
- **WHEN** the file is missing, unreadable or non-executable, checksum data is missing, malformed or duplicated, the host target is unknown, or the version probe fails, times out or mismatches
- **THEN** preflight reports a failed check with installation guidance and never substitutes another target, a cached pass or an automatic repair

### Requirement: Strict validator compatibility

Strict validation SHALL delegate parsing to OpenSpec 1.16.1 and preserve normal issue/refusal handling, duplicate scenario IDs and unfinished-task archive safeguards. The harness SHALL retain its existing physical-line scenario lint and SHALL NOT begin validating archived changes.

#### Scenario: Incomplete scenarios fail strict validation
- **WHEN** a scenario lacks both WHEN and GIVEN or lacks THEN
- **THEN** the pinned strict validator reports failure and the harness surfaces its issues through normal validation outcomes

#### Scenario: Unknown requirement sections fail
- **WHEN** a requirement appears under an unrecognized `## … Requirements` header
- **THEN** strict validation fails rather than merely warning

#### Scenario: Closing hashes and local lint coexist
- **WHEN** valid headings have closing hash runs and scenarios have four opening hashes
- **THEN** the pinned validator accepts those headings
- **WHEN** a live change spec has a physical line matching `^#{1,3}\s+Scenario:`, including a literal matching example
- **THEN** the harness still reports the existing exactly-four-hashes problem without semantic fence or HTML parsing

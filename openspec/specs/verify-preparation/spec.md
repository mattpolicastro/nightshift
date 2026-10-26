# verify-preparation Specification

## Purpose
Trusted steps that prepare a candidate's dependencies before sandboxed,
network-free verification.

## Requirements

### Requirement: Prepare steps
A manifest's `[[prepare]]` steps SHALL run after the candidate is extracted and
before its verify steps, under a sandbox that allows network access but
confines writes to the build directory and denies credential reads, in the
build directory, and SHALL be recorded as receipts. A prepare step that does
not meet its expected exit SHALL fail verification.

#### Scenario: Dependencies ready for sandboxed tests
- **WHEN** a prepare step writes a file into the build directory and a verify step reads it
- **THEN** both steps pass and the receipts list the prepare step before the verify step

#### Scenario: Failing prepare fails verification
- **WHEN** a prepare step exits with an unexpected status
- **THEN** verification fails at that step and no verify step runs

### Requirement: Source stays read-only
Prepare steps SHALL NOT be able to modify the extracted candidate source.

#### Scenario: Write to source refused
- **WHEN** a prepare step tries to create a file in the source tree
- **THEN** the write fails

### Requirement: Preparation visible in agent recipes

The implementer recipe and feasibility review prompt SHALL show every configured prepare step before every verify step, in declaration order within clearly labeled groups. Each step SHALL include its ID, expanded shell-quoted command, and expected exit status. The prompts SHALL identify preparation as harness execution with network access before offline verification, preserving existing agent restrictions and execution semantics.

#### Scenario: Prepare commands precede checks
- **WHEN** a manifest contains multiple prepare and verify steps, variables, arguments containing spaces, and nonzero expected exits
- **THEN** both generated prompts show all prepare steps in order before the verify steps with their IDs, correctly quoted expanded commands, and expected exits
- **THEN** each prompt retains its existing source, build, and revision substitution context and identifies the harness preparation environment without executing any step

#### Scenario: No preparation configured
- **WHEN** a manifest has no prepare steps
- **THEN** both prompts show preparation as absent and retain all verify commands and expected exits

### Requirement: Agent homes denied in both modes

Preparation and ordinary verification SHALL deny lexical and canonical agent-home paths independently of manifest exclusions.

#### Scenario: Legacy exclusions cannot expose new homes
- **WHEN** both modes build profiles with empty deny_read or only ~/.specloop/homes
- **THEN** profiles deny ~/.specloop/homes, ~/.nightshift/homes and homes_dir() with resolved paths, even when prepare_read allows a parent

### Requirement: Credential directories denied in both modes

Both verification modes SHALL unconditionally deny every CREDENTIAL_DIRS entry and each home-level .codex* directory discovered during profile construction.

#### Scenario: Profile login directories are denied
- **WHEN** both modes encounter .codex-<suffix>/auth.json with no manifest exclusion for it
- **THEN** generated profiles deny lexical and real directory paths and the login sentinel cannot be read while an allowed control file can

### Requirement: Auth targets denied by real path

Both modes SHALL deny expanded and resolved manifest auth paths, resolved default Codex auth, and external agent-home auth-link targets independently of manifest exclusions.

#### Scenario: External targets cannot escape denial
- **WHEN** sentinel auth links leave agent homes, profile auth lies outside HOME, and an unused manifest profile names another target
- **THEN** generated profiles include every lexical/resolved target and real sandbox probes refuse all sentinel reads in both modes while the control reads; source stays read-only and network policy remains mode-specific

#### Scenario: Sandbox evidence is explicit
- **WHEN** credential tests run on unsandboxed macOS, nested macOS or another platform
- **THEN** profile-text assertions run everywhere, unsandboxed macOS executes real denial probes, and macOS probes skip only when already_sandboxed() is true

## Purpose

Protect credential paths and targets during both preparation and offline verification.

## ADDED Requirements

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


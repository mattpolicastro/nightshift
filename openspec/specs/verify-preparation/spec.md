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

### Requirement: Snapshot manifest declarations

The trusted manifest SHALL accept optional `[[snapshot]]` entries with unique safe `id`, nonempty canonical repository-relative `lockfiles` and nonempty argv-list `warm`, exposed as immutable `Manifest.snapshots` with an empty default. It SHALL reserve `snapshot:` variables, validate snapshot references, and preserve existing manifest behavior and serialization.

#### Scenario: Declarations round trip
- **WHEN** a manifest with multiple snapshot declarations is parsed, templated with run overrides and loaded again
- **THEN** IDs, lockfiles and argv retain their values and order, and existing step settings remain unchanged

#### Scenario: Invalid declarations fail before execution
- **WHEN** snapshots have duplicate or unsafe IDs, malformed fields, unsafe or duplicate paths, reserved variable overrides, unknown snapshot references or warm references to another snapshot or mutant path
- **THEN** parsing raises a setting-specific `ValueError` before any warm or verification command executes

### Requirement: Lockfile content identity

The harness SHALL compute a deterministic SHA-256 key from versioned, length-framed sorted path names and exact contents of all declared lockfiles in the extraction being used. It SHALL reject missing, unreadable, non-regular or symlink-traversing lockfiles as unavailable keys, and SHALL NOT substitute a key from another commit or build.

#### Scenario: Lockfile changes invalidate reuse
- **WHEN** any declared lockfile bytes change, or a declared path changes, while other inputs remain equal
- **THEN** `test_snapshot_key_changes_with_lockfile` verifies that the key changes, while declaration reordering, mtimes and unrelated source edits do not change it

#### Scenario: Unsafe key inputs are unavailable
- **WHEN** a declared lockfile is missing, unreadable, a directory or reached through a symlink
- **THEN** no usable key is produced and no external symlink target is read as a lockfile

### Requirement: Trusted base warming

The harness SHALL execute snapshot warm argv only during driver base synchronization against a read-only extraction of the exact freshly fetched configured base SHA. It SHALL perform an initial snapshot sync before task/evidence dispatch for opted-in runs, honor requested pauses first, and reuse completed matching entries. It SHALL never warm a candidate, mutant, integrated run head or stale fetched ref, or warm inline during publish.

#### Scenario: Candidate changes cannot warm
- **WHEN** trusted base, candidate and integrated-head commits contain distinguishable warm scripts or lockfiles
- **THEN** `test_snapshot_warms_only_on_base_commit` verifies that initial and subsequent driver syncs execute only the freshly fetched base's script with base `{src}` and `{sha}`, and candidate verification and mutants never invoke warm

#### Scenario: Sync failure and publish preserve behavior
- **WHEN** initial snapshot-only fetching fails, a requested pause is pending, or a publish attempt needs base preparation
- **THEN** initial fetch failure records an event and permits existing task preparation, a requested pause runs no sync, and publish performs only its existing handoff without warming
- **THEN** final driver fetch failures retain `base_fetch` behavior and no previous fetched ref authorizes a warm

#### Scenario: Matching base reuses completed work
- **WHEN** a later base sync or restart has the same snapshot ID and lockfile key as a completed retained entry
- **THEN** it reuses that entry without executing warm again, even if unrelated base source changed

### Requirement: Immutable snapshot publication

Warming SHALL run with the existing prepare-mode network, source, environment, credential and write restrictions, expected exit zero, a 600-second timeout and process-group termination. Only successful validated dependency payloads SHALL be published atomically under the run state directory with harness-owned key/base metadata and read-only permissions. Ordinary candidate receipts SHALL exclude warm receipts.

#### Scenario: Warm is confined and payload is read only
- **WHEN** a warm command creates dependency files and attempts source, credential or outside-build access
- **THEN** allowed payload writes succeed, prohibited access remains denied, and a successful validated payload is published with execute bits preserved and all write bits removed
- **THEN** later prepare and verify commands cannot chmod or write the stored payload even through a manifest writable-parent exception

#### Scenario: Failed or interrupted warm is not reusable
- **WHEN** warming fails, signals, times out, produces a symlink/special-file/hard-linked payload, or stops before atomic publication
- **THEN** no partial entry is selectable, diagnostics are retained, descendants in the command group are terminated, and existing entries remain usable
- **THEN** a later locked sync cleans owned staging and may retry, while a crash after complete publication reuses that entry

### Requirement: Private snapshot consumption

Each fresh verification build SHALL clone every available matching snapshot with APFS `clonefile` into `{build}/snapshots/<id>` before preparation, expose that owner-writable path as `{snapshot:<id>}`, and preserve executable bits. Steps within that prepared build SHALL share its private clone. No clone SHALL share writable files with the store or another build, and build cleanup SHALL leave the store intact.

#### Scenario: Clones are writable and isolated
- **WHEN** two builds consume the same stored payload and one changes, removes or creates dependency files
- **THEN** `test_snapshot_clone_is_writable_and_isolated` verifies that only its clone changes, the stored payload and sibling remain unchanged, executable files stay executable, and cleanup removes only that build's clone
- **THEN** APFS integration uses the real clone API; unsupported-host skips are explicit and are not reported as proof of COW behavior

#### Scenario: Preparation and checks share the private path
- **WHEN** a prepare step and subsequent verify or acceptance step reference a declared snapshot
- **THEN** they receive the same private path for that execution, ordinary prepare receipts precede check receipts, and source and network restrictions remain unchanged

### Requirement: Snapshot fallback events

An absent, stale, invalid or unusable snapshot SHALL produce an empty writable directory at the same placeholder and continue existing prepare/check commands without invoking warm. The harness SHALL remove partial clones before fallback, record the ID, requested key, execution SHA/path and stable reason, and preserve original receipts and verdicts. It SHALL never use stale dependencies or silently implement a hit through ordinary copying.

#### Scenario: Missing or stale snapshot uses existing preparation
- **WHEN** the store lacks a matching entry, the candidate lockfile differs, its key is unavailable or the caller has no store context
- **THEN** `test_missing_snapshot_falls_back` verifies empty-path expansion, normal dependency fetch/copy preparation and checks, and an attributable fallback event with the appropriate reason
- **THEN** warm is never called, the old payload is not selected, and any ordinary prepare failure still stops subsequent checks

#### Scenario: Clone failure discards partial output
- **WHEN** clonefile is unavailable, fails across filesystems or fails partway through a payload, or entry validation fails
- **THEN** the consumer discards partial output, records `clone_failed` or `invalid`, and runs the normal sequence with an empty cache
- **THEN** inability to clear the partial destination raises an infrastructure error before commands run rather than supplying uncertain dependencies

#### Scenario: Legacy manifests keep their behavior
- **WHEN** a manifest declares no snapshots
- **THEN** existing fetch behavior, preparation, commands, variables, receipts, cleanup and verdicts remain unchanged with no snapshot events or store creation

### Requirement: Run snapshot retention

The harness SHALL retain the newest three completed snapshot entries in total per run, ordered by publication sequence across all IDs and keys. Reuse SHALL NOT refresh that order. Pruning SHALL occur under the run lock without active consumers, after successful publication and during recovery, with no other completed-snapshot eviction policy and no removal of logs or other runs' data.

#### Scenario: Fourth publication evicts oldest entry
- **WHEN** four distinct entries across one or more IDs publish successfully and an older entry has been reused
- **THEN** only the three newest publication sequences remain, reuse does not promote the old entry, and another run's entries and retained diagnostics remain untouched

#### Scenario: Interrupted retention recovers
- **WHEN** warming fails or pruning is interrupted or encounters an I/O error
- **THEN** failed warming evicts nothing, interrupted pruning resumes at the next locked sync, and pruning errors are recorded without claiming the three-entry bound was enforced
- **THEN** no age, size, access-time or cross-run eviction is performed and legacy runs with no store need no migration

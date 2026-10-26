## Purpose

Keep current command and rollout guidance consistent with executable behavior.

## ADDED Requirements

### Requirement: Unified command guidance

Active operator documentation SHALL use canonical command forms, explain the alias and bare-run exception, and preserve frozen-wrapper inventories and unrelated guidance.

#### Scenario: Guides distinguish daemon and build
- **WHEN** README, the build guide and command-JSON guide describe status, run and pause/resume
- **THEN** examples distinguish daemon controls from build controls and retain schemas, operational boundaries, issue citations and valid links
- **THEN** state guidance maps legacy build proposals/<change> to canonical drafts/<change> and distinguishes unchanged daemon proposals/<repository> receipts and jobs

#### Scenario: Executable examples retain regression coverage
- **WHEN** the keyed revision guide and direct supervisor README example switch to canonical commands
- **THEN** existing documentation tests select and execute the canonical revision example, preserving schema/table agreement, keyed replay and later-state snapshots, and changed-feedback refusal
- **THEN** direct supervisor assertions expect nightshift serve with unchanged configured executable/cwd/environment and shell-syntax coverage, while frozen-wrapper argv, bootstrap and credential assertions stay intact

#### Scenario: Example config points to daemon status
- **WHEN** config.example.toml explains daemon output and how to check alerting
- **THEN** both examples use nightshift daemon status and structural checks confirm daemon-status handler selection, while the complete TOML remains valid with unchanged settings, defaults and enrollment semantics
- **THEN** direct supervisor guidance uses nightshift serve and the comment examples follow the same deterministic command checks as the guides

### Requirement: Safe migration guidance

Setup and deployment guidance SHALL prescribe stopped-supervisor migration and inspection before restart/resume, preserve frozen-wrapper prerequisites, and identify live checks as human checks.

#### Scenario: Deployment and validation use safe controls
- **WHEN** an operator follows docs/deploy.md apply/restart and host checklist or docs/validation.md unload/reload procedure
- **THEN** guidance uses migrate while supervisor is stopped, inspection before restart, requested holds before supervisor startup and explicit resume afterwards; daemon pause survives reload via daemon-group commands

#### Scenario: Frozen inventories match the parser
- **WHEN** documentation command checks inspect the unchanged daemon and supervisor wrapper inventory
- **THEN** actual bare nightshift run and specloop serve argv match the inventory and parse, with bootstrap and credential assertions preserved and no required wrapper edit

#### Scenario: Documentation checks are structural
- **WHEN** touched documentation is checked under the design's physical-line extraction rule
- **THEN** all recognized inline/fenced CLI examples parse without dispatch or removed daemon forms, links resolve, ambiguity blocks validation and no exact-prose assertion is added

## MODIFIED Requirements

### Requirement: Active job status guidance

The root README and specloop guide SHALL describe active background rows in nightshift daemon status consistently with local job inventory.

#### Scenario: Operator reads job status
- **WHEN** a reader follows status instructions
- **THEN** the docs explain job ID, stage, repository/item, elapsed and remaining or overdue time, legacy timing unavailability, and historical inspection through `nightshift jobs --all`
- **THEN** they preserve the distinction between Nightshift job status, daemon liveness and independent specloop run status

### Requirement: Worker retirement guidance

Current guidance in allowed documentation SHALL explain sole-engine enrollment and worker retirement, distinguish retained history from execution, and document compatibility boundaries and human rollout checks without claiming unperformed validation.

#### Scenario: New enrollment follows one engine
- **WHEN** an operator follows README, config.example.toml and the specloop guide
- **THEN** the runnable example enrolls a specloop repository with a trusted manifest and explains omission defaults, explicit worker rejection and inert obsolete settings
- **THEN** it explains repository-local holds for missing or unusable manifests and removal of worker status output, including the off-subscription model report, while all other daemon status output remains unchanged under nightshift daemon status
- **THEN** direct Python front-door instructions do not require worker verification strings, endpoint models or OAuth; manifest providers retain their own requirements
- **THEN** bundled service instructions explicitly retain the frozen daemon wrapper's env-file, worker OAuth, GH_TOKEN and gh/claude/pnpm/node/git/swift checks, distinguishing direct invocation from service startup without claiming Python changes bypass shell checks

#### Scenario: Existing installation is checked before merge
- **WHEN** an operator reads retirement and rollout guidance
- **THEN** it identifies retained historical attention/recovery, the canonical nightshift CLI/state root and one-release legacy compatibility, separate daemon/supervisor jobs and the need to inspect live config/plists/external callers before merge
- **THEN** it explains the CLI merge and state migration delivered by #40, preserves unrelated limitations, and cites issue #16 without inventing an implementation PR
- **THEN** live config, plist and external-caller inspection remains a pre-merge human checklist, never a claimed CI result
- **THEN** it describes guarded migration without claiming host checks performed, safety on the live machine, rollback of remote effects or automatic restart of interrupted worker work
- **THEN** it identifies obsolete worker instructions/metadata in frozen CLAUDE.md and pyproject.toml, historical worker documents and their removed-source references, and resolved type-baseline entries as deferred cleanup outside the implementation allowance

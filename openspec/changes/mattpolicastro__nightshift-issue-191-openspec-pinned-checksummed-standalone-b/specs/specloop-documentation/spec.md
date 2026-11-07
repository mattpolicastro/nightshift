## Purpose

Keep operator installation, format and host guidance consistent with pinned standalone OpenSpec and explicit deployment evidence limits.

## ADDED Requirements

### Requirement: Standalone OpenSpec operator guidance

The prerequisite, engine, deployment, platform and plugin guides SHALL consistently describe the standalone OpenSpec pin, trusted executable selection, preflight checksum/version checks, install refusals and timeout-only transience. They SHALL distinguish OpenSpec's removal of Node/npm dependency from the plugin's retained Node setting and SHALL preserve concurrent unrelated documentation.

#### Scenario: Operator installs and verifies the release
- **WHEN** an operator follows `docs/deploy.md#install-openspec` from the prerequisite or host guidance
- **THEN** instructions identify the official 1.16.1 archive for the host, verify its SHA-256 against SHA256SUMS before extraction, place the executable at the configured path and run its version and preflight checks
- **THEN** archive and executable digests are distinguished, runtime acquisition/fallback is absent, and affected consumers must be restarted after replacement

#### Scenario: Operator understands stricter drafts and rollout
- **WHEN** an operator reads the validator and deployment guidance
- **THEN** it explains missing WHEN/GIVEN or THEN and unknown requirements-header failures, accepted closing hashes and the retained harness scenario lint
- **THEN** pre-merge validation of throwaway main and Scheming Vizier, pre-deploy validation of both enrolled checkouts and the Mac Studio admission/finish cut-over remain explicit pending human checks recorded on #191/#184
- **THEN** archived failures are informational and offline tests do not claim live deployment success

## MODIFIED Requirements

### Requirement: Host portability guidance

`KNOWN-LIMITATIONS.md`'s platform section SHALL document host path configuration consistently with the setup guides and example config. Guidance SHALL preserve unrelated assertions and citations and distinguish automated configuration checks from real second-install validation.

#### Scenario: Operator configures an alternate layout
- **WHEN** an operator follows the setup and platform guidance
- **THEN** they can identify `NIGHTSHIFT_BIN_DIR`, `NIGHTSHIFT_NODE_BIN`, `NIGHTSHIFT_UV`, `NIGHTSHIFT_CHECKOUT` and the standalone validator's `NIGHTSHIFT_OPENSPEC_BIN`, their defaults, applicable consumers and how to supply applicable settings to Python and Paseo processes, including inherited service environments, the existing daemon env file's precedence for downstream settings and required process restarts
- **THEN** examples in the root README, preparation guide, plugin README and example config agree with the platform guidance on configurable checkout/tool paths and `[daemon].worktree_root`, including its default and compatibility-only role after worker retirement
- **THEN** guidance explains literal path handling and the changed relative-root anchor, and retains manifest preparation-read restrictions

#### Scenario: Platform claims retain their evidence limits
- **WHEN** the platform limitations are updated after implementation tests pass
- **THEN** fixed-location claims for Python and plugin consumers become documented defaults with overrides, while macOS sandbox-exec, launchd, Paseo-host and second-install validation limits remain explicit
- **THEN** guidance cites issue #15 without an invented implementation PR, preserves unrelated limitations and marks unperformed second-install checks as pending

#### Scenario: Frozen service wrappers remain explicit
- **WHEN** an operator reads setup examples and platform limitations for the new host settings
- **THEN** guidance names `bin/nightshift-daemon` and `bin/specloop-serve` as wrappers with fixed bootstrap checkout, uv and remaining PATH locations, explicitly noting only the supervisor's removal of ~/n/bin and the associated OpenSpec comment change, and says environment settings do not relocate those wrapper commands
- **THEN** guidance describes wrapper portability as deferred because `bin/**` remains frozen apart from that narrow supervisor PATH/comment exception, distinguishes direct CLI configuration from bundled service startup, and does not claim a new helper or relocated wrapper pathname solves the limitation
- **THEN** full daemon rollout remains pending separate wrapper work or an independently provisioned operator service preserving credential and preflight guards
- **THEN** the same freeze covers launchd plists and all other wrapper comments; current guidance explains the retained worker startup prerequisites and real pause/supervisor boundaries without promising those files were corrected

### Requirement: Format selection guidance

The engine guide, command/manifest guide and self-hosting example SHALL
document `[run] format`, its OpenSpec default and spec-kit's admission refusal.
Guidance SHALL preserve existing workflows, cite #187 and #184, describe the separate #191 standalone binary replacement, and distinguish offline compatibility checks from deployed resume.

#### Scenario: Operator selects the supported format
- **WHEN** an operator reads How the engine works in docs/specloop/README.md, docs/specloop/command-json.md and examples/specloop/self-hosting.toml
- **THEN** the formats paragraph and manifest guidance explain omitted or explicit `openspec`, the accepted but unavailable `speckit` value, the exact refusal message and nonzero JSON admission outcome
- **THEN** the example parses with `format = "openspec"`, existing manifests need no migration, and no text claims the seam itself implements spec-kit or replaces the validator; standalone OpenSpec 1.16.1 installation is attributed separately to #191 and the obsolete current-npx guarantee is removed

#### Scenario: Compatibility claims retain their limits
- **WHEN** format documentation and the implementation PR are reviewed
- **THEN** existing OpenSpec, finish, human-check and recovery guidance remains true, unrelated limitations and concurrent files remain untouched, and live legacy-run resume is identified as a pending human check recorded on #184
- **THEN** any structural documentation checks use deterministic physical-line boundaries, block missing or ambiguous boundaries and avoid semantic Markdown parsing and exact-prose assertions

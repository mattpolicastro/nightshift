## Purpose

Explain the engine format seam and its compatibility boundary without
presenting future spec-kit support as shipped.

## ADDED Requirements

### Requirement: Format selection guidance

The engine guide, command/manifest guide and self-hosting example SHALL
document `[run] format`, its OpenSpec default and spec-kit's admission refusal.
Guidance SHALL preserve existing workflow and binary requirements, cite #187
and #184, and distinguish offline compatibility checks from deployed resume.

#### Scenario: Operator selects the supported format
- **WHEN** an operator reads How the engine works in docs/specloop/README.md, docs/specloop/command-json.md and examples/specloop/self-hosting.toml
- **THEN** the formats paragraph and manifest guidance explain omitted or explicit `openspec`, the accepted but unavailable `speckit` value, the exact refusal message and nonzero JSON admission outcome
- **THEN** the example parses with `format = "openspec"`, existing manifests need no migration, and no text claims the seam implements spec-kit or changes npx invocation

#### Scenario: Compatibility claims retain their limits
- **WHEN** format documentation and the implementation PR are reviewed
- **THEN** existing OpenSpec, finish, human-check and recovery guidance remains true, unrelated limitations and concurrent files remain untouched, and live legacy-run resume is identified as a pending human check recorded on #184
- **THEN** any structural documentation checks use deterministic physical-line boundaries, block missing or ambiguous boundaries and avoid semantic Markdown parsing and exact-prose assertions

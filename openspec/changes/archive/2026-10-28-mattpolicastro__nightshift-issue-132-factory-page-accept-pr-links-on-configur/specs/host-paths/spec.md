## Purpose

Resolve the Factory operator's forge-origin file using the existing trusted, literal host-path rules without changing other consumers.

## ADDED Requirements

### Requirement: Factory forge origins file path

Factory status SHALL resolve `NIGHTSHIFT_FORGE_ORIGINS_FILE` from the trusted process environment, defaulting to `~/.config/nightshift/forge-origins`. Resolution SHALL follow the existing Literal path resolution requirement and SHALL NOT accept RPC-supplied paths. Other consumers SHALL NOT validate this unused setting. Invalid settings SHALL retain existing sanitized plugin configuration-failure behavior.

#### Scenario: Default and overridden origins paths
- **WHEN** Factory status resolves an unset setting, an absolute override, or a leading-home override containing spaces and literal shell metacharacters
- **THEN** it selects the default under the host home or the exact override with only leading `~/` expanded, without shell evaluation, path normalization, filesystem probing or directory creation
- **THEN** the existing `resolveHostPaths()` return shape and other command settings remain unchanged

#### Scenario: Invalid origins paths refuse status
- **WHEN** the setting is blank, relative, an unsupported tilde form, or contains NUL or a line break
- **THEN** Factory status fails before command execution, retaining its previous snapshot marked stale or reporting sanitized unavailable when cold, without exposing the supplied path
- **THEN** attention, ordinary act and landing operations that do not consume this setting remain unaffected, and RPC fields cannot override it

## Purpose

Describe optional verification loopback access accurately, with executable configuration checks and explicit limits on automated evidence.

## ADDED Requirements

### Requirement: Verification loopback guidance

`README.md`, `KNOWN-LIMITATIONS.md` and `docs/specloop/README.md` SHALL correct invalidated verification claims, preserve unrelated guidance and cite issue #151. They SHALL explain opt-in loopback, launch-time exclusions, unchanged defaults/preparation and failure/timing limits. The guide SHALL provide a structurally tested Flutter fragment using step env. Real Flutter success and host-service exclusion SHALL remain pending human checks until performed.

#### Scenario: Operator configures a Flutter test step
- **WHEN** the guide's example is extracted using the design's exact physical-line marker and delimiter rules and parsed with the real TOML and manifest parsers
- **THEN** its analysis step remains unflagged, its test step has loopback true, both use the direct tool snapshot and string-valued FLUTTER_ALREADY_LOCKED env setting, and placeholders target the configured SDK and prepared project without executing Flutter
- **THEN** malformed or ambiguous boundaries block validation regardless of surrounding Markdown, HTML or indentation context, and existing env-example checks remain intact
- **THEN** the scoped prose explains protected launch-time ports, services starting afterwards, failure diagnostics, nested-sandbox limits and the human host checklist without claiming CI performed it
- **THEN** citations identify issue #151 without inventing an implementation PR, and the guide does not claim the IPv4 spike established IPv6 Flutter operation

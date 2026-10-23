## Purpose

Document host path configuration and its remaining platform and real-installation limits without changing unrelated workflow guidance.

## ADDED Requirements

### Requirement: Host portability guidance

`KNOWN-LIMITATIONS.md`'s platform section SHALL document host path configuration consistently with the setup guides and example config. Guidance SHALL preserve unrelated assertions and citations and distinguish automated configuration checks from real second-install validation.

#### Scenario: Operator configures an alternate layout
- **WHEN** an operator follows the setup and platform guidance
- **THEN** they can identify `NIGHTSHIFT_BIN_DIR`, `NIGHTSHIFT_NODE_BIN`, `NIGHTSHIFT_UV` and `NIGHTSHIFT_CHECKOUT`, their defaults, applicable consumers and how to supply them to the daemon, supervisor and Paseo host, including the daemon env file's precedence and service restarts
- **THEN** examples in the root README, preparation guide, plugin README and example config agree with the platform guidance on configurable checkout/tool paths and `[daemon].worktree_root`, including its default
- **THEN** guidance explains literal path handling and the changed relative-root anchor, and retains manifest preparation-read restrictions

#### Scenario: Platform claims retain their evidence limits
- **WHEN** the platform limitations are updated after implementation tests pass
- **THEN** fixed-location claims become documented defaults with overrides, while macOS sandbox-exec, launchd, Paseo-host and second-install validation limits remain explicit
- **THEN** guidance cites issue #15 without an invented implementation PR, preserves unrelated limitations and marks unperformed second-install checks as pending

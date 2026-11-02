## Purpose

Explain source-local authentication for run base fetch, publish and finish in the existing engine guide without changing unrelated operator guidance.

## ADDED Requirements

### Requirement: Source local credential guidance

The explicit-publication guide SHALL describe ordered, ephemeral source-local helper forwarding for base fetch, publish and finish, separate from forge API credentials. It SHALL preserve existing command examples and boundaries and identify live Gitea validation as a human check.

#### Scenario: Operator publishes and finishes with local helpers
- **WHEN** an operator follows the explicit-publication passage in docs/specloop/README.md
- **THEN** it explains forwarding including empty resets via the Git config environment, no helper copies into repo.git/config, unchanged gh argv and no-helper behavior, and helper path/host credential-store prerequisites
- **THEN** it cites issue #174 and states that both publish and finish use the source helpers; finish still archives and marks ready without merging, and daemon/land boundaries remain unchanged
- **THEN** the unique physical heading bounds the edit as defined in the design; missing, duplicate or indented starts block structural selection, and concurrent steering-file guidance, command examples and links remain intact

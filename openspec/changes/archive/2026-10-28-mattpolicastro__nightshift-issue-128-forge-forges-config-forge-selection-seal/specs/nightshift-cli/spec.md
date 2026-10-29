## Purpose

Prevent daemon status from querying Gitea enrollment through GitHub while
preserving existing read-only status behavior.

## ADDED Requirements

### Requirement: Forge aware daemon status

Daemon status SHALL query GitHub only for rows with implicit GitHub or an
explicit GitHub-kind forge. It SHALL list Gitea rows as `not queried (gitea)`.
The change SHALL remain confined to that repository filter, preserving liveness,
alerts, jobs, GitHub details and exit codes.

#### Scenario: Gitea status is explicitly unqueried
- **WHEN** daemon status reads mixed GitHub and Gitea enrollment
- **THEN** each Gitea row prints `not queried (gitea)` and invokes no ready, blocker or contradiction query
- **THEN** GitHub rows retain existing counts, details and diagnostics, with no remote writes

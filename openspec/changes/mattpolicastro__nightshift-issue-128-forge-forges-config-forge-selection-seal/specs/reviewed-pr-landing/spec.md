## Purpose

Keep GitHub landing authorization confined to GitHub enrollment.

## ADDED Requirements

### Requirement: GitHub kind enrollment boundary

`enrolled_repo` SHALL count only rows with no forge selection or a selected
forge of kind github when authorizing GitHub landing. The function SHALL retain
its existing identity and ambiguity diagnostics. No other landing behavior or
mutation boundary SHALL change.

#### Scenario: Gitea enrollment grants no GitHub authority
- **WHEN** landing resolves a GitHub PR whose owner/name appears only in a Gitea enrollment
- **THEN** it refuses as not enrolled before any mutation, even if the names match exactly
- **THEN** legacy and explicitly GitHub-kind rows remain eligible for all existing gates, and existing invalid-identity and ambiguous-enrollment assertions still pass

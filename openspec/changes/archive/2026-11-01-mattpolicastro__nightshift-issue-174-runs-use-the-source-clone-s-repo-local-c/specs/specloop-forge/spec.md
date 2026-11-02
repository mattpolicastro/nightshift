## Purpose

Qualify Git transport compatibility for source-local helper forwarding while preserving forge selection, API commands and publication recovery.

## MODIFIED Requirements

### Requirement: Manifest forge selection

The engine SHALL select its forge from `[run] github`. No host or `github.com` SHALL retain byte-identical `gh` argv and no-helper Git transport. Helper-bearing Git transport SHALL follow publish-base-sync credential requirements. Other hosts SHALL uniquely match configured Gitea URL hostnames and construct GiteaForge using trusted configuration and bot credentials. Invalid or ambiguous selection SHALL fail without GitHub fallback.

#### Scenario: GitHub stays compatible
- **WHEN** the manifest names `owner/repo` or `github.com/owner/repo`
- **THEN** publication, finish and critic retain their existing `gh` argv, including whether `--hostname` is present, without requiring forge configuration or loading Gitea credentials
- **THEN** no-helper Git transport stays unchanged; helper-bearing fetch and push use config environment forwarding, and helper-bearing pushes add SAFE_FLAGS without putting helpers in argv

#### Scenario: Gitea host selects configured credentials
- **WHEN** `forge.example/owner/repo` uniquely matches a Gitea definition with URL `https://forge.example:8443/git`
- **THEN** the engine constructs GiteaForge with that URL, bot token, bot login, trusted logins and WIP prefix, making no request during construction
- **THEN** configuration comes from process-selected `NIGHTSHIFT_FORGE_CONFIG` or the installed default, and the trusted env file overrides the process value of the selected bot variable without exporting secrets

#### Scenario: Invalid selection refuses
- **WHEN** a non-GitHub host has no match, multiple matching definitions, unreadable or invalid configuration, or missing/empty bot credentials
- **THEN** publish and finish fail before pushes or API writes with sanitized diagnostics, while critic returns unavailable-context notes
- **THEN** neither a manifest env file nor a landing token nor ambient `gh` credentials supplies a fallback

### Requirement: Gitea draft publication

Publish SHALL retain the existing readiness, base-fetch, PR-body and human-checks contracts, with git-push helper forwarding as defined in publish-base-sync. Gitea publication SHALL reuse a valid recorded PR or find an open same-repository PR with exact head and base refs through complete forge reads before creating one. Creation SHALL use GiteaForge.create_draft_pr and the configured WIP prefix; failed lookup SHALL never authorize creation.

#### Scenario: New draft opens with WIP title
- **WHEN** a ready Gitea run passes origin, base and workflow checks and no matching PR exists
- **THEN** the branch is pushed with source-local helper config environment when present, one WIP-prefixed PR is created with the current title/body/base/head, its URL is saved and the run pauses at `human_checks`
- **THEN** no-helper push argv remains unchanged; helper-bearing push adds SAFE_FLAGS while preserving destination and refspec

#### Scenario: Existing PR is reused
- **WHEN** a run has a valid recorded PR, or a complete listing finds one open PR with the run's head repository and exact head/base refs
- **THEN** publication pushes and reuses that PR without changing its title/body or creating another
- **THEN** fork PRs sharing branch names and PRs for other bases do not match; multiple exact matches or foreign recorded URLs refuse

#### Scenario: Interrupted creation is reconciled
- **WHEN** creation fails or its response/state save is lost
- **THEN** the invocation sends no second POST, and a later publish with no saved PR repeats a complete matching lookup before considering creation
- **THEN** a failed, malformed or incomplete lookup prevents POST and leaves publication retryable; a visible earlier create is reused

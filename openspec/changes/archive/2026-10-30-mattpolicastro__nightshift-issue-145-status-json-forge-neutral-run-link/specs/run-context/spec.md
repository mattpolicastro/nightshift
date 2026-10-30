## Purpose

Expose a forge-neutral change-folder link in status JSON for published non-GitHub runs while preserving the existing GitHub context contract.

## ADDED Requirements

### Requirement: Forge neutral status link

Named and all-run `status --json` SHALL add string `context.link` to readable non-GitHub contexts. Published runs SHALL link to the resolved change directory on their manifest branch using the uniquely matched configured Gitea instance and its branch-view route; unavailable or unpublished links SHALL be empty. Status SHALL preserve GitHub contexts, all existing fields, version, ordering, exit codes, error isolation and credential-free, network-free, read-only behavior.

#### Scenario: Run forge selects destination
- **WHEN** named or all-run JSON status reads a run with a saved PR, manifest repository `forge.example/Owner/Repo`, branch `spec/change`, live change `change`, and a uniquely matching Gitea instance URL `https://forge.example:8443/git`
- **THEN** `context.link` equals `https://forge.example:8443/git/Owner/Repo/src/branch/spec/change/openspec/changes/change` and `context.github` remains empty
- **THEN** selection is case-insensitive for hostnames, follows the explicit absolute config override when set or the default otherwise, and distinguishes runs using different configured hosts independently of table order or PR URL
- **THEN** repository spelling, configured prefix and port are retained, reserved characters in appended paths are URL-quoted, and slash-separated branches are preserved

#### Scenario: GitHub context remains compatible
- **WHEN** JSON status reads published or unpublished runs naming `Owner/Repo`, `github.com/Owner/Repo` or a case variant of `github.com`, or a run naming no repository
- **THEN** each existing context is unchanged, including the published GitHub `/tree/` URL, empty links where previously empty, and absence of `context.link`
- **THEN** these runs require no forge configuration or credentials, even when the forge-config override is invalid

#### Scenario: Unpublished forge run has no destination
- **WHEN** JSON status reads a non-GitHub manifest run with no saved PR
- **THEN** `context.link` and `context.github` are empty and every other status field retains its existing value
- **THEN** it makes no remote branch probe or credential lookup

#### Scenario: Unavailable forge metadata preserves context
- **WHEN** a published non-GitHub run has an invalid repository identity, missing/unreadable/invalid config, a blank or relative config override, no matching Gitea definition or multiple definitions matching its host
- **THEN** its `context.link` is empty without a guessed GitHub or PR-derived destination
- **THEN** its existing context and status fields remain intact, healthy neighboring runs remain visible, and the command retains its existing JSON envelope and successful collection exit code

#### Scenario: Archived change uses resolved directory
- **WHEN** JSON status resolves a published non-GitHub run's tasks from its exact-change archive after finish has archived them, including a failed external finish push
- **THEN** `context.link` uses the parent of that resolved archived task path on the manifest branch instead of the former live change directory
- **THEN** the existing head, task counts, saved PR, pause and actions remain unchanged and status does not retry finish

#### Scenario: Link lookup remains read only
- **WHEN** named and all-run JSON status generate non-GitHub links with valid configuration and no bot or landing credentials
- **THEN** links are produced without loading credential files, constructing authenticated forge clients, invoking `gh` or making network requests
- **THEN** persisted state, events and refs remain unchanged, and removing only the additive `context.link` from the response leaves all preexisting status fields unchanged
- **THEN** unrelated context/core-read failures retain their existing fallback/error rows without a new `link` key, and non-JSON status remains unchanged

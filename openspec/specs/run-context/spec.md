# run-context Specification

## Purpose
TBD - created by archiving change factory-context. Update Purpose after archive.

## Requirements

### Requirement: Context in status
`specloop status --json` SHALL include a `context` object for every run with the
change summary, repository, branch, current task, full pause detail, the last five
events in plain words, and a GitHub link to the change folder when the branch is
published.

#### Scenario: Paused run has context
- **WHEN** a run with a proposal whose Why section begins "The page needs links." is paused stuck on task 1.2 with detail "Failed twice: x"
- **THEN** its context has summary "The page needs links.", task label "1.2" with that task's text, detail "Failed twice: x", and the run's repository and branch

#### Scenario: Recent events in plain words
- **WHEN** a run's events include task 1.1 landing and then task 1.2's first attempt failing
- **THEN** its context lists "task 1.2 attempt 1 failed" before "task 1.1 landed", with at most five entries

#### Scenario: GitHub link only when published
- **WHEN** one run has a PR and its manifest names GitHub repository owner/repo, and another run has no PR
- **THEN** the first run's context links to https://github.com/owner/repo/tree/<branch>/openspec/changes/<change> and the second run's link is empty

#### Scenario: Broken context does not break status
- **WHEN** a run's branch has no proposal.md
- **THEN** status still succeeds and that run's summary is empty

### Requirement: Details in the Factory section

Each Factory row SHALL offer a collapsed Details section that shows the run's context, a link on GitHub or a configured forge origin when present, the command that shows more on the Studio, and an Open in Paseo button when a Paseo workspace for the run's repository exists. Existing labels and other Details behavior SHALL remain unchanged.

#### Scenario: Workspace matched for Open in Paseo
- **WHEN** workspaces include one being archived and an active one whose workspaceDirectory, with a leading /Users/<name> written as ~, equals the run's repo
- **THEN** the active workspace is chosen, and no workspace is chosen when only ones being archived match

#### Scenario: Context redacted and links checked
- **WHEN** a status context has a repo under the home directory and a github link outside the allowed HTTPS origins or containing credentials
- **THEN** the projected repo starts with ~ and the github link is dropped
- **THEN** GitHub remains allowed and configured origins come only from the operator file used for this status load

#### Scenario: More command offered
- **WHEN** Details is shown for change factory-context
- **THEN** it offers the command `nightshift status factory-context --events 20`

### Requirement: Configured forge context links

Factory SHALL apply the same per-load origin allowlist to `context.github` as to PR display validation, while retaining context-link redaction and rejecting credentials. Context links SHALL NOT be restricted to PR path shapes and SHALL retain existing query/fragment handling. This SHALL NOT change status JSON producers or introduce a new field.

#### Scenario: Configured context destination retained
- **WHEN** an otherwise valid status contains a context link on an allowed HTTPS forge origin, including a change-folder path with a query or fragment
- **THEN** Factory preserves that destination after existing redaction in `context.github`
- **THEN** malformed, credential-bearing or unallowed context links are blanked without failing an otherwise valid status read

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

## Purpose

Permit explicitly configured forge destinations in Factory's existing context display field while preserving the status producer contract and other Details behavior.

## ADDED Requirements

### Requirement: Configured forge context links

Factory SHALL apply the same per-load origin allowlist to `context.github` as to PR display validation, while retaining context-link redaction and rejecting credentials. Context links SHALL NOT be restricted to PR path shapes and SHALL retain existing query/fragment handling. This SHALL NOT change status JSON producers or introduce a new field.

#### Scenario: Configured context destination retained
- **WHEN** an otherwise valid status contains a context link on an allowed HTTPS forge origin, including a change-folder path with a query or fragment
- **THEN** Factory preserves that destination after existing redaction in `context.github`
- **THEN** malformed, credential-bearing or unallowed context links are blanked without failing an otherwise valid status read

## MODIFIED Requirements

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

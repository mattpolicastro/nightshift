## Purpose

Offer canonical detail commands while retaining context, links and navigation.

## MODIFIED Requirements

### Requirement: Details in the Factory section
Each Factory row SHALL offer a collapsed Details section that shows the run's
context, the GitHub link when present, the command that shows more on the Studio,
and an Open in Paseo button when a Paseo workspace for the run's repository exists.

#### Scenario: Workspace matched for Open in Paseo
- **WHEN** workspaces include one being archived and an active one whose workspaceDirectory, with a leading /Users/<name> written as ~, equals the run's repo
- **THEN** the active workspace is chosen, and no workspace is chosen when only ones being archived match

#### Scenario: Context redacted and links checked
- **WHEN** a status context has a repo under the home directory and a github link that is not an https://github.com/ URL
- **THEN** the projected repo starts with ~ and the github link is dropped

#### Scenario: More command offered
- **WHEN** Details is shown for change factory-context
- **THEN** it offers the command `nightshift status factory-context --events 20`


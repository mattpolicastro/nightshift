## Purpose

Keep Factory publication confirmations accurate when its shared engine selects
GitHub or Gitea, preserving existing confirmation and landing boundaries.

## ADDED Requirements

### Requirement: Forge publication confirmation

Factory Publish and Finish SHALL warn that they push to the forge selected by the run manifest, including before a PR exists. They SHALL retain confirmation before dispatch, no send on Cancel, existing action availability, pause binding and duplicate/pending guards. Display URLs SHALL NOT select the write destination. GitHub-only Land and existing display-origin validation SHALL remain unchanged.

#### Scenario: Confirmation works before a PR exists
- **WHEN** Publish or Finish is selected for an eligible run with no PR URL or with a GitHub PR
- **THEN** the warning is `<Action> <change>? This pushes to the forge selected by the run manifest.` and opening or cancelling it sends no action
- **THEN** confirmation sends only the existing authorized act request, with its pause identity and pending/duplicate protections, without new host fields or lookups

#### Scenario: Gitea uses the shared action path
- **WHEN** a displayed run has a configured Gitea PR and its allowed Publish or Finish action is confirmed
- **THEN** the same manifest-based warning precedes the unchanged literal `nightshift act` invocation and the engine selects and validates the forge
- **THEN** Finish removes the Gitea WIP prefix through the engine and does not merge; Gitea has no usable Land action and display configuration does not authorize writes

## MODIFIED Requirements

### Requirement: Confirmation button presentation

Confirmation controls SHALL keep padded, rounded, minimum 44-by-44 buttons. Confirm SHALL be primary for Publish/Finish and destructive for Abort; Cancel SHALL be outlined. Publish/Finish messages SHALL follow Forge publication confirmation. All other messages, labels, roles, disabled conditions and confirmation semantics SHALL remain unchanged, including no send before confirmation or on Cancel.

#### Scenario: Confirmation styles preserve the action gate
- **WHEN** Publish, Finish, or Abort is pressed
- **THEN** its confirmation appears without sending an action, Confirm has the appropriate primary or destructive rendered style, and Cancel has the outlined rendered style
- **THEN** only the Publish/Finish warning text changes; Abort, accessibility labels, minimum targets, theme colors and disabled conditions remain unchanged
- **WHEN** Cancel is pressed, or the action is reopened and Confirm is pressed
- **THEN** Cancel sends nothing, and Confirm dispatches only the existing authorized request with existing pending-action guards

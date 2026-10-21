## Purpose

Give the Factory section a consistent visual hierarchy using host theme tokens
while preserving its existing run, action, navigation, and disclosure behavior.

## ADDED Requirements

### Requirement: Action button presentation

Factory run actions SHALL be padded, rounded buttons with minimum width and
height of 44 points in wrapping groups. The first listed verb SHALL be primary,
Abort SHALL always be destructive, and remaining verbs SHALL be outlined,
without changing action availability, order, accessibility, or behavior.

#### Scenario: Primary destructive and outlined actions
- **WHEN** a paused run lists Resume, Accept, and Abort, or a non-abort verb is first in another allowed action list
- **THEN** the first verb uses an `accent` fill and `accentForeground` text, Abort uses `statusDanger` text and border on `surface1`, and other verbs use a `border` outline with `foreground` text on `surface1`
- **THEN** the group wraps and each button has minimum width and height 44, horizontal and vertical padding, rounded corners, and unchanged visible labels, accessibility attributes, disabled conditions, and dispatch behavior
- **THEN** the supplied action list and order are unchanged

#### Scenario: Abort takes precedence over primary
- **WHEN** Abort is the first or only listed verb, or there are no listed verbs
- **THEN** Abort remains destructive without promoting any later verb to primary, and an empty list renders no run action buttons

### Requirement: Confirmation button presentation

Confirmation controls SHALL use the same padded, rounded, minimum 44-by-44
button treatment as run actions. Confirm SHALL be primary for Publish and
Finish and destructive for Abort; Cancel SHALL be outlined. Existing messages,
labels, roles, disabled conditions, and confirmation semantics SHALL remain
unchanged, including no send before confirmation and no send on Cancel.

#### Scenario: Confirmation styles preserve the action gate
- **WHEN** Publish, Finish, or Abort is pressed
- **THEN** its existing confirmation appears without sending an action, Confirm has the appropriate primary or destructive rendered style, and Cancel has the outlined rendered style
- **WHEN** Cancel is pressed, or the action is reopened and Confirm is pressed
- **THEN** Cancel sends nothing, and Confirm dispatches only the existing authorized request with existing pending-action guards

### Requirement: Secondary navigation group

Each card SHALL place all currently visible Open PR, Open change on GitHub,
and Open in Paseo controls, in that order, in one wrapping secondary row
separate from run actions and confirmation buttons. The links SHALL use accent
text with underlining and no action-button fill or enclosing button border.
Their existing labels, roles, destinations, errors, and visibility conditions
SHALL remain unchanged. An empty navigation row SHALL be omitted.

#### Scenario: Links share a group apart from actions
- **WHEN** a run has a PR and change URL, Details is opened, and a matching supported Paseo workspace is found
- **THEN** the three navigation items share one wrapping row outside the action and confirmation groups, use link-styled text, and retain their original labels and roles, including the button role for Open in Paseo
- **THEN** pressing them opens the same URLs or host workspace and sends no run action

#### Scenario: Grouping preserves link availability
- **WHEN** Details is collapsed, reopened, or closed after workspace loading, or URLs/workspace/navigation support are absent
- **THEN** Open PR appears exactly when its PR exists, the other two controls appear only while Details is open and their existing conditions hold, and the row is absent when no items are visible
- **THEN** grouping causes no additional workspace lookup or RPC and the existing Details fields, events, and command remain available when expanded

### Requirement: Status chip presentation

Each visible card SHALL show a rounded, status-colored chip on `surface2`
immediately below the change title, beside muted task progress, preserving the
existing status/progress text, run ordering, and finished visibility.

#### Scenario: Chip follows all run statuses
- **WHEN** paused and running runs are visible and the finished disclosure is expanded to show done and aborted runs
- **THEN** chip text uses `statusWarning` for paused, `accent` for running, `statusSuccess` for done, and `foregroundMuted` for aborted, on a rounded `surface2` background
- **THEN** adjacent task progress uses `foregroundMuted` and the combined text remains exactly `<status> · <done>/<total> tasks done`, without duplicate or hidden substitute text
- **THEN** run ordering is unchanged and finished cards remain hidden until their disclosure is expanded

### Requirement: Secondary disclosure presentation

Details and Finished SHALL be secondary disclosure rows with muted semibold
labels and state-dependent chevrons, preserving every existing accessibility
label, role, expanded state, tested text, and toggle behavior.

#### Scenario: Disclosures decorate existing state
- **WHEN** Details or Finished is opened and closed
- **THEN** its chevron reflects the existing expanded state while its label, button role, and accessibility state remain unchanged and its text remains secondary to action buttons
- **THEN** Details keeps its existing `▴`/`▾` text, and Finished retains the exact descendant text `Finished (N)` with a non-text decorative chevron
- **THEN** Finished starts collapsed, its toggling causes no RPC or workspace lookup, and both disclosure rows retain paddingVertical 10, with Finished retaining alignSelf flex-start and label fontWeight 600

### Requirement: Secondary refresh presentation

Refresh Factory SHALL be a compact secondary button with a surface background,
border, padding, rounded corners, and a minimum 44-by-44 target, preserving its
existing text, accessibility attributes, disabled conditions, and behavior.

#### Scenario: Refresh remains a secondary button
- **WHEN** Factory is idle, loading, or sending an action
- **THEN** Refresh Factory has a rounded surface2 background, border-colored outline and foreground label with a minimum 44-by-44 target, retaining its original accessibility label and button role
- **THEN** its existing Refresh Factory or Checking… text, disabled conditions, and refresh handler are preserved

### Requirement: Host theme presentation

Factory presentation SHALL obtain all colors from the allowed host tokens:
`accent`, `accentForeground`, `surface0`, `surface1`, `surface2`, `border`,
`foreground`, `foregroundMuted`, `statusSuccess`, `statusWarning`, and
`statusDanger`. It SHALL introduce no hard-coded or derived color values and
SHALL resolve styling from the current theme without changing interactions.

#### Scenario: Rendered styles follow the supplied theme
- **WHEN** Factory renders with distinct token values and then a different supplied theme
- **THEN** action, confirmation, navigation, chip, disclosure, and refresh styles use the corresponding current tokens rather than fixed color values
- **THEN** their existing accessibility attributes and visible text remain unchanged

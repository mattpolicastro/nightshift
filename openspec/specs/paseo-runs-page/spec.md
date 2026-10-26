# paseo-runs-page Specification

## Purpose
The Factory section of the Nightshift Paseo page: specloop runs, and acting on
the ones waiting for Matt.

## Requirements

### Requirement: Runs listed
The page SHALL list each specloop run with its change, status, tasks done/total
and PR link, putting runs that wait on a human first, with their action headline
and why.

#### Scenario: Waiting run first
- **WHEN** one run is running and another is paused at a phase gate
- **THEN** the paused run is listed first with its headline

### Requirement: Buttons fit the pause
The page SHALL offer exactly the verbs the run's action lists, with a note field
for resume, and SHALL ask for confirmation before publish, finish or abort.

#### Scenario: Publish needs confirmation
- **WHEN** a run is ready to publish and its Publish button is pressed
- **THEN** nothing is sent until the confirmation is accepted

### Requirement: Server checks before acting
The plugin server SHALL run `nightshift act` with an argument array, only for a
change present in the last good status and a verb in its action list, and SHALL
pass only display fields to the client.

#### Scenario: Unknown change refused
- **WHEN** the client asks to act on a change not in the last status
- **THEN** the server refuses without running nightshift

#### Scenario: Argv not shell
- **WHEN** a note contains shell metacharacters
- **THEN** it reaches nightshift as one literal argument

### Requirement: Stale tolerance
When a status read fails, the page SHALL keep the last good list, marked stale.

#### Scenario: Read failure keeps the list
- **WHEN** a refresh fails after a successful read
- **THEN** the previous runs are still shown, marked stale

### Requirement: Results stay in view
The outcome of an action sent from the page SHALL be shown with the run it was
sent for, or, while that run is not displayed, above the list naming the change.
It SHALL never be shown only somewhere the user did not act.

#### Scenario: Refused publish shown in its row
- **WHEN** Publish is confirmed on the second of two runs and the action is refused
- **THEN** the refusal and its reason appear once, in that run's row

#### Scenario: Run gone after the action
- **WHEN** the run an action was sent for is no longer listed after the refresh
- **THEN** its outcome appears above the list, naming the change

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

### Requirement: Pause display metadata

The Factory shared schema SHALL accept optional nullable pause metadata with
optional nullable finite-number `since` in epoch seconds and optional nullable
nonnegative-integer `auto_resumes_remaining`. The existing server projection
SHALL forward only those two pause fields, preserve supplied nulls, and retain
its validation, redaction, caching, and action protections.

#### Scenario: Only pause display fields cross the server
- **WHEN** version-1 status supplies pause timestamps and remaining-resume counts alongside reason, task, detail, resume time, or other internal fields
- **THEN** the Factory response includes only the supplied `since` and `auto_resumes_remaining` pause properties with their original numeric or null values
- **THEN** existing validated display fields and action behavior are unchanged

#### Scenario: Older producers remain supported
- **WHEN** status omits pause, supplies null pause, or supplies a pause object missing one or both display fields
- **THEN** the server accepts the otherwise valid response without defaulting a missing timestamp or budget to zero
- **THEN** absent pause and pause objects with no display fields are omitted from the projection, explicit null pause is retained, and any supplied valid display field is retained

#### Scenario: Invalid metadata retains validation behavior
- **WHEN** a status read supplies an invalid timestamp type or a negative, fractional, or incorrectly typed remaining-resume count
- **THEN** existing schema validation fails the read, retaining the last good snapshot marked stale or reporting unavailable when no snapshot exists
- **THEN** existing version, status, action, PR URL, and context validation are not relaxed

### Requirement: Human pause waiting time

Factory SHALL display elapsed waiting time only for runs with status `paused`,
`pause.auto_resumes_remaining` exactly zero, and a finite numeric `pause.since`.
It SHALL show floored whole minutes below one hour and floored whole hours
thereafter, using `waiting Nm` or `waiting Nh` and clamping future timestamps to
zero elapsed time. It SHALL use `foregroundMuted` through three hours and
`statusWarning` strictly after three hours, based on unrounded elapsed time.

#### Scenario: Waiting time shows for human pauses only
- **WHEN** Factory renders paused runs with known timestamps and zero remaining resumes alongside paused runs with positive, null, or missing budgets or missing/null timestamps, plus running, done, and aborted runs
- **THEN** only the paused runs with known timestamps and zero remaining resumes show waiting text, regardless of pause reason or available action verbs
- **THEN** running and finished runs show no waiting text even if their payloads retain pause metadata and finished rows are expanded

#### Scenario: Waiting duration uses whole units
- **WHEN** eligible runs have elapsed waits of 59 seconds, 60 seconds, 3,599 seconds, 3,600 seconds, and 18,000 seconds
- **THEN** their labels are respectively `waiting 0m`, `waiting 1m`, `waiting 59m`, `waiting 1h`, and `waiting 5h`
- **WHEN** an eligible run has a future pause timestamp or a valid epoch-zero timestamp
- **THEN** the future timestamp shows `waiting 0m`, and epoch zero is treated as a known timestamp rather than missing data

#### Scenario: Waiting time turns to warning after three hours
- **WHEN** an eligible run's unrounded elapsed wait is below or exactly 10,800 seconds
- **THEN** its waiting text uses the current theme's `foregroundMuted`
- **WHEN** elapsed time exceeds 10,800 seconds, including by a fractional second
- **THEN** its waiting text uses the current theme's `statusWarning` even while the floored label remains `waiting 3h`
- **THEN** a theme change updates the waiting color from the new theme's tokens without changing the text or task-progress color

#### Scenario: Waiting text preserves the existing row
- **WHEN** an eligible row renders waiting text
- **THEN** it appears once as separate readable text beside the existing status chip/progress block below the title, in a layout that allows wrapping
- **THEN** the status/progress text remains exactly `<status> · <done>/<total> tasks done`, its chip styling and muted progress remain unchanged, and rows without an indicator retain their existing presentation
- **THEN** all existing accessibility labels, roles, expanded states, action availability, confirmations, notices, Details, navigation, ordering, and finished disclosure behavior remain unchanged

#### Scenario: Existing refreshes update the wait
- **WHEN** an existing Factory refresh or render occurs with a later client time, a newer pause timestamp, or a run that has resumed or finished
- **THEN** the label and warning reflect the current elapsed time, reset for the new pause timestamp, or disappear for the resumed or finished run
- **WHEN** a refresh fails after a good snapshot
- **THEN** the retained snapshot keeps its waiting eligibility and the page's existing stale/error indications
- **THEN** waiting-time presentation adds no timer, RPC, workspace lookup, or change to refresh and cache behavior

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

The page SHALL offer exactly the verbs the run's action lists in its existing action group, with a note field for resume, and SHALL ask for confirmation before publish, finish or abort. It MAY additionally offer the separate Land control under Human checks landing; Land SHALL NOT become an act verb or replace Finish.

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

Confirmation controls SHALL keep padded, rounded, minimum 44-by-44 buttons. Confirm SHALL be primary for Publish/Finish and destructive for Abort; Cancel SHALL be outlined. Publish/Finish messages SHALL follow Forge publication confirmation. All other messages, labels, roles, disabled conditions and confirmation semantics SHALL remain unchanged, including no send before confirmation or on Cancel.

#### Scenario: Confirmation styles preserve the action gate
- **WHEN** Publish, Finish, or Abort is pressed
- **THEN** its confirmation appears without sending an action, Confirm has the appropriate primary or destructive rendered style, and Cancel has the outlined rendered style
- **THEN** only the Publish/Finish warning text changes; Abort, accessibility labels, minimum targets, theme colors and disabled conditions remain unchanged
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

### Requirement: External links open outside Paseo

Every forge link the plugin renders, on Factory and on the attention page,
SHALL open through the SDK's `openExternalUrl`, never react-native `Linking`.
On the desktop app `Linking.openURL` becomes `window.open`, which Electron
shows as a bare in-app window with no browser controls.

#### Scenario: Links reach the system browser
- **WHEN** Open PR, Open change on GitHub, or an attention task's Open on GitHub is pressed
- **THEN** its URL is passed to `openExternalUrl`, and a rejected open shows the existing error

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

The Factory shared schema SHALL accept optional nullable pause metadata with optional nullable finite-number `since` in epoch seconds and optional nullable nonnegative-integer `auto_resumes_remaining`. The existing server projection SHALL forward only those two pause fields, preserve supplied nulls, and retain its validation, redaction, caching, and action protections, with PR and context URL validation extended only by Configured forge links.

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
- **THEN** existing version, status, action and context-field validation are not relaxed; PR URL validation accepts only existing GitHub PRs or configured forge origins with the specified PR shape, and context URL filtering uses that same allowlist

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

### Requirement: Human checks landing

Factory SHALL offer separate Land only for a matching non-stale human_checks offer with a validated `https://github.com` PR link and preserve every existing label and behavior. Configuring another forge for display SHALL NOT authorize a landing offer or submission. New controls SHALL use host theme tokens and minimum 44-by-44 targets.

#### Scenario: Land action appears only at human checks
- **WHEN** Factory renders human_checks, other-paused, running, done, aborted and unavailable runs
- **THEN** Land appears only for a verifiable paused human_checks offer with a validated GitHub PR link, separate from unchanged ordered status verbs and Finish
- **THEN** running landing disables another start and exposes Stop landing; ordinary confirmations, notes, waiting time, Details, navigation and Finished retain their labels and behavior

#### Scenario: Land offered only for GitHub links
- **WHEN** otherwise eligible human_checks runs include a configured Gitea PR and a GitHub PR
- **THEN** the Gitea run remains displayable with its Open PR destination but has no usable landing offer or Land button, while the matching non-stale GitHub offer still permits Land
- **THEN** a forged non-GitHub landing confirmation is refused before private start execution, and a cached GitHub offer cannot authorize a run whose PR has changed to Gitea
- **THEN** landing validation, confirmation identity, stale checks, independent cache behavior, retained history and Stop behavior remain unchanged

### Requirement: Landing confirmation

Land SHALL require explicit confirmation that the PR's human checks were performed. Its dialog SHALL freeze change, PR, pause_id, head and since and never silently rebind to new data.

#### Scenario: Land asks for confirmation before starting
- **WHEN** Land opens and is cancelled or confirmed
- **THEN** opening and Cancel send nothing, while Confirm land sends one frozen offer with checks_done true and guards duplicate clicks
- **THEN** the dialog identifies PR/head and merge intent, with an alert, Land/Confirm land labels naming the change, primary Confirm and outlined Cancel

#### Scenario: Changed offer invalidates the dialog
- **WHEN** any offer field changes while the dialog is open, including republication with unchanged pause_id
- **THEN** submission is blocked with a stale reason and requires fresh confirmation, while server and worker independently refuse stale requests

### Requirement: Landing RPC isolation

The plugin SHALL expose separate validated landing read, submit and Stop RPCs using independent landing cache state, trusted host configuration and literal argv. A lost submit response SHALL recover through status without durable replay or automatic resubmission.

#### Scenario: Submission is bounded and recoverable
- **WHEN** confirmed, malformed, unknown-change or stale-offer requests reach the server, or a start response is lost
- **THEN** only a valid matching displayed offer proceeds to fresh admission, accepted start returns an attempt ID promptly without waiting for land, and recovery reads retained status
- **THEN** private transport has a 60-second limit and 2 MiB cap, worker lifetime survives transport cleanup, and inputs cannot select arbitrary executables or paths

#### Scenario: Independent caches discard stale responses
- **WHEN** one channel fails after a good read or a deferred landing read completes after a newer submission generation
- **THEN** that channel retains good data marked stale while healthy reads update, and the old-generation response is discarded
- **THEN** only validated bounded display fields, redacted home paths and checked GitHub links cross RPC

### Requirement: Landing notices stay visible

Factory SHALL retain landing progress, results and refusal reasons by attempt independently of ordinary action notices. Each notice SHALL appear once in its originating visible row, otherwise above the list naming its change.

#### Scenario: Land result and refusal stay in the row
- **WHEN** landing progresses, succeeds or refuses and a refresh, ordinary action or newer attempt follows
- **THEN** already displayed results and refusal reasons stay in that run's row without replacement or duplication, including after failed refreshes
- **THEN** lost-response messaging requests a status refresh without automatically sending another Land

#### Scenario: Landing notice follows a hidden run
- **WHEN** the run becomes finished/folded, disappears or is expanded and folded again
- **THEN** notices move between row and above-list slot exactly once without opening Finished or clearing outcomes
- **THEN** remount restores bounded recent history and named reads restore current progress outside it, while current-session notices remain retained

### Requirement: Landing refresh integration

Factory SHALL read landing status on existing refresh triggers without a new timer or action submission. Rendered harnesses SHALL distinguish every RPC descriptor and preserve existing assertions.

#### Scenario: Refresh never submits an action
- **WHEN** Factory mounts, refreshes manually, reaches the existing active interval or returns to the foreground
- **THEN** independent 15-second landing caches serve bounded reads with at most four named reads concurrently, without another timer, action or workspace lookup
- **THEN** factory-style-harness.ts and factory-view.test.ts use separate landing responses/counters, reject unknown descriptors and preserve all assertions including factory-waiting-view.test.ts's empty actions after mounting
- **THEN** Details/Finished toggles add no status read and existing disposal cleanup remains intact

### Requirement: Configured forge links

Factory SHALL validate PR and context origins against one allowlist loaded from the local operator file on each uncached status read, always allowing `https://github.com`. Only a wholly valid file SHALL add origins. PR validation SHALL retain GitHub's existing form and accept the configured forge form specified below; other nonempty PR links SHALL fail the whole read. Existing caching and stale fallback SHALL remain unchanged.

#### Scenario: Accepts configured forge links
- **WHEN** a valid file lists `https://forge.example.test` and `https://forge.example.test:8443` among blank lines, full-line comments and duplicate entries, and status mixes GitHub PRs with configured `/owner/repo/pulls/7` PRs and allowed context links
- **THEN** LF and CRLF files are supported by splitting on LF and removing one terminal CR per line; whitespace-only lines and lines whose first non-whitespace character is `#` are ignored, and duplicate origins are harmless
- **THEN** every other line is used whole without trimming or removing inline comments, and is valid only if `new URL(line)` succeeds, its protocol is `https:`, and its origin equals the exact line
- **THEN** the entire Factory snapshot loads as fresh, preserving every run and its validated PR/context destination
- **THEN** GitHub PRs retain the existing `/<owner>/<repo>/pull/<n>` form and configured non-GitHub PRs use `/<owner>/<repo>/pulls/<n>`, with nonempty single-segment owner/repo and one or more decimal digits for n; empty PR fields remain valid
- **THEN** changing, creating or deleting the file is reflected on the next uncached status load without plugin restart, while cached/concurrent reads retain the existing 15-second throttle and shared load

#### Scenario: Rejects foreign origins
- **WHEN** a status supplies a PR from an unconfigured origin, look-alike hostname, unlisted subdomain or port, or a PR with wrong path, scheme, credentials, query or fragment
- **THEN** validation uses exact parsed-origin equality and rejects credentials, query and hash for both GitHub and configured forge PRs
- **THEN** the whole read fails, retaining the last good snapshot marked stale or reporting unavailable when cold, even when other rows are valid
- **THEN** the same foreign origin in a context link alone is blanked without failing the read, and GitHub's `/pulls/` and a configured non-GitHub forge's `/pull/` paths remain invalid PRs

#### Scenario: Invalid origins file allows GitHub only
- **WHEN** the file has a valid extra origin followed or preceded by any invalid line, including HTTP, a path, trailing slash, query, fragment, credentials, surrounding whitespace, noncanonical origin spelling or inline comment
- **THEN** all extras are ignored for that load, valid GitHub PR/context links still load, extra-forge PRs fail the read and extra-forge context links are blanked
- **THEN** missing, unreadable, empty and comment-only files also allow GitHub only, and a formerly valid file becoming invalid does not retain its previous extra origins for fresh validation

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

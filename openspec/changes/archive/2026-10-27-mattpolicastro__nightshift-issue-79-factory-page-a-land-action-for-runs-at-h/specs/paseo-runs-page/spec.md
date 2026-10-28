## Purpose

Add confirmed Land and retained attempt notices to Factory without changing existing action, accessibility, disclosure or navigation behavior.

## MODIFIED Requirements

### Requirement: Buttons fit the pause

The page SHALL offer exactly the verbs the run's action lists in its existing action group, with a note field for resume, and SHALL ask for confirmation before publish, finish or abort. It MAY additionally offer the separate Land control under Human checks landing; Land SHALL NOT become an act verb or replace Finish.

#### Scenario: Publish needs confirmation
- **WHEN** a run is ready to publish and its Publish button is pressed
- **THEN** nothing is sent until the confirmation is accepted

## ADDED Requirements

### Requirement: Human checks landing

Factory SHALL offer separate Land only for a matching non-stale human_checks offer and preserve every existing label and behavior. New controls SHALL use host theme tokens and minimum 44-by-44 targets.

#### Scenario: Land action appears only at human checks
- **WHEN** Factory renders human_checks, other-paused, running, done, aborted and unavailable runs
- **THEN** Land appears only for a verifiable paused human_checks offer, separate from unchanged ordered status verbs and Finish
- **THEN** running landing disables another start and exposes Stop landing; ordinary confirmations, notes, waiting time, Details, navigation and Finished retain their labels and behavior

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

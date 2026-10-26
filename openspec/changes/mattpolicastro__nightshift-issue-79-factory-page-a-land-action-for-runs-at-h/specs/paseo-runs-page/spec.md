## Purpose

Add an explicitly confirmed Land action and persistent landing progress to Factory while preserving existing actions, accessibility, status, Details and Finished behavior.

## MODIFIED Requirements

### Requirement: Buttons fit the pause

The page SHALL offer exactly the verbs the run's action lists in its existing action group, with a note field for resume, and SHALL ask for confirmation before publish, finish or abort. It MAY additionally offer the separate Land control under the Human checks landing requirement; Land SHALL NOT become an act verb or replace Finish.

#### Scenario: Publish needs confirmation
- **WHEN** a run is ready to publish and its Publish button is pressed
- **THEN** nothing is sent until the confirmation is accepted

## ADDED Requirements

### Requirement: Human checks landing

Factory SHALL offer a separate Land control only for a current verifiable human_checks pause and SHALL preserve every existing action, accessibility label and behavior. New controls SHALL use host theme tokens and at least 44-by-44 targets.

#### Scenario: Land action appears only at human checks
- **WHEN** Factory renders human_checks, other-paused, running, done, aborted and unavailable runs
- **THEN** Land appears only for paused human_checks with a valid matching offer, remains separate from the unchanged ordered status verbs, and never replaces Finish
- **THEN** missing/stale identity or a current attempt/hold prevents a new start; existing labels, notes, Details, navigation, waiting time, theme behavior, grouping and ordinary confirmations remain intact

### Requirement: Landing confirmation

Factory SHALL require confirmation that the PR's human checks were completed before sending Land. The confirmation SHALL freeze the offer's change, PR, pause_id, head and since with a request UUID and SHALL NOT silently rebind to newer row data.

#### Scenario: Land asks for confirmation before starting
- **WHEN** Land is pressed, then Cancel or Confirm land is pressed
- **THEN** opening and cancelling send no start request; confirmation sends one frozen request with checks_done true and its UUID, with duplicate-submit guards
- **THEN** controls expose `Land <change>` and `Confirm land <change>` button labels, an alert explaining checks and merge, primary Confirm and outlined Cancel styles without changing any existing label

#### Scenario: Changed offer invalidates an open dialog
- **WHEN** any offer field changes while confirmation is open, including revision and republication with unchanged pause_id
- **THEN** the old dialog cannot authorize the new pause, a stale reason remains visible, and another confirmation is needed
- **THEN** a late request from another tab is independently refused by the gateway

### Requirement: Landing RPC isolation

The plugin SHALL expose separate landing read and submit RPCs using validated display schemas, trusted host configuration and literal CLI argv. It SHALL resolve durable request replay before new-offer checks and preserve independent run and landing cache state.

#### Scenario: Submission is bounded and replayable
- **WHEN** a confirmed request, a forged new offer, an unknown change, an altered UUID payload or an uncertain submission retry reaches the server
- **THEN** only a matching new displayed offer proceeds to fresh admission, durable matching UUIDs return their existing outcome even after the run changes or the server restarts, and invalid requests cannot launch work
- **THEN** the server uses argv without a shell, a 60-second gateway limit and a 2 MiB buffer; the detached land process is not bound to the RPC lifetime

#### Scenario: Independent caches retain good data
- **WHEN** run or landing status fails after loading, or a pre-submission read completes after a newer outcome
- **THEN** the failed channel retains its last good snapshot with a stale diagnostic, healthy reads still update, and generation guards prevent old reads from overwriting newer outcomes
- **THEN** only validated bounded display fields cross RPC, with home redaction and checked GitHub links; raw errors, local paths and arbitrary execution inputs remain excluded

### Requirement: Landing notices stay visible

Factory SHALL retain per-attempt progress, results and refusal reasons independently of ordinary action notices. Each notice SHALL appear once in its rendered originating row, otherwise above the list naming its change, and SHALL survive refresh and disclosure changes.

#### Scenario: Land result and refusal stay in the row
- **WHEN** landing reports progress, succeeds or refuses with a reason, and another action or landing attempt completes
- **THEN** each retained landing notice stays with its originating visible row, a new attempt does not replace an earlier refusal, and ordinary action notices retain their existing behavior
- **THEN** subsequent successful or failed refreshes retain the outcome and reason, and a lost start response retains its UUID with an explicit unknown-result message for replay

#### Scenario: Landing notice follows a hidden run
- **WHEN** the run becomes finished and folded, is removed, or is expanded and folded again
- **THEN** its landing notices move between the row and the above-list slot exactly once without clearing, duplicating or forcing open Finished
- **THEN** remount restores the bounded durable recent history, named reads restore current progress outside that window, and current-session submitted attempts remain pinned in client state

### Requirement: Landing refresh integration

Factory SHALL read landing status on its existing refresh triggers without another timer, action submission or workspace lookup. Rendered fixtures SHALL distinguish all RPC kinds and preserve existing assertions.

#### Scenario: Refresh never submits an action
- **WHEN** Factory mounts, refreshes manually, reaches its existing active 60-second interval or returns to the foreground
- **THEN** it performs separate bounded landing reads using shared 15-second server caches and at most four concurrent named queries, with no extra timer or action
- **THEN** both factory-style-harness.ts and factory-view.test.ts route distinct landing descriptors to their own responses and counters, reject unknown descriptors, and preserve all existing assertions including no actions after mounting in factory-waiting-view.test.ts
- **THEN** Details and Finished toggles still cause no additional status RPC, and disposal retains existing timer/subscription cleanup

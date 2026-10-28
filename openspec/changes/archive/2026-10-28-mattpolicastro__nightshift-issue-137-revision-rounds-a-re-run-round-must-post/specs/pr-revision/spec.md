## Purpose

Deliver every newly selected revision result attempt while preserving legacy
markers, retry reconciliation and the existing revision lifecycle.

## ADDED Requirements

### Requirement: Attempt scoped revision results

Revision completion delivery SHALL identify results by change, round and
attempt, retaining the existing marker for the implicit first attempt.
Same-round replacement snapshots SHALL save a fresh `delivery_attempt` UUID
before dispatch or delivery. Retries of retained snapshots SHALL reuse their
identity. Selection, build replay, consumption, authorization, pause, stop and
label handling SHALL remain unchanged.

#### Scenario: Rerun round posts new result
- **WHEN** a spec revision returns `rejected` or `needs_input` and normal eligibility later selects another snapshot under the same label event and round ID
- **THEN** its new result has a distinct attempt-qualified comment containing its questions or contradictions, while the prior comment remains unchanged
- **THEN** separate attempts remain distinct even with identical result text, and rejection still retains `agent:revise`, adds `needs-human` and consumes no selected feedback

#### Scenario: Attempt identity is durable before execution
- **WHEN** a same-round replacement is prepared for synchronous execution, background dispatch or empty-feedback delivery
- **THEN** its fresh attempt UUID is saved atomically with the snapshot before dispatch or external delivery and survives collection and restart
- **THEN** another same-round replacement gets a different UUID, a different round returns to the implicit initial identity, and a failed save dispatches nothing and leaves the old receipt unchanged

#### Scenario: Invalid attempt metadata blocks processing
- **WHEN** a loaded receipt has a present `delivery_attempt` that is not a 32-character lowercase hexadecimal string or has no nonempty string round ID
- **THEN** validation fails before revision command dispatch or revision completion delivery, including its completion-comment and label writes, without replacing or marking the receipt delivered or resetting consumption
- **THEN** absent attempt metadata remains valid and is not silently substituted for malformed metadata

#### Scenario: Invalid attempt metadata retains failure reporting
- **WHEN** an eligible receipt's invalid attempt metadata reaches the existing daemon item-failure escalation threshold
- **THEN** the existing error path can post its stage-error comment and add `needs-human`, while retaining `agent:revise`, the original receipt bytes and consumption history and posting no revision completion comment
- **THEN** existing pause guards still prevent further failure-reporting operations while paused; failure reporting does not mark the revision result delivered

#### Scenario: Retained attempt survives delivery interruption
- **WHEN** an eligible saved result with an attempt suffix is retried after a delivery error, pause or daemon restart
- **THEN** it retains its marker and result, reconciles only its own comment, and starts no replacement command merely to retry notification
- **THEN** pause prevents further external operations and premature delivery completion, and authorization and stopped-job holds remain effective

### Requirement: Legacy result marker compatibility

Receipts without `delivery_attempt` SHALL retain their original unqualified or
round-qualified revision markers during recovery. Matching SHALL use the
complete literal marker including its closing delimiter, never a round prefix
or a fallback alias. Attempt-qualified delivery SHALL leave earlier markers
and comments intact. Unrelated receipt fields and other delivery namespaces
SHALL retain their existing behavior.

#### Scenario: Old round markers still recognised
- **WHEN** a pending legacy result with an unqualified, event-derived or UUID-round marker is recovered after restart without attempt metadata
- **THEN** the existing comment is reconciled using the exact old marker without a second create or retroactive attempt field
- **THEN** the saved result, consumption history and unknown receipt fields remain available under existing recovery rules

#### Scenario: Initial markers and build replay remain compatible
- **WHEN** a new logical round is selected or an ordinary keyed build retry retains its saved snapshot
- **THEN** the first result keeps the exact round-only marker and the keyed retry keeps its original round and delivery identity
- **THEN** no attempt suffix changes the `--round-id` argument, keyed result validation or existing successful replay wording

## MODIFIED Requirements

### Requirement: Spec-stage revision

`agent:revise` on an otherwise eligible spec PR whose change is not admitted SHALL re-run `specloop propose` only after Revision actor gate succeeds and repository readiness permits new drafting, with the PR's eligible unconsumed review feedback added to the idea, push the revised branch, acknowledge the selected IDs, remove the corresponding label application and comment what changed using Attempt scoped revision results. Selection and acknowledgement SHALL follow Consumption based eligibility and Durable round snapshot. Unhealthy enrollment SHALL hold new drafting without consuming feedback or its trigger; existing successful work SHALL retain recovery under Health gates new work only. Build-stage revision and empty-feedback handling SHALL remain unchanged except for the compatible delivery identity extension.

#### Scenario: Spec revised from review
- **WHEN** a reviewer comments on an unapproved spec PR in a healthy repository with an eligible unconsumed ID and a trusted actor labels it agent:revise
- **THEN** propose runs again with that comment as feedback, the branch is updated, its ID is consumed, and the corresponding agent:revise application is removed
- **THEN** a success replacing an earlier rejected or needs-input result under the same round is posted separately and preserves the earlier result comment

### Requirement: Revision comment retry reconciliation

Revision completion comments SHALL reconcile the original `revise` marker inside `queue._gh` before retrying an ambiguous create, for legacy, round-qualified and attempt-qualified markers. Reconciliation SHALL search all comment pages, update the matching comment when found, and allow another create only after confirming absence. Failed reconciliation reads SHALL prevent a blind POST. Existing draft, admit and publish marker behavior and pause guards SHALL remain intact.

#### Scenario: Lost revision comment response reconciled
- **WHEN** a revision completion POST stores a comment remotely but its response is lost and the production `queue._gh` transient retry runs
- **THEN** reconciliation finds the original marker even on a later page and PATCHes that comment ID without a second comment-create call
- **THEN** legacy, round-qualified and attempt-qualified markers behave this way, while a distinct round or attempt marker remains a separate comment and any earlier comment remains unchanged

#### Scenario: Revision reconciliation read fails
- **WHEN** a revision comment POST has an ambiguous outcome and the production retry cannot read the comment history
- **THEN** the read error propagates without another POST, consumed IDs and the saved revision result remain intact, and subsequent recovery can reuse the original marker without another revision command

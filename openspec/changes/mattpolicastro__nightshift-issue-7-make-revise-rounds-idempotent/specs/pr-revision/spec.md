## Purpose

Preserve a build revision's saved identity across crashed attempts and deliver one accurate acknowledgement per logical round.

## ADDED Requirements

### Requirement: Stable build round identity

Each newly selected build snapshot SHALL save a fresh UUID round_id and revision_key_version before dispatch and pass that key to revise. Ordinary crash redispatch SHALL reuse its saved identity, feedback IDs and blocks before considering a newer trigger or newly available feedback. The key SHALL NOT be a job ID. Every invocation SHALL retain the existing distinct immutable JSON input-file contract.

#### Scenario: Ordinary retry keeps the snapshot
- **WHEN** an ordinary failed or crashed keyed attempt has been detached and normal eligibility permits retry, including after restart and after a newer authorized label application or feedback edit
- **THEN** intake launches a new job with the original saved key, IDs and blocks and a distinct immutable input file, without reselecting feedback
- **THEN** the earlier event is completed before later feedback is selected and its delivery does not remove the newer label application

#### Scenario: Prepared snapshot survives launch failure
- **WHEN** input preparation, a synchronous injected command or background launch fails after saving a keyed snapshot but before a valid result is saved
- **THEN** the next eligible retry keeps that snapshot and key without consuming feedback or inventing another logical round
- **THEN** no invocation is dispatched before its full input file is written and closed
- **WHEN** preparing the first input for a new selection fails before its snapshot is saved
- **THEN** no command is dispatched and the prior receipt and historical inputs remain byte-for-byte unchanged

#### Scenario: Replacement selection creates a new identity
- **WHEN** a delivered refusal is resolved or a confirmed stopped attempt is explicitly released, even under an unchanged trigger event
- **THEN** normal unconsumed-feedback selection creates a fresh build-round key and input file while preserving consumed IDs and unknown fields
- **THEN** the stopped attempt stays cancelled, its result is never acknowledged, and an unreleased stop remains held

#### Scenario: New event selects an independent round
- **WHEN** a newer authorized revision event remains after the original round completes and unconsumed feedback is selected for a build revision
- **THEN** the new snapshot keeps the newer event ID separately from its fresh UUID round_id, which differs from the completed round's ID, and its completion comment uses exactly that saved round marker
- **THEN** the original snapshot and comment stay unchanged, only newly selected IDs are consumed, and further retries add no dispatch or comment for the completed round
- **THEN** spec-stage snapshots continue to use their event-derived round IDs and markers

#### Scenario: Legacy rounds keep existing semantics
- **WHEN** a pre-upgrade receipt or request lacks keyed-round metadata, or an empty or spec-stage revision is processed
- **THEN** existing selection, marker, consumption and control behavior remains compatible without inferring a key from old task text or applying build-key replay to propose
- **THEN** malformed known metadata in a receipt declaring keyed support causes an error without overwriting its history

### Requirement: Keyed result acknowledgement

Intake SHALL validate keyed success against the saved round before durably consuming only that snapshot's selected IDs. It SHALL retain existing authorized, unpaused delivery, label-event binding and comment reconciliation. New build completion markers SHALL identify the saved logical round; successful replay wording SHALL accurately report the run state observed in the result.

#### Scenario: Result identity gates consumption
- **WHEN** a keyed result has the wrong or missing key, missing commit, invalid replay flag or invalid status/pause, or instead contains a complete matching success
- **THEN** invalid success is rejected without consuming IDs or posting success, and valid success durably consumes only saved IDs before notification
- **THEN** legacy unkeyed results remain valid under their existing schema

#### Scenario: Refusal and replacement have distinct comments
- **WHEN** a build round is refused, the human resolves it, and a replacement round succeeds under the same event
- **THEN** the refusal and success have two distinct exact saved-round markers and one comment each, with the successful commit in the success comment
- **THEN** repeated polling, restart and ambiguous comment-delivery retries add no comment within either round and preserve all label and feedback-consumption checks

#### Scenario: Replay reports observed control state
- **WHEN** successful keyed replay acknowledges a run observed running, paused, aborted or done
- **THEN** the completion message describes that observed state, including a pause reason when paused, without claiming resumed for paused, aborted or finished work
- **THEN** replay changes no run-control state for delivery and legacy unkeyed success wording stays compatible

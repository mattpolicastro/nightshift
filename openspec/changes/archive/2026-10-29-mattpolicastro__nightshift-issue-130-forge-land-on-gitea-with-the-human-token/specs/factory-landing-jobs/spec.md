## Purpose

Preserve confirmed Factory landing while moving its GitHub command definitions to the shared landing boundary. This replaces the historical restriction that land's only implementation change is passing the finish head; it does not enable new plugin offers or weaken any handoff gate.

## ADDED Requirements

### Requirement: Shared landing operation boundary

Factory Land SHALL obtain remote PR reads and pre-finish command classification from named helpers in `nightshift.land`. It SHALL contain no direct GitHub argv builders or command-construction disguises. Existing confirmation eligibility, runner injection, timeouts, output handling and execution isolation SHALL remain unchanged.

#### Scenario: Factory delegates GitHub definitions
- **WHEN** Factory reads the remote head or classifies a confirmed invocation's pre-finish operation
- **THEN** named landing helpers supply the existing behavior and exact GitHub argv while the existing injectable runner performs subprocess execution
- **THEN** the worker passes the AST scan without its temporary allowlist entry and no plugin, daemon, lifecycle or public CLI path changes

## MODIFIED Requirements

### Requirement: Confirmed landing handoff

The worker SHALL supply the confirmed identity to nightshift.land through its injectable runner and named landing helpers, use checks-done and preserve land as the sole merge path. It SHALL NOT duplicate landing gates or silently adopt an unrelated head.

#### Scenario: Head changes at handoff
- **WHEN** PR or pause changes between preflight and the actual initial responses consumed by land, or before a pre-finish mutation
- **THEN** the adapter refuses stale confirmation before that mutation rather than authorizing the newly discovered head or pause

#### Scenario: Landing gates and head pin remain authoritative
- **WHEN** land executes through the adapter and finish or a validated base update advances the head
- **THEN** existing checklist, attestation, finish, CI, provenance and protection gates decide the outcome and the GitHub merge uses --match-head-commit with land's verified successor head
- **THEN** land passes the validated PR head to finish, all existing gate assertions remain, and unknown pre-finish mutation command shapes refuse through the shared command classifier

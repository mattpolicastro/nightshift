## Purpose

Hold new spec drafting on unhealthy enrollment while preserving admitted build revisions, authorization, feedback consumption and saved-result recovery.

## ADDED Requirements

### Requirement: Revision health isolation

Revision health checks SHALL distinguish new spec drafting from admitted-run revision and retained-result delivery. A hold SHALL preserve eligible feedback and trigger state without consuming IDs or starting another job. Runtime edits SHALL remain in `nightshift/specloop_intake.py` and `nightshift/daemon.py` with corresponding tests.

#### Scenario: Spec drafting waits for repair
- **WHEN** an authorized unadmitted PR has new eligible feedback but its repository enrollment is unhealthy
- **THEN** no propose command or manifest-dependent git operation starts and the request and feedback remain eligible
- **THEN** after repair a later poll starts the normal spec revision once

## MODIFIED Requirements

### Requirement: Spec-stage revision

`agent:revise` on an otherwise eligible spec PR whose change is not admitted SHALL re-run `specloop propose` only after Revision actor gate succeeds and repository readiness permits new drafting, with the PR's eligible unconsumed review feedback added to the idea, push the revised branch, acknowledge the selected IDs, remove the corresponding label application and comment what changed. Selection and acknowledgement SHALL follow Consumption based eligibility and Durable round snapshot. Unhealthy enrollment SHALL hold new drafting without consuming feedback or its trigger; existing successful work SHALL retain recovery under Health gates new work only. Build-stage revision and empty-feedback handling SHALL remain unchanged.

#### Scenario: Spec revised from review
- **WHEN** a reviewer comments on an unapproved spec PR in a healthy repository with an eligible unconsumed ID and a trusted actor labels it agent:revise
- **THEN** propose runs again with that comment as feedback, the branch is updated, its ID is consumed, and the corresponding agent:revise application is removed

## Purpose

Preserve pause and resumed-delivery behavior while making the accepted Gitea
consistency assumption explicit in completion-effect deduplication.

## ADDED Requirements

## MODIFIED Requirements

### Requirement: Resume pending delivery

After pause is removed, the daemon SHALL resume eligible unstopped completion
delivery from durable state using existing idempotency markers, without
relaunching the completed job or duplicating completed delivery effects.
For Gitea, the no-duplicate guarantee SHALL assume read-after-write-consistent
complete listings and no concurrent creator for the same marker or branch.
If an accepted ambiguous comment or PR POST remains invisible to a subsequent
successful lookup, a later eligible daemon poll MAY repeat that create,
including after pause/resume or restart. This accepted limitation SHALL NOT
introduce persistent create reservations or other new recovery state. Failed
lookups SHALL still prevent blind creates. GitHub guarantees, pause and
stopped-job gates, durable results and the prohibition on relaunching completed
jobs SHALL remain unchanged.

#### Scenario: Resume once after restart
- **WHEN** a job finishes while paused, the daemon restarts, pause is removed and successive polls run
- **THEN** the daemon delivers its retained result once without another job launch, duplicate PR or duplicate completion comment, and marks delivery complete; Gitea effect uniqueness assumes the stated read-after-write consistency and single creator
- **THEN** under those assumptions, an accepted create whose response or acknowledgement was lost is found by the next complete lookup and reconciled using the retained result and existing marker or branch

#### Scenario: Delayed visibility limits resumed delivery
- **WHEN** an accepted ambiguous Gitea comment or PR create remains invisible to a later successful lookup after resume or restart
- **THEN** lookup-before-create may repeat the POST and uniqueness is not guaranteed, as documented in KNOWN-LIMITATIONS.md for daemon operation
- **THEN** completed work is still retained without relaunch, normal eligibility gates still apply, and this exception does not permit a blind create after a failed lookup or add persistent recovery state

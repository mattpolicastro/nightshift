## Purpose

Carry logical build-round identity through background execution and cleanup without changing crash detachment, deadline escalation or stopped-job release.

## ADDED Requirements

### Requirement: Revision attempt provenance

A new keyed build-revision attempt SHALL persist its round identity and separate latest-attempt provenance before launch. Provenance SHALL support distinguishing ordinary failure detachment from explicit release, without making the prior attempt a retained job hold. Receipt coordination SHALL preserve the existing control-lock then release-lock order and reread current state before mutation.

#### Scenario: Key and attempt are saved before launch
- **WHEN** a keyed build revision launches or its launcher raises
- **THEN** the request round_id agrees with argv and the saved snapshot, and the receipt records round_job alongside job before launch
- **THEN** launch failure retains the logical key and input evidence for recovery while removing job as today

#### Scenario: Crashed attempt detaches normally
- **WHEN** an idle keyed job has no result or an error result, whether before or after its reporting deadline
- **THEN** collection removes only the job reference as today and reports the existing failure, retaining round identity and attempt provenance
- **THEN** it neither retains an expired-job hold nor changes live-overdue, cancellation, pause or authorization precedence

#### Scenario: Concurrent provenance stays current
- **WHEN** release, collection or another eligible poll changes a receipt while retry processing holds a stale copy
- **THEN** coordinated rereading prevents restoration of a detached job or overwrite of a newer snapshot or successor job
- **THEN** malformed, missing or inconsistent declared attempt provenance prevents automatic redispatch, and retry inspection creates no release lock

#### Scenario: Mixed legacy and keyed evidence is refused
- **WHEN** a saved attempt retains keyed evidence but its request round_id, argv --round-id or receipt key metadata is independently removed or disagrees with the other records
- **THEN** provenance validation reports an inconsistency without treating the attempt as legacy, dispatching a replacement, consuming feedback or rewriting its evidence
- **THEN** a consistently unkeyed historical request and receipt remain supported, including an event-derived round_id and event-named feedback input retained through explicit stopped-job release

#### Scenario: Compaction preserves round identity
- **WHEN** a keyed delivered attempt is compacted or its interrupted compaction resumes
- **THEN** its minimal request retains the same round_id, remaining exact args, original timing and mtime, and unchanged result/control evidence and feedback input
- **THEN** legacy requests are not assigned a key and stopped, undelivered or unauthorized evidence remains protected by existing compaction rules

## MODIFIED Requirements

### Requirement: Durable command timing

New background requests SHALL persist stage, exact args, started_at, timeout_seconds, deadline_at and round_id before launch. round_id SHALL be the saved key for keyed revise commands and null otherwise; older five-field requests SHALL remain readable as unkeyed. Keyed request identity SHALL agree with argv and the parent snapshot. Budgets SHALL be 3600 seconds for propose, 300 for admit and 1800 for revise. UTC epoch timing SHALL survive restart without extension and SHALL remain readable without subprocess or network access.

#### Scenario: Timing survives restart
- **WHEN** proposal, admission and revision jobs launch and in-memory handles are discarded before another poll
- **THEN** each request has exactly the six specified fields, including a matching round_id for keyed revise or null for unkeyed commands, the original complete args and stage, and a deadline equal to its original start plus the command budget
- **THEN** inventory preserves IDs, execution state, deterministic ordering and timing without launching commands, contacting services or rewriting records

#### Scenario: Legacy and malformed timing
- **WHEN** inventory reads a legacy request with no timing, a compacted legacy request with null timing, and a request with partial, non-finite, boolean or inconsistent timing alongside healthy records
- **THEN** legacy jobs remain readable with timing unavailable and no inferred deadline, malformed timing produces a per-record diagnostic, and healthy rows remain visible
- **THEN** unknown fields are tolerated and no request is repaired or migrated by the read

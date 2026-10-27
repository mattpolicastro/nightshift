## Purpose

Document Factory landing and accepted recovery limits while preserving intake guidance and verifying structure rather than wording.

## ADDED Requirements

### Requirement: Factory landing entry

The plugin README and engine guide SHALL document confirmed Land as a separate human_checks action alongside unchanged status verbs, distinguishing Finish from merge and preserving operator-only landing.

#### Scenario: Operator chooses Land or Finish
- **WHEN** the reader consults Factory controls in either guide
- **THEN** guidance identifies bound confirmation, background progress and retained refusal, and explains that Finish does not merge while Land invokes existing landing
- **THEN** the blanket no-merge-operation claim becomes an explicit confirmation boundary, preserving canonical CLI/daemon isolation

### Requirement: Landing control boundaries

The main README, engine guide and KNOWN-LIMITATIONS.md SHALL distinguish landing's lifetime lock and verified Stop from intake inventory, stopped holds, release and escalation.

#### Scenario: Operator handles a stopped or interrupted landing
- **WHEN** the operator follows landing recovery beside the intake release rule
- **THEN** guidance explains separate records, no manual lock release, display-only overdue reporting and no jobs inventory, delivery receipts or escalation for landing
- **THEN** worker-only death can release the lock while descendants survive; Stop refuses and the operator must wait for or manually terminate them before landing again
- **THEN** intake receipt/release guidance remains correct and independent terminal land is outside the Factory lock

### Requirement: Landing evidence retention

Operator guidance SHALL distinguish bounded landing display history from complete manually pruned evidence and existing intake compaction.

#### Scenario: Operator interprets landing history
- **WHEN** the reader checks landing storage and progress guidance
- **THEN** it describes newest 20 plus total and separate current progress, persistent logs/results, no read acknowledgement and manual pruning
- **THEN** KNOWN-LIMITATIONS.md qualifies delivered-log cleanup without promising bounded disk use or applying jobs compact to landing

### Requirement: Delayed attestation recovery guidance

The landing guide and KNOWN-LIMITATIONS.md SHALL document the cross-attempt delayed-POST duplicate-marker exception and manual recovery, replacing the unconditional never-posted-twice promise.

#### Scenario: Operator resolves duplicate markers
- **WHEN** a timed-out POST commits after a later attempt creates an identical marker
- **THEN** docs/landing.md and KNOWN-LIMITATIONS.md explain `duplicate marker matches; resolve duplicates before retrying` and deletion of one duplicate before Land again if eligible or standalone land
- **THEN** neither describes the duplicate as harmless, promises automatic success or suggests changing land.py

### Requirement: Landing documentation structure

Checks SHALL validate scoped headings, links, command examples and recovery structures through deterministic physical-line rules and real parsers, without exact explanatory prose assertions.

#### Scenario: Examples describe supported behavior
- **WHEN** checks inspect the five updated documents
- **THEN** unique physical headings and pipe-table keys select relevant passages, supported examples parse without dispatch and relative links resolve
- **THEN** ambiguous structures, unmatched command backticks and unknown placeholders fail closed; fences, HTML and indentation do not hide recognized lines
- **THEN** existing assertions remain intact and desktop/phone review stays on the human checklist

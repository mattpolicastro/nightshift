## Purpose

Explain the separate Factory landing action and its job-control exceptions without changing unrelated operator guidance or claiming unperformed host checks.

## ADDED Requirements

### Requirement: Factory landing entry

The plugin README and engine guide SHALL describe Land as a separate confirmed action at human_checks, preserving the status verb list and explaining visible progress and outcomes.

#### Scenario: Operator chooses Land or Finish
- **WHEN** the operator reads the Factory action table and engine guide's status-verbs passage
- **THEN** both distinguish Finish from Land, explain human-check confirmation and head/pause binding, and show that Land invokes existing landing as a background job while Finish alone does not merge
- **THEN** results/refusals stay in view, stale confirmation requires another review, and recovery after finish uses standalone landing without offering Land for done runs
- **THEN** the plugin's general no-merge-operation claim is updated to describe this explicit operator action

### Requirement: Landing hold recovery

The main README and engine guide SHALL document per-attempt landing hold receipts and offline stopped release beside the existing intake-receipt rule.

#### Scenario: Operator releases a stopped landing
- **WHEN** a reader follows inventory, stop, inspection and release instructions
- **THEN** landing uses its own hold receipt, exact-ID release detaches only the confirmed stopped hold, evidence and cancellation survive, and release launches nothing
- **THEN** another landing requires a fresh human_checks confirmation and UUID; daemon polls, resume and label changes cannot restart it, and remote effects are not rolled back

### Requirement: Landing job exceptions

The main README, engine guide and KNOWN-LIMITATIONS.md SHALL distinguish landing's display-only overdue status, per-attempt holds and permanently retained evidence from intake escalation, delivery and compaction.

#### Scenario: Operator interprets landing history
- **WHEN** a reader consults background-job deadline, delivery and retention passages
- **THEN** each document explains that landing has no daemon needs-human escalation or delivery acknowledgement, finished attempts remain visible as pending, and jobs compact skips their logs and evidence
- **THEN** KNOWN-LIMITATIONS.md places these exceptions beside its existing escalation, item-receipt delivery and delivered-log-removal claims, preserving those claims for intake
- **THEN** bounded recent display history is distinguished from complete local evidence and pending desktop/phone validation is not presented as automated proof

### Requirement: Landing documentation structure

Documentation tests SHALL verify the landing guidance's structure, executable command forms, receipt examples and links without exact-prose assertions, using deterministic physical-line extraction and refusing ambiguous examples.

#### Scenario: Examples describe executable behavior
- **WHEN** root Python documentation tests inspect the four updated documents
- **THEN** uniquely identified headings/tables and enumerated command examples parse against the real CLI without live effects, example receipts distinguish intake from landing, and relative links resolve
- **THEN** headings match `^#{1,6} +\S`, tables use physical pipe rows, and whole-line or isolated single-backtick commands count wherever they occur; fences, HTML and indentation receive no semantic parsing
- **THEN** unmatched command backticks, unknown placeholders, continuations or ambiguous structure block the check, and no existing assertion is weakened

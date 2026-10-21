# verify-preparation Specification

## Purpose
Trusted steps that prepare a candidate's dependencies before sandboxed,
network-free verification.

## Requirements

### Requirement: Prepare steps
A manifest's `[[prepare]]` steps SHALL run after the candidate is extracted and
before its verify steps, under a sandbox that allows network access but
confines writes to the build directory and denies credential reads, in the
build directory, and SHALL be recorded as receipts. A prepare step that does
not meet its expected exit SHALL fail verification.

#### Scenario: Dependencies ready for sandboxed tests
- **WHEN** a prepare step writes a file into the build directory and a verify step reads it
- **THEN** both steps pass and the receipts list the prepare step before the verify step

#### Scenario: Failing prepare fails verification
- **WHEN** a prepare step exits with an unexpected status
- **THEN** verification fails at that step and no verify step runs

### Requirement: Source stays read-only
Prepare steps SHALL NOT be able to modify the extracted candidate source.

#### Scenario: Write to source refused
- **WHEN** a prepare step tries to create a file in the source tree
- **THEN** the write fails

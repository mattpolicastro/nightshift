## Purpose

Document keyed revision recovery precisely while retaining the remaining process, storage and operator-control limits.

## ADDED Requirements

### Requirement: Keyed revision recovery guidance

The root README's specloop passages, specloop guide, CLI JSON guide and known limitations SHALL consistently document optional --round-id, stable automatic build keys, original-commit replay and accurate observed-state results. They SHALL distinguish same-round retries from explicit new snapshot selection and legacy/unkeyed behavior, citing issue #7 until an implementation PR is known.

#### Scenario: Operator retries a logical round
- **WHEN** a reader follows CLI or daemon build-revision recovery guidance
- **THEN** the docs explain retaining the same key and saved feedback, refusing changed payload under that key, returning the original commit and preserving later paused, aborted or finished state
- **THEN** they explain that ordinary crashed attempts detach as before, explicit stopped-job release selects a new round after inspection, and legacy/unkeyed calls have no retrospective deduplication guarantee

#### Scenario: Recovery limits remain reviewable
- **WHEN** a reader compares the four documents and their examples
- **THEN** key syntax and keyed version-1 result fields agree with the CLI, relative links resolve, and known limitations distinguish deterministic process-crash tests from an unperformed physical machine-crash check
- **THEN** examples and documentation tests verify structure and command/schema compatibility rather than requiring particular prose phrases

## MODIFIED Requirements

### Requirement: Current limitations

`KNOWN-LIMITATIONS.md` SHALL remove demonstrated fixed claims, preserve
unresolved portions of mixed claims, and describe the remaining gaps supplied
from issue #17's ledger. It SHALL not claim a gap fixed solely because a
related feature shipped. It SHALL describe confirmed stopped-job release as
supported recovery rather than listing issue #34's missing command as open.

#### Scenario: Fixed limitations leave the open list
- **WHEN** the limitations list is reconciled with the merged behavior
- **THEN** it no longer lists serial mutants, unchecked label actors, timestamp-based specloop feedback loss/replay, missing job stop or confirmed-stop release, whole-status failure, missing requested/exhausted-infra attention, idea payloads in argv or the resolved CLI/prepare-prompt gaps as open
- **THEN** deadlines, retention, unresolved crash boundaries, config, credential, quota and platform gaps remain unless a specific fix supports their removal

#### Scenario: Remaining ledger gaps are explicit
- **WHEN** a reader checks what automation still cannot guarantee
- **THEN** the list includes admission completing while paused and still building, touchpoint undercount from missing history, descoped JSX style filtering, and GitHub-only automated front door and publication
- **THEN** it describes `nightshift jobs release <id>` as explicit recovery for confirmed stops while retaining the limits on automatic retry, committed effects and legacy/unkeyed revision commit/result risk and unproven machine-crash durability for keyed revisions
- **THEN** it distinguishes configured `mutation_skip` from automatic JSX filtering and local execution from hosted front-door/publication support, linking the tracking ledger

### Requirement: Release recovery boundaries

Both guides and `KNOWN-LIMITATIONS.md` SHALL describe explicit release while
preserving evidence-retention, committed-effect and automatic-retry limits.
They SHALL NOT retain the claim that issue #34 has no recovery command.

#### Scenario: Operator understands remaining limits
- **WHEN** a reader compares the guides with known limitations
- **THEN** all three describe release consistently and distinguish historical stopped attempts from future eligible work
- **THEN** they preserve the lack of rollback, independent admitted builds, legacy/unkeyed revision commit/result crash risk, explicit new-round effects after release and unproven keyed machine-crash durability, retention limits and absence of automatic retries without recommending manual receipt editing for a normal confirmed stop

### Requirement: Git timeout guidance

The scoped operator documents SHALL describe process-group termination for timed-out daemon git commands and retain unresolved remote-effect, platform and supervisor limits.

#### Scenario: Operator understands timeout limits
- **WHEN** the Background jobs limitations and corresponding guide passages are updated
- **THEN** they replace the direct-child-only claim with the 30-second daemon git timeout and group kill, and remove only demonstrated fixed deadline/retention claims
- **THEN** they retain ambiguous remote acceptance, escaped descendants, committed-admission independence, stopped-job recovery and legacy/unkeyed revision commit/result crash caveats and unproven keyed machine-crash durability, citing issue #9 or a verified implementing PR
- **THEN** real stalled-origin validation is identified as a pending human check unless performed, rather than inferred from CI

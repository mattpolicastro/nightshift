## Purpose

Document supported stopped-job recovery consistently without presenting
explicit release as rollback, unconditional retry or supervisor control.

## ADDED Requirements

### Requirement: Stopped job release workflow

The root README and specloop guide SHALL document `nightshift jobs release
<id>` as the supported local recovery for a confirmed stopped job, citing
issue #34 or its verified implementation PR.

#### Scenario: Operator releases a stopped hold
- **WHEN** a reader follows stopped-job recovery in either guide
- **THEN** it explains inventory, stop confirmation, inspection and release using the exact full ID, followed by normal next-poll eligibility
- **THEN** it states that resume and relabeling alone leave the hold intact, and that new work requires an armed, authorized and otherwise eligible item with applicable blockers clear

### Requirement: Release command outcomes

The root README SHALL explain release success, no-op, refusal and usage exit
codes, and its independence from daemon availability and configuration.

#### Scenario: Operator interprets release output
- **WHEN** a reader checks release command behavior
- **THEN** the docs describe offline operation, exit 0 for released or already released, exit 1 for running, stopping, finishing or unverifiable work, and exit 2 for invalid syntax
- **THEN** they explain that repeating an old release cannot detach a replacement attempt

### Requirement: Release recovery boundaries

Both guides and `KNOWN-LIMITATIONS.md` SHALL describe explicit release while
preserving evidence-retention, committed-effect and automatic-retry limits.
They SHALL NOT retain the claim that issue #34 has no recovery command.

#### Scenario: Operator understands remaining limits
- **WHEN** a reader compares the guides with known limitations
- **THEN** all three describe release consistently and distinguish historical stopped attempts from future eligible work
- **THEN** they preserve the lack of rollback, independent admitted builds, revision commit/result crash risk, retention limits and absence of automatic retries without recommending manual receipt editing for a normal confirmed stop

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
- **THEN** it describes `nightshift jobs release <id>` as explicit recovery for confirmed stops while retaining the limits on automatic retry, committed effects and revision commit/result atomicity
- **THEN** it distinguishes configured `mutation_skip` from automatic JSX filtering and local execution from hosted front-door/publication support, linking the tracking ledger

## Purpose

Let operator-invoked landing wait safely through GitHub's post-push check registration window without weakening reviewed-head validation or bounded CI requirements.

## ADDED Requirements

### Requirement: CI registration delay

Landing SHALL treat `gh pr checks` exit 1 with exactly empty stdout and stderr containing the case-sensitive literal `no checks reported` as pending CI registration, polling within the existing shared deadline with unchanged head validation.

#### Scenario: Checks register after push
- **WHEN** finish pushes a new head and checks queries return exit 1, exactly empty stdout, and stderr containing the case-sensitive literal `no checks reported`, then pending checks, then at least one pass with all checks pass or skipping before the deadline
- **THEN** landing treats the registration response as pending rather than green or malformed JSON, retains the existing poll cadence, reads the expected new head before and after every query, and proceeds through the existing final revalidation, base, and merge gates only after green checks

#### Scenario: No checks before deadline
- **WHEN** the existing shared deadline expires and the most recent successfully classified, head-bracketed poll returned the recognized registration response
- **THEN** landing exits nonzero with `no CI checks were reported for head <sha> within <timeout>s; inspect checks and rerun land`, identifying the expected full head SHA and configured total CI timeout in seconds, without a malformed-JSON diagnosis, another poll after expiry, or a merge
- **THEN** previous command time and CI rounds consume the same budget, and the reported timeout is the configured total rather than a new registration grace period

#### Scenario: Other responses retain validation
- **WHEN** a response has another exit code, stdout that is not exactly empty (including whitespace-only stdout), or stderr without the case-sensitive literal `no checks reported`, including after a recognized registration delay
- **THEN** landing applies its existing JSON, check-schema, classification, and exit-status validation, refusing malformed or inconsistent results immediately
- **THEN** an ordinary pending, empty-array, or skipped-only response restores ordinary waiting and its existing deadline refusal, and failing or cancelled checks still refuse without merging

#### Scenario: Head changes during registration
- **WHEN** the head read before a query, after a registration-delay response, or before the next poll differs from the expected head
- **THEN** landing refuses with the existing head-change reason and review guidance, without accepting the new head, merging, or replacing that reason with a missing-check timeout

## MODIFIED Requirements

### Requirement: Bounded CI wait

Landing SHALL wait for checks on the expected PR head with a shared timeout of 1800 seconds by default, configurable by `--timeout`. It SHALL require at least one passing check and all checks to finish as pass or skipping, and SHALL refuse failure, cancellation, invalid results or timeout. The response defined by `CI registration delay` SHALL be pending rather than invalid; no other malformed-response exception is introduced.

#### Scenario: Pending then green
- **WHEN** checks for the expected head progress from pending to a complete set with at least one pass and otherwise pass or skipping
- **THEN** landing proceeds to final revalidation and the base comparison, using head reads before and after check queries

#### Scenario: Failing CI refused
- **WHEN** any check fails or is cancelled, or check output is malformed or has an unknown classification, excluding the recognized pending registration response
- **THEN** landing exits nonzero with the offending check or read error and never merges

#### Scenario: Shared deadline expires
- **WHEN** checks remain pending or empty, all checks are skipped, or branch-update observation exhausts the shared deadline
- **THEN** landing exits nonzero without merging, and another CI round does not reset the deadline
- **THEN** persistent recognized registration delay receives the specific missing-check diagnostic, while other timeout refusals retain their existing behavior

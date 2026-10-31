## Purpose

Correct the local daemon support statement without changing terminal landing behavior, credentials, assertions or Factory eligibility.

## ADDED Requirements



## MODIFIED Requirements

### Requirement: Gitea operator guidance

`docs/landing.md` SHALL document Gitea URL selection, human token file/precedence, trusted logins, independent finish provisioning, single-attempt updates, exact-head CI, pinned merge confirmation and recovery limits. It SHALL distinguish terminal Gitea support from unchanged Factory UI and eligible Gitea daemon dispatch subject to normal gates and foreign-journal holds and retain the live validation checklist.

#### Scenario: Operator can prepare and recover
- **WHEN** an operator follows the landing guide
- **THEN** examples explain the configured land_token_env assignment in land.env, no bot fallback, expected trust membership, custom config alignment and unchanged GitHub commands
- **THEN** the guide describes update/provenance and final-base races, existing recovery cases and the pending human observation of the first real Gitea merge commit, without claiming CI establishes live permissions or merge behavior

#### Scenario: Landing guide states current daemon scope
- **WHEN** an operator enters through docs/landing.md Support boundaries
- **THEN** the obsolete blanket disabled-dispatch claim is locally corrected or explicitly superseded with current enrollment guidance, without changing terminal human-token landing or GitHub-only Factory Land
- **THEN** daemon status and worker recovery retain their GitHub-only boundaries and existing landing commands, examples and tests remain intact


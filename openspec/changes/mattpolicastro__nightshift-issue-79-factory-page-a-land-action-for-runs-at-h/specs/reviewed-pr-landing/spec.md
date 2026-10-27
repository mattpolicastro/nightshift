## Purpose

Bind landing's finish call to its validated head at the run lock without changing attestation, successor-head handling or other gates.

## MODIFIED Requirements

### Requirement: Eligible run state

A matching run SHALL be paused at `human_checks` or already `done` before landing may mutate anything. Landing SHALL invoke finish only for the former state, after the human-check gate passes, supplying the validated PR head through `--expect-head` for verification under the run lock.

#### Scenario: Finish at human checks
- **WHEN** the matching run is paused at `human_checks` and human checks pass
- **THEN** landing refreshes eligibility and invokes `nightshift finish <change> --expect-head <validated-pr-head>` once through the runner before waiting for CI
- **THEN** it accepts the resulting head only after status reports the same run done and its finished head equals the remote PR head

#### Scenario: Wrong state refused
- **WHEN** the matching run is running, aborted, or paused for any reason other than `human_checks`, including `ready_to_publish`
- **THEN** landing refuses immediately with that state and makes no checklist edit, finish, update or merge call

#### Scenario: Finish fails safely
- **WHEN** finish fails or its refreshed result does not establish the same done run and finished remote head
- **THEN** landing exits nonzero without waiting for CI or merging and leaves existing run recovery intact

#### Scenario: Revision wins the finish handoff
- **WHEN** revision republishes after land's final identity check but before finish obtains the run lock, including with unchanged pause_id
- **THEN** finish receives the head land validated, refuses the new branch head under the lock, and no archive, finish push, ready transition or merge occurs
- **THEN** this protection applies to terminal and Factory invocations without changing land's attestation or successor-head gates

### Requirement: Finish through the interpreter

Landing SHALL invoke eligible specloop finish as `[sys.executable, "-m", "nightshift.cli", "finish", change, "--expect-head", validated_pr_head]` through the existing injectable runner.

#### Scenario: Eligible finish uses the current interpreter
- **WHEN** a matching run paused at human checks passes all existing pre-finish gates
- **THEN** landing invokes finish once using the current interpreter, passing the just-validated pre-finish PR head and preserving the change argument and 300-second limit
- **THEN** finish failure or failure to establish the same done run and matching remote head refuses before CI or merge; direct PRs and initially done runs still skip finish

## Purpose

Bind landing's finish call to its validated head at the run lock, migrate failed-push recovery, and explicitly scope unchanged attestation implementation guarantees to one invocation. Preserve successor-head handling and other gates.

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
- **THEN** landing exits nonzero without waiting for CI or merging; direct finish/act without the optional head guard retains existing run recovery, while guarded landing refuses a local/remote head mismatch

#### Scenario: Revision wins the finish handoff
- **WHEN** revision republishes after land's final identity check but before finish obtains the run lock, including with unchanged pause_id
- **THEN** finish receives the head land validated, refuses the new branch head under the lock, and no archive, finish push, ready transition or merge occurs
- **THEN** this protection applies to terminal and Factory invocations without changing land's attestation or successor-head gates

#### Scenario: Archived commit was not pushed
- **WHEN** finish archives local head H into A but its remote push fails, leaving human_checks and remote head H, and terminal landing is retried
- **THEN** landing passes H to finish again and locked finish refuses local A before another archive, push or ready call, and landing does not wait for CI or merge
- **THEN** Factory also refuses this local/remote mismatch rather than adopting A or falling back to unguarded finish
- **WHEN** the operator inspects the local archive and remote state and completes direct finish or act finish without --expect-head
- **THEN** existing recovery reuses A without rearchiving, pushes it and marks the run done; a subsequent terminal landing skips finish and applies fresh human-check, CI, base and merge gates

#### Scenario: Archived commit was already pushed
- **WHEN** the archive push succeeded but ready or state completion failed, leaving human_checks with matching local and remote head A
- **THEN** landing passes A to finish and guarded recovery retains the existing archive/push/ready recovery and validated successor-head checks

### Requirement: Finish through the interpreter

Landing SHALL invoke eligible specloop finish as `[sys.executable, "-m", "nightshift.cli", "finish", change, "--expect-head", validated_pr_head]` through the existing injectable runner.

#### Scenario: Eligible finish uses the current interpreter
- **WHEN** a matching run paused at human checks passes all existing pre-finish gates
- **THEN** landing invokes finish once using the current interpreter, passing the just-validated pre-finish PR head and preserving the change argument and 300-second limit
- **THEN** finish failure or failure to establish the same done run and matching remote head refuses before CI or merge; direct PRs and initially done runs still skip finish

### Requirement: Stable check attestation

For listed checks and `--checks-done`, landing SHALL look up `<!-- nightshift:land-checks:<pr>:<head sha> -->`, update its unique trusted match or POST once when absent, and record actor, items, sources and pre-finish head. Within each invocation it SHALL reconcile ambiguous creates before continuing and SHALL NOT repeat the POST. Fresh invocations SHALL repeat lookup without a cross-attempt create guard; late remote commits can produce duplicates.

#### Scenario: Repeated attestation updates
- **WHEN** `--checks-done` is repeated for the same PR head, including after checklist edits succeeded but attestation failed, and lookup finds one trusted matching comment
- **THEN** checked items remain eligible for attestation and the matching comment is updated rather than reposted, identifying the current authenticated actor as recording checks through `land`
- **THEN** a different head uses a different marker

#### Scenario: Lost comment response
- **WHEN** an attestation POST succeeds remotely but its response is lost and its marker is on a later comment page
- **THEN** reconciliation finds and reuses that comment without a second POST

#### Scenario: Uncertain attestation refused
- **WHEN** marker lookup fails, is ambiguous or untrusted, or an ambiguous POST cannot be reconciled
- **THEN** that invocation refuses without a blind POST retry or proceeding to finish or merge

#### Scenario: Late create crosses invocation boundaries
- **WHEN** invocation A loses its POST response and refuses after reconciliation finds none, invocation B finds none and posts, and A's POST subsequently commits remotely
- **THEN** two keyed comments may exist even after A's local process group has drained; B's valid acknowledgement need not cause another lookup and no cross-attempt guard is added
- **THEN** a subsequent invocation observing both refuses with `duplicate marker matches; resolve duplicates before retrying` before finish or merge
- **WHEN** the operator inspects effects, deletes one duplicate and explicitly retries while eligible
- **THEN** land updates the sole remaining trusted comment and may land only through all existing head, finish, CI and merge gates

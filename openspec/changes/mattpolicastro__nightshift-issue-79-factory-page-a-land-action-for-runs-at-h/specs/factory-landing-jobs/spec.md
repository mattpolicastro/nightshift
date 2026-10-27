## Purpose

Provide small detached Factory landing attempts triggered by explicit human confirmation, using existing landing logic and retained records outside daemon job control.

## ADDED Requirements

### Requirement: Operator execution isolation

Only confirmed Factory submission SHALL start a Factory landing attempt. Its private module SHALL remain unreachable from daemon, serve, intake, job-control and canonical CLI execution paths, preserving existing dependency assertions.

#### Scenario: Background paths cannot start landing
- **WHEN** dependency traversal and launch spies inspect daemon, supervisor, job-control and canonical/alias CLI paths
- **THEN** none imports or launches nightshift.factory_land or nightshift.land, and no new landing subcommand or job kind exists
- **THEN** the existing dependency checker and every assertion remain intact

#### Scenario: Plugin uses private literal transport
- **WHEN** a confirmed submission uses configured tool and checkout paths containing spaces
- **THEN** the server invokes configured uv with literal run --no-sync python -m nightshift.factory_land arguments and trusted cwd/PATH, without shell or caller-selected executable
- **THEN** only private start transport launches the detached worker and ordinary status/act retain their existing executor

### Requirement: Published pause identity

A Land offer SHALL bind change, canonical PR, pause_id, published head and pause since for a readable human_checks pause. Admission and worker startup SHALL revalidate every field and the remote head before acting.

#### Scenario: Only verifiable human checks qualify
- **WHEN** status includes human-check, other-paused, running, done, aborted, erroneous or incomplete runs
- **THEN** only paused human_checks with valid complete identity receives an offer, preserving zero and fractional since values
- **THEN** missing metadata produces a diagnostic without breaking ordinary Factory reads

#### Scenario: Republication invalidates confirmation
- **WHEN** revision republishes the same change and PR with unchanged pause_id but a changed head or since after confirmation opens
- **THEN** admission and independently worker startup refuse the old offer as stale before check edits, finish or merge
- **THEN** either a changed head with unchanged since or changed since with unchanged head suffices

#### Scenario: Pause blocks new execution
- **WHEN** the daemon pause file exists at admission or worker startup, or fresh identity cannot be read
- **THEN** new execution refuses with its reason, status and Stop remain usable, and already-started landing is not automatically killed

### Requirement: Confirmed landing handoff

The worker SHALL supply the confirmed identity to unchanged nightshift.land through its injectable runner, use checks-done and preserve land as the sole merge path. It SHALL NOT duplicate landing gates or silently adopt an unrelated head.

#### Scenario: Head changes at handoff
- **WHEN** PR or pause changes between preflight and the actual initial responses consumed by land, or before a pre-finish mutation
- **THEN** the adapter refuses stale confirmation before that mutation rather than authorizing the newly discovered head or pause

#### Scenario: Landing gates and head pin remain authoritative
- **WHEN** unchanged land executes through the adapter and finish or a validated base update advances the head
- **THEN** existing checklist, attestation, finish, CI, provenance and protection gates decide the outcome and merge uses --match-head-commit with land's verified successor head
- **THEN** land.py and existing landing assertions stay unchanged and unknown pre-finish mutation command shapes refuse

### Requirement: Retained attestation create evidence

The Factory adapter SHALL persist create intent before delegating an attestation POST and retain it across attempts. An unresolved create SHALL prevent further mutations until reconciled through land's existing selectors; empty lookup SHALL NOT authorize another create. The adapter SHALL leave land.py unchanged.

#### Scenario: Delayed create blocks a valid second acknowledgement
- **WHEN** a first POST times out without a visible marker and a second confirmed attempt sees none, with the fake service configured to acknowledge a second POST validly
- **THEN** the second attempt refuses before any mutation, finish or merge, and the service receives no second POST
- **THEN** the same refusal occurs with an incomplete acknowledgement configured; safety does not depend on the second response shape
- **WHEN** the first POST commits late and a fresh eligible attempt is confirmed
- **THEN** existing paginated lookup and trusted-marker selection reconcile its ID, land reuses that marker and lands with exactly one create

#### Scenario: Create evidence survives interruption
- **WHEN** execution dies after a remote create but before recording its response, or unresolved evidence is older than the newest 20 attempts
- **THEN** subsequent execution reconciles the retained intent before any mutation and refuses absent, unreadable, ambiguous or untrusted matches
- **THEN** neither a terminal result, a changed head, elapsed time nor a new confirmation clears unresolved evidence
- **WHEN** intent cannot be persisted before POST
- **THEN** no POST is sent and the attempt retains a persistence refusal

#### Scenario: Missing recorded marker cannot be recreated
- **WHEN** a prior create is known or was reconciled but the marker disappears before land would issue a POST for the same key
- **THEN** the adapter refuses that POST and requests inspection instead of replacing the marker or fabricating an acknowledgement

#### Scenario: Late attestation duplicate recovery
- **WHEN** reconciliation of retained unresolved intent finds two trusted matching markers, including duplicates from earlier unguarded invocations
- **THEN** unchanged land selection refuses with `duplicate marker matches; resolve duplicates before retrying` and the Factory adapter sends no mutation
- **WHEN** one duplicate comment is removed and a fresh eligible attempt is confirmed
- **THEN** the adapter reconciles the remaining marker and unchanged land lands without another POST

### Requirement: Detached lifetime lock

Each attempt SHALL retain a log and small records and execute detached. The worker SHALL hold one per-PR flock for its own lifetime, refuse contention and release it on exit without receipts, descriptor inheritance or manual release. Acquiring flock SHALL NOT bypass verification that prior execution groups have drained.

#### Scenario: Caller exits while landing runs
- **WHEN** start returns its attempt ID and the RPC, page or plugin exits while land runs
- **THEN** the detached worker and landing child continue, progress remains readable and its lock stays held until worker exit
- **THEN** complete output and outcome or interruption diagnostics survive restart

#### Scenario: Concurrent click refuses and exit permits retry
- **WHEN** another confirmation targets the same canonical PR while the first worker runs
- **THEN** the contender records a lock refusal and cannot invoke land
- **WHEN** the first worker exits and another explicit confirmation is made
- **THEN** the lock is available without release and prior-group drainage, retained-create reconciliation, fresh identity and land's gates decide the new attempt, with no replay or automatic retry

### Requirement: Prior execution exclusion

Before launching an invocation child, the worker SHALL persist its process identity and, under flock, prove earlier recorded execution groups are absent through a kernel existence probe. Missing worker identity, a terminal result or lock release SHALL NOT establish drainage. Unverifiable inspection SHALL refuse new execution.

#### Scenario: Worker death leaves a mutating child
- **WHEN** only the worker dies while its invocation child waits before a GitHub write and all five confirmation fields remain unchanged
- **THEN** a new worker may acquire flock but refuses before invoking land or making mutations while the earlier group has an executing member
- **WHEN** the earlier tree exits and a fresh confirmation is submitted
- **THEN** the worker verifies drainage under flock before permitting execution, retaining all other admission and reconciliation gates

#### Scenario: Exclusion evidence fails closed
- **WHEN** a prior attempt has a terminal result but a surviving child, or the group contains only zombies, its existence probe fails or its identity record is corrupt
- **THEN** new invocation refuses with a retained reason, including for attempts older than the newest 20; only ESRCH from a safe group probe establishes absence
- **THEN** status reads do not clear exclusion and no orphan group is signalled or automatically retried

#### Scenario: Process record precedes child creation
- **WHEN** the worker dies before publishing process metadata or immediately after spawning its invocation child
- **THEN** the former case has started no invocation and the latter retains a durable group record that blocks overlapping execution until drainage is proved
- **THEN** all landing and finish subprocesses remain in that group without inherited lock descriptors or another session

### Requirement: Verified landing Stop

Stop SHALL verify recorded worker PID, start time and safe group membership before sending SIGTERM to its group. Missing or unverifiable identity SHALL refuse; Stop SHALL NOT imply rollback or guaranteed termination.

#### Scenario: Stop reaches the normal tree
- **WHEN** Stop targets a verified worker with landing and finish descendants in the same new session/group
- **THEN** SIGTERM reaches that group, normal shutdown records stopped and releases the lock, and logs remain
- **THEN** signal request alone is not confirmed completion and a previously terminal result remains intact

#### Scenario: Missing worker refuses Stop
- **WHEN** the worker is gone, its PID is reused, start identity is unreadable or its group is unsafe
- **THEN** Stop explicitly refuses without signalling, even if descendants survive
- **THEN** recovery directs waiting for descendants or killing them by hand, and a fresh attempt still verifies group drainage before acting, without a jobs hold or release mechanism

### Requirement: Bounded retained status

Status SHALL return newest N attempts (default/max 20), total count and separate named current progress, with JSON below 1 MiB. Reads SHALL preserve local evidence and never launch, signal, deliver, compact or repair attempts.

#### Scenario: Long history retains current progress
- **WHEN** more than 20 attempts include oversized escaped diagnostics and a running attempt older than the newest page
- **THEN** bounded newest-first summaries and total are returned and named status still returns current progress
- **THEN** UTF-8 output remains below 1 MiB and the 2 MiB subprocess cap with explicit truncation, without reading or changing full logs

#### Scenario: Progress and refusal survive reads
- **WHEN** an attempt progresses, succeeds, refuses or loses its worker before writing a result
- **THEN** status retains phase/outcome/reason or an explicit interruption diagnostic without inferring success, creating an uncertain hold or retrying
- **THEN** overdue is display-only after 2400 seconds, damaged records do not erase healthy results and evidence remains until manual pruning, preserving unresolved-create and undrained-group records

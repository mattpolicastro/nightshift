# factory-landing-jobs Specification

## Purpose
Provide small detached Factory landing attempts triggered by explicit human confirmation, using existing landing logic and retained records outside daemon job control.

## Requirements

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

The worker SHALL supply the confirmed identity to nightshift.land through its injectable runner, use checks-done and preserve land as the sole merge path. It SHALL NOT duplicate landing gates or silently adopt an unrelated head.

#### Scenario: Head changes at handoff
- **WHEN** PR or pause changes between preflight and the actual initial responses consumed by land, or before a pre-finish mutation
- **THEN** the adapter refuses stale confirmation before that mutation rather than authorizing the newly discovered head or pause

#### Scenario: Landing gates and head pin remain authoritative
- **WHEN** land executes through the adapter and finish or a validated base update advances the head
- **THEN** existing checklist, attestation, finish, CI, provenance and protection gates decide the outcome and merge uses --match-head-commit with land's verified successor head
- **THEN** land's only implementation change passes the validated PR head to finish, all existing gate assertions remain, and unknown pre-finish mutation command shapes refuse

### Requirement: Terminal attestation behavior

Factory SHALL delegate attestation to land's existing within-invocation reconciliation and duplicate-marker refusal. It SHALL NOT persist create intent, unresolved-key evidence or a cross-attempt POST guard.

#### Scenario: Late attestation duplicate recovery
- **WHEN** an earlier POST commits late after another invocation created the same marker and a subsequent Factory attempt reads both comments
- **THEN** land refuses with `duplicate marker matches; resolve duplicates before retrying`, the row retains that reason, and this attempt neither finishes nor merges
- **WHEN** one duplicate comment is deleted and a fresh eligible attempt is confirmed
- **THEN** land updates the remaining marker and lands through its existing gates without another create

#### Scenario: No cross attempt create restriction
- **WHEN** a prior invocation failed before sending its POST or could not reconcile a lost response, and a fresh eligible invocation finds no marker
- **THEN** Factory adds no historical-create restriction; land's unchanged attestation logic alone decides whether to POST or refuse
- **THEN** a valid POST acknowledgement need not trigger another lookup, and all head, finish, CI and merge gates still apply

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
- **THEN** the lock is available without release and prior-group drainage, fresh identity and land's gates decide the new attempt, with no replay or automatic retry

### Requirement: Prior execution exclusion

Before launching an invocation child, the worker SHALL persist its process identity and, under flock, prove earlier recorded execution groups are absent through a kernel existence probe. Missing worker identity, a terminal result or lock release SHALL NOT establish drainage. Unverifiable inspection SHALL refuse new execution.

#### Scenario: Worker death leaves a mutating child
- **WHEN** only the worker dies while its invocation child waits before a GitHub write and all five confirmation fields remain unchanged
- **THEN** a new worker may acquire flock but refuses before invoking land or making mutations while the earlier group has an executing member
- **WHEN** the earlier tree exits and a fresh confirmation is submitted
- **THEN** the worker verifies drainage under flock before permitting execution, retaining all other admission and landing gates

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
- **THEN** overdue is display-only after 2400 seconds, damaged records do not erase healthy results and evidence remains until manual pruning, preserving undrained-group records

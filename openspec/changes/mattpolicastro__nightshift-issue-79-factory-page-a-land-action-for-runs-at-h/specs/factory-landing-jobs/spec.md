## Purpose

Invoke existing reviewed landing from an explicit Factory confirmation through durable, inspectable background work, without introducing autonomous landing or changing landing's gates.

## ADDED Requirements

### Requirement: Published pause identity

The landing gateway SHALL offer landing only for a readable run paused at `human_checks`, binding change, canonical PR URL, pause_id, published head SHA and finite pause.since. It SHALL revalidate every field against fresh status and the remote PR at admission and worker startup.

#### Scenario: Only verifiable human checks qualify
- **WHEN** the gateway reads human-check, other-paused, running, done, aborted, missing and erroneous runs
- **THEN** only human-check runs with complete valid identity receive an offer; unavailable or null metadata produces no offer and a diagnostic without launching anything
- **THEN** epoch zero and fractional pause timestamps are preserved without rounding

#### Scenario: Republication invalidates confirmation
- **WHEN** revision republishes the same change to the same PR with an unchanged `human_checks:` pause_id but a new head or pause.since
- **THEN** the earlier confirmation is refused as stale at admission or worker startup, before any check edit, finish or merge
- **THEN** a changed timestamp with unchanged head and a changed head with unchanged timestamp each independently invalidate the offer

### Requirement: Durable request replay

The gateway SHALL bind each valid request UUID to its immutable confirmed payload and retained outcome. It SHALL resolve a matching existing UUID before checking current run eligibility or pause, refuse changed payloads, and serialize new admissions per canonical PR.

#### Scenario: Completed UUID survives changed eligibility
- **WHEN** a start response is lost and the same UUID and payload are retried after the run becomes done, the PR merges, the daemon pauses or the gateway restarts
- **THEN** the retained outcome and attempt ID are returned without launching, checking current eligibility as a new admission, or modifying the original evidence
- **THEN** an originally refused UUID also returns its original refusal

#### Scenario: Conflicting or concurrent requests refuse
- **WHEN** a UUID is reused with different fields, or two new UUIDs target the same canonical PR concurrently
- **THEN** conflicting reuse preserves the original outcome, and at most one new attempt starts; the other request receives a retained reason
- **THEN** another change name, tab or gateway process cannot bypass a live, uncertain or unreleased stopped hold

### Requirement: Gateway command contract

The canonical CLI SHALL expose bounded landing status, request resolution and confirmed start commands with singular version-1 JSON outcomes and literal arguments. New admissions SHALL require checks_done and refuse a daemon pause or failed eligibility read before launch.

#### Scenario: Parse and dispatch safely
- **WHEN** valid status, resolve and start argv or invalid identifiers, timestamps, flags and paths are supplied
- **THEN** valid commands retain argument boundaries and return one validated document; malformed syntax exits 2 before state mutation, read success and accepted replay exit 0, and admission refusal exits 1 with its reason
- **THEN** existing canonical and alias CLI behavior remains unchanged and no client can supply executable, cwd or arbitrary command options

#### Scenario: Pause blocks only new work
- **WHEN** the daemon pause file exists before a new admission or worker startup, or a fresh eligibility read fails
- **THEN** new execution refuses with a retained reason, while read-only status and matching UUID replay remain available
- **THEN** a worker already past startup may complete while paused, and reading its result does not deliver or launch anything

### Requirement: Durable detached launch

An accepted landing SHALL persist request identity, timing and a hold before spawning an independent worker with an inherited lifetime lock. Loss of the caller or response SHALL NOT cancel the worker or cause the accepted UUID to relaunch.

#### Scenario: Caller exits after launch
- **WHEN** the gateway exits after spawn but before acknowledgement, or the plugin unloads during landing
- **THEN** the detached job retains its lock and evidence, is discoverable through status and jobs, and replay returns its identity without another worker
- **THEN** subprocesses retain the lifetime FD so a dead wrapper with executing descendants does not look idle

#### Scenario: Interrupted launch retains uncertainty
- **WHEN** launch fails before spawn, or an accepted worker dies before writing its result
- **THEN** known launch failure and uncertain completion remain distinguishable with retained evidence, and no read or repeated UUID restarts work or claims merge success
- **THEN** an uncertain attempt blocks a new attempt pending operator inspection; it is never falsely recorded as a confirmed stopped job

### Requirement: Confirmed landing handoff

The worker SHALL invoke existing land with checks_done through a runner bound to the confirmed identity. It SHALL reject changed initial reads and stale pre-finish mutations, preserve land's validated head transitions, and retain its final head-conditioned merge and refusal behavior.

#### Scenario: Head changes after preflight
- **WHEN** the PR head or pause changes between startup validation and the initial reads or pre-finish mutations made by land
- **THEN** the adapter refuses before recording checks against changed inputs and preserves the original reason
- **THEN** an unrecognized pre-finish mutation is refused rather than bypassing the adapter

#### Scenario: Existing landing gates remain authoritative
- **WHEN** a confirmed attempt passes startup and land performs finish, a proven base update and CI
- **THEN** the actual land entry point receives the confirmed-head runner and checks_done, and its merge uses `--merge --match-head-commit` with the resulting verified head
- **THEN** direct CLI landing, checklist selection, attestation, refusal, timeout and protection behavior remain unchanged; daemon and supervisor never import or invoke landing

### Requirement: Retained progress and outcome

The worker SHALL persist bounded display progress and its success, refusal or uncertain failure while retaining complete local logs and result detail. Stop intent SHALL take precedence over later progress or result publication.

#### Scenario: Progress becomes a retained result
- **WHEN** an attempt advances through validation, finishing, CI and merge, or land returns a specific refusal
- **THEN** status reports the latest observed phase and durable outcome without waiting for the entire job, preserving land's refusal reason and never equating launch or exit without confirmed success to a merge
- **THEN** later reads and gateway restart retain the outcome; stopped work cannot be reported successful because of a late result

### Requirement: Bounded landing history

Landing status SHALL return at most the newest requested N attempts, default and maximum 20, plus total count, and a separate current attempt for named queries. It SHALL bound projected output below 1 MiB without reading unbounded log/result bodies or deleting evidence.

#### Scenario: Long history preserves current progress
- **WHEN** more than 20 retained attempts and oversized escaped diagnostics exist, with a current attempt older than the newest page
- **THEN** the global read returns newest-first bounded history and the full count, and a named read still returns that current attempt's progress independently of the history window
- **THEN** serialized UTF-8 output stays below 1 MiB and the subprocess's 2 MiB limit, truncation is explicit, and full on-disk evidence remains byte-identical

#### Scenario: Read faults do not erase evidence
- **WHEN** a selected summary or run status is unreadable, the run disappears, or a status request is repeated
- **THEN** healthy retained attempts remain available with explicit diagnostics, and no read repairs files, acquires launch authority, clears holds or acknowledges delivery
- **THEN** full logs and unselected historical result bodies are not read merely to produce the bounded page

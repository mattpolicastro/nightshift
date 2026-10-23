## Purpose

Isolate specloop enrollment failures and recover leftover worker claims without blocking healthy repositories. Runtime edits are scoped to `nightshift/config.py`, `nightshift/daemon.py` and `nightshift/specloop_intake.py`, with associated tests.

## ADDED Requirements

### Requirement: Repository manifest health

Configuration loading SHALL retain repositories whose specloop manifest is omitted, missing, unreadable, non-regular or invalid. Polling SHALL validate each specloop repository's manifest with the existing schema and report failures as repository health, preserving worker defaults and configuration-shape errors. Every enrollment-manifest read SHALL first require a regular file; FIFOs and devices SHALL NOT be opened. File repairs SHALL take effect on a later poll without configuration reload.

#### Scenario: Bad manifest isolates the repository
- **WHEN** configuration enrolls a healthy worker, a healthy specloop repository and a specloop repository with an absent, missing, unreadable, malformed or schema-invalid manifest
- **THEN** configuration loads, the bad repository reports health failure and launches no new draft or admission, and healthy repositories continue normal work

#### Scenario: Nonregular manifest is never read
- **WHEN** the configured manifest is a directory, FIFO or device, or a valid path is replaced by one before the next poll
- **THEN** validation rejects it before opening or reading it and other repositories can complete that poll

#### Scenario: Repair clears the hold
- **WHEN** an invalid manifest is repaired at the same configured path between polls
- **THEN** the next eligible poll validates it, clears that configuration health problem and permits retained triggers to proceed without rearming or restart
- **THEN** a continuing problem produces one outage notification, while a later outage after recovery can notify again

#### Scenario: Configuration shape remains strict
- **WHEN** config TOML is malformed, an engine is unknown, or a supplied manifest path is empty or has an invalid type
- **THEN** configuration loading still rejects that shape error, while valid relative paths resolve beside the config and tilde paths retain expansion

### Requirement: Health gates new work only

Repository configuration health SHALL gate new drafting and admission, including spec-stage drafting, without consuming triggers or escalating issues. Already-admitted runs SHALL retain publication, build-stage revision and completion delivery using their own manifest and recorded branch. Successful later stages SHALL NOT clear an unresolved configuration error. Existing pause, authorization, feedback-consumption and durable-cancellation requirements SHALL remain in force.

#### Scenario: Unhealthy repository still publishes
- **WHEN** an admitted daemon-managed run is ready to publish with a valid copied manifest and safe recorded branch but its enrollment manifest is bad or missing, or its repository spelling is unsupported for new names
- **THEN** the unpaused daemon publishes to its recorded PR and delivers human checks under existing publication rules, while retaining repository health failure and holding new work

#### Scenario: Unhealthy repository still takes build feedback
- **WHEN** an admitted run has authorized unconsumed build feedback and its enrollment is unhealthy
- **THEN** build-stage revision and successful consumption/delivery proceed on the recorded identity without reading the enrollment manifest or starting a spec draft

#### Scenario: Saved work survives a hold
- **WHEN** a dispatched draft or admission completes before or during a configuration hold
- **THEN** its job and result remain recoverable without redispatch; delivery requiring the unavailable template waits for repair without item escalation
- **THEN** an already-committed admission can complete notifications without another admit or healthy enrollment template

#### Scenario: Pause and stop still prevail
- **WHEN** an unhealthy repository has a publishable or revisable admitted run while the daemon is paused, or a pending job has durable stop intent
- **THEN** health bypass does not override pause or cancellation and no prohibited launch or delivery occurs

### Requirement: Worker switch reconciliation

On unpaused startup/polls for specloop repositories, the daemon SHALL reconcile leftover working issues before new intake even when their manifest is unhealthy. It SHALL release work with no surviving worktree to ready, hold surviving work for human inspection, preserve branches/worktrees and never start a worker for that repository. Unknown state SHALL fail locally without guessing. Worker repositories SHALL retain existing recovery behavior.

#### Scenario: Switch recovers working issues
- **WHEN** a formerly worker repository starts as specloop with orphaned working issues, claims with missing worktrees and claims with surviving worktrees
- **THEN** the first two cases transition to ready and surviving-work cases transition to needs-human with a phase/branch/location explanation
- **THEN** outcomes record recovery, surviving work remains intact, no worker starts for the switched repository and other worker repositories still recover normally

#### Scenario: Recovery does not need a manifest
- **WHEN** the switched repository's manifest is missing or invalid
- **THEN** leftover worker recovery still runs while new specloop drafting and admission remain held by repository health

#### Scenario: Unknown recovery state is retained
- **WHEN** a claim is unreadable or a required GitHub read fails
- **THEN** evidence is preserved, repository health reports failure and no uncertain release or new intake occurs
- **THEN** independent repositories and eligible admitted-run stages continue

### Requirement: Durable switch recovery

Switch recovery SHALL save intent and a claim snapshot before remote changes, reconcile current labels and marked comments on later polls, and complete durable outcome bookkeeping before claim cleanup. Pending recovery SHALL survive restart even if working has disappeared. Pause SHALL guard external recovery operations. This requirement does not change retries inside the existing GitHub transport.

#### Scenario: Lost transition response recovers
- **WHEN** a recovery label transition succeeds remotely but its response is lost and the daemon restarts before recording completion
- **THEN** the pending journal is processed even without the working label, local outcome bookkeeping completes and the claim is cleared only after durable bookkeeping
- **THEN** poll-level recovery does not repeat a completed label transition or create another explanatory comment

#### Scenario: Partial recovery retries safely
- **WHEN** a comment, label write or local journal/outcome write fails, or pause begins before the next external operation
- **THEN** recovery retains intent and work evidence, withholds unsafe further operations and resumes from reconciled progress on an eligible later poll
- **THEN** a conflicting human label change is preserved and reported instead of blindly rearming the issue

## Purpose

Preserve isolated manifest health and durable recovery after the sole-engine transition.

## MODIFIED Requirements

### Requirement: Repository manifest health

Omitted or unusable manifests SHALL cause repository health failures, not config-load failures. Every enrollment-manifest read SHALL require a regular file first. Readiness SHALL use `specloop.manifest.template()` with safe change/branch overrides, accepting omitted per-change fields without persisting probe values. Other schema and configuration-shape checks SHALL remain strict; omitted engines SHALL default to specloop and explicit worker engines SHALL be rejected. File repair SHALL clear the hold on a later eligible poll.

#### Scenario: Bad manifest isolates the repository
- **WHEN** configuration enrolls a healthy default-engine repository, a healthy explicit-specloop repository and a repository with an absent, missing, unreadable, malformed or schema-invalid manifest
- **THEN** configuration loads, the bad repository reports health failure and launches no new draft or admission, and healthy repositories continue normal work

#### Scenario: Nonregular manifest is never read
- **WHEN** the configured manifest is a directory, FIFO or device, or a valid path is replaced by one before the next poll
- **THEN** validation rejects it before opening or reading it and other repositories can complete that poll

#### Scenario: Template omits per change fields
- **WHEN** an otherwise valid regular-file template omits `run.change`, `run.branch` or both
- **THEN** readiness applies safe representative overrides before parsing, reports no configuration health failure and leaves the template bytes unchanged
- **THEN** an unrelated missing required field such as `run.repo` still causes repository-local health failure

#### Scenario: Dispatch uses actual change identity
- **WHEN** an authorized eligible issue draft, new admission or spec-stage revision uses a healthy template with omitted or default per-change fields
- **THEN** dispatch passes the actual new or recorded change and branch as overrides, and that effective template passes the same parser
- **THEN** no receipt, PR lookup, command or saved run identity uses readiness probe values or unrelated template defaults

#### Scenario: Repair clears the hold
- **WHEN** an invalid manifest is repaired at the same configured path between polls
- **THEN** the next eligible poll validates it, clears that configuration health problem and permits retained triggers to proceed without rearming or restart
- **THEN** a continuing problem produces one outage notification, while a later outage after recovery can notify again

#### Scenario: Configuration shape remains strict
- **WHEN** config TOML is malformed, an engine is unknown, or a supplied manifest path is empty or has an invalid type
- **THEN** configuration loading still rejects that shape error, while valid relative paths resolve beside the config and tilde paths retain expansion

### Requirement: Worker switch reconciliation

On unpaused startup/polls for every enrolled repository, the daemon SHALL reconcile leftover working issues before new intake even when their manifest is unhealthy. It SHALL release work with no surviving worktree to ready, hold surviving work for human inspection, preserve branches/worktrees and never start a worker for that repository. Unknown state SHALL fail locally without guessing. Legacy worker resume, ship and destructive reconciliation paths SHALL NOT run.

#### Scenario: Switch recovers working issues
- **WHEN** a formerly worker repository starts as specloop with orphaned working issues, claims with missing worktrees and claims with surviving worktrees
- **THEN** the first two cases transition to ready and surviving-work cases transition to needs-human with a phase/branch/location explanation
- **THEN** outcomes record recovery, surviving work remains intact, no worker starts for the switched repository and independent repositories still receive normal specloop polling and safe leftover-claim recovery

#### Scenario: Recovery does not need a manifest
- **WHEN** the switched repository's manifest is missing or invalid
- **THEN** leftover worker recovery still runs while new specloop drafting and admission remain held by repository health

#### Scenario: Unknown recovery state is retained
- **WHEN** a claim is unreadable or a required GitHub read fails
- **THEN** evidence is preserved, repository health reports failure and no uncertain release or new intake occurs
- **THEN** independent repositories and eligible admitted-run stages continue

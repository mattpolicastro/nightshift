## Purpose

Preserve historical worker evidence and compatibility APIs while retiring worker
records from the active attention display.

## MODIFIED Requirements

### Requirement: Historical evidence remains available

Retirement SHALL preserve historical state, outcomes, transcripts, branches and
worktrees without migration. Claim cleanup SHALL occur only under existing durable
recovery after saving its outcome. Existing outcome database tables and historical
APIs SHALL remain readable with unchanged resolution behavior; attention SHALL
stop rendering their records and SHALL NOT refresh or reconcile historical
outcomes. Attention SHALL instead follow the version-1, stale/offline and
exit-code contracts in `attention`. Explicit historical `outcomes.refresh()`
calls SHALL retain their existing resolution and stale-observation behavior.
Startup, polling and reconcile SHALL use safe
leftover-claim recovery, never worker execution or destructive legacy reconciliation.

#### Scenario: Historical attention remains readable
- **WHEN** an existing worker outcome database is opened through its historical APIs before and after online or offline CLI/Paseo attention reads
- **THEN** historical entries remain readable with the same schema and resolution rules, while attention omits worker records and shows its current sources
- **THEN** reading history does not start a worker, move state or delete work, and adding a separate repository-snapshot table does not alter historical rows or columns

#### Scenario: Historical reconciliation stays explicit
- **WHEN** online or offline attention reads occur after historical worker outcomes have been recorded, including when repository discovery fails or no longer lists their issues
- **THEN** attention leaves historical events and observations unchanged, returns empty legacy `tasks` and `stale` arrays and reports only current-source failures
- **WHEN** an older caller explicitly invokes `outcomes.refresh()` against those historical records
- **THEN** failed reads retain their prior observations and successful reads resolve closed issues or merged PRs under the existing rules, independently of attention's repository snapshots

#### Scenario: Historical mirroring remains usable
- **WHEN** the retained mirror utility is imported and used for a historical transcript after worker.py is removed, with an omitted or explicit config_root
- **THEN** omission still searches `~/.config/nightshift/claude` through a mirror-owned default, and an explicit root still overrides it without importing the worker engine
- **THEN** symlink placement, Paseo import argv/labels/cwd, transcript parsing and best-effort missing-session/worktree/tool behavior remain unchanged, and no worker execution or credential initialization occurs

#### Scenario: Recovery preserves interrupted work
- **WHEN** retirement encounters surviving, missing or unreadable worker claim evidence
- **THEN** existing durable recovery rules preserve surviving work for human inspection, release known missing work and retain uncertain evidence without restarting the worker

### Requirement: Client command boundaries remain stable

Paseo attention SHALL retain its `nightshift attention --json` command and
installation check, replacing historical data projection with front-door
escalations and compact Factory pointers under `attention`. Factory actions and
new notification commands SHALL use canonical `nightshift` commands; the
one-release specloop alias SHALL preserve existing callers. Host path resolution,
argv boundaries, allowed-action validation, caches, redaction and child cleanup
SHALL remain unchanged.

#### Scenario: Existing clients use surviving commands
- **WHEN** attention, Factory status or an allowed action runs and a specloop pause notification is rendered
- **THEN** their argv or suggested command resolves to the existing surviving CLI, with version-1 attention's additive fields, the same action contract and no worker invocation
- **THEN** CLI and state names follow the rename while internal packages, existing RPC fields, historical database APIs and worker-retirement safeguards remain compatible

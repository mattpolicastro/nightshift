## Purpose

Explain explicit Gitea engine commands and their remaining limits while
preserving daemon, landing and existing operator guidance.

## ADDED Requirements

### Requirement: Explicit forge command guidance

README.md and docs/specloop/README.md SHALL explain manifest-host selection, trusted config and bot credential sources, origin matching, workflow refusal and Gitea WIP publication/finish. Guidance SHALL distinguish explicit engine commands from disabled daemon execution and GitHub-only landing. Existing runnable examples and unrelated guidance SHALL remain valid.

#### Scenario: Operator selects and publishes
- **WHEN** an operator follows the explicit engine publication guidance
- **THEN** they can configure `[run] github` as `forge.example/owner/repo`, uniquely match a Gitea URL, provision `NIGHTSHIFT_FORGE_CONFIG` and the bot token, and use the existing publish/finish commands
- **THEN** docs explain file-wins bot-token precedence, no landing-token or manifest-env fallback, separate git credentials, scheme/host/effective-port/prefix/repository checks and HTTP(S)-only Gitea origins
- **THEN** docs retain implicit/explicit `github.com` compatibility and identify all three forbidden workflow directories

#### Scenario: Explicit commands do not enable the daemon
- **WHEN** an operator compares forge configuration, preflight and engine instructions
- **THEN** the docs distinguish WIP-title drafts and finish's title PATCH/read-back from native draft state or merging
- **THEN** all Gitea daemon stages and pending delivery stay disabled, and arming, landing and unmigrated plugin paths retain their boundaries
- **THEN** existing base preparation and recovery, frozen wrappers, enrollment examples and issue #135 per-step env guidance remain intact

### Requirement: Gitea operation limitations

KNOWN-LIMITATIONS.md SHALL state that finish can overwrite a concurrent human title edit and read-back cannot detect that loss. It SHALL describe critic's 30-second socket timeout and 120-second request-start cutoff, explicitly permitting slowly arriving responses to overrun. It SHALL retain delayed-create duplication risk and mark real Gitea publication as pending human verification.

#### Scenario: Operator understands residual risks
- **WHEN** an operator reads the updated limitations and retry guidance
- **THEN** they learn that GET followed by title PATCH can overwrite an intervening edit, no conflict detection is supplied, and verification proves only the resulting title
- **THEN** they learn that socket timeouts are not elapsed-time bounds, slow response bodies can overrun, and concurrent creators or late visibility can defeat lookup-before-create deduplication
- **THEN** the first real Gitea publish must be checked by a human for WIP PR creation and finish prefix removal; CI does not claim that validation

### Requirement: Forge guidance integrity

Documentation checks SHALL validate scoped structure, relative links and command examples using deterministic physical-line rules and real parsers, without exact explanatory-prose assertions. Missing or ambiguous boundaries SHALL block validation. Existing assertions SHALL be preserved, with only explicitly named fixture-input migrations for changed host selection.

#### Scenario: Examples remain structurally valid
- **WHEN** touched documentation examples and links are validated
- **THEN** exact unique physical headings select passages through the next heading of the same or higher level, and fences, indentation or HTML do not hide recognized lines
- **THEN** command examples parse without dispatch, links resolve, unknown placeholders or ambiguous command syntax fail closed, and no extra raw `[[repos]]` guide block breaks existing extraction

## MODIFIED Requirements

### Requirement: Current limitations

KNOWN-LIMITATIONS.md SHALL remove demonstrated fixed claims, preserve unresolved portions of mixed claims, and describe remaining gaps from issue #17's ledger. It SHALL not claim a gap fixed solely because a related feature shipped. It SHALL describe confirmed stopped-job release as supported recovery and distinguish explicit Gitea engine publication from GitHub-only daemon publication.

#### Scenario: Fixed limitations leave the open list
- **WHEN** the limitations list is reconciled with the merged behavior
- **THEN** it no longer lists serial mutants, unchecked label actors, timestamp-based specloop feedback loss/replay, missing job stop or confirmed-stop release, whole-status failure, missing requested/exhausted-infra attention, idea payloads in argv or the resolved CLI/prepare-prompt gaps as open
- **THEN** deadlines, retention, unresolved crash boundaries, config, credential, quota and platform gaps remain unless a specific fix supports their removal
- **THEN** the blanket claim that engine PR commands have not migrated to Gitea is replaced with explicit publish/finish support and the remaining command boundaries

#### Scenario: Remaining ledger gaps are explicit
- **WHEN** a reader checks what automation still cannot guarantee
- **THEN** the list includes admission completing while paused and still building, touchpoint undercount from missing history, descoped JSX style filtering, and GitHub-only automated daemon front door and publication
- **THEN** it describes `nightshift jobs release <id>` as explicit recovery for confirmed stops while retaining the limits on automatic retry, committed effects and legacy/unkeyed revision commit/result risk and unproven machine-crash durability for keyed revisions
- **THEN** it distinguishes configured `mutation_skip` from automatic JSX filtering and local execution plus explicit forge publication from automated hosted front-door support, linking the tracking ledger

### Requirement: Gitea readiness evidence limits

Guidance SHALL distinguish read-only Gitea preflight evidence from permission to run daemon stages. It SHALL state that neither successful checks, a populated allowlist nor clearing a foreign journal enables daemon execution. Explicit engine publish, finish and critic context SHALL be documented separately from those holds.

#### Scenario: Preflight does not enable execution
- **WHEN** an operator follows Gitea configuration and preflight guidance
- **THEN** the guides and limitations explain that daemon drafting, admission, revisions, publication and pending delivery remain disabled pending core authorization and durable transition recovery
- **THEN** real-instance checks remain human verification and are not described as CI proof of live workflow support

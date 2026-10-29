## Purpose

Update the explicit forge command guidance contract for #130 terminal Gitea landing. The existing GitHub-only landing guarantee must change because operator-initiated Gitea landing is now supported; publication/finish guarantees and unrelated documentation requirements remain intact. The scoped landing guide supersedes historical terminal landing wording without requiring edits to the concurrently maintained README and engine guide. Current limitations retains its existing obligations and gains an explicit #130 correction: the limitations ledger must not keep a demonstrated fixed terminal-landing claim. Only that command-boundaries entry is permitted to overlap concurrent #151 documentation work.

## MODIFIED Requirements

### Requirement: Explicit forge command guidance

README.md and docs/specloop/README.md SHALL continue to explain manifest-host selection, trusted config and bot credential sources, origin matching, workflow refusal and Gitea WIP publication/finish. Guidance SHALL distinguish explicit engine commands from disabled daemon execution and human-initiated landing. For #130, `docs/landing.md` SHALL document terminal landing for GitHub and configured Gitea and explicitly supersede historical GitHub-only terminal landing wording in those shared guides; those guides SHALL remain unedited by this change. Factory Land SHALL remain GitHub-only, and arming and unrelated plugin paths SHALL retain their existing boundaries. Existing runnable examples and unrelated guidance SHALL remain valid.

#### Scenario: Operator selects and publishes
- **WHEN** an operator follows the explicit engine publication guidance
- **THEN** they can configure `[run] github` as `forge.example/owner/repo`, uniquely match a Gitea URL, provision `NIGHTSHIFT_FORGE_CONFIG` and the bot token, and use the existing publish/finish commands
- **THEN** docs explain file-wins bot-token precedence, no landing-token or manifest-env fallback, separate git credentials, scheme/host/effective-port/prefix/repository checks and HTTP(S)-only Gitea origins
- **THEN** docs retain implicit/explicit `github.com` compatibility and identify all three forbidden workflow directories

#### Scenario: Explicit commands do not enable the daemon
- **WHEN** an operator compares forge configuration, preflight and engine instructions with the current landing guide
- **THEN** the docs distinguish WIP-title drafts and finish's title PATCH/read-back from native draft state or merging
- **THEN** all Gitea daemon stages and pending delivery stay disabled, Factory Land remains GitHub-only, and arming and unrelated plugin paths retain their boundaries
- **THEN** `docs/landing.md` explicitly supersedes the shared guides' historical GitHub-only terminal landing wording, describes human-initiated terminal Gitea landing with the configured `land_token_env` sourced by land from `~/.config/nightshift/land.env` and no bot-token fallback, and keeps finish's separate bot/git credentials distinct
- **THEN** existing base preparation and recovery, frozen wrappers, enrollment examples and issue #135 per-step env guidance remain intact, without edits to README.md or docs/specloop/README.md

### Requirement: Current limitations

KNOWN-LIMITATIONS.md SHALL remove demonstrated fixed claims, preserve unresolved portions of mixed claims, and describe remaining gaps from issue #17's ledger. It SHALL not claim a gap fixed solely because a related feature shipped. It SHALL describe confirmed stopped-job release as supported recovery and distinguish explicit Gitea engine publication from GitHub-only daemon publication. For #130, it SHALL correct the GitHub-only command boundaries entry to describe human-initiated terminal Gitea landing with the configured human `land_token_env`, link `docs/landing.md` and cite issue #130 or its verified implementation PR. It SHALL preserve unresolved command boundaries and all unrelated limitations, including concurrent #151 loopback guidance, and SHALL keep real Gitea merge validation pending until performed.

#### Scenario: Fixed limitations leave the open list
- **WHEN** the limitations list is reconciled with the merged behavior
- **THEN** it no longer lists serial mutants, unchecked label actors, timestamp-based specloop feedback loss/replay, missing job stop or confirmed-stop release, whole-status failure, missing requested/exhausted-infra attention, idea payloads in argv or the resolved CLI/prepare-prompt gaps as open
- **THEN** deadlines, retention, unresolved crash boundaries, config, credential, quota and platform gaps remain unless a specific fix supports their removal
- **THEN** the blanket claim that engine PR commands have not migrated to Gitea is replaced with explicit publish/finish support and the remaining command boundaries
- **THEN** the blanket unmigrated-landing claim is replaced in the existing command-boundaries entry with terminal Gitea landing support using the human `land_token_env`, a link to `docs/landing.md` and an issue #130 or verified implementation PR citation

#### Scenario: Remaining ledger gaps are explicit
- **WHEN** a reader checks what automation still cannot guarantee
- **THEN** the list includes admission completing while paused and still building, touchpoint undercount from missing history, descoped JSX style filtering, and GitHub-only automated daemon front door and publication
- **THEN** it describes `nightshift jobs release <id>` as explicit recovery for confirmed stops while retaining the limits on automatic retry, committed effects and legacy/unkeyed revision commit/result risk and unproven machine-crash durability for keyed revisions
- **THEN** it distinguishes configured `mutation_skip` from automatic JSX filtering and local execution plus explicit forge publication from automated hosted front-door support, linking the tracking ledger

#### Scenario: Landing correction preserves unresolved boundaries
- **WHEN** task 1.6 reconciles the command-boundaries entry with implemented terminal Gitea landing on the latest documentation revision
- **THEN** only that entry changes in KNOWN-LIMITATIONS.md, preserving concurrent #151 loopback text and every unrelated limitation
- **THEN** it retains GitHub-only Factory Land, arming and unrelated plugin paths, explicit engine publish/finish and critic support, the Gitea-enrollment/GitHub-authority separation, skipped daemon queue queries and ambient named-GitHub authentication
- **THEN** Gitea daemon stages and pending delivery remain disabled, and the first real Gitea landing and merge-commit inspection remain pending human checks rather than claimed CI evidence
- **THEN** neighboring publication title-edit, critic timeout and delayed-create risks and pending live publication checks remain intact
- **THEN** structural validation selects the unique physical `- **GitHub-only command boundaries:**` start through the next column-zero `- ` bullet, physical `^#{1,6} +\S.*$` heading or EOF; missing or duplicate starts block, and fences, HTML and indentation receive no semantic interpretation

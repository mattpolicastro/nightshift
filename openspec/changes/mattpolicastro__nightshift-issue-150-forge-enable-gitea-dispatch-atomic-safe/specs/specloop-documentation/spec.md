## Purpose

Keep the five affected operator guides consistent with enabled Gitea dispatch, settled recovery limits and pending live enrollment checks.

## ADDED Requirements

### Requirement: Gitea dispatch recovery guidance

README.md, KNOWN-LIMITATIONS.md and docs/specloop/README.md SHALL describe
eligible Gitea dispatch, allowlist-only non-bot actors, bot-only canonical marker
evidence and add-first label transitions. docs/arming.md and docs/landing.md
SHALL correct or locally supersede stale blanket holds. Unrelated guidance,
examples, anchors, frozen wrappers and plugin behavior SHALL remain intact.
Documentation SHALL cite issue #150 without inventing an implementation PR.

#### Scenario: Retained trigger requires human inspection
- **WHEN** an operator reads recovery instructions for needs-human alongside a retained source trigger after an ambiguous addition
- **THEN** guidance explains that both labels are a safe hold across polls/restart; after resolving the refusal, removing needs-human permits ordinary authorized receipt processing, without a new automatic recovery path
- **THEN** it distinguishes that recovery from starting a fresh round through existing input or trusted remove-and-reapply rules, and does not promise completed work is rerun

#### Scenario: Delayed creates are an accepted daemon limit
- **WHEN** an operator reads KNOWN-LIMITATIONS.md before unattended Gitea use
- **THEN** it states that deduplication assumes read-after-write consistency and no concurrent creator, and an accepted ambiguous comment or PR POST invisible to later listings can cause a duplicate on a later daemon poll or after restart
- **THEN** it explains the deployed single-node commit-before-response assumption, requires revisiting it for replicated Gitea, and retains pause/inspect/resolve guidance without reservations, permanent false holds or a claim that manual inspection prevents every automatic duplicate

#### Scenario: Live enrollment remains a human check
- **WHEN** offline front-door tests pass and the implementation PR is prepared
- **THEN** the design retains unchecked live preflight, daemon bot-token sourcing with every landing variable absent, matching supervisor forge selection and small human-filed spec:draft/WIP-PR checks
- **THEN** refusal of an unlisted label actor is also a human check, distinct from CI evidence; live tasks are not assigned to implementing agents

## MODIFIED Requirements

### Requirement: Front door guidance

The guide and root README SHALL consistently explain dependency gating,
trigger authorization, revision feedback IDs, file-based drafting input and
isolated status reads, with citations to their implementation PRs. They SHALL
describe specloop as the only engine, with historical worker material clearly identified as retired behavior.

#### Scenario: Dependencies and label actors gate work
- **WHEN** a reader arms an issue or applies an approval or revision label
- **THEN** the docs explain that open or unreadable `blocked-by` issues defer new drafts without removing the trigger, while existing work can be collected and delivered under normal eligibility rules
- **THEN** they explain latest-application authorization for GitHub OWNER, MEMBER or COLLABORATOR and Gitea casefolded exact allowlist membership excluding the bot, independent feedback trust, and trusted reapplication after rejection, citing issue #24 / PR #29 and issue #5 / PR #42

#### Scenario: Revision feedback survives a long round
- **WHEN** comments arrive during a spec or build revision or a consumed comment is edited
- **THEN** the docs explain per-repository/PR, source-qualified consumed IDs; saved round snapshots; later comments remaining eligible; same-ID edits remaining consumed; and new IDs for further requests
- **THEN** they describe consumption after successful spec push or durable build revision success, before notifications and without waiting for publication, plus one-time legacy initialization, citing issue #6 / PR #61

#### Scenario: Input and status remain usable
- **WHEN** a reader submits a long issue thread or inspects a collection containing an unreadable run
- **THEN** the guide explains durable UTF-8 `--idea-file` input instead of idea text in argv, and non-actionable `status_error` rows that preserve healthy runs in version-1 JSON with exit 0
- **THEN** it distinguishes per-run errors from top-level status failure and stale attention snapshots, citing issue #12 / PR #35 and issue #10 / PR #32

### Requirement: Current limitations

KNOWN-LIMITATIONS.md SHALL remove demonstrated fixed claims, preserve unresolved portions of mixed claims, and describe remaining gaps from issue #17's ledger. It SHALL not claim a gap fixed solely because a related feature shipped. It SHALL describe confirmed stopped-job release as supported recovery and distinguish explicit Gitea engine publication from eligible Gitea daemon publication and its normal gates. For #130, it SHALL correct the GitHub-only command boundaries entry to describe human-initiated terminal Gitea landing with the configured human `land_token_env`, link `docs/landing.md` and cite issue #130 or its verified implementation PR. It SHALL preserve unresolved command boundaries and all unrelated limitations, including concurrent #151 loopback guidance, and SHALL keep real Gitea merge validation pending until performed.

#### Scenario: Fixed limitations leave the open list
- **WHEN** the limitations list is reconciled with the merged behavior
- **THEN** it no longer lists serial mutants, unchecked label actors, timestamp-based specloop feedback loss/replay, missing job stop or confirmed-stop release, whole-status failure, missing requested/exhausted-infra attention, idea payloads in argv or the resolved CLI/prepare-prompt gaps as open
- **THEN** deadlines, retention, unresolved crash boundaries, config, credential, quota and platform gaps remain unless a specific fix supports their removal
- **THEN** the blanket claim that engine PR commands have not migrated to Gitea is replaced with explicit publish/finish support and the remaining command boundaries
- **THEN** the blanket unmigrated-landing claim is replaced in the existing command-boundaries entry with terminal Gitea landing support using the human `land_token_env`, a link to `docs/landing.md` and an issue #130 or verified implementation PR citation

#### Scenario: Remaining ledger gaps are explicit
- **WHEN** a reader checks what automation still cannot guarantee
- **THEN** the list includes admission completing while paused and still building, touchpoint undercount from missing history, descoped JSX style filtering, and remaining Gitea foreign-journal, membership, consistency and live-validation limits
- **THEN** it describes `nightshift jobs release <id>` as explicit recovery for confirmed stops while retaining the limits on automatic retry, committed effects and legacy/unkeyed revision commit/result risk and unproven machine-crash durability for keyed revisions
- **THEN** it distinguishes configured `mutation_skip` from automatic JSX filtering and local execution, explicit forge publication and gated automated hosted front-door support, linking the tracking ledger

#### Scenario: Landing correction preserves unresolved boundaries
- **WHEN** the command-boundaries entry is reconciled with terminal Gitea landing and eligible daemon dispatch on the latest documentation revision
- **THEN** only entries made false by implemented Gitea support change in KNOWN-LIMITATIONS.md, preserving concurrent #151 loopback text and every unrelated limitation
- **THEN** it retains GitHub-only Factory Land and unrelated plugin paths, explicit configured Gitea arming, explicit engine publish/finish and critic support, the Gitea-enrollment/GitHub-authority separation, skipped daemon queue queries and ambient named-GitHub authentication
- **THEN** Gitea daemon stages and pending delivery follow their normal eligibility and foreign-journal gates, and the first real Gitea landing and merge-commit inspection remain pending human checks rather than claimed CI evidence
- **THEN** neighboring publication title-edit, critic timeout and delayed-create risks and pending live publication checks remain intact
- **THEN** structural validation selects the unique physical `- **GitHub-only command boundaries:**` start through the next column-zero `- ` bullet, physical `^#{1,6} +\S.*$` heading or EOF; missing or duplicate starts block, and fences, HTML and indentation receive no semantic interpretation

### Requirement: Configuration resilience guidance

README.md, docs/specloop/README.md and KNOWN-LIMITATIONS.md SHALL describe manifest
hold/repair, GitHub-only worker recovery and owner-qualified naming. They SHALL
preserve unrelated guidance and verified issue #13 references without inventing
a PR. Only the three resolved Config limitations SHALL be removed; migration
and unsupported-name limits SHALL remain explicit. Admitted-run health bypass
SHALL be qualified by the Gitea foreign-journal hold, not an unconditional forge hold.

#### Scenario: Operator repairs enrollment
- **WHEN** an operator consults enrollment and recovery guidance
- **THEN** the docs explain regular-file template validation, optional run.change and run.branch supplied by intake, repository-local health, retained triggers, retry after file repair, and config-restart requirements
- **THEN** they explain continued publication/build revisions for admitted GitHub and otherwise eligible Gitea runs, GitHub-only orphan recovery and preserved worker work without claiming automatic resume
- **THEN** Gitea foreign journals or unreadable journal locations hold all stages regardless of manifest health; operator clearing permits otherwise eligible stages without enabling worker recovery

#### Scenario: Operator understands names and limits
- **WHEN** an operator reads naming examples and remaining limitations
- **THEN** both guides retain owner__sample-issue-7-add-a-gate and its spec branch, unsupported unsafe spellings and preservation of recorded legacy identities
- **THEN** limitations distinguish completed fixes from unautomated deployed-run migration, receipt-loss plus title-change discovery limits and unchanged transport retry risks

### Requirement: Gitea readiness evidence limits

Guidance SHALL distinguish read-only Gitea preflight evidence from permission to run daemon stages. It SHALL state that successful preflight, configured allowlists and a clear foreign-journal location permit otherwise eligible Gitea dispatch, without overriding per-item authorization, pause, stop or health gates. Explicit engine publish, finish and critic context SHALL be documented separately from daemon eligibility.

#### Scenario: Preflight does not enable execution
- **WHEN** an operator follows Gitea configuration and preflight guidance
- **THEN** the guides and limitations explain that daemon drafting, admission, revisions, publication and pending delivery are enabled subject to the applicable authorization, health, pause, stop and foreign-journal gates; preflight alone does not authorize a label actor
- **THEN** real-instance checks remain human verification and are not described as CI proof of live workflow support

### Requirement: Explicit forge command guidance

README.md and docs/specloop/README.md SHALL continue to explain manifest-host selection, trusted config and bot credential sources, origin matching, workflow refusal and Gitea WIP publication/finish. Guidance SHALL distinguish explicit engine commands from eligible daemon execution and human-initiated landing. For #130, `docs/landing.md` SHALL document terminal landing for GitHub and configured Gitea and explicitly supersede historical GitHub-only terminal landing wording in those shared guides; those guides SHALL also correct their obsolete daemon-hold statements in this change. Factory Land SHALL remain GitHub-only, and arming and unrelated plugin paths SHALL retain their existing boundaries. Existing runnable examples and unrelated guidance SHALL remain valid.

#### Scenario: Operator selects and publishes
- **WHEN** an operator follows the explicit engine publication guidance
- **THEN** they can configure `[run] github` as `forge.example/owner/repo`, uniquely match a Gitea URL, provision `NIGHTSHIFT_FORGE_CONFIG` and the bot token, and use the existing publish/finish commands
- **THEN** docs explain file-wins bot-token precedence, no landing-token or manifest-env fallback, separate git credentials, scheme/host/effective-port/prefix/repository checks and HTTP(S)-only Gitea origins
- **THEN** docs retain implicit/explicit `github.com` compatibility and identify all three forbidden workflow directories

#### Scenario: Explicit commands do not enable the daemon
- **WHEN** an operator compares forge configuration, preflight and engine instructions with the current landing guide
- **THEN** the docs distinguish WIP-title drafts and finish's title PATCH/read-back from native draft state or merging
- **THEN** eligible Gitea daemon stages and pending delivery use the selected adapter subject to ordinary gates and foreign-journal holds, Factory Land remains GitHub-only, explicit arming supports configured Gitea with verified label writes, and unrelated plugin paths retain their boundaries
- **THEN** `docs/landing.md` explicitly supersedes the shared guides' historical GitHub-only terminal landing wording, describes human-initiated terminal Gitea landing with the configured `land_token_env` sourced by land from `~/.config/nightshift/land.env` and no bot-token fallback, and keeps finish's separate bot/git credentials distinct
- **THEN** existing base preparation and recovery, frozen wrappers, enrollment examples and issue #135 per-step env guidance remain intact during the scoped README.md and docs/specloop/README.md corrections

### Requirement: Factory forge action guidance

The plugin README and engine guide SHALL describe Factory Publish/Finish as confirmed calls to the shared engine's manifest-selected forge. They SHALL explain Gitea WIP behavior, host credential setup and existing display restrictions, preserve GitHub-only Land and describe daemon eligibility separately from Factory confirmation, and state that Finish does not merge.

#### Scenario: Factory operator understands the destination
- **WHEN** an operator reads Factory's action table, confirmation and act guidance
- **THEN** it describes GitHub or configured Gitea publication according to the manifest, WIP-title creation and prefix removal on finish, with confirmation before either push and no send on Cancel
- **THEN** it explains Paseo-host config/bot and git credential provisioning, links engine setup, and distinguishes the display-origin allowlist from write routing
- **THEN** HTTPS/root-path display restrictions remain explicit, engine-supported HTTP loopback or prefixed URLs may need direct CLI use, Land remains GitHub-only and the engine guide describes current gated Gitea daemon support; this change does not edit the plugin README or plugin behavior

### Requirement: Gitea operation limitations

KNOWN-LIMITATIONS.md SHALL state that finish can overwrite a concurrent human title edit and read-back cannot detect that loss. It SHALL describe critic's 30-second socket timeout and 120-second request-start cutoff, explicitly permitting slowly arriving responses to overrun. It SHALL retain delayed-create duplication risk, explicitly describe the daemon read-after-write/single-creator assumptions under Gitea dispatch recovery guidance, and mark real Gitea publication as pending human verification.

#### Scenario: Operator understands residual risks
- **WHEN** an operator reads the updated limitations and retry guidance
- **THEN** they learn that GET followed by title PATCH can overwrite an intervening edit, no conflict detection is supplied, and verification proves only the resulting title
- **THEN** they learn that socket timeouts are not elapsed-time bounds, slow response bodies can overrun, and concurrent creators or late visibility can defeat lookup-before-create deduplication
- **THEN** the first real Gitea publish must be checked by a human for WIP PR creation and finish prefix removal; CI does not claim that validation

### Requirement: Gitea arming support boundary

The arming guide, known limitations and engine guide SHALL distinguish verified explicit Gitea arming from daemon stages and pending delivery subject to normal eligibility and foreign-journal gates. They SHALL remove obsolete GitHub-only arming and unconditional daemon-hold claims, preserve current human-initiated terminal landing and unrelated limits, and identify real issue arming and eventual drafting as pending human checks without claiming CI proves live operation.

#### Scenario: Operator understands the continuing hold
- **WHEN** an operator compares the updated arming guide, limitations and explicit-publication guidance
- **THEN** all describe configured arming and bot-backed verified labels alongside eligible Gitea drafting; force or preflight cannot bypass actor authorization, pause, stopped-job or foreign-journal holds
- **THEN** Factory Land remains GitHub-only and human-initiated terminal Gitea landing remains supported; publication guidance and concurrent loopback content stay intact apart from the scoped daemon-support corrections
- **THEN** the human checks require observing real issue arming and drafting after the prerequisites land, without claiming automated fixtures prove live enrollment

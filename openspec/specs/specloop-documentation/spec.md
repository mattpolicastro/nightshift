# specloop-documentation Specification

## Purpose
Keep the specloop guide, front-door overview and known limitations consistent
with the shipped workflow, with traceable references and clear operator
boundaries, without changing runtime behavior.

## Requirements

### Requirement: Publication guidance

The guide SHALL describe base integration before readiness and the publish
driver handoff, citing issue #46 and PR #59. It SHALL explain failure states,
recovery and same-PR publication without claiming publish merges or verifies
inline. The root README's specloop flow SHALL agree with that description.

#### Scenario: Base moves before publication
- **WHEN** a reader follows publication with a newly advanced configured base or outstanding integration verification
- **THEN** the docs explain the fresh fetch, retryable handoff to running state, driver merge and trusted checks, and retry after readiness before publishing to the same PR

#### Scenario: Base preparation pauses
- **WHEN** a reader encounters `base_conflict`, `base_drift` or a base fetch failure
- **THEN** the guide distinguishes unchanged-head conflicts from retained-merge verification failures, identifies paths or receipts to inspect, and explains repair and re-verification
- **THEN** it distinguishes driver `base_fetch` from retryable publication fetch errors without suggesting automatic acceptance or an unchecked push

### Requirement: Spec drafting guidance

The guide SHALL explain the in-flight file map, overlap feedback, human-task
lint and spec-guarantee feasibility rule with the corresponding PR citations.
It SHALL document the four-round drafting default and put human-only checks
in design documents rather than implementation task lists.

#### Scenario: Concurrent files inform a draft
- **WHEN** a reader plans a change alongside unfinished runs
- **THEN** the guide describes same-repository running/paused task-path snapshots, function suffixes and existing root files, first-round test-overlap feedback, hands-off exclusions and residual risks
- **THEN** it says necessary avoidance instructions name files in the draft, with citations to PRs #36, #37, #43 and #60 and issue #58

#### Scenario: Drafting rules preserve feasible tasks
- **WHEN** a reader authors a human-only task or proposes breaking an existing spec guarantee
- **THEN** the guide explains human-task lint and feasibility rejection, human checks in `design.md`, and justified MODIFIED or REMOVED deltas for changed guarantees, citing PRs #30 and #36

### Requirement: Spec critic guidance

The guide SHALL describe the independent read-only critic after lint and
feasibility, its concurrent-file context, bounded correction and outcomes,
citing issue #51 and PRs #57 and #62. It SHALL enumerate all eight current
checklist items.

#### Scenario: Critic checklist is complete
- **WHEN** a reader reviews the critic section
- **THEN** it lists over-engineering; file collisions and hidden instructions; invalidated protected assertions; conflicts with unowned behavior; tests broken by pending changes; unplanned migrations; retry and idempotency gaps; and stale operator docs and manual steps

#### Scenario: Critic outcome is actionable
- **WHEN** a proposed draft passes lint and feasibility but receives critic findings
- **THEN** the guide explains full in-flight and open-PR file context, at most one correction within the drafting budget, blocking contradictions and `Critic: ` risks
- **THEN** it does not describe overlap alone as a blocker, unavailable context as proof of safety, or a failed critic review as a pass

### Requirement: Front door guidance

The guide and root README SHALL consistently explain dependency gating,
trigger authorization, revision feedback IDs, file-based drafting input and
isolated status reads, with citations to their implementation PRs. They SHALL
describe specloop as the only engine, with historical worker material clearly identified as retired behavior.

#### Scenario: Dependencies and label actors gate work
- **WHEN** a reader arms an issue or applies an approval or revision label
- **THEN** the docs explain that open or unreadable `blocked-by` issues defer new drafts without removing the trigger, while existing work can be collected and delivered under normal eligibility rules
- **THEN** they explain latest-application authorization for OWNER, MEMBER or COLLABORATOR, independent feedback trust, and trusted reapplication after rejection, citing issue #24 / PR #29 and issue #5 / PR #42

#### Scenario: Revision feedback survives a long round
- **WHEN** comments arrive during a spec or build revision or a consumed comment is edited
- **THEN** the docs explain per-repository/PR, source-qualified consumed IDs; saved round snapshots; later comments remaining eligible; same-ID edits remaining consumed; and new IDs for further requests
- **THEN** they describe consumption after successful spec push or durable build revision success, before notifications and without waiting for publication, plus one-time legacy initialization, citing issue #6 / PR #61

#### Scenario: Input and status remain usable
- **WHEN** a reader submits a long issue thread or inspects a collection containing an unreadable run
- **THEN** the guide explains durable UTF-8 `--idea-file` input instead of idea text in argv, and non-actionable `status_error` rows that preserve healthy runs in version-1 JSON with exit 0
- **THEN** it distinguishes per-run errors from top-level status failure and stale attention snapshots, citing issue #12 / PR #35 and issue #10 / PR #32

### Requirement: Mutation evidence guidance

The guide SHALL explain mutant concurrency, classifications, exclusions and
guard-clause operators with their implementation PR citations, plus native
operator selection and configurable invalid steps with the issue #133 citation. It SHALL
preserve the distinction between measured evidence, unmeasured generated
mutants and a no-candidate skip.

#### Scenario: Parallel and timed out mutants are interpreted
- **WHEN** a reader configures or reads a section mutation result
- **THEN** the guide describes baseline-first isolated execution, default `mutation_parallel = 2`, serial value `1`, and scoring only killed/survived results
- **THEN** it distinguishes selected-test `timeout` from preparation/configured-step `invalid` (configure/build by default, replaced by an explicit array), excludes timeouts from both score terms, and cites issue #4 / PR #27 and issue #31 / PR #44

#### Scenario: Exclusions do not fabricate evidence
- **WHEN** prose, comment lines or configured skip patterns remove candidates, or all generated results are equivalent, invalid or timed out
- **THEN** the guide describes Python docstring/comment protection, limited non-Python comment filtering and case-sensitive literal-substring `mutation_skip` before sampling
- **THEN** it explains that generated mutants require at least one killed or survived result; otherwise evidence is unmeasured and pauses without strengthening tasks, while no mutable lines is a separate skip retaining coverage checks, citing issue #21 / PRs #48 and #63

#### Scenario: Guard clauses produce candidates
- **WHEN** a reader checks evidence for Python guard changes
- **THEN** the guide illustrates `negate-condition` for eligible bare-name `if`/`while` headers and `drop-return` replacing an eligible branch-ending guard return with `pass` when its owning conditional has a following statement
- **THEN** it names the restrictions and preserved exclusions rather than promising mutation of every condition or return, citing issue #33 / PR #64

### Requirement: Operations guidance

The guide SHALL document job inventory and stopping, the touchpoint report,
and parallel pytest execution with accurate commands and PR citations. The
root README SHALL link or summarize these specloop operations consistently.

#### Scenario: Operator distinguishes pause and stop
- **WHEN** a reader chooses between pause, `nightshift jobs` and `nightshift jobs stop <id>`
- **THEN** the docs explain local inventory, exact job IDs, verified process-group termination, retained evidence and durable stopped-job holds, citing issue #8 / PR #28
- **THEN** they distinguish daemon launch/delivery control from independent admitted builds, including admission that finishes while paused, and do not promise resume or relabel retries a stopped job

#### Scenario: Touchpoints describe observed history
- **WHEN** a reader runs the documented touchpoint report
- **THEN** the docs show `uv run python -m nightshift.touchpoints` with optional `--since YYYY-MM-DD` and `--json`, and describe question rounds, spec revisions, build revisions, human pauses, manual resumes, human accepts and admission refusals
- **THEN** they identify local-history coverage, approximate draft-date filtering, unknown merge boundary, diagnostics and totals as category observations, citing issue #50 / PR #53

#### Scenario: Parallel tests retain scenario evidence
- **WHEN** a reader chooses `uv run pytest -n 4`
- **THEN** the guide explains pytest-xdist and the existing scenario plugin's forwarding of passed IDs to the controller for deduplicated `SCENARIO` output, citing PR #54
- **THEN** it retains `uv run pytest` as the acceptance command and does not claim CI runs four workers by default

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

### Requirement: Documentation integrity

Documentation-only refreshes SHALL stay within scoped docs and leave runtime code/tests unchanged. Runtime changes MAY update matching docs and tests within explicit scope, including justified schema, fixture or assertion migrations. All edits SHALL preserve unaffected requirements and assertions, use concise present-tense prose and verified references, and require green uv run pytest plus the applicable design's human checklist.

#### Scenario: Scope and regression checks remain intact
- **WHEN** the implementation diff and local validation are reviewed
- **THEN** documentation-only refreshes change only allowed docs, with root README edits limited to specloop/front-door passages, and leave code and tests unchanged; runtime changes stay within their declared runtime, test and documentation scope
- **THEN** only explicitly justified runtime-contract migrations may replace affected assertions, preserving equivalent behavioral coverage, and every unaffected assertion remains intact
- **THEN** this change explicitly migrates the exact request schema, git runner doubles, status inventory seam and trusted post-delivery request/input assertions while preserving authorization-denial snapshots, exact args, offline inventory and all scheduling/delivery assertions
- **THEN** relative documentation links resolve and uv run pytest passes without weakening preserved behavior

#### Scenario: References and reader checks are reviewable
- **WHEN** the documentation is prepared for merge
- **THEN** changed behavior has adjacent verified PR citations when an implementation PR identity is known, or its verified tracking issue until then, issue and PR numbers are distinguished, and no pending work is described as shipped
- **THEN** the PR carries the applicable design's human checklist for live reference verification and a readability pass, retaining ledger verification and Matt's single readability pass for documentation refreshes, without assigning these checks as implementing-agent tasks

### Requirement: Task sizing guidance

The guide's drafting paragraph SHALL describe risk-based task sizing:
same-file work for one concern belongs together, documentation-only changes
use one or two tasks in one section, and most changes need six tasks or fewer
in as few sections as dependencies allow, within the existing task ceiling.
It SHALL explain that each task incurs a review cycle and each section incurs
an evidence gate, while preserving existing drafting workflow guidance.

#### Scenario: Reader sizes a plan
- **WHEN** a reader consults the drafting discussion in `docs/specloop/README.md`
- **THEN** it explains the grouping advice, documentation-only size, normal six-task target and dependency-driven section count with their review-cycle and evidence-gate rationale
- **THEN** the existing proposal stages, four-round default and references remain intact

### Requirement: Routed evidence guidance

Section evidence SHALL explain trusted route configuration, glob and fallback semantics, mutation-only steps, `{mutant_path}`, full baselines, confirmation, result classifications and decision receipts. It SHALL preserve existing guidance and citations and explain selector fallback. Private-manifest rollout and timing comparison SHALL remain human checks, without claims of measured speed-up before rollout.

#### Scenario: Reader configures routed evidence
- **WHEN** a reader follows Section evidence and `examples/specloop/self-hosting.toml`
- **THEN** the Python route and mutation-only command use the selector from the trusted checks directory, the existing verification and coverage commands remain intact, and documentation shows how a second plugin route can refer to a separately defined suite
- **THEN** the reader can identify the first matching route, the unmatched fallback, the full baseline per used sequence and the receipt deciding a mutant's outcome

#### Scenario: Example fallback runs the whole suite
- **WHEN** the example mutation command is exercised against a fixture root with a selected importing test or with an empty/unresolvable selector input
- **THEN** its argv runs the selected test paths or the full `tests/` directory respectively, preserves paths containing spaces and the existing pytest environment/options, and propagates the test process exit status
- **THEN** the guide explains full confirmation, selector limits, unchanged scoring and the human timing comparison against the approximately 14-minute baseline

### Requirement: Configuration resilience guidance

README.md, docs/specloop/README.md and KNOWN-LIMITATIONS.md SHALL describe manifest
hold/repair, GitHub-only worker recovery and owner-qualified naming. They SHALL
preserve unrelated guidance and verified issue #13 references without inventing
a PR. Only the three resolved Config limitations SHALL be removed; migration
and unsupported-name limits SHALL remain explicit. Admitted-run health bypass
SHALL be qualified by the Gitea dispatch hold.

#### Scenario: Operator repairs enrollment
- **WHEN** an operator consults enrollment and recovery guidance
- **THEN** the docs explain regular-file template validation, optional run.change and run.branch supplied by intake, repository-local health, retained triggers, retry after file repair, and config-restart requirements
- **THEN** they explain continued publication/build revisions for admitted GitHub runs, GitHub-only orphan recovery and preserved worker work without claiming automatic resume
- **THEN** Gitea stages and delivery remain disabled regardless of manifest health; foreign journals require inspection and clearing them does not enable execution

#### Scenario: Operator understands names and limits
- **WHEN** an operator reads naming examples and remaining limitations
- **THEN** both guides retain owner__sample-issue-7-add-a-gate and its spec branch, unsupported unsafe spellings and preservation of recorded legacy identities
- **THEN** limitations distinguish completed fixes from unautomated deployed-run migration, receipt-loss plus title-change discovery limits and unchanged transport retry risks

### Requirement: Provider fallback guidance

The guide's existing usage-limit paragraph SHALL describe usage-limit
fallback, immediate fallback for recognized authentication/plan failures,
fallback after per-task/profile infra retries, the 15-minute unavailable
cooldown, and all-blocked earliest-deadline pauses. It SHALL distinguish
uncapped usage-limit recovery from the existing capped infra recovery and
SHALL NOT claim live-provider error wording has been validated by fixtures.

#### Scenario: Reader understands provider recovery
- **WHEN** a reader consults the provider section of `docs/specloop/README.md`
- **THEN** the usage-limit paragraph explains when the next profile is tried, when a profile becomes eligible again, when the role pauses, and how the unchanged automatic-resume limits apply
- **THEN** the edit cites issue #72 without inventing a PR reference and preserves all other guide sections, including concurrent arming/front-door changes

### Requirement: Host portability guidance

`KNOWN-LIMITATIONS.md`'s platform section SHALL document host path configuration consistently with the setup guides and example config. Guidance SHALL preserve unrelated assertions and citations and distinguish automated configuration checks from real second-install validation.

#### Scenario: Operator configures an alternate layout
- **WHEN** an operator follows the setup and platform guidance
- **THEN** they can identify `NIGHTSHIFT_BIN_DIR`, `NIGHTSHIFT_NODE_BIN`, `NIGHTSHIFT_UV` and `NIGHTSHIFT_CHECKOUT`, their defaults, applicable consumers and how to supply applicable settings to Python and Paseo processes, including inherited service environments, the existing daemon env file's precedence for downstream settings and required process restarts
- **THEN** examples in the root README, preparation guide, plugin README and example config agree with the platform guidance on configurable checkout/tool paths and `[daemon].worktree_root`, including its default and compatibility-only role after worker retirement
- **THEN** guidance explains literal path handling and the changed relative-root anchor, and retains manifest preparation-read restrictions

#### Scenario: Platform claims retain their evidence limits
- **WHEN** the platform limitations are updated after implementation tests pass
- **THEN** fixed-location claims for Python and plugin consumers become documented defaults with overrides, while macOS sandbox-exec, launchd, Paseo-host and second-install validation limits remain explicit
- **THEN** guidance cites issue #15 without an invented implementation PR, preserves unrelated limitations and marks unperformed second-install checks as pending

#### Scenario: Frozen service wrappers remain explicit
- **WHEN** an operator reads setup examples and platform limitations for the new host settings
- **THEN** guidance names `bin/nightshift-daemon` and `bin/specloop-serve` as unchanged wrappers with fixed bootstrap checkout, uv and PATH locations, and says environment settings do not relocate those wrapper commands
- **THEN** guidance describes wrapper portability as deferred because `bin/**` is frozen, distinguishes direct CLI configuration from bundled service startup, and does not claim a new helper or relocated wrapper pathname solves the limitation
- **THEN** full daemon rollout remains pending separate wrapper work or an independently provisioned operator service preserving credential and preflight guards
- **THEN** the same freeze covers launchd plists and wrapper comments; current guidance explains the retained worker startup prerequisites and real pause/supervisor boundaries without promising those files were corrected

### Requirement: Stopped job release workflow

The root README and specloop guide SHALL document `nightshift jobs release
<id>` as the supported local recovery for a confirmed stopped job, citing
issue #34 or its verified implementation PR.

#### Scenario: Operator releases a stopped hold
- **WHEN** a reader follows stopped-job recovery in either guide
- **THEN** it explains inventory, stop confirmation, inspection and release using the exact full ID, followed by normal next-poll eligibility
- **THEN** it states that resume and relabeling alone leave the hold intact, and that new work requires an armed, authorized and otherwise eligible item with applicable blockers clear

### Requirement: Release command outcomes

The root README SHALL explain release success, no-op, refusal and usage exit
codes, and its independence from daemon availability and configuration.

#### Scenario: Operator interprets release output
- **WHEN** a reader checks release command behavior
- **THEN** the docs describe offline operation, exit 0 for released or already released, exit 1 for running, stopping, finishing or unverifiable work, and exit 2 for invalid syntax
- **THEN** they explain that repeating an old release cannot detach a replacement attempt

### Requirement: Release recovery boundaries

Both guides and `KNOWN-LIMITATIONS.md` SHALL describe explicit release while
preserving evidence-retention, committed-effect and automatic-retry limits.
They SHALL NOT retain the claim that issue #34 has no recovery command.

#### Scenario: Operator understands remaining limits
- **WHEN** a reader compares the guides with known limitations
- **THEN** all three describe release consistently and distinguish historical stopped attempts from future eligible work
- **THEN** they preserve the lack of rollback, independent admitted builds, legacy/unkeyed revision commit/result crash risk, explicit new-round effects after release and unproven keyed machine-crash durability, retention limits and absence of automatic retries without recommending manual receipt editing for a normal confirmed stop

### Requirement: Deadline guidance

The root README and specloop guide SHALL document per-command reporting deadlines and the operator recovery path, distinguishing deadline escalation from process termination.

#### Scenario: Operator handles overdue work
- **WHEN** a reader consults background-job guidance
- **THEN** it states propose 3600 seconds, admit 300 seconds and revise 1800 seconds, persistence across restart, and reporting on the next authorized eligible unpaused poll
- **THEN** it explains needs-human, retained job identity, stop/inspection/release recovery, late-result recovery and delays when the daemon or GitHub is unavailable, without promising automatic termination or retry

### Requirement: Active job status guidance

The root README and specloop guide SHALL describe active background rows in nightshift daemon status consistently with local job inventory.

#### Scenario: Operator reads job status
- **WHEN** a reader follows status instructions
- **THEN** the docs explain job ID, stage, repository/item, elapsed and remaining or overdue time, legacy timing unavailability, and historical inspection through `nightshift jobs --all`
- **THEN** they preserve the distinction between Nightshift job status, daemon liveness and independent specloop run status

### Requirement: Delivered retention guidance

All three scoped operator documents SHALL distinguish delivered-attempt compaction from retention of pending and stopped evidence. Their drafting-input and job-control passages SHALL describe consistent input lifetimes and the explicit `nightshift jobs compact` exception to automatic cleanup eligibility.

#### Scenario: Operator understands retained artifacts
- **WHEN** the root README, KNOWN-LIMITATIONS.md and docs/specloop/README.md describe job storage
- **THEN** they explain removal of delivered logs and owned idea payloads, redacted minimal requests, retained results/control metadata and restored request mtimes for touchpoint history
- **THEN** the guide's drafting-input paragraph qualifies retention by delivery, and its job-control paragraph no longer claims all artifacts remain without automatic cleanup
- **THEN** all three documents explain that automatic interrupted cleanup waits for a later eligible authorized unpaused visit, so unarmed, closed or denied items can retain residual artifacts indefinitely; they do not recommend rearming solely for cleanup because normal new work may follow
- **THEN** they describe explicit offline compaction while paused for already-delivered history whose current receipt matches, with identity/ownership skips and repeat safety; missing identity or a replacement receipt still prevents that manual cleanup, even if an old journal exists
- **THEN** they preserve stopped/released and undelivered evidence retention, workflow-receipt and build-feedback retention, remaining storage growth and missing-history limitations without promising secure erasure

### Requirement: Git timeout guidance

The scoped operator documents SHALL describe process-group termination for timed-out daemon git commands and retain unresolved remote-effect, platform and supervisor limits.

#### Scenario: Operator understands timeout limits
- **WHEN** the Background jobs limitations and corresponding guide passages are updated
- **THEN** they replace the direct-child-only claim with the 30-second daemon git timeout and group kill, and remove only demonstrated fixed deadline/retention claims
- **THEN** they retain ambiguous remote acceptance, escaped descendants, committed-admission independence, stopped-job recovery and legacy/unkeyed revision commit/result crash caveats and unproven keyed machine-crash durability, citing issue #9 or a verified implementing PR
- **THEN** real stalled-origin validation is identified as a pending human check unless performed, rather than inferred from CI

### Requirement: Keyed revision recovery guidance

The root README's specloop passages, specloop guide, CLI JSON guide and known limitations SHALL consistently document optional --round-id, stable automatic build keys, original-commit replay and accurate observed-state results. They SHALL distinguish same-round retries from explicit new snapshot selection and legacy/unkeyed behavior, citing issue #7 until an implementation PR is known.

#### Scenario: Operator retries a logical round
- **WHEN** a reader follows CLI or daemon build-revision recovery guidance
- **THEN** the docs explain retaining the same key and saved feedback, refusing changed payload under that key, returning the original commit and preserving later paused, aborted or finished state
- **THEN** they explain that ordinary crashed attempts detach as before, explicit stopped-job release selects a new round after inspection, and legacy/unkeyed calls have no retrospective deduplication guarantee

#### Scenario: Recovery limits remain reviewable
- **WHEN** a reader compares the four documents and their examples
- **THEN** key syntax and keyed version-1 result fields agree with the CLI, relative links resolve, and known limitations distinguish deterministic process-crash tests from an unperformed physical machine-crash check
- **THEN** examples and documentation tests verify structure and command/schema compatibility rather than requiring particular prose phrases

### Requirement: Job inventory view guidance

`README.md`, `KNOWN-LIMITATIONS.md` and `docs/specloop/README.md` SHALL describe default inventory, `--all`, receipt-based delivery wording and `jobs compact` consistently wherever this change invalidates existing claims. Guidance SHALL cite issue #103 or its verified implementation PR, preserve unaffected assertions, and distinguish deterministic fixture validation from the pending real-tree human check.

#### Scenario: Operator selects a view and cleanup
- **WHEN** an operator reads the updated usage, job-control and retention passages
- **THEN** they can identify default active/stopped/pending and diagnostic rows, hidden-history counts, full history including failed jobs via `--all`, and the item-receipt basis for `delivered` versus `finished (delivery pending)`
- **THEN** they can run the explicit one-pass compact command, interpret compacted/skipped counts and retained-target warnings, and understand that missing or replaced attempt identity prevents deletion even when the display says delivered
- **THEN** the docs explain pausing and comparing full inventory before/after on the real tree as a human validation step, without claiming CI performed it or changing pause/stop/release and supervisor boundaries

### Requirement: Worker retirement guidance

Current guidance in allowed documentation SHALL explain sole-engine enrollment and worker retirement, distinguish retained history from execution, and document compatibility boundaries and human rollout checks without claiming unperformed validation.

#### Scenario: New enrollment follows one engine
- **WHEN** an operator follows README, config.example.toml and the specloop guide
- **THEN** the runnable example enrolls a specloop repository with a trusted manifest and explains omission defaults, explicit worker rejection and inert obsolete settings
- **THEN** it explains repository-local holds for missing or unusable manifests and removal of worker status output, including the off-subscription model report, while all other daemon status output remains unchanged under nightshift daemon status
- **THEN** direct Python front-door instructions do not require worker verification strings, endpoint models or OAuth; manifest providers retain their own requirements
- **THEN** bundled service instructions explicitly retain the frozen daemon wrapper's env-file, worker OAuth, GH_TOKEN and gh/claude/pnpm/node/git/swift checks, distinguishing direct invocation from service startup without claiming Python changes bypass shell checks

#### Scenario: Existing installation is checked before merge
- **WHEN** an operator reads retirement and rollout guidance
- **THEN** it identifies retained historical attention/recovery, the canonical nightshift CLI/state root and one-release legacy compatibility, separate daemon/supervisor jobs and the need to inspect live config/plists/external callers before merge
- **THEN** it explains the CLI merge and state migration delivered by #40, preserves unrelated limitations, and cites issue #16 without inventing an implementation PR
- **THEN** live config, plist and external-caller inspection remains a pre-merge human checklist, never a claimed CI result
- **THEN** it describes guarded migration without claiming host checks performed, safety on the live machine, rollback of remote effects or automatic restart of interrupted worker work
- **THEN** it identifies obsolete worker instructions/metadata in frozen CLAUDE.md and pyproject.toml, historical worker documents and their removed-source references, and resolved type-baseline entries as deferred cleanup outside the implementation allowance

### Requirement: Unified command guidance

Active operator documentation SHALL use canonical command forms, explain the alias and bare-run exception, and preserve frozen-wrapper inventories and unrelated guidance.

#### Scenario: Guides distinguish daemon and build
- **WHEN** README, the build guide and command-JSON guide describe status, run and pause/resume
- **THEN** examples distinguish daemon controls from build controls and retain schemas, operational boundaries, issue citations and valid links
- **THEN** state guidance maps legacy build proposals/<change> to canonical drafts/<change> and distinguishes unchanged daemon proposals/<repository> receipts and jobs

#### Scenario: Executable examples retain regression coverage
- **WHEN** the keyed revision guide and direct supervisor README example switch to canonical commands
- **THEN** existing documentation tests select and execute the canonical revision example, preserving schema/table agreement, keyed replay and later-state snapshots, and changed-feedback refusal
- **THEN** direct supervisor assertions expect nightshift serve with unchanged configured executable/cwd/environment and shell-syntax coverage, while frozen-wrapper argv, bootstrap and credential assertions stay intact

#### Scenario: Example config points to daemon status
- **WHEN** config.example.toml explains daemon output and how to check alerting
- **THEN** both examples use nightshift daemon status and structural checks confirm daemon-status handler selection, while the complete TOML remains valid with unchanged settings, defaults and enrollment semantics
- **THEN** direct supervisor guidance uses nightshift serve and the comment examples follow the same deterministic command checks as the guides

### Requirement: Safe migration guidance

Setup and deployment guidance SHALL prescribe stopped-supervisor migration and inspection before restart/resume, preserve frozen-wrapper prerequisites, and identify live checks as human checks.

#### Scenario: Deployment and validation use safe controls
- **WHEN** an operator follows docs/deploy.md apply/restart and host checklist or docs/validation.md unload/reload procedure
- **THEN** guidance uses migrate while supervisor is stopped, inspection before restart, requested holds before supervisor startup and explicit resume afterwards; daemon pause survives reload via daemon-group commands

#### Scenario: Frozen inventories match the parser
- **WHEN** documentation command checks inspect the unchanged daemon and supervisor wrapper inventory
- **THEN** actual bare nightshift run and specloop serve argv match the inventory and parse, with bootstrap and credential assertions preserved and no required wrapper edit

#### Scenario: Documentation checks are structural
- **WHEN** touched documentation is checked under the design's physical-line extraction rule
- **THEN** all recognized inline/fenced CLI examples parse without dispatch or removed daemon forms, links resolve, ambiguity blocks validation and no exact-prose assertion is added

### Requirement: Session scratch retention guidance

The run-state passage in `docs/specloop/README.md` and one focused entry in
`KNOWN-LIMITATIONS.md` SHALL explain Codex scratch cleanup and its remaining
limits, cite issue #116 or its verified implementation PR and preserve
unrelated guidance. Documentation checks SHALL validate structure, not prose
phrases. The live-host check SHALL remain pending until performed.

#### Scenario: Operator distinguishes scratch from evidence
- **WHEN** an operator reads the run-state and limitations passages
- **THEN** the docs distinguish disposable `<stem>.tmp` from retained transcripts, outputs, seals, receipts, reviews and verify builds, and locate non-fatal cleanup events in session stderr
- **THEN** they explain finalization after the existing runner returns or setup raises, accepted descendant races, historical leftovers and harness/host crash limits without promising confirmed group exit or changing Claude behavior

#### Scenario: Host validation remains explicit
- **WHEN** automated cleanup tests pass and documentation is checked
- **THEN** structural checks use the design's physical-line rules, block ambiguous boundaries or link syntax, and validate links without exact-prose assertions
- **THEN** the docs still identify inspection of one finished real Codex attempt after the first updated build as a pending human check, without claiming fixtures prove live-host cleanup

### Requirement: Factory landing entry

The plugin README and engine guide SHALL document confirmed Land as a separate human_checks action alongside unchanged status verbs, distinguishing Finish from merge and preserving operator-only landing.

#### Scenario: Operator chooses Land or Finish
- **WHEN** the reader consults Factory controls in either guide
- **THEN** guidance identifies bound confirmation, background progress and retained refusal, and explains that Finish does not merge while Land invokes existing landing
- **THEN** the blanket no-merge-operation claim becomes an explicit confirmation boundary, preserving canonical CLI/daemon isolation

### Requirement: Landing control boundaries

The main README, engine guide and KNOWN-LIMITATIONS.md SHALL distinguish landing's lifetime lock and verified Stop from intake inventory, stopped holds, release and escalation.

#### Scenario: Operator handles a stopped or interrupted landing
- **WHEN** the operator follows landing recovery beside the intake release rule
- **THEN** guidance explains separate records, no manual lock release, display-only overdue reporting and no jobs inventory, delivery receipts or escalation for landing
- **THEN** worker-only death can release the lock while descendants survive; Stop refuses and the operator must wait for or manually terminate them before a fresh attempt independently verifies group drainage
- **THEN** lock availability, an unchanged head and a terminal result never promise that another attempt may mutate while descendants remain
- **THEN** intake receipt/release guidance remains correct and independent terminal land is outside the Factory lock

### Requirement: Landing evidence retention

Operator guidance SHALL distinguish bounded landing display history from complete manually pruned evidence and existing intake compaction.

#### Scenario: Operator interprets landing history
- **WHEN** the reader checks landing storage and progress guidance
- **THEN** it describes newest 20 plus total and separate current progress, persistent logs/results, no read acknowledgement and manual pruning that preserves undrained-group evidence
- **THEN** KNOWN-LIMITATIONS.md qualifies delivered-log cleanup without promising bounded disk use or applying jobs compact to landing

### Requirement: Delayed attestation recovery guidance

The landing guide and KNOWN-LIMITATIONS.md SHALL document the shared Factory/terminal late-POST duplicate exception and deletion recovery, replacing the unconditional never-posted-twice promise. They SHALL describe within-invocation reconciliation without a retained-create guard.

#### Scenario: Operator resolves duplicate markers
- **WHEN** a timed-out POST commits after another invocation creates an identical marker
- **THEN** docs/landing.md and KNOWN-LIMITATIONS.md explain that a valid acknowledgement need not detect duplicates immediately, while a subsequent marker lookup refuses `duplicate marker matches; resolve duplicates before retrying`
- **THEN** recovery requires inspecting effects and deleting one duplicate before Land again if eligible or terminal land after outstanding attempts are resolved
- **THEN** neither promises cross-attempt deduplication nor suggests a retained-create guard, and the other landing gates remain unchanged

### Requirement: Guarded finish guidance

The landing guide and KNOWN-LIMITATIONS.md SHALL explain the locked expected-head precondition and the failed-push recovery migration to inspected direct Finish, distinguishing it from validated successor-head handling.

#### Scenario: Operator encounters a revised head
- **WHEN** revision republishes between landing's identity check and finish
- **THEN** guidance explains refusal before archiving, reviewing the new head before retrying and preservation of existing finish and merge gates
- **THEN** private Factory transport is described in prose without executable examples or new unified-command documentation routing

#### Scenario: Operator recovers an unpushed archive
- **WHEN** a finish push failed after creating local archive commit A while the remote remains H and the run remains at human_checks
- **THEN** docs/landing.md and KNOWN-LIMITATIONS.md identify guarded landing's mismatch refusal and replace blanket rerun advice with inspection of prior effects, local archive/tasks and remote refs, followed by explicit direct Finish without --expect-head and terminal landing after the run is done
- **THEN** guidance distinguishes an already-pushed archive with a ready failure, preserves ordinary finish/act recovery, and promises neither automatic guard bypass nor run reset

### Requirement: Landing documentation structure

Checks SHALL validate scoped headings, links, command examples and recovery structures through deterministic physical-line rules and real parsers, without exact explanatory prose assertions.

#### Scenario: Examples describe supported behavior
- **WHEN** checks inspect the five updated documents
- **THEN** unique physical headings and pipe-table keys select relevant passages, supported examples parse without dispatch and relative links resolve
- **THEN** ambiguous structures, unmatched command backticks and unknown placeholders fail closed; fences, HTML and indentation do not hide recognized lines
- **THEN** existing assertions remain intact and desktop/phone review stays on the human checklist

### Requirement: Native evidence example

The evidence guide SHALL document invalid-step replacement/defaults, prepare
precedence, C-family suffixes and operators, physical-line exclusions and their
limits, citing issue #133. It SHALL link a parseable Dart manifest example and
identify first-run validation on scheming-vizier as a pending human check.

#### Scenario: Dart configuration is structurally valid
- **WHEN** `examples/specloop/dart.toml` is loaded with the real manifest parser
- **THEN** mutation selects defined `dart-analyze` then `dart-tests` steps, classifies `dart-analyze` failures as invalid and targets `lib/**`, with commands using the configured toolchain and prepared writable project
- **THEN** tests check argv, IDs, order and placeholders without invoking Dart, installing dependencies or claiming project-specific coverage integration

#### Scenario: Guidance states operational limits
- **WHEN** the evidence passage and example are reviewed using the design's deterministic physical-line boundary and standalone-link rules
- **THEN** the link resolves, ambiguous boundaries block validation and no prose-exact assertions or Markdown parsing are introduced
- **THEN** guidance explains the conservative loss of string-containing lines, limited comment protection, lack of whole-file refusal and pending real-engine usefulness check without claiming fixtures prove operational support

### Requirement: Revision rerun workaround

`KNOWN-LIMITATIONS.md` SHALL explain that separate result-attempt comments do
not remove `agent:revise` after rejection or needs-input. It SHALL retain the
manual remove-and-reapply workaround, its normal eligibility gates and the
pending live verification boundary, citing issue #137 without inventing an
implementation PR. Unrelated limitations SHALL remain intact.

#### Scenario: Operator starts a fresh revision round
- **WHEN** an operator reads the revision recovery limitations after a rejected or needs-input result
- **THEN** the guidance says to resolve the questions or contradictions, remove `agent:revise`, clear `needs-human`, and have a trusted actor re-add `agent:revise` to obtain a new label event
- **THEN** it explains that applying an already-present label creates no fresh event, pause and stopped-job holds still apply, and the next live rejected revision's posted reason remains a human check rather than a claimed CI result

### Requirement: Snapshot preparation guidance

`docs/specloop/prepare.md` SHALL document snapshot configuration, trusted warming, private consumption, fallback and retention. It SHALL preserve existing guidance, provide a structurally validated example with empty-cache fallback, cite issue #134 and identify scheming-vizier timing and relocation validation as pending human checks.

#### Scenario: Operator configures a cache with fallback
- **WHEN** the documented snapshot fragment is extracted by the design's exact physical-line marker rules and loaded with the real manifest parser
- **THEN** fields and references parse, local fixture execution demonstrates distinct warm/base and prepare/candidate sources plus hit and empty-cache behavior, and ambiguous markers or fences block validation
- **THEN** guidance explains dependency-only Dart caching and regeneration of candidate project configuration, does not claim CI proved a speedup, and keeps human-only comparisons outside implementation tasks
- **THEN** it names all manifest fields, lockfile identity, base-sync-only warming, payload output, clone placeholders, unconditional candidate preparation, fallback events, APFS limits and the three-entry total retention rule

### Requirement: Gitea readiness evidence limits

Guidance SHALL distinguish read-only Gitea preflight evidence from permission to run daemon stages. It SHALL state that neither successful checks, a populated allowlist nor clearing a foreign journal enables daemon execution. Explicit engine publish, finish and critic context SHALL be documented separately from those holds.

#### Scenario: Preflight does not enable execution
- **WHEN** an operator follows Gitea configuration and preflight guidance
- **THEN** the guides and limitations explain that daemon drafting, admission, revisions, publication and pending delivery remain disabled pending core authorization and durable transition recovery
- **THEN** real-instance checks remain human verification and are not described as CI proof of live workflow support

### Requirement: Step environment guidance

`docs/specloop/README.md` SHALL explain the per-step env contract for all four
step kinds, cite issue #135 and preserve existing guidance. Its example SHALL
be validated using deterministic physical-line boundaries and the real TOML
and manifest parsers, without prose-exact assertions or semantic Markdown
parsing.

#### Scenario: Operator configures step environments
- **WHEN** the guide's env example is extracted using the design's exact physical-line marker and fence rules and parsed with a minimal manifest fixture
- **THEN** all four step kinds have valid distinct IDs and string-valued env entries whose placeholders expand consistently with argv
- **THEN** missing, duplicate, reversed or indented delimiters, interior fence tokens and malformed TOML block validation regardless of surrounding Markdown or HTML context
- **THEN** guidance covers empty defaults, string validation, value substitution, isolation, non-inheritance, protected PATH/HOME/TMPDIR and permitted overrides of other fixed defaults
- **THEN** the guide explains the environment contract and unchanged execution boundaries without claiming env-only mutation narrowing, additional filesystem access or inherited host credentials

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
- **THEN** all Gitea daemon stages and pending delivery stay disabled; explicit arming supports configured Gitea with verified label writes, while landing and unrelated plugin paths retain their boundaries
- **THEN** existing base preparation and recovery, frozen wrappers, enrollment examples and issue #135 per-step env guidance remain intact

### Requirement: Factory forge action guidance

The plugin README and engine guide SHALL describe Factory Publish/Finish as confirmed calls to the shared engine's manifest-selected forge. They SHALL explain Gitea WIP behavior, host credential setup and existing display restrictions, preserve GitHub-only Land and disabled Gitea daemon execution, and state that Finish does not merge.

#### Scenario: Factory operator understands the destination
- **WHEN** an operator reads Factory's action table, confirmation and act guidance
- **THEN** it describes GitHub or configured Gitea publication according to the manifest, WIP-title creation and prefix removal on finish, with confirmation before either push and no send on Cancel
- **THEN** it explains Paseo-host config/bot and git credential provisioning, links engine setup, and distinguishes the display-origin allowlist from write routing
- **THEN** HTTPS/root-path display restrictions remain explicit, engine-supported HTTP loopback or prefixed URLs may need direct CLI use, and Land and daemon restrictions remain intact

### Requirement: Gitea operation limitations

KNOWN-LIMITATIONS.md SHALL state that finish can overwrite a concurrent human title edit and read-back cannot detect that loss. It SHALL describe critic's 30-second socket timeout and 120-second request-start cutoff, explicitly permitting slowly arriving responses to overrun. It SHALL retain delayed-create duplication risk and mark real Gitea publication as pending human verification.

#### Scenario: Operator understands residual risks
- **WHEN** an operator reads the updated limitations and retry guidance
- **THEN** they learn that GET followed by title PATCH can overwrite an intervening edit, no conflict detection is supplied, and verification proves only the resulting title
- **THEN** they learn that socket timeouts are not elapsed-time bounds, slow response bodies can overrun, and concurrent creators or late visibility can defeat lookup-before-create deduplication
- **THEN** the first real Gitea publish must be checked by a human for WIP PR creation and finish prefix removal; CI does not claim that validation

### Requirement: Forge guidance integrity

Documentation checks SHALL validate scoped structure, relative links and command examples using deterministic physical-line rules and real parsers, without exact explanatory-prose assertions. Missing or ambiguous boundaries SHALL block validation. Existing assertions SHALL be preserved, with only explicitly named host-fixture and Factory confirmation-expectation migrations.

#### Scenario: Examples remain structurally valid
- **WHEN** touched documentation examples and links are validated
- **THEN** exact unique physical headings select passages through the next heading of the same or higher level, and fences, indentation or HTML do not hide recognized lines
- **THEN** command examples parse without dispatch, links resolve, unknown placeholders or ambiguous command syntax fail closed, and no extra raw `[[repos]]` guide block breaks existing extraction

### Requirement: Configured arming guidance

The arming guide SHALL document explicit config selection, checkout matching, Gitea bot credentials, client-side label filtering and verified writes. It SHALL retain legacy GitHub commands, lint rules, modes and examples. New examples SHALL be checked with deterministic physical-line rules and the real command parser without network access or exact-prose assertions.

#### Scenario: Operator configures arming
- **WHEN** an operator reads docs/arming.md's prerequisites, Invocation and Configured Gitea passages
- **THEN** they can use --config with a unique matching enrollment and existing spec:draft label, provision file-wins bot credentials, and run check and arming examples without gh on Gitea
- **THEN** instructions retain GitHub and enterprise discovery when the flag is omitted, explain missing completion reasons and verification failure, and preserve the original template and named-test examples

#### Scenario: Arming examples are structurally checked
- **WHEN** new documentation tests select the configured arming section and its shell examples
- **THEN** unique column-zero headings and exact physical fence delimiters select it regardless of surrounding Markdown or HTML; missing, duplicate, indented or reversed delimiters fail closed
- **THEN** shlex tokenization and the real module parser/main with fake transport validate both commands, relative links resolve and all existing documentation assertions remain intact

### Requirement: Gitea arming support boundary

The arming guide, known limitations and engine guide SHALL distinguish verified explicit Gitea arming from disabled daemon stages and pending delivery. They SHALL remove only obsolete GitHub-only arming claims, preserve landing and unrelated limits, and identify real issue arming and eventual drafting as pending human checks without claiming CI proves live operation.

#### Scenario: Operator understands the continuing hold
- **WHEN** an operator compares the updated arming guide, limitations and explicit-publication guidance
- **THEN** all describe configured arming and bot-backed verified labels while retaining unconditional Gitea daemon holds; force, allowlists and preflight do not enable drafting
- **THEN** landing remains GitHub-only, publication guidance and concurrent loopback content stay intact, and only arming claims change in the shared documents
- **THEN** the human checks require a real armed issue and a later drafting observation once separate prerequisites permit it; until then drafting remains pending rather than bypassing the hold

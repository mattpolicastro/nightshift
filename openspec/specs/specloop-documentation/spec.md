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
- **THEN** they explain latest-application authorization for GitHub OWNER, MEMBER or COLLABORATOR and Gitea casefolded exact allowlist membership excluding the bot, independent feedback trust, and trusted reapplication after rejection, citing issue #24 / PR #29 and issue #5 / PR #42

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

### Requirement: Documentation integrity

Documentation-only refreshes SHALL stay within scoped docs and leave runtime code unchanged. They MAY add documentation-integrity tests in `tests/test_specloop_documentation_integrity.py` for the structures they introduce, and MAY add Markdown or YAML authoring assets under `presets/`, without changing any existing assertion, fixture or other test file. Runtime changes MAY update matching docs and tests within explicit scope, including justified schema, fixture or assertion migrations. All edits SHALL preserve unaffected requirements and assertions, use concise present-tense prose and verified references, and require green uv run pytest plus the applicable design's human checklist.

#### Scenario: Scope and regression checks remain intact
- **WHEN** the implementation diff and local validation are reviewed
- **THEN** documentation-only refreshes stay within scoped docs and may add documentation-integrity tests in `tests/test_specloop_documentation_integrity.py` for the structures they introduce and Markdown or YAML authoring assets under `presets/`, leaving runtime code, existing assertions, fixtures and other test files unchanged
- **THEN** root README edits remain limited to scoped specloop/front-door passages and authoring-asset Layout entries; runtime changes stay within their declared runtime, test and documentation scope
- **THEN** only explicitly justified runtime-contract migrations may replace affected assertions, preserving equivalent behavioral coverage, and every unaffected assertion remains intact
- **THEN** the previously specified migrations of the exact request schema, git runner doubles, status inventory seam and trusted post-delivery request/input assertions remain intact, preserving authorization-denial snapshots, exact args, offline inventory and all scheduling/delivery assertions
- **THEN** relative documentation links resolve and uv run pytest passes without weakening preserved behavior

#### Scenario: References and reader checks are reviewable
- **WHEN** the documentation is prepared for merge
- **THEN** documentation-only refreshes stay within scoped docs and may add documentation-integrity tests in `tests/test_specloop_documentation_integrity.py` for the structures they introduce and Markdown or YAML authoring assets under `presets/`, leaving runtime code, existing assertions, fixtures and other test files unchanged
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
- **THEN** they can identify `NIGHTSHIFT_BIN_DIR`, `NIGHTSHIFT_NODE_BIN`, `NIGHTSHIFT_UV`, `NIGHTSHIFT_CHECKOUT` and the standalone validator's `NIGHTSHIFT_OPENSPEC_BIN`, their defaults, applicable consumers and how to supply applicable settings to Python and Paseo processes, including inherited service environments, the existing daemon env file's precedence for downstream settings and required process restarts
- **THEN** examples in the root README, preparation guide, plugin README and example config agree with the platform guidance on configurable checkout/tool paths and `[daemon].worktree_root`, including its default and compatibility-only role after worker retirement
- **THEN** guidance explains literal path handling and the changed relative-root anchor, and retains manifest preparation-read restrictions

#### Scenario: Platform claims retain their evidence limits
- **WHEN** the platform limitations are updated after implementation tests pass
- **THEN** fixed-location claims for Python and plugin consumers become documented defaults with overrides, while macOS sandbox-exec, launchd, Paseo-host and second-install validation limits remain explicit
- **THEN** guidance cites issue #15 without an invented implementation PR, preserves unrelated limitations and marks unperformed second-install checks as pending

#### Scenario: Frozen service wrappers remain explicit
- **WHEN** an operator reads setup examples and platform limitations for the new host settings
- **THEN** guidance names `bin/nightshift-daemon` and `bin/specloop-serve` as unchanged wrappers with fixed bootstrap checkout, uv and PATH locations, explicitly noting the supervisor's retained ~/n/bin prefix and stale OpenSpec comment, and says environment settings do not relocate those wrapper commands
- **THEN** guidance describes wrapper portability as deferred because `bin/**` remains frozen without exceptions, distinguishes direct CLI configuration from bundled service startup, and does not claim a new helper or relocated wrapper pathname solves the limitation
- **THEN** full daemon rollout remains pending separate wrapper work or an independently provisioned operator service preserving credential and preflight guards
- **THEN** the same freeze covers launchd plists and all wrapper comments; current guidance explains the retained worker startup prerequisites and real pause/supervisor boundaries without promising those files were corrected

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

Guidance SHALL distinguish read-only Gitea preflight evidence from permission to run daemon stages. It SHALL state that successful preflight, configured allowlists and a clear foreign-journal location permit otherwise eligible Gitea dispatch, without overriding per-item authorization, pause, stop or health gates. Explicit engine publish, finish and critic context SHALL be documented separately from daemon eligibility.

#### Scenario: Preflight does not enable execution
- **WHEN** an operator follows Gitea configuration and preflight guidance
- **THEN** the guides and limitations explain that daemon drafting, admission, revisions, publication and pending delivery are enabled subject to the applicable authorization, health, pause, stop and foreign-journal gates; preflight alone does not authorize a label actor
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

The plugin README and engine guide SHALL describe Factory Publish/Finish as
confirmed shared-engine calls using the manifest-selected forge. They SHALL
explain Gitea WIP behavior, host credentials and existing display restrictions,
preserve GitHub-only Land and distinguish daemon eligibility from confirmation.
Finish SHALL be described as OpenSpec archival or native Status completion,
followed by push/readiness without merge. This permits the two scoped plugin
README corrections previously excluded by the earlier forge-only change;
plugin behavior and tests stay unchanged.

#### Scenario: Factory operator understands the destination
- **WHEN** an operator reads Factory's action table, confirmation and act guidance
- **THEN** it describes GitHub/configured Gitea publication, WIP-title creation and prefix removal, confirmation before either push and no send on Cancel
- **THEN** it retains Paseo-host config/bot/git credential instructions, engine links and display-origin versus write-routing distinctions
- **THEN** HTTPS/root-path display limits, direct-CLI needs for HTTP loopback/prefixed URLs and GitHub-only Land remain explicit, while both Finish passages state the selected format's lifecycle without changing plugin code

### Requirement: Gitea operation limitations

KNOWN-LIMITATIONS.md SHALL state that finish can overwrite a concurrent human title edit and read-back cannot detect that loss. It SHALL describe critic's 30-second socket timeout and 120-second request-start cutoff, explicitly permitting slowly arriving responses to overrun. It SHALL retain delayed-create duplication risk, explicitly describe the daemon read-after-write/single-creator assumptions under Gitea dispatch recovery guidance, and mark real Gitea publication as pending human verification.

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

The arming guide, known limitations and engine guide SHALL distinguish verified explicit Gitea arming from daemon stages and pending delivery subject to normal eligibility and foreign-journal gates. They SHALL remove obsolete GitHub-only arming and unconditional daemon-hold claims, preserve current human-initiated terminal landing and unrelated limits, and identify real issue arming and eventual drafting as pending human checks without claiming CI proves live operation.

#### Scenario: Operator understands the continuing hold
- **WHEN** an operator compares the updated arming guide, limitations and explicit-publication guidance
- **THEN** all describe configured arming and bot-backed verified labels alongside eligible Gitea drafting; force or preflight cannot bypass actor authorization, pause, stopped-job or foreign-journal holds
- **THEN** Factory Land remains GitHub-only and human-initiated terminal Gitea landing remains supported; publication guidance and concurrent loopback content stay intact apart from the scoped daemon-support corrections
- **THEN** the human checks require observing real issue arming and drafting after the prerequisites land, without claiming automated fixtures prove live enrollment

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
- **THEN** it explicitly applies that assumption to resumed completion delivery and publication-comment recovery after pause/resume or restart, while preserving completed results, full publication checklists and the rule against republishing solely for comment delivery

#### Scenario: Live enrollment remains a human check
- **WHEN** offline front-door tests pass and the implementation PR is prepared
- **THEN** the design retains unchecked live preflight, daemon bot-token sourcing with every landing variable absent, matching supervisor forge selection and small human-filed spec:draft/WIP-PR checks
- **THEN** refusal of an unlisted label actor is also a human check, distinct from CI evidence; live tasks are not assigned to implementing agents

### Requirement: Structured quota guidance

The guide SHALL document structured Claude classification, diagnostic-only
fallback, reset plus 60-second grace, the 30-minute fallback and the overage
human hold. It SHALL preserve existing fallback guidance, qualify its
all-blocked rule and cite issue #162. KNOWN-LIMITATIONS.md SHALL distinguish
after-session telemetry from advance detection and leave real-limit
validation pending. Other passages SHALL change only when made false.

#### Scenario: Operator distinguishes a quota wait from an overage hold
- **WHEN** an operator consults provider recovery and quota limitations
- **THEN** guidance identifies the evidence and deadline to inspect in run events, ordinary scheduled recovery, explicit human resume for overage, and the lack of live interruption or prevention of already-incurred charges
- **THEN** automated fixtures are not described as proof of a real limit, existing assertions, issue #72 references and unrelated guidance remain intact, and the new telemetry paragraph and issue link are validated with the design's physical-line rule that blocks missing or ambiguous boundaries

### Requirement: Source local credential guidance

The explicit-publication guide SHALL describe ordered, ephemeral source-local
helper forwarding for base fetch, publish and finish, separately from forge
API credentials. It SHALL preserve command examples/boundaries, identify live
Gitea validation as a human check and qualify completion by format. The former
blanket archive wording changes only because native completion retains files.

#### Scenario: Operator publishes and finishes with local helpers
- **WHEN** an operator follows the explicit-publication passage
- **THEN** it explains forwarding including empty resets via Git config environment, no helper copies into repo.git/config, unchanged gh argv/no-helper behavior and helper path/host credential-store prerequisites
- **THEN** it retains the #174 citation and explains both publish and finish use source helpers; finish archives OpenSpec or completes native Status and marks ready without merging, with daemon/land boundaries unchanged
- **THEN** edits use the existing unique physical-heading boundary, block missing/duplicate/indented starts and preserve concurrent steering-file guidance, command examples and links

### Requirement: Format selection guidance

The engine guide, command/manifest guide and self-hosting example SHALL document
`[run] format` and its OpenSpec default. They SHALL replace the temporary
spec-kit admission refusal with explicit native build support and its scope.
Guidance SHALL preserve OpenSpec workflow/executable requirements and incoming
binary guidance, cite #187 and #184 alongside #188, and distinguish offline
compatibility checks from deployed resume. This removes a deliberately temporary
unavailability claim, not the compatibility guarantees.

#### Scenario: Operator selects the supported format
- **WHEN** an operator reads How the engine works, command-json.md's Manifest format and the self-hosting example
- **THEN** the formats paragraph explains omitted or explicit OpenSpec and native spec-kit build selection, links both examples and retains the existing house-rules link sentence
- **THEN** self-hosting parses as OpenSpec, native structural admission can succeed and existing manifests need no migration or change to their OpenSpec executable contract

#### Scenario: Compatibility claims retain their limits
- **WHEN** format guidance and implementation are reviewed
- **THEN** OpenSpec finish/human-check/recovery guidance remains true, and the separate unchecked legacy-run deployed-resume item and its #184/#187 links remain present
- **THEN** the three named obsolete native-refusal cases are relocated into new tests/test_speckit_adapter.py and the revision adapter case into new tests/test_speckit_revisions.py; only the new files receive changed expectations or mock wiring, preserving selection/refusal-cleanup coverage, parameterization, markers and all unaffected assertions
- **THEN** the shared source test file receives only the four necessary definition removals, retaining its helpers and incoming binary assertions; no skip, xfail or collection override hides an obsolete expectation
- **THEN** documentation checks use deterministic physical boundaries, block ambiguity and avoid semantic Markdown parsing or exact-prose assertions

### Requirement: Spec-kit feature identity

`docs/specloop/speckit.md` SHALL explain feature directories `specs/<NNN>-<slug>/`
and branches `spec/<NNN>-<slug>`, using the repository's positive issue number
padded to at least three digits without truncation and the intake title-slug
algorithm (at most 40 lowercase ASCII characters, fallback `issue`). It SHALL
explain repository-local numbering, acceptable gaps, parallel-draft collision
avoidance and upstream directory-rule compatibility.

#### Scenario: Author chooses a stable feature identity
- **WHEN** an author reads the Feature directory and branch rule
- **THEN** the guide gives `042-add-a-gate` and `1234-big-feature` examples, the matching spec directories and spec branches, the precise slug rule and the numbering rationale
- **THEN** it explains repository-local issue numbers on GitHub and Gitea alike, why sequential scans collide, and compatibility with `^[0-9]{3,}-`
- **THEN** it preserves committed identities rather than renaming them after title edits, without changing existing OpenSpec naming

### Requirement: Spec-kit issue-first authoring

The guide SHALL instruct authors to open the issue before running
`/speckit.specify`, passing its number as the first argument (`42` or `#42`).
It SHALL explain the preset wrapper's `SPECIFY_FEATURE_DIRECTORY` override,
link spec-kit's `docs/overview/core.md`, and explain why sequential or timestamp
numbering configuration cannot derive a repository issue number.

#### Scenario: Author opens the issue before specifying
- **WHEN** an author follows the Open the issue first rule
- **THEN** both invocation forms use the issue title after the number and select `specs/042-add-a-gate` for Add a gate
- **THEN** the guide states the issue-first ordering and its reason without requiring a forge API lookup or a new numbering script

### Requirement: Spec-kit labelled scenarios

The guide SHALL require acceptance lines in the form
`N. **ASn** **Given** ... **When** ... **Then** ...`, numbered within each
user story and never renumbered once committed. It SHALL explain stable IDs
`<NNN>-<slug>/us<k>/as<n>`, include `042-add-a-gate/us1/as2`, and connect this
three-part shape to the unchanged scenario regex, pytest marker and per-story
grouping. It SHALL relate stable labels to `/speckit.converge`'s task-ID rule.

#### Scenario: Tests retain a scenario reference
- **WHEN** an author reads Labelled acceptance scenarios before adding or revising a story
- **THEN** the guide demonstrates the labelled list and full scenario citation, forbids renumbering or reusing committed IDs, and explains why tests can retain their references

### Requirement: Spec-kit task traceability

The guide SHALL retain spec-kit's task grammar
`- [ ] T001 [P] [US1] Description with file path`, backticked scenario IDs on
every user-story task and at least one covering task for every scenario. It
SHALL document review-feedback appends as `## Phase N: Review round K`, section
evidence/reviewer fixes as extensions of their existing phase, and final
Coverage/Acceptance fixes as new phases with those supplied titles. All new
base task IDs SHALL follow the largest existing T root using `T{M+1:03d}`;
harness ticking SHALL use `[X]`. This replaces the prior documentation-only
description that conflated coverage additions with review-round appends, to
match the existing engine's distinct operations without changing scoring.

#### Scenario: Author links tasks and scenarios
- **WHEN** an author reads Tasks cite scenarios
- **THEN** examples retain optional parallel markers, story tags, file paths and backticked scenario IDs, while setup tasks need no invented story reference
- **THEN** the guide requires full scenario coverage, stable task identities and distinct harness addition forms with collision-free phase/T numbering

### Requirement: Spec-kit human checks

The guide SHALL place human-only checks in `plan.md` under `## Human checks`
as `- [ ]` items, each naming exactly one scenario ID. It SHALL place
`<!-- nightshift:post-deploy -->` in the corresponding scenario body for
post-deploy checks, explain the analogy to OpenSpec's `design.md`, and explain
why human checks are neither implementing-agent tasks nor automated proof.

#### Scenario: Author records a live check
- **WHEN** an author reads Human-only checks
- **THEN** the guide shows the plan checklist, one scenario per item and the post-deploy marker's location in the scenario body
- **THEN** it does not require a plan template override or claim CI performed the check

### Requirement: Spec-kit flow-forward records

The guide SHALL describe immutable delivered feature records with harness task
updates and final `**Status**: Complete` in `spec.md`, without archival or merging
into a consolidated spec. It SHALL identify `.specify/memory/constitution.md`
as the overarching specification and require later behavior changes to name
superseded features in a new feature's `## Assumptions`. It SHALL explain stable
history and distinguish record persistence from Git PR merging.

#### Scenario: Author changes delivered behavior
- **WHEN** an author reads Flow-forward persistence
- **THEN** the guide explains the Complete status and new-feature supersession workflow rather than editing or archiving the delivered feature
- **THEN** it distinguishes drafting edits from delivered records and preserves existing OpenSpec lifecycle guidance

### Requirement: Spec-kit frozen authoring paths

The guide SHALL state that `specs/**` and `.specify/**` are frozen for
implementing agents, with the harness writing `tasks.md` and the Status line.
It SHALL explain protection of acceptance criteria and separation of authoring
from implementation without claiming that this documentation change implements
runtime freezing.

#### Scenario: Implementer leaves acceptance criteria intact
- **WHEN** an author reads Frozen paths
- **THEN** the guide names both globs, the harness-owned writes and the protection against an agent changing the criteria that judge its work

### Requirement: Spec-kit clarification boundary

The guide SHALL forbid unresolved `[NEEDS CLARIFICATION]` markers at approval,
including markers with a question suffix, and explain that remaining ambiguity
becomes drafter questions to the issue rather than a build. The reason SHALL
be unresolved intent requiring human input, not a claim of newly shipped
validator behavior.

#### Scenario: Draft still needs an answer
- **WHEN** an author reads No [NEEDS CLARIFICATION] at approval
- **THEN** the guide explains returning unresolved questions to the issue before approval and keeps enforcement work out of this change

### Requirement: Spec-kit discovery

The engine guide SHALL retain its one-sentence house-rules link under How the
engine works, and root README SHALL retain its presets/ Layout entry.
Guidance SHALL distinguish authoring asset installation from explicit native
drafting/builds and manifest-enrolled native front-door support. It SHALL
preserve existing assertions and other in-flight changes, correcting only
passages made false. This replaces the future-front-door wording because this
change supplies that integration; installation alone still does not enroll it.

#### Scenario: Find authoring guidance
- **WHEN** a reader follows the engine guide link or root Layout entry
- **THEN** house rules/preset remain discoverable, relative links resolve and no unperformed installation or live validation is described as complete
- **THEN** the eight authoring rules retain distinct headings or bold leads and the guide retains 042-add-a-gate/us1/as2, with original authoring-asset assertions unchanged

### Requirement: Standalone OpenSpec operator guidance

The prerequisite, engine, deployment, platform and plugin guides SHALL consistently describe the standalone OpenSpec pin, trusted executable selection, preflight checksum/version checks, install refusals and timeout-only transience. They SHALL distinguish OpenSpec's removal of Node/npm dependency from the plugin's retained Node setting and SHALL preserve concurrent unrelated documentation.

#### Scenario: Operator installs and verifies the release
- **WHEN** an operator follows `docs/deploy.md#install-openspec` from the prerequisite or host guidance
- **THEN** instructions identify the official 1.16.1 `openspec-darwin-arm64.tar.gz` archive for the sole mapped host and explain that all other hosts fail preflight as unknown, verify its SHA-256 against SHA256SUMS before extraction, place the executable at the configured path and run its version and preflight checks
- **THEN** archive and executable digests are distinguished, runtime acquisition/fallback is absent, and affected consumers must be restarted after replacement

#### Scenario: Operator understands stricter drafts and rollout
- **WHEN** an operator reads the validator and deployment guidance
- **THEN** it explains missing WHEN/GIVEN or THEN and unknown requirements-header failures, accepted closing hashes and the retained harness scenario lint
- **THEN** exactly three human checks remain pending and recorded on #191/#184: pre-merge validation of throwaway main and Scheming Vizier including disposable strict examples, pre-deploy validation of both enrolled deployment checkouts, and the Mac Studio admission/finish cut-over
- **THEN** the bundled supervisor PATH/comment remain unchanged; absolute executable selection plus the cut-over establish dependency removal, with no separate supervisor-without-`~/n/bin` experiment, while exact bundled-wrapper cleanup remains deferred on #184's ledger
- **THEN** archived failures are informational and offline tests do not claim live deployment success

### Requirement: Native build operator guidance

The engine guide SHALL describe explicit native drafting/builds, Complete Status,
no archive and frozen authoring paths. Enrollment guidance SHALL explain
[run] format as the selector for native front-door support, with no new repository configuration field. Both plugin
Finish passages SHALL continue qualifying completion by format. False
pending-drafting/feasibility passages in root README, the house rules, command
JSON guide, preset README, native manifest comments and the native-spec-kit
KNOWN-LIMITATIONS bullet SHALL receive scoped corrections.

Existing skip-feasibility examples SHALL remain valid as explicitly optional
operator-requested bypass examples, while a separate native workflow SHALL show
normal admission with feasibility. This replaces the obsolete requirement that
native builds need that interim bypass, without changing the option or protected
example assertions. Task-scaffold revision/coverage instructions SHALL remain
unchanged.

#### Scenario: Operator finishes the selected format
- **WHEN** an operator reads How the engine works, Publication, root enrollment and the plugin Finish action/confirmation guidance
- **THEN** the docs agree that OpenSpec Finish archives into openspec/specs/, native Finish sets Status Complete and archives nothing, and both push and mark ready without merging
- **THEN** native examples distinguish the optional existing skip-feasibility bypass from the normal reviewed admission path, retain structural admission validation and describe native daemon intake under the existing enrollment and authorization gates
- **THEN** the preset README describes the same support without claiming installation configures it, and its task template retains review-round appends, existing-phase fixes and final Coverage/Acceptance fixes

#### Scenario: Flow forward context limits are explicit
- **WHEN** an operator reads KNOWN-LIMITATIONS.md and the native engine path
- **THEN** guidance distinguishes selected-feature/constitution build context from feasibility/critic reads of explicitly named Complete features, with no consolidated current spec, automatic supersession traversal or exhaustive cross-feature conflict detection
- **THEN** delivered feature records remain in place, later changes name superseded features, unrelated limitations remain and real build/drafting observations remain pending until performed
- **THEN** the KNOWN-LIMITATIONS edit is confined to its native-spec-kit bullet and preserves concurrent readiness changes

### Requirement: Native manifest example

`examples/specloop/speckit.toml` SHALL be a complete parsable trusted-manifest
template with native format, editable repository/profile/check paths,
`specs/**` and `.specify/**` frozen, workflow protections, positive phase-gate
numbers and a `[coverage] step` naming an actual verify step. Guidance SHALL
instruct operators to keep the effective trusted manifest outside the target
repository.

#### Scenario: Example selects native build and coverage
- **WHEN** the new example is loaded through the real manifest loader without running commands
- **THEN** it selects SPECKIT, freezes both authoring globs and workflows, uses only positive example gates and resolves its coverage step to a declared verifier using the existing scenario-log convention
- **THEN** self-hosting remains OpenSpec and only its obsolete spec-kit-unavailable comments change

### Requirement: Native documentation verification

New documentation checks SHALL live in a new test file, validate real TOML/CLI
syntax and local links without dispatch, and select passages using the design's
deterministic physical-line heading/fence rules. Missing or ambiguous boundaries
SHALL fail. Unrelated examples, links, pending checks and existing plugin
assertions SHALL remain intact.

#### Scenario: Documentation remains structurally reviewable
- **WHEN** native documentation and examples are verified
- **THEN** unique exact column-zero headings bound passages through the next same-or-higher heading, exact physical delimiters select command blocks and missing/duplicate boundaries fail without semantic Markdown parsing
- **THEN** real parsers accept examples, relative links resolve, new tests avoid exact explanatory-prose assertions and all unaffected existing assertions remain

### Requirement: Native drafting guidance

The engine guide and house rules SHALL explain native draft artifacts,
local templates, stable revisions, scenario/task shape, questions and human
checks. They SHALL state the six-task per-story target, unchanged roughly
300-changed-line per-task ceiling, advisory risk above eight tasks in a story
phase and absence of whole-feature count risk. Existing OpenSpec drafting prose
SHALL remain unchanged.

#### Scenario: Author finds the native workflow
- **WHEN** an author reads the native drafting guide and house rules
- **THEN** the guide explains templates, ASn labels, prioritized story phases, story tags, backticked citations, plan human checks, up to three genuine questions and revision in the existing undelivered feature directory
- **THEN** it shows explicit propose and normal admission without a feasibility bypass, states the per-story sizing rule and links the supported native daemon front door without adding internal base-pin options to explicit CLI examples
- **THEN** the new drafting paragraph identifies `specs/` and `openspec/` as reserved specification roots excluded from shared planned-file extraction for every format, with or without a root, and explains the deliberate OpenSpec change: `specs/helper.py` is no longer a planned edit while `src/helper.py` remains eligible

#### Scenario: Quality evidence remains human
- **WHEN** documentation describes verification of native drafting
- **THEN** it leaves a real feature-sized scratch-repository draft with real profiles and spec-PR inspection pending until performed, recording rounds, questions and critic findings on tracking issue 184
- **THEN** offline prompt/routing tests are not described as proving draft quality or completed live checks

### Requirement: Native front door guidance

The scoped guides SHALL explain native enrollment, naming, reuse, PR text, AS markers, revisions, isolated draft workspaces and bounded recovery. The arming guide SHALL document native issue attribution and both reserved spec roots. Updates SHALL preserve examples, headings, prerequisites, assertions and pending checks. New tests SHALL use deterministic physical heading/fence boundaries, fail ambiguity and preserve #182's protected files.

#### Scenario: Operator finds native enrollment
- **WHEN** an operator reads the native front-door and enrollment guidance
- **THEN** it shows issue 7 becoming 007-add-a-gate and issue 1234 growing without truncation, the spec/ branch and specs/ directory, and format = speckit in the trusted manifest without a new repos field
- **THEN** it explains existing-feature reuse, unchanged recorded identities, same-line AS markers, adapter revision numbering, native drafts/@speckit/<owner>/<repo>/<change> isolation, unchanged flat OpenSpec workspaces, existing daemon one-job-per-item receipts/stopped-job holds, direct CLI same-repository/change coordination and pre-upgrade writer draining
- **THEN** the ledger retains globally change-keyed run storage with existing collision refusal and separate state roots for concurrent identical native names; no draft lock or run namespace is claimed
- **THEN** docs/arming.md recognizes native <NNN>-<slug> and compatibility <owner>__<repo>-<NNN>-<slug> identities using the numeric prefix as the source issue, excludes openspec/ and specs/ from scope paths, and retains existing OpenSpec examples
- **THEN** existing OpenSpec naming literals, CLI examples, heading/fence boundaries, links, plugin content and the separate legacy-resume checklist remain intact; new offline checks parse real CLI/TOML examples without dispatch

#### Scenario: Live enrollment remains pending
- **WHEN** documentation reports native front-door availability and verification
- **THEN** it leaves the first spec-kit-only enrollment through draft, review, approval, build, publish and merge as a human check on either forge, with labels, trusted manifest and installed preset
- **THEN** the operator records issue/PR identity and a touchpoint report against the baseline on #184, while existing draft-quality, build/Finish and deployed legacy-resume checks remain pending until performed
- **THEN** KNOWN-LIMITATIONS changes only its native-spec-kit bullet, retains #182 readiness material and does not present offline tests as live success

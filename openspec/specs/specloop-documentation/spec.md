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
guard-clause operators with their implementation PR citations. It SHALL
preserve the distinction between measured evidence, unmeasured generated
mutants and a no-candidate skip.

#### Scenario: Parallel and timed out mutants are interpreted
- **WHEN** a reader configures or reads a section mutation result
- **THEN** the guide describes baseline-first isolated execution, default `mutation_parallel = 2`, serial value `1`, and scoring only killed/survived results
- **THEN** it distinguishes selected-test `timeout` from preparation/configure/build `invalid`, excludes timeouts from both score terms, and cites issue #4 / PR #27 and issue #31 / PR #44

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

`KNOWN-LIMITATIONS.md` SHALL remove demonstrated fixed claims, preserve
unresolved portions of mixed claims, and describe the remaining gaps supplied
from issue #17's ledger. It SHALL not claim a gap fixed solely because a
related feature shipped. It SHALL describe confirmed stopped-job release as
supported recovery rather than listing issue #34's missing command as open.

#### Scenario: Fixed limitations leave the open list
- **WHEN** the limitations list is reconciled with the merged behavior
- **THEN** it no longer lists serial mutants, unchecked label actors, timestamp-based specloop feedback loss/replay, missing job stop or confirmed-stop release, whole-status failure, missing requested/exhausted-infra attention, idea payloads in argv or the resolved CLI/prepare-prompt gaps as open
- **THEN** deadlines, retention, unresolved crash boundaries, config, credential, quota and platform gaps remain unless a specific fix supports their removal

#### Scenario: Remaining ledger gaps are explicit
- **WHEN** a reader checks what automation still cannot guarantee
- **THEN** the list includes admission completing while paused and still building, touchpoint undercount from missing history, descoped JSX style filtering, and GitHub-only automated front door and publication
- **THEN** it describes `nightshift jobs release <id>` as explicit recovery for confirmed stops while retaining the limits on automatic retry, committed effects and legacy/unkeyed revision commit/result risk and unproven machine-crash durability for keyed revisions
- **THEN** it distinguishes configured `mutation_skip` from automatic JSX filtering and local execution from hosted front-door/publication support, linking the tracking ledger

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

`README.md`, `docs/specloop/README.md` and `KNOWN-LIMITATIONS.md` SHALL describe the implemented manifest hold/repair, engine-switch recovery and owner-qualified naming behavior in their relevant passages. They SHALL preserve unrelated documentation and cite verified issue #13 or its implementing PR without inventing a PR. Only the three resolved Config limitations SHALL be removed; migration and unsupported-name limits SHALL remain explicit.

#### Scenario: Operator repairs enrollment
- **WHEN** an operator consults enrollment and recovery guidance
- **THEN** the docs explain regular-file template validation, optional `run.change` and `run.branch` fields supplied by intake, repository-local health, retained triggers, retry after file repair, config-restart requirements and continued publication/build revisions of admitted runs
- **THEN** they explain release of orphaned working issues and human inspection of surviving worker work without claiming those tasks resume automatically

#### Scenario: Operator understands names and limits
- **WHEN** an operator reads naming examples and remaining limitations
- **THEN** both guides use owner__sample-issue-7-add-a-gate and its spec branch, explain unsupported unsafe spellings and preservation of recorded legacy identities
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

The root README and specloop guide SHALL describe active background rows in nightshift status consistently with local job inventory.

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
- **THEN** it explains repository-local holds for missing or unusable manifests and removal of worker status output, including the off-subscription model report, while all other status output remains unchanged
- **THEN** direct Python front-door instructions do not require worker verification strings, endpoint models or OAuth; manifest providers retain their own requirements
- **THEN** bundled service instructions explicitly retain the frozen daemon wrapper's env-file, worker OAuth, GH_TOKEN and gh/claude/pnpm/node/git/swift checks, distinguishing direct invocation from service startup without claiming Python changes bypass shell checks

#### Scenario: Existing installation is checked before merge
- **WHEN** an operator reads retirement and rollout guidance
- **THEN** it identifies retained historical attention/recovery, unchanged CLI and state locations, separate daemon/supervisor jobs and the need to inspect live config/plists/external callers before merge
- **THEN** it keeps the CLI merge and state move assigned to #40, preserves unrelated limitations, and cites issue #16 without inventing an implementation PR
- **THEN** live config, plist and external-caller inspection remains a pre-merge human checklist, never a claimed CI result
- **THEN** it does not claim host checks performed, automatic migration, rollback of remote effects or automatic restart of interrupted worker work
- **THEN** it identifies obsolete worker instructions/metadata in frozen CLAUDE.md and pyproject.toml, historical worker documents and their removed-source references, and resolved type-baseline entries as deferred cleanup outside the implementation allowance

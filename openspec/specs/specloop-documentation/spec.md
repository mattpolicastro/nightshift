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
retain the distinction between specloop and worker behavior.

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
related feature shipped.

#### Scenario: Fixed limitations leave the open list
- **WHEN** the limitations list is reconciled with the merged behavior
- **THEN** it no longer lists serial mutants, unchecked label actors, timestamp-based specloop feedback loss/replay, missing job stop, whole-status failure, missing requested/exhausted-infra attention, idea payloads in argv or the resolved CLI/prepare-prompt gaps as open
- **THEN** deadlines, retention, unresolved crash boundaries, config, credential, quota and platform gaps remain unless a specific fix supports their removal

#### Scenario: Remaining ledger gaps are explicit
- **WHEN** a reader checks what automation still cannot guarantee
- **THEN** the list includes admission completing while paused and still building, the stopped-job hold with no retry command (issue #34), touchpoint undercount from missing history, descoped JSX style filtering, and GitHub-only automated front door and publication
- **THEN** it distinguishes configured `mutation_skip` from automatic JSX filtering and local execution from hosted front-door/publication support, linking the tracking ledger

### Requirement: Documentation integrity

The three-document-only implementation scope applies to #67's documentation reconciliation, not to subsequent features with explicit code scope and requirement deltas. Subsequent guide updates SHALL stay within their declared sections, use short, concrete, present-tense prose, preserve unrelated runtime requirements and all existing assertions, and cite verified issue/PR identities without inventing references. This change SHALL limit guide edits to Section evidence and leave the root README and known-limitations list unchanged. Implementation acceptance SHALL include green `uv run pytest`; human rollout checks SHALL remain in the design and implementation PR, not implementing-agent tasks.

#### Scenario: Scope and regression checks remain intact
- **WHEN** the implementation diff and local validation are reviewed
- **THEN** changes stay within this proposal's named files, guide edits stay within Section evidence, unrelated documentation and existing test assertions remain unchanged, relative documentation links resolve, and `uv run pytest` passes without weakening checks

#### Scenario: References and reader checks are reviewable
- **WHEN** the documentation is prepared for merge
- **THEN** existing implementation citations remain and new references distinguish issue #73 from any verified implementation PR, without inventing a PR number or describing pending rollout as shipped
- **THEN** the PR carries the design's human checklist for private-manifest rollout and real-gate timing comparison; #67's historical live-reference/ledger and readability checks are not reassigned as implementing-agent tasks

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

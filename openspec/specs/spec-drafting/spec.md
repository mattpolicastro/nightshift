# spec-drafting Specification

## Purpose
Guide task and section sizing by risk, concern and dependencies while retaining
single-session work limits and scenario coverage.

## Requirements

### Requirement: Risk based task sizing

The drafter's `tasks.md` instructions SHALL combine work touching the same file
for one concern and minimize sections as dependencies allow. For OpenSpec they
SHALL recommend six tasks or fewer for most changes. For feature-shaped
spec-kit input they SHALL recommend six tasks or fewer per user story, with
one story phase per user story; the whole-feature count SHALL NOT be a size
risk or rejection condition. Both formats SHALL explain that every task incurs
a review cycle and every section an evidence gate. They SHALL retain the roughly
300-changed-line ceiling per task, one-concern session boundary, dependency
ordering, scenario coverage and tests with code. Human checks SHALL remain in
the design for OpenSpec and in plan.md for spec-kit.

This changes the unit of the native recommendation because a feature can
contain several independently useful stories; it does not raise the task ceiling.

#### Scenario: Related work shares a task
- **WHEN** the drafter receives instructions for an OpenSpec change whose same-file work addresses one concern
- **THEN** the instructions call for combining that work within the task ceiling, normally using six tasks or fewer and as few sections as dependencies allow
- **THEN** they explain the review-cycle and evidence-gate costs without making six tasks a hard rejection limit

#### Scenario: Necessary boundaries remain
- **WHEN** separate concerns, dependencies or the task ceiling require splitting work
- **THEN** the instructions retain single-session tasks of about 300 changed lines at most, dependency ordering, scenario references and coverage, tests with their code, and human-only checks in the format's design document

#### Scenario: Native feature size is per story
- **WHEN** a native feature has three stories with five tasks each
- **THEN** each story satisfies the six-task recommendation and the fifteen-task total is not a size risk
- **THEN** the same roughly 300-changed-line ceiling applies to each task, not to the whole story or feature

### Requirement: Compact documentation plans

For OpenSpec, the drafter SHALL be instructed to use one or two tasks in one
section for a documentation-only change, subject to the retained single-session
task ceiling. For native documentation-only features it SHALL recommend one or
two tasks per story within the required story-phase structure, without imposing
a whole-feature task-count risk. Neither format SHALL split merely by document
heading. The native exception preserves required story structure rather than
forcing a multi-story feature into one section.

#### Scenario: Documentation only sizing
- **WHEN** an OpenSpec change updates documentation without changing executable behavior
- **THEN** the drafter's instructions specify one or two tasks in one section instead of splitting tasks or sections by document heading

#### Scenario: Native documentation retains story phases
- **WHEN** a documentation-only native feature contains multiple user stories
- **THEN** the prompt retains one phase per story and compact one/two-task guidance within each story, with no whole-feature count finding

### Requirement: Draft base

A draft SHALL branch from the base as resolved in the source checkout, independent of the branch that checkout currently has out. Before resolving, drafting SHALL fetch the base from the source's `origin` when there is one (best effort, bounded) and prefer that copy, so a blocker merged upstream moments earlier is included. Without an origin, or when the fetch fails, the local base is used. A reviewed `from_branch` tip is used as given.

The front door MAY pass the exact base commit it resolved for existing-feature discovery before admission. In that case drafting SHALL use that commit without a second base resolution. The pinned commit SHALL be the commit ordinary resolution produced at discovery time, so discovery and drafting inspect the same tree. An unpinned invocation SHALL behave exactly as before. A pinned input SHALL be a full existing commit object, not a moving ref, and SHALL NOT be combined with a reviewed from_branch. This qualifies the fetch-before-drafting rule only when that fetch/resolution already occurred for discovery.

#### Scenario: A just-merged blocker is in the base
- **WHEN** a blocker's PR merges upstream and its dependent is drafted before the source checkout pulls
- **THEN** the draft starts from the upstream base that includes the blocker

#### Scenario: The source checkout branch does not matter
- **WHEN** the source checkout has a feature branch checked out, or has no origin
- **THEN** the draft still starts from the base (upstream if available, otherwise local), not from the checked-out branch

#### Scenario: Reviewed tip remains authoritative
- **WHEN** a spec revision supplies a reviewed from_branch tip without a base pin
- **THEN** drafting uses that tip as given without substituting the upstream or local base

#### Scenario: Existing feature on stale local base
- **WHEN** origin's base contains specs/042-add-a-gate/ and the local base does not, and issue 42 explicitly names that directory
- **THEN** discovery performs ordinary upstream-first resolution, pins that exact commit and finds the existing feature there
- **THEN** drafting uses the pinned commit without resolving the base again and revises that directory rather than creating a second feature, even if origin advances after discovery

#### Scenario: Invalid or conflicting pin refuses
- **WHEN** a proposal supplies a non-commit, abbreviated or missing base object, or combines a base pin with from_branch
- **THEN** drafting refuses before replacing the workspace or invoking an agent and does not fall back to a different tree

### Requirement: Rich text line rules

The drafter prompt SHALL require that tasks recognizing structure in Markdown, HTML or other rich text specify a deterministic line rule and the safe direction for ambiguity, and SHALL forbid requiring semantic parsing of fences, HTML blocks or indentation.

#### Scenario: Structure is specified by line rules
- **WHEN** a change needs to recognize a checklist, heading or similar structure in rich text
- **THEN** its tasks state a line rule and that ambiguous input resolves to the safe outcome, rather than asking for Markdown-aware parsing

### Requirement: Native feature drafting

For `format = "speckit"` the drafter SHALL receive native instructions to read
local `.specify/templates/` scaffolds and produce `spec.md`, `plan.md` and
`tasks.md` only under the adapter's `specs/<change>/` root. The instructions
SHALL require the house rules, prohibit network/template installation and
`specify` invocation, and retain scenario coverage and feasible agent work.
The existing OpenSpec prompt SHALL remain byte-identical.

#### Scenario: Native prompt names house rules
- **WHEN** a spec-kit manifest renders a drafting prompt for an ordinary idea
- **THEN** the instructions name the three local templates and native root, ASn-labelled scenarios, backticked stable scenario IDs, positional story labels, and one phase per prioritized user story after Setup/Foundational
- **THEN** they require human checks in plan.md and at most three genuine clarification questions in spec.md, contain no generated openspec/ references, and retain tests with code and the per-task session ceiling

#### Scenario: Existing feature is revised in place
- **WHEN** the idea names an existing undelivered feature matching the effective change
- **THEN** the prompt directs revision within that directory while preserving committed task/scenario identities and unrelated content instead of creating another feature
- **THEN** conflicting target identities require clarification without writing elsewhere, and Complete records remain immutable with later behavior changes expressed as a new feature

#### Scenario: OpenSpec prompt remains unchanged
- **WHEN** the format is omitted or explicitly openspec
- **THEN** the rendered drafting prompt retains its previous bytes, root, task shape and guidance

### Requirement: Native draft lint

Proposal lint and overlap checks SHALL use the selected adapter's task parser.
Lint SHALL retain adapter validation, scenario discovery, task paths and drafter
root confinement, including ordinary parse-error feedback and transient
OpenSpec-validator propagation. Native human-task diagnostics SHALL reference
plan.md and recognize human-task text after positional native task flags.

#### Scenario: Fixture passes and stray files refuse
- **WHEN** lint checks a copied valid native fixture and subsequently a changed file outside its feature root
- **THEN** the valid fixture passes and the stray change is reported with the adapter's specs/<change>/ root in the message
- **THEN** native validation requires no external validator and a similarly prefixed sibling feature is outside the root

#### Scenario: Native tasks retain lint and overlap checks
- **WHEN** native tasks contain unknown or uncovered scenario IDs, invalid phase/task identities, a flagged human task or a task editing a concurrently planned test file
- **THEN** lint or first-round overlap feedback reports the applicable problem using parsed native tasks rather than treating the document as an empty OpenSpec plan
- **THEN** human-task feedback sends the check to plan.md, and existing correction and necessary-overlap policies remain unchanged

### Requirement: Native draft questions

After a successful native drafting session, `speckit.questions` applied to the
parsed drafted spec SHALL feed the existing Draft.questions/needs-input path
before strict lint. Explicit agent questions SHALL be retained first, followed
by marker questions in source order with repeated parser occurrences retained.
This SHALL NOT weaken direct lint or admission clarification validation.

#### Scenario: Clarification marker becomes question
- **WHEN** an agent reports drafted but the native spec contains a clarification marker
- **THEN** the marker is returned through the existing question outcome without a lint correction, commit, feasibility review, PR lookup or critic call
- **THEN** colon-bearing and bare markers use the existing literal parser, and more than three existing markers are not silently discarded

#### Scenario: Explicit questions and unreadable input
- **WHEN** an agent returns explicit questions alongside readable native markers, or no explicit questions and an unreadable spec
- **THEN** the first case retains the agent questions and parser markers, and the second reports a draft problem rather than a clean empty spec
- **THEN** a direct lint or admission call continues rejecting unresolved markers in all currently validated feature files

### Requirement: Repository qualified native draft workspaces

Managed native proposals SHALL use `<state_root>/drafts/@speckit/<owner>/<repo>/<change>` via the shared proposal CLI, with separate validated owner/repository components from the trusted manifest. The unchanged `SAFE_CHANGE` SHALL reject `@speckit`. OpenSpec output SHALL remain `<state_root>/drafts/<change>`. Replacement SHALL affect only the selected leaf after path-safety checks on every managed parent.

#### Scenario: Native workspaces are isolated
- **WHEN** native manifests for a/x and b/x propose 007-add-a-gate through daemon or direct CLI entry points
- **THEN** their managed outputs are drafts/@speckit/a/x/007-add-a-gate and drafts/@speckit/b/x/007-add-a-gate, and replacing either leaves the other's files intact
- **THEN** owner/repository components retain exact spelling without delimiter concatenation, so distinct accepted repository tuples have distinct managed outputs
- **THEN** native a__b/c and a/b__c also resolve distinctly to drafts/@speckit/a__b/c/007-add-a-gate and drafts/@speckit/a/b__c/007-add-a-gate; sequential direct CLI proposals and re-drafts in either order leave the other workspace's sentinel files intact after each replacement
- **THEN** namespace selection uses the trusted repository; missing/unsafe repository components or linked/non-directory managed parents refuse before replacement, and explicit isolated Python output paths retain their supported behavior
- **THEN** the change requires no workspace migration, lock, wrapper handoff or new receipt/state kind; existing daemon one-job-per-item receipts and stopped-job holds remain authoritative

#### Scenario: OpenSpec workspaces remain flat and intact
- **WHEN** native a/x issue 7 and OpenSpec 1-a/1-x__007 issue 7, both titled Add a gate, are drafted or redrafted in either order
- **THEN** their directories are drafts/@speckit/a/x/007-add-a-gate and drafts/1-a__1-x__007-issue-7-add-a-gate, and drafting either leaves sentinel files in the other's workspace intact
- **THEN** SAFE_CHANGE.fullmatch("@speckit") is None, no valid OpenSpec or explicit CLI change can occupy the namespace root, and existing flat OpenSpec paths, legacy migration refusals and compatibility links remain unchanged

## Purpose

Guide cohesive single-session task planning and proportional native specification
detail while preserving scenario coverage, required structure and execution limits.

## ADDED Requirements

### Requirement: Proportional native detail

The native drafter prompt SHALL require detail proportional to observable behavior:
only the user stories, requirements and acceptance scenarios needed to specify the
feature and its meaningful failure cases. It SHALL forbid inventing optional
subsystems, duplicated requirements or implementation-level requirements merely to
fill template sections. It SHALL preserve required structure, scenario identities,
coverage and genuine clarification handling.

#### Scenario: Small feature has only necessary detail
- **WHEN** the native drafter receives a small feature with limited observable behavior
- **THEN** its instructions require only needed stories, requirements and acceptance scenarios, including meaningful failures, and forbid padding with optional subsystems, duplicate requirements or implementation detail to fill sections
- **THEN** required sections, Setup/Foundational and prioritized story phases, stable AS identities, task citations, full coverage and at most three genuine missing-intent questions remain required

#### Scenario: Smaller plan retains meaningful failures
- **WHEN** a small native feature has a meaningful failure case or genuinely missing intent
- **THEN** proportional-detail guidance does not authorize omitting that failure case, required structure or scenario coverage, or guessing the missing intent
- **THEN** existing clarification handling, revision identity preservation and Complete-record rules remain intact

## MODIFIED Requirements

### Requirement: Risk based task sizing

Both drafter prompts SHALL require one coherent, independently verifiable concern
per single-session task and keep coupled production changes and their tests
together, including work spanning files. They SHALL combine same-file work for one
concern, preserve dependency ordering and minimize sections as dependencies allow.

About 600 changed lines SHALL be a soft planning guideline per task, counting
production code, tests and documentation together; it SHALL NOT be a target or an
automatic rejection threshold. The instructions SHALL split at independent behavior,
migration/recovery boundaries, distinct verification strategies or substantial
uncertainty. Above roughly 600 lines they SHALL prefer a safe split; if splitting
would break cohesion, they SHALL require the boundary and verification rationale
in the existing OpenSpec design.md or native plan.md. Estimated lines or total task
count alone SHALL NOT require another issue or PR.

For OpenSpec the instructions SHALL recommend six tasks or fewer for most changes.
For native features they SHALL recommend six tasks or fewer per story and retain
one phase per story. Both SHALL explicitly permit fewer tasks and forbid padding
to six. The whole-feature native task count SHALL NOT be a size risk or rejection
condition. Both formats SHALL explain that every task incurs an implement, verify
and review cycle and every section or phase an evidence gate. They SHALL retain
scenario references, coverage, tests with code and human-only checks in design.md
for OpenSpec or plan.md for native features. Execution budgets and gates SHALL
remain unchanged; this replaces only the former roughly 300-line hard ceiling.

#### Scenario: Related work shares a task
- **WHEN** the drafter receives an OpenSpec change whose same-file or coupled cross-file work addresses one independently verifiable concern
- **THEN** its instructions combine that work and tests under the soft guideline, normally use six tasks or fewer with as few sections as dependencies allow, explicitly allow fewer tasks and never pad to six
- **THEN** they explain review-cycle and evidence-gate costs without making six a rejection limit

#### Scenario: Necessary boundaries remain
- **WHEN** independent behavior, migration/recovery boundaries, distinct verification strategies, substantial uncertainty or dependencies require splitting work
- **THEN** instructions retain those boundaries, single-session work, dependency order, scenario references and coverage, tests with code and human checks in the format's design document
- **THEN** the new guideline changes no adapter, turn/time budget, seal limit or verification/review/evidence gate

#### Scenario: Native feature size is per story
- **WHEN** a native feature has three stories with five tasks each
- **THEN** each story satisfies the six-task recommendation and the fifteen-task total is not a size risk or rejection condition
- **THEN** about 600 combined production/test/documentation changed lines is a soft per-task guideline, not a story or feature ceiling, and required story phases remain

#### Scenario: Cohesive task exceeds old ceiling
- **WHEN** a valid draft has one coherent independently verifiable task estimated at 450 changed lines including coupled production work, tests and documentation
- **THEN** drafting and review instructions allow that task without a size-only rejection, forced split, additional issue or PR
- **THEN** scripted clean feasibility and critic outcomes permit a committed draft through existing gates with no correction round

#### Scenario: Above guideline retains rationale
- **WHEN** a task is estimated above roughly 600 changed lines and splitting it would break cohesion
- **THEN** the instructions prefer a safe split but permit keeping the task together with its boundary and verification rationale in the existing design.md or plan.md
- **THEN** estimated lines or total task count alone do not force rejection or a separate issue or PR, and 600 is never a target to fill

### Requirement: Compact documentation plans

For OpenSpec, the drafter SHALL be instructed to use one or two tasks in one
section for documentation-only changes, retaining coherent single-session work
and the soft planning guideline. Native documentation-only features SHALL retain
one or two tasks per story within required story phases without a whole-feature
task-count risk. Neither format SHALL split merely by document heading or pad
a plan to six tasks. This replaces references to the former hard task ceiling.

#### Scenario: Documentation only sizing
- **WHEN** an OpenSpec change updates documentation without changing executable behavior
- **THEN** instructions specify one or two tasks in one section instead of splitting by document heading, with coherent single-session work and the soft 600-line guideline

#### Scenario: Native documentation retains story phases
- **WHEN** a documentation-only native feature contains multiple user stories
- **THEN** the prompt retains one phase per story and compact one/two-task guidance within each story, with no whole-feature count finding
- **THEN** the guideline remains per task and no story is padded to six tasks

### Requirement: Native feature drafting

For `format = "speckit"` the drafter SHALL receive native instructions to read
local `.specify/templates/` scaffolds and produce `spec.md`, `plan.md` and
`tasks.md` only under the adapter's `specs/<change>/` root. The instructions
SHALL require the house rules, prohibit network/template installation and
`specify` invocation, and retain scenario coverage and feasible agent work.
The OpenSpec prompt SHALL preserve its root, task shape and all unrelated
guidance. Only the cohesive-task and soft-600-line planning policy, including
dependent ceiling language and explicit permission for fewer tasks, SHALL
replace the former byte-identical sizing guidance.

#### Scenario: Native prompt names house rules
- **WHEN** a spec-kit manifest renders a drafting prompt for an ordinary idea
- **THEN** the instructions name the three local templates and native root, ASn-labelled scenarios, backticked stable scenario IDs, positional story labels, and one phase per prioritized user story after Setup/Foundational
- **THEN** they require human checks in plan.md and at most three genuine clarification questions in spec.md, contain no generated openspec/ references, and retain tests with code and the single-session boundary with a soft per-task planning guideline

#### Scenario: Existing feature is revised in place
- **WHEN** the idea names an existing undelivered feature matching the effective change
- **THEN** the prompt directs revision within that directory while preserving committed task/scenario identities and unrelated content instead of creating another feature
- **THEN** conflicting target identities require clarification without writing elsewhere, and Complete records remain immutable with later behavior changes expressed as a new feature

#### Scenario: OpenSpec prompt remains unchanged
- **WHEN** the format is omitted or explicitly openspec
- **THEN** the rendered drafting prompt retains its root, task shape and unrelated guidance, with only the explicitly authorized cohesive-task and soft-600-line sizing migration

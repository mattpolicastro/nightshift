## Purpose

Review native proposals and native admission feasibility using constitution and
named delivered-feature guarantees, retaining existing review outcomes.

## ADDED Requirements

### Requirement: Native feasibility and critic context

Native feasibility and critic prompts SHALL read the effective feature,
constitution and Complete features explicitly named under Depends on or
Assumptions, using the design's literal rules and exact candidate checkout.
They SHALL treat undisclosed constitution/Complete-feature behavior changes as
contradictions, retain `feasibility.SCHEMA` and existing call contracts, and
never silently pass unreadable or ambiguous required context.

#### Scenario: Constitution and named features are read
- **WHEN** native feasibility or the critic reviews a candidate whose plan names Complete features under Depends on and Assumptions
- **THEN** its prompt instructs reading the constitution and each named spec.md, with the actual committed files available in its read-only candidate extraction
- **THEN** the selected feature is the effective change, not a manifest template name, and unrelated history is not automatically traversed

#### Scenario: Undisclosed behavior changes contradict
- **WHEN** a native plan breaks a constitution principle or changes a named Complete feature guarantee without identifying that change in Assumptions
- **THEN** review instructions require a contradiction with the task and concrete evidence
- **THEN** an explicit, justified Assumptions statement removes that missing-disclosure basis only, while frozen-file and trusted-verifier contradictions remain

#### Scenario: Missing or ambiguous context is not a pass
- **WHEN** required constitution or named feature context is unreadable, identities are ambiguous or status lines conflict
- **THEN** the prompts require an evidenced inability-to-complete finding rather than silently omitting context and reporting a clean review
- **THEN** Draft features are not treated as Complete guarantees, and matching physical lines in examples receive no semantic Markdown exemption

#### Scenario: Native admission uses feasibility normally
- **WHEN** an otherwise valid native feature is admitted without skip-feasibility
- **THEN** the existing admission path invokes the native feasibility prompt and respects its verdict using the existing schema and refusal behavior
- **THEN** OpenSpec prompt bytes, critic checklist items, question ordering, correction limits and profile/time-limit defaults remain unchanged

## MODIFIED Requirements

### Requirement: Advisory task count findings

For OpenSpec, the critic's over-engineering item SHALL flag plans with more than
eight tasks, or documentation-only plans with more than two tasks, as
non-blocking risks. For spec-kit it SHALL count entries separately in each
user-story phase using existing native physical-line task/phase rules and flag
more than eight in any one story phase. It SHALL NOT flag the native total,
including documentation-only totals, or count non-story phases toward this
threshold. Other concrete design concerns SHALL remain reviewable.

Both formats SHALL name task labels that could merge and their shared concern,
or explain why necessary boundaries prevent a safe merge. Task count alone
SHALL never be a contradiction or require a correction. Independent concrete
defects SHALL retain their existing blocking criteria. This native exception
replaces whole-change sizing because feature-shaped input spans multiple stories.

#### Scenario: Excessive plans receive merge advice
- **WHEN** the critic reviews an OpenSpec plan with nine or more tasks, or an OpenSpec documentation-only plan with three or more tasks
- **THEN** its checklist instructs it to report a non-blocking risk identifying tasks that could merge and why, without inventing a merge when boundaries are necessary

#### Scenario: Threshold boundaries are advisory
- **WHEN** an OpenSpec general plan has eight tasks or an OpenSpec documentation-only plan has two tasks
- **THEN** the checklist does not flag it solely for task count, and the drafter's usual six-task target does not become a critic rejection threshold

#### Scenario: Count risk preserves successful drafting
- **WHEN** lint and feasibility pass and the critic's only finding is a task-count risk naming possible merges
- **THEN** the draft retains no contradictions, carries the finding through existing risks with one Critic: prefix, and spends no correction round

#### Scenario: Native counts per story
- **WHEN** a native plan has fifteen tasks across three five-task story phases, eight tasks in one story phase, or nine in one story phase
- **THEN** the first two plans receive no count-only risk and the third warrants a non-blocking risk naming that story phase and possible merges
- **THEN** checked and unchecked native task entries and dotted children count, and the whole-feature total never triggers a count risk


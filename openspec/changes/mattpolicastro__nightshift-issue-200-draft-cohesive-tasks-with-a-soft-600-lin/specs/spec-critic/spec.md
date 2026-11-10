## Purpose

Keep estimated task size advisory through the existing critic and feasibility
outcomes without changing count thresholds, evidence standards or correction gates.

## ADDED Requirements

### Requirement: Advisory estimated size

Both critic prompts SHALL use the soft 600-combined-line per-task guideline and
existing risks channel: estimated size alone SHALL NOT cause rejection,
contradiction, correction or another issue/PR. Both feasibility prompts SHALL
state that estimated lines alone cannot establish impossibility. Independently
evidenced blockers, count thresholds, schemas, gates and budgets SHALL remain
unchanged, without size estimators or finding filters.

#### Scenario: Size risk consumes no correction
- **WHEN** lint and feasibility pass and the critic returns only a risk about estimated task size
- **THEN** the draft retains empty contradictions and the note appears once with the existing Critic: prefix in returned and persisted risks
- **THEN** exactly one drafting write, one feasibility review and one critic review occur, with no correction artifacts or new result fields
- **THEN** review instructions consider cohesion, safe split boundaries and the existing design/plan verification rationale; roughly 600 production/test/documentation lines is neither a target nor an automatic rejection threshold

#### Scenario: Size estimate is not impossibility
- **WHEN** feasibility reviews an otherwise feasible task with a large estimated line count
- **THEN** both formats' prompts explicitly forbid treating the estimate alone as impossibility and direct any non-blocking concern to risks
- **THEN** a scripted risk-only feasibility verdict proceeds to critic with the original risk preserved and no feasibility correction

#### Scenario: Independent unsafe design still blocks
- **WHEN** review has concrete evidence of an unsafe design or impossible task independently of estimated lines
- **THEN** the finding remains a contradiction under the existing criteria and follows existing refusal or bounded correction behavior
- **THEN** soft sizing does not waive frozen paths, trusted verification, protected guarantees, required context or uncertainty-driven split guidance

#### Scenario: Count advice retains existing boundaries
- **WHEN** the size guidance is rendered in either critic prompt
- **THEN** OpenSpec risks remain above eight tasks generally or two documentation tasks, with exactly eight or two respectively below the count finding threshold
- **THEN** native risks remain above eight entries per story phase, ignoring whole-feature and non-story totals; fifteen tasks across three five-task stories still cause no count risk
- **THEN** concrete task merge advice and explanations of necessary boundaries remain, and the six-task recommendation never becomes a rejection threshold
- **THEN** estimated lines or total task count alone never mandate another issue or PR

## MODIFIED Requirements

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
- **THEN** OpenSpec prompt bytes remain unchanged except for the explicitly authorized advisory-size critic guidance and the size-alone-is-not-impossibility feasibility clarification; all eight critic checklist items, count thresholds, question ordering, correction limits and profile/time-limit defaults remain unchanged

## Context

`specloop/propose.py` embeds the OpenSpec `PROMPT` and native `SPECKIT_PROMPT`.
Both currently impose about 300 changed lines at most. OpenSpec recommends six
tasks or fewer per change and one/two tasks for documentation-only work; native
recommendations apply per story and retain one phase per story. The critic's
OpenSpec item 1 reports count risks above eight general tasks or two documentation
tasks. `_SPECKIT_TASK_SIZING` reports above eight entries in a story phase and
ignores whole-feature totals. These thresholds are advisory and remain unchanged.

`propose.draft` already orders lint, feasibility and critic review. Only
contradictions trigger correction; critic risks flow through `Draft.risks` with
`Critic: ` attribution. `feasibility.SCHEMA` already distinguishes contradictions
from risks. No new runtime decision is needed. Native feasibility derives its
ordinary impossibility rules from the shared feasibility prompt.

The evidence supplied with issue #200 covers 98 local runs and 523 initially
planned tasks. Sealed candidates are a different population, including attempts:
84/465, 87/253, 28/71 and 19/34 failed in the at-most-300, 301–600, 601–1,000 and
above-1,000 bands respectively. The 165 landed candidates at 301–600 show that the
old ceiling excludes useful cohesive work. Higher failure rates at larger sizes
support caution. Completed-session timing (4.53-minute median, 14.99-minute p95,
35.85-minute maximum) is observational and excludes incomplete sessions; it
does not establish headroom for every provider or justify increased budgets.

No AGENTS.md was found in the checkout or its ancestors. The current protected
requirements include hard sizing and prompt-byte promises that this change must
explicitly modify; all unrelated requirements remain in force.

## Goals / Non-Goals

Make task boundaries reflect cohesion, verification and risk; retain coupled code
and tests across files; make small native drafts specify only needed behavior and
meaningful failures. Use existing prompts, scenarios and risk routing. Keep the
six-or-fewer recommendations without making six a minimum or quota.

Do not increase execution budgets, introduce automatic line measurement or runtime
splitting, alter parsers or authoring templates, or change any admission, seal,
verification, independent review, evidence or correction gate. Do not turn native
feature totals into risks. Do not require new issues/PRs merely to fit a number.
No new schema, CLI option, manifest setting, telemetry, helper service or agent
adapter is needed. No deployment or live-provider work is an implementation task.

## Decisions

### 1. Change the existing drafting instructions in place

Update both prompt literals in `specloop/propose.py`, including dependent phrases
such as "within that ceiling" so they cannot restore a hard limit implicitly.
The policy is one coherent, independently verifiable concern per single-session
task, with coupled production changes and tests together even across files.
About 600 changed lines counts production, tests and documentation together and
is a soft planning guideline per task, never a target or automatic rejection
threshold. No line-count algorithm or required size field is introduced.

Split at independent behavior, migration/recovery boundaries, distinct verification
strategies or substantial uncertainty, retaining dependency order. Above roughly
600 lines prefer a safe split; when that would break cohesion, record the task
boundary and verification rationale in existing OpenSpec `design.md` or native
`plan.md`. For example, a 450-line production/test change for one behavior can
remain one task; an 850-line cohesive change needs the rationale if kept together;
a 250-line migration with an independent recovery boundary can still need a split.
Neither estimated lines nor total task count alone requires another issue or PR.

Retain OpenSpec six-or-fewer and compact one/two-task documentation guidance, with
as few sections as dependencies permit. Retain the native six-or-fewer per-story
recommendation and one/two documentation tasks per story. Explicitly allow fewer
and never pad to six. Retain the cycle/gate cost explanation, scenario references,
coverage and human-only checks in design/plan. Required native Setup/Foundational
and prioritized story phases, T labels and story tags do not change.

In `SPECKIT_PROMPT`, add concise proportional-detail instructions alongside scaffold
guidance: only stories, requirements and acceptance scenarios needed for observable
behavior and meaningful failures. Do not invent optional subsystems, duplicate
requirements or implementation-level requirements to fill sections. Required
sections remain; no minimum story/scenario count or new template is introduced.
Retain stable AS identities, full task coverage, local-template prerequisites,
revision rules and at most three genuine missing-intent questions. Preserve the
existing question-before-lint path rather than instructing the model to guess intent.

### 2. Clarify review using existing outcomes

In `specloop/critic.py`, add estimated-size guidance to OpenSpec `PROMPT` item 1
and replace `_SPECKIT_TASK_SIZING`'s 300-line ceiling with the same soft policy.
Both rendered prompts must say that size alone never produces contradictions,
rejection or correction. A size concern may use existing `risks`; a coherent
301–600-line task does not automatically need a risk. An above-guideline task
with a cohesion/verification explanation is reviewable without a numeric blocker.
Independent evidence of unsafe design remains eligible for a contradiction.
Keep all eight checklist items and existing count clauses and concrete merge
advice, including advice explaining why a safe merge is unavailable.

In `specloop/feasibility.py`, add only the clarification that estimated line count
alone cannot establish impossibility; it can be a risk. Put it in the shared
ordinary feasibility rules so both formats receive it. Preserve required-context,
constitution, frozen-path, trusted-verifier and protected-spec contradictions.
Do not post-process model text to suppress findings or change `draft`, `check`,
`SCHEMA`, call budgets, result serialization or correction logic.

### 3. Explicitly migrate protected contracts without weakening other assertions

The deltas replace `spec-drafting`'s **Risk based task sizing** and **Compact
documentation plans**, and amend **Native feature drafting** solely to permit the
new guidance instead of its obsolete byte-identical OpenSpec promise. The new
proportional-detail requirement preserves its house rules. In `spec-critic`, add
estimated-size guidance and amend **Native feasibility and critic context** only
to qualify its OpenSpec prompt-byte promise. Count requirements remain intact.
In documentation, modify **Task sizing guidance** and **Native drafting guidance**,
including the latter's obsolete blanket ban on changes to OpenSpec drafting prose.
No requirements are removed and no other capability is changed.

Migrate only affected 300-line/ceiling assertions and their replacement sizing
clauses in `tests/test_specloop_task_sizing.py`,
`tests/test_specloop_critic_task_risk.py` and `tests/test_speckit_drafting.py`,
including matching documentation expectations. Preserve count thresholds, phase
shape, scenario coverage, question handling, gate assertions and existing markers.
The exact feasibility and critic SHA-256 assertions in
`test_original_review_prompts_and_native_checklist_are_preserved` also cover the
newly authorized size-only text: preserve the existing digests by asserting the
new clause occurs exactly once, removing only that exact addition in the test,
and hashing the remaining legacy prompt bytes. This narrow migration keeps every
unaffected byte protected; do not broadly normalize prose or delete hash checks.
Preserve native checklist composition assertions and all build-prompt hashes.

Add the following tests with existing fixtures and scripted agents, alongside
the code they exercise; parameterize the applicable contracts across both formats:

| Test | Offline evidence |
| --- | --- |
| `test_cohesive_task_above_300_lines_is_not_rejected_for_size` | A valid one-concern plan explicitly estimated at 450 combined production/test/doc lines passes real task lint with mocked external OpenSpec validation, then scripted clean reviews; one write, a committed draft and no contradictions or size rejection. Assert the rendered soft-guideline contract too. |
| `test_size_estimate_alone_is_not_a_feasibility_contradiction` | Assert both feasibility prompts exclude size-only impossibility; script a size risk with empty contradictions and verify the draft proceeds to critic with the original feasibility risk retained. Keep existing evidenced-contradiction refusal/correction tests. |
| `test_critic_size_risk_does_not_consume_correction_round` | Script a size risk, including prefixed/unprefixed forms, through the actual proposal gate sequence; assert one write, one feasibility and one critic call, no second artifacts, no contradictions and one `Critic: ` prefix in result and persisted draft JSON. |
| `test_sizing_guidance_preserves_risk_boundaries_and_session_limits` | Assert all four split boundaries, above-guideline rationale, no padding and no numeric issue/PR split requirement in both prompts. Capture calls to retain manifest implementation/idle budgets and the existing 20-minute review budgets; run existing adapter/timeout and gate regressions unchanged for turn/seal/evidence limits. |
| `test_small_native_feature_prompt_requires_proportional_detail` | Render a small-feature prompt using the existing native fixture; assert proportional stories/requirements/scenarios, meaningful failures and all three anti-padding rules alongside required structure, AS identities, coverage and genuine clarification instructions. No model-quality assertion. |

Keep native fifteen-task/three-story coverage, eight/nine per-story boundaries,
checked/dotted-entry physical-line tests, documentation count boundaries and
unsafe-design corrections green. Run targeted tests as each concern lands, then
`uv run pytest` with offline fixtures and mocked providers for final acceptance.
No test invokes a real model or depends on that model obeying a prompt.

### 4. Synchronize only affected operator guidance

Update `docs/specloop/speckit.md` under **What the drafter produces** and **Story
sizing examples**, plus the native/OpenSpec drafting and affected feasibility/
critic passages in `docs/specloop/README.md`. Retain the story example table
([5, 5, 5], [8], [9]), existing proposal stages, four-round default, ordinary native
admission, references and pending #184 checks. Cite tracking issue #200 and #80
without inventing an implementation PR; retain existing #75 overhead attribution.
Audit root `README.md` and `KNOWN-LIMITATIONS.md` and change only passages this
policy makes false; the current line-limit wording is confined to the two guides.
Do not replace unrelated numbers such as admission's 300-second deadline.

Reuse existing documentation assertions and table/link checks. If new passage
selection is necessary, require a unique exact column-zero heading and terminate
at the next physical column-zero heading of the same or higher level or EOF;
headings match `^#{1,6} +\S.*$`, with level given by the leading hash count.
For the existing OpenSpec prose block, require the unique physical line starting
with the literal `**` followed by backticked `propose` and `**`, ending at the next
line matching `^\*\*[^*\r\n]+\*\*` or the heading rule above. Missing or duplicate
starts or ambiguous boundaries fail the
test. Treat fences, HTML and indentation literally, with no semantic exemptions
or Markdown parser. Existing native phase/count parsing stays unchanged.

### 5. Keep implementation compact and rollout observational

Use three tasks in one section: drafting contracts with their tests and spec
delta; review contracts with their tests and spec delta; affected docs with their
spec delta and assertions. Each is planned at about 300 changed lines or less,
using current fixtures and short prompt edits; there is no need to pad to six or
create a new mechanism. These estimates describe this implementation plan, not
an enforcement mechanism for future drafts. Changes to the same test file across
tasks concern separate drafting, review or documentation contracts and land in
dependency order. Issue 2 follows this change to avoid concurrent prompt edits.

## Risks / Trade-offs

Larger tasks have higher observed failure rates and can still exceed unchanged
session limits. Cohesion is not permission to ignore independent recovery paths,
verification strategies or uncertainty. The split guidance and rationale make
review possible without pretending a line estimate proves safety.

Prompt instructions cannot force a provider to produce concise drafts or correctly
classify a size-only concern. Scripted verdicts prove routing and preservation,
not model behavior or causal improvement. Reviewers still need evidence for
blockers, and the harness will not silently discard a model's contradiction.

Proportional detail can be misread as permission to omit important failures;
required structure, meaningful failure cases, stable identities, coverage and
genuine questions remain explicit. No template/parser changes are needed.

## Human checks

- [ ] After merge, inspect actual small native drafts for unnecessary stories,
  duplicated requirements, invented subsystems and implementation detail while
  retaining behavior, meaningful failures and clarification needs;
  `spec-drafting/proportional-native-detail/small-feature-has-only-necessary-detail`.
- [ ] Follow a sample of cohesive 301–600-line tasks through review and compare
  task counts, review cycles, elapsed time and human interventions with the supplied
  baseline. Record observations against #200/#80, without claiming causal proof;
  `specloop-documentation/sizing-evidence-limits/live-comparison-remains-pending`.
- [ ] Confirm sampled Claude sessions still fit their unchanged turn/time limits;
  do not enlarge budgets to make the observation pass;
  `spec-drafting/risk-based-task-sizing/necessary-boundaries-remain`.
- [ ] Review changed guidance for readability and verify live references, keeping
  existing #184 quality/build/enrollment and legacy-resume checks pending until
  actually performed;
  `specloop-documentation/native-drafting-guidance/quality-evidence-remains-human`.

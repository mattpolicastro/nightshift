## Purpose

Explain cohesive task planning and proportional native detail consistently, with
honest limits on offline evidence and unchanged operational budgets.

## ADDED Requirements

### Requirement: Sizing evidence limits

Affected guidance SHALL distinguish offline prompt-contract and scripted gate tests
from actual drafting quality or causal improvement. It SHALL identify the issue
#200/#80 follow-up as pending human observation, preserve existing pending live
checks, and SHALL NOT claim larger execution budgets or proven performance gains.

#### Scenario: Live comparison remains pending
- **WHEN** a reader follows updated sizing and native-detail guidance after offline tests pass
- **THEN** the guidance leaves actual small native draft inspection and sampled cohesive 301–600-line task review pending, comparing task counts, review cycles, elapsed time and human interventions with the baseline
- **THEN** confirming Claude sessions fit unchanged turn/time limits remains a human check, and offline tests are not described as quality or causal proof

## MODIFIED Requirements

### Requirement: Task sizing guidance

The guide's drafting paragraph SHALL describe one coherent, independently verifiable
concern per single-session task, combining same-file work and coupled production
changes with their tests even across files. It SHALL describe about 600 changed
lines including production, tests and documentation as a soft guideline, neither a
target nor a rejection threshold, replacing the former hard ceiling. It SHALL
describe independent behavior, migration/recovery boundaries, distinct verification
strategies and substantial uncertainty as split boundaries; above roughly 600 lines
it SHALL prefer safe splits or a cohesion/boundary/verification rationale in the
existing design/plan. Estimated lines or total task count alone SHALL NOT require
another issue or PR.

OpenSpec documentation-only changes SHALL retain one or two tasks in one section,
and most changes six tasks or fewer with as few sections as dependencies allow.
The guide SHALL permit fewer tasks, forbid padding to six, explain review-cycle
and evidence-gate costs, and preserve existing proposal stages, references and
four-round default. It SHALL describe size concerns as advisory risks rather than
impossibility, size-only contradictions or correction rounds, and SHALL preserve
independently evidenced blockers and unchanged execution budgets and gates.

#### Scenario: Reader sizes a plan
- **WHEN** a reader consults the drafting discussion in docs/specloop/README.md
- **THEN** it explains cohesive cross-file grouping, combined-line soft sizing, meaningful split boundaries, above-guideline rationale, compact documentation plans and six-or-fewer guidance without padding
- **THEN** review-cycle and evidence-gate costs, existing proposal stages, four-round default and references remain intact, and estimated size/count alone never mandates another issue or PR

#### Scenario: Reader distinguishes guidance from limits
- **WHEN** a reader compares drafting, feasibility and critic guidance in the scoped operator documents
- **THEN** the affected passages consistently distinguish advisory size risks from evidenced blockers, preserve count thresholds and describe no correction for size alone
- **THEN** session timeouts, turn limits, seal limits and verification/review/evidence gates are unchanged; root README and KNOWN-LIMITATIONS receive only corrections to passages made false by this change

### Requirement: Native drafting guidance

The engine guide and house rules SHALL explain native draft artifacts,
local templates, stable revisions, scenario/task shape, questions and human
checks. They SHALL state the six-or-fewer per-story recommendation without
padding, the soft roughly 600-combined-line per-task guideline, advisory risk
above eight tasks in a story phase and absence of whole-feature count risk.
They SHALL describe proportional native detail, preserving observable behavior,
meaningful failures, required structure and genuine clarification. Only OpenSpec
drafting prose made false by this sizing migration SHALL change; its unrelated
workflow guidance SHALL remain intact. This replaces the old 300-line ceiling
and blanket OpenSpec-prose preservation promise.

#### Scenario: Author finds the native workflow
- **WHEN** an author reads the native drafting guide and house rules
- **THEN** the guide explains templates, ASn labels, prioritized story phases, story tags, backticked citations, plan human checks, up to three genuine questions and revision in the existing undelivered feature directory
- **THEN** it shows explicit propose and normal admission without a feasibility bypass, states the per-story sizing rule and links the supported native daemon front door without adding internal base-pin options to explicit CLI examples
- **THEN** the new drafting paragraph identifies `specs/` and `openspec/` as reserved specification roots excluded from shared planned-file extraction for every format, with or without a root, and explains the deliberate OpenSpec change: `specs/helper.py` is no longer a planned edit while `src/helper.py` remains eligible

#### Scenario: Quality evidence remains human
- **WHEN** documentation describes verification of native drafting
- **THEN** it leaves a real feature-sized scratch-repository draft with real profiles and spec-PR inspection pending until performed, recording rounds, questions and critic findings on tracking issue 184
- **THEN** offline prompt/routing tests are not described as proving draft quality or completed live checks

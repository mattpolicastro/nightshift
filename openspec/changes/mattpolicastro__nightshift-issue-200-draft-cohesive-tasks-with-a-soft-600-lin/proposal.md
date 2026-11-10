## Why

The mandatory 300-changed-line task ceiling encourages splitting coupled code and
tests, adding implement/verify/review cycles and section evidence gates. An enrolled
repository's lead independently identified that ceiling as the leading pain point.
Native scaffolds also invite unnecessary detail when a small feature has little
observable behavior to specify.

Issue #200, part of #80, reports 98 local runs from 2026-10-20 through 2026-11-09
with 523 initially planned tasks. Among sealed candidates, failures were 84/465
at at most 300 changed lines, 87/253 at 301–600, 28/71 at 601–1,000, and 19/34
above 1,000; 165 candidates in the 301–600 band landed. Completed implementation
sessions had a 4.53-minute median, 14.99-minute p95 and 35.85-minute maximum.
These observations support a cautious planning relaxation, not larger execution
budgets or a claim that larger tasks improve outcomes.

## What Changes

- Update existing OpenSpec and native drafting prompts to prioritize one coherent,
  independently verifiable concern, keeping coupled production work and tests
  together across files. About 600 changed lines, including tests and docs, becomes
  a soft per-task planning guideline, neither a target nor a rejection threshold.
- Prefer safe splits above roughly 600 lines and at independent behavior,
  migration/recovery boundaries, distinct verification strategies or substantial
  uncertainty. If a split would break cohesion, explain the boundary and
  verification rationale in the existing design or plan. Estimated lines or total
  task count alone never require another issue or PR.
- Retain six-or-fewer recommendations, explicitly permit fewer tasks and forbid
  padding to six. Retain compact documentation plans, native per-story sizing,
  required story phases, stable scenario identities and full coverage.
- Make native specification detail proportional to observable behavior and meaningful
  failure cases; preserve required structure and genuine clarification handling
  without inventing optional subsystems, duplicated requirements or implementation
  requirements merely to fill template sections.
- Clarify the existing critic risk channel and feasibility instructions: estimated
  size alone establishes neither a contradiction nor impossibility and consumes no
  correction round. Preserve count thresholds, concrete merge advice and blockers
  supported by independent evidence.
- Record protected specification migrations in this change's deltas for the
  existing harness Finish/archive flow. Implementation updates sizing assertions
  and affected operator guidance, with offline prompt-contract and scripted gate
  regression coverage.

Implementation scope is limited to `specloop/propose.py`, `specloop/critic.py`,
the size/impossibility clarification in `specloop/feasibility.py`,
`tests/test_specloop_task_sizing.py`, `tests/test_specloop_critic_task_risk.py`,
`tests/test_speckit_drafting.py`, `tests/test_specloop_propose.py`,
`docs/specloop/speckit.md`, and any passage of `README.md`, `KNOWN-LIMITATIONS.md`
or `docs/specloop/README.md` made false by this change. This draft writes only
this change directory. The three capabilities below are represented by deltas
here; implementing agents do not edit the frozen `openspec/**` tree. The existing
harness Finish/archive flow later incorporates those approved deltas into the
three named consolidated specifications under its normal completion gates.

Out of scope: size estimators, telemetry, configuration or enforcement machinery;
agent adapters, session timeouts, turn limits, runtime task splitting, seal limits,
verification/review/evidence gates, native parser grammar, templates, constitution,
daemon scheduling and attention UI. No other `openspec/specs/**` capability changes.

## Capabilities

- **spec-drafting** (modified): replace the hard sizing contract, qualify the old
  byte-identical OpenSpec prompt promise only for this policy migration, and add
  proportional native detail guidance.
- **spec-critic** (modified): add advisory estimated-size review, preserve existing
  count guidance and gate behavior, and qualify prompt-byte preservation only for
  the size guidance and feasibility clarification.
- **specloop-documentation** (modified): synchronize sizing and native authoring
  guidance and distinguish offline evidence from pending live quality observations.

Issue 2 shares `specloop/propose.py` and documentation and should follow this
change. There is no direct overlap with #117's named attention files.

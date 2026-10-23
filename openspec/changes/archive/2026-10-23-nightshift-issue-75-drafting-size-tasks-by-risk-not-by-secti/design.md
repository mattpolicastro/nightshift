## Context

`specloop.propose.PROMPT` currently requires one concern and about 300 changed
lines per task, dependency ordering, scenario references and tests with their
code. It does not explain the cost of splitting work. `specloop.critic.PROMPT`
has eight checklist items, but the canonical Independent checklist review
requirement still lists five. Item 1 distinguishes demonstrated over-engineering
from preference; item 7 requires evidence of duplicate or dropped external
writes. The existing `critic.check` / `propose.draft` path already transports
risks with a `Critic: ` prefix without spending a correction round.

The #67 documentation baseline is present in the archive and canonical specs.
No repository `AGENTS.md` was found. Existing prompt and guide tests constrain
wording and checklist headings; preserve their assertions and add guidance
without replacing protected passages.

## Goals / Non-Goals

Make the default task plan economical while preserving meaningful risk,
dependency and review boundaries. Keep count findings advisory and scale
durability advice to actual operational consequences. Document the guidance
and test the prompt contracts and existing risk routing offline.

Do not change feasibility, in-flight discovery, the task ceiling, gate execution,
critic schema, correction budget, daemon behavior or GitHub write handling.
Do not build a task classifier, count parser, journal, benchmark system or new
review mechanism. Six tasks is a drafting default, not a rejection threshold.

## Decisions

1. **Change prompt text at its existing interfaces.** Add rules under the
   `tasks.md` instructions in `specloop/propose.py: PROMPT`: combine tasks
   touching the same file for one concern; docs-only means the implementation
   changes documentation without changing executable behavior and should use
   one or two tasks in one section; most changes need six tasks or fewer and
   as few sections as their dependencies allow. Keep the existing single-session,
   roughly 300-line limit, coverage references, tests-with-code and human-check
   instructions. Distinct concerns or work exceeding the ceiling still split.
   Explicitly explain that every task is a review cycle and every section is
   an evidence gate. The measured minutes motivate the policy, not an SLA.

2. **Count advice belongs in critic item 1.** Extend only that item in
   `specloop/critic.py: PROMPT`, retaining its existing mismatch/evidence rule.
   Count task entries across the whole plan, not headings or scenarios. More
   than eight tasks means nine or more; docs-only more than two means three or
   more. Exactly eight general tasks and exactly two docs tasks do not trigger
   the count finding. A risk names concrete task labels that could merge and
   their shared concern; where legitimate boundaries prevent merging, explain
   that rather than inventing a merge. Count alone never enters contradictions,
   demands correction or rejects the proposal. An independently evidenced
   unsafe design can still be a contradiction. Keep the existing schema and
   risk delivery; do not add a harness rule that rewrites agent verdicts.

3. **Qualify critic item 7 by blast radius.** Preserve its external-write,
   lost-response and retry-path checks. For an operator-attended local command,
   safe re-runs that re-check actual current state satisfy the item; lack of a
   durable journal, transaction record or interrupted-run reconciliation is
   not itself a gap. Such machinery is over-engineering in that case. Being
   invoked from a CLI does not make unattended execution or a GitHub write
   low risk: comments, labels, pushes and PR edits retain the existing strict
   duplicate/drop scrutiny, as do unattended daemon operations. State checks
   must make a re-run safe; operator attendance alone is not sufficient.

4. **Keep documentation changes local.** Add concise sizing guidance to the
   `**propose**` drafting discussion (the existing bold heading contains the
   backticked command) in `docs/specloop/README.md`, retaining its workflow,
   four-round default and references. Include the review-cycle/evidence-gate
   rationale and cite issue #75 without inventing a merged PR number. Leave
   the existing eight-item critic list and all other guide sections intact.
   Extend `specloop-documentation` with an additive sizing requirement.
   Modify Documentation integrity to apply its docs-only edit restriction to
   documentation-only changes, while requiring explicit capability deltas for
   changes also touching code/tests. This removes the accidental global ban
   inherited from #67, preserving its other guarantees and scenario names.
   Update Independent checklist review to enumerate all eight existing checks;
   retain both existing scenarios and all other critic requirements unchanged.

5. **Use new, offline tests.** Put drafter and guide coverage in
   `tests/test_specloop_task_sizing.py`, including
   `test_drafter_rules_size_tasks_by_risk`. Check the formatted prompt's
   complete sizing advice and retained task constraints, plus the guide's
   corresponding paragraph. Put critic coverage in
   `tests/test_specloop_critic_task_risk.py`, including
   `test_critic_checklist_flags_excess_task_count`,
   `test_task_count_finding_is_a_risk_not_a_contradiction`, and
   `test_critic_idempotency_item_scales_to_blast_radius`.
   Inspect item-scoped, whitespace-normalized prompt clauses, not disconnected
   keyword presence. Check strict thresholds, named merge advice, advisory
   classification, safe local re-runs, all three excessive remedies and both
   high-blast-radius exceptions. Exercise `propose.draft` with a temporary
   repository and fake agents returning a count-only risk: assert no
   contradictions, one drafting call, no correction and the once-prefixed
   risk in the returned/persisted draft. An unrelated concrete blocker must
   retain the existing correction behavior, covered by the existing suite.
   Use scenario markers for the new scenarios; existing scenario tests cover
   retained contracts. Do not change existing test files or their assertions.
   Implementation acceptance is `uv run pytest` green, including all four named
   tests. No live model, GitHub or host application is needed for these tests.

6. **Constrain unavoidable concurrent edits.** #76 also touches
   `specloop/critic.py` and `docs/specloop/README.md`; these two overlaps are
   necessary to implement the requested behavior. Apply narrow additions to
   items 1 and 7 and the drafting paragraph, preserving any landed #76 edits.
   Do not edit `KNOWN-LIMITATIONS.md`, `README.md`, `docs/arming.md`,
   `docs/landing.md`, `nightshift/arm.py`, `nightshift/cli.py`,
   `nightshift/land.py`, `pyproject.toml`, `specloop/cli.py`,
   `tests/test_arm.py` or `tests/test_land.py`. No map/parser or other
   production-file changes are needed. Spec deltas live in this change until
   the normal archive process applies them to canonical requirements.

## Risks / Trade-offs

Prompt tests prove which instructions the agent receives, and fake-agent tests
prove risk transport; neither proves that a live model always follows them.
Counts can expose excessive fragmentation without proving which tasks should
merge. Broad tasks can also increase failed reviews, which is why risk,
dependencies and the 300-line ceiling remain controlling constraints.

The necessary #76 overlap can still conflict at merge. Preserve both changes'
intended guidance without broad reformatting. Existing assertions, especially
exact prompt phrases and guide claims, remain protected.

Human checklist for the resulting PR and follow-up:

- Review the guide for readability and accurate issue/PR references, including
  the existing live-reference/ledger check and Matt's single readability pass;
  do not describe unlanded behavior as already shipped.
- Inspect representative generated plans for useful grouping and critic advice,
  including a local deploy re-run and a GitHub-writing or unattended operation.
- Over the next ten completed changes, compare tasks per change, build hours
  and review-failure rate in the timing report against the 2026-10-23 baseline.
  Use the same report definitions and denominators for both cohorts, record
  the ten change IDs and documentation-only mix, and note queueing differences
  and missing observations. The supplied baseline establishes nineteen changes
  and median five tasks, not build-hour or review-failure values: obtain those
  from that dated report rather than inventing them. Report whether time and
  task counts fell without a higher review-failure rate; this is observational
  evidence, not a CI guarantee or an implementing-agent task. No timing-report
  code changes or live measurement are required to land the guidance.

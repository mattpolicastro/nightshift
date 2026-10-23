## Why

Task and section boundaries have material cost: each task pays a full
implement, verify and review cycle (about eight minutes plus slot queueing),
and each section pays an evidence gate (about fourteen minutes). The
2026-10-23 sample of nineteen changes had a median of five tasks, but issues
#46, #8 and #6 had fifteen, fourteen and twelve respectively. The docs-only
#67 had nine tasks in seven sections. Checklist-driven durability advice
also grew spec PR #82 to sixteen tasks of journaling for a local deploy command.

This change depends on #67; its archived change and documentation requirements
are present in this checkout. Size work by risk and dependencies so unnecessary
boundaries and durability machinery do not dominate useful implementation.

## What Changes

- Extend the `tasks.md` guidance in `specloop/propose.py` to combine same-file,
  same-concern work, use one or two tasks in one section for docs-only changes,
  and normally use six tasks or fewer in as few sections as dependencies allow.
  Explain the cost of each task and section.
- Extend checklist item 1 in `specloop/critic.py`: more than eight tasks, or
  more than two for a docs-only plan, warrants a non-blocking risk identifying
  tasks that could merge. Task count alone is never a contradiction.
- Extend checklist item 7 to accept safe re-runs that re-check real state for
  operator-attended local commands. Durable journals, transaction records and
  interrupted-run reconciliation are excessive for that case. Retain strict
  scrutiny of unattended daemon operations and GitHub writes.
- Update the guide's drafting paragraph and add focused tests in new files,
  preserving all existing assertions. Record the ten-change timing comparison
  in the design's human checklist.

Out of scope: feasibility rules, the in-flight map, the 300-line task ceiling,
new admission/lint gates, result schemas, task-count enforcement code, runtime
retry machinery, timing-report implementation, and unrelated operator docs.
Necessary overlap with #76 is limited to the critic's two checklist items and
the guide's drafting paragraph; all other listed in-flight files stay untouched.

## Capabilities

### New Capabilities

- `spec-drafting`: prompt guidance that sizes tasks and sections by risk and
  dependencies while retaining existing task contracts.

### Modified Capabilities

- `spec-critic`: additive task-count and blast-radius guidance; update the
  existing independent-review requirement from five to all eight shipped
  checklist items without changing its read contract or failure behavior.
- `specloop-documentation`: add task-sizing guidance. Modify Documentation
  integrity because its inherited blanket docs-only scope would otherwise
  prohibit this explicitly scoped prompt-and-test change; retain documentation
  scope, assertion, reference and human-review protections.

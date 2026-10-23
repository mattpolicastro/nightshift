"""Prompts and output schemas. Provider-neutral: no tool names, no CLI flags.

The review rubric is deliberately narrow, so a FAIL must point at something
concrete: an open-ended rubric lets each review find "the next missing
assertion", which is how one task can become eight runs.
"""

IMPLEMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["status", "summary", "subtasks"],
    "properties": {
        "status": {"type": "string", "enum": ["ready", "blocked", "too_big"]},
        "summary": {"type": "string"},
        "subtasks": {"type": "array", "items": {"type": "string"}},
    },
}

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "prior", "findings", "needs_human"],
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "prior": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["finding", "resolved"],
            "properties": {"finding": {"type": "string"}, "resolved": {"type": "boolean"}}}},
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["blocking", "basis", "text"],
            "properties": {"blocking": {"type": "boolean"}, "basis": {"type": "string"}, "text": {"type": "string"}}}},
        "needs_human": {"type": "array", "items": {"type": "string"}},
    },
}

IMPLEMENT = """\
# Task {label}: {text}

You are implementing ONE task of an approved OpenSpec change, in this
repository (your working directory). A harness — not you — commits your work,
verifies it with its own trusted commands, and has it reviewed independently.

Read before editing:
- {ctx}/task.md — this task, the scenario IDs it covers and their full text
- {ctx}/proposal.md, {ctx}/design.md, {ctx}/specs/ — the approved change
- AGENTS.md, and the existing code and tests
{feedback_line}
Rules:
- Implement this task completely, and only this task. Other tasks are run separately.
- For every scenario ID the task names, add or extend a test that exercises it and
  contains the scenario ID string verbatim, so coverage can be traced to it.
- You may build and test in `build/` (gitignored). The harness's checks are:
{recipe}
  Your own results are not acceptance evidence; the harness reruns its checks.
  Report them in `summary`, never in a repository file: a task saying "record
  the result" means `summary`, not a doc or log in the change.
  If a check fails only because your sandbox forbids something (writing outside the
  workspace, listing processes, the network), say so in `summary` and still report
  "ready": the harness runs its checks in its own environment. That is not "blocked".
- Change only: {allowed}
  Never change: {frozen}
  (tasks.md included — the harness ticks tasks). Other changes are reverted.
- Do not commit, push, create branches or worktrees, use the network, or read credentials.
- Never weaken, delete or skip an existing test to make something pass. If an existing
  test is wrong, change it only if this task requires it, and explain why in `summary`.
  An existing test is one that predates this change; tests added by earlier tasks of
  this same change may be updated when this task's behaviour requires it.
- Test documentation by structure, never by wording: links resolve, examples parse or
  run, tables and commands match the code. Never assert that a document contains
  particular phrases; later changes edit prose freely.

Finish with the structured result:
- "ready" — the task is implemented and you believe each named scenario holds;
- "too_big" — with 2 to 6 `subtasks`, each a complete task sentence naming its scenario
  IDs in backticks, if this cannot be done well in one session;
- "blocked" — the spec is contradictory or a prerequisite is missing; explain in `summary`.
  This pauses the run for a human, so use it only when no reasonable reading works.
"""

REVIEW = """\
# Review of task {label}: {text}

You are the independent reviewer of one sealed candidate commit. Your working
directory is a clean checkout of it. You did not write it. Do not modify files.

Read:
- {ctx}/task.md — the task and the full text of its scenarios
- {ctx}/proposal.md, {ctx}/design.md, {ctx}/specs/
- {ctx}/diff.patch — the candidate against the task's starting point
- {ctx}/receipts.md — the harness's own verification, which passed
- {ctx}/test-edits.md — every existing test line the candidate changed or removed
{prior_line}
Judge only this task. A finding may be blocking ONLY if it does one of these,
recorded in `basis`:
- "scenario:<id>" — a scenario this task names is not satisfied; say how
- "task" — a requirement stated in the task text is not met
- "weakened-test" — an existing test was weakened, deleted or made vacuous without
  the task requiring it (check every line in test-edits.md). An existing test is one
  that predates this change; tests an earlier task of this change added may evolve
  with it when this task's behaviour requires it.
- "scope" — a change that does not belong to this task
- "input:<input>" — a concrete input or sequence that produces a wrong result
  in a real deployment. Code that exists only on this change's branch, whether
  landed by an earlier task or left by a previous attempt, has never run
  anywhere else. Data only that code could have written (old receipt or state
  formats) is not such an input, unless the spec asks for compatibility.
If the candidate changes a mechanism that several code paths share (a helper, a
marker or key format, a lock, a retry or timeout policy), check every caller,
not only the one the task names: an unconverted caller is a "task" or "input:"
finding.
Everything else — style, naming, refactors, ideas, future work — is non-blocking.
Non-blocking findings are kept for the pull request, so they are still worth writing.
{prior_rule}
`needs_human`: only genuine contradictions or gaps in the spec itself that stop a
correct implementation, or a problem with the review itself (a file above that
you cannot read). They pause the run for a human. Never report a problem with
the review as a finding against the candidate.

verdict: "PASS" when there is no blocking finding, otherwise "FAIL".
"""

PRIOR_RULE = """
This is attempt {attempt}. First, rule on each prior blocking finding in
`prior` (resolved true/false). A NEW blocking finding on this attempt must use
basis "scenario:<id>", "weakened-test" or "input:<input>".
"""

GATE_REVIEW = """\
# Review of section {n}: {title}

You are the independent reviewer of a finished section of an approved OpenSpec
change. Every task in it has already passed its own review. Your job is what
those reviews could not see: the section as a whole. Your working directory is
a clean checkout of the branch head. Do not modify files.

Read:
- {ctx}/section.md — the section's tasks, all landed, and the scenarios they cite
- {ctx}/proposal.md, {ctx}/design.md, {ctx}/specs/
- {ctx}/diff.patch — everything the section changed
- {ctx}/evidence.md — the harness's coverage and mutation evidence for the section
- {ctx}/followups.md — non-blocking notes the per-task reviewers left
{prior_line}
Most of your time should go to tracing code. Work through every item below,
and report every blocking finding you find, not only the first:
1. Wiring. For each requirement the section's tasks cite, start at the real
   entry point (the service's main loop, the CLI command) and follow it to the
   behaviour. Is it actually reached, in the right order and under the right
   guards, or only exercised by tests that call it directly with fakes?
2. Hand-offs. Does each stage read the state the previous stage writes, in the
   same format? Is there a value one task writes that another never reads, two
   code paths for one behaviour, or an error one layer raises that the next
   swallows or caches?
3. Lifecycle. Restart in the middle of an operation. An outage that then
   recovers: is the failure retried, or remembered forever? A human undoing or
   redoing a step: closing something, removing and re-adding a label,
   approving again. A second round of the same flow: does it still notify, or
   silently overwrite the first?
4. Shared process. Does anything the section added block or starve other work
   in the same process, such as a long call inside a poll loop?
5. Per-task notes in followups.md that matter once taken together.

A finding may be blocking ONLY with one of these bases, recorded in `basis`:
- "scenario:<id>" — a scenario the section cites does not hold on the branch head; say how
- "requirement:<capability>/<requirement>" — a requirement the section was meant to deliver is not met
- "integration:<how>" — tasks that conflict or do not connect, with the concrete path through the code
- "input:<input>" — a concrete input or sequence that produces a wrong result in a
  real deployment. Code that exists only on this change's branch has never run
  anywhere else; data only that code could have written is not such an input.
Each blocking finding becomes one fix task, so write it as one session's work.
Everything else is non-blocking and kept for the pull request.
{prior_rule}
`needs_human`: only genuine contradictions or gaps in the spec itself, or a
problem with the review itself (a file above that you cannot read). They pause
the run for a human.

verdict: "PASS" when there is no blocking finding, otherwise "FAIL".
"""

GATE_PRIOR_RULE = """
This is the section's second review, after one round of fix tasks. First, rule
on each finding in {ctx}/prior-review.md in `prior` (resolved true/false).
"""

ARBITER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["decision", "reason", "note"],
    "properties": {
        "decision": {"type": "string", "enum": ["accept", "retry", "human"]},
        "reason": {"type": "string"},
        "note": {"type": "string"},
    },
}

ARBITER = """\
# Arbitration for task {label}: {text}

This task has failed {attempts} attempts, and you decide what happens next. You
are neither its implementer nor its reviewer. Your working directory is a
checkout of the last candidate. Do not modify files.

Read:
- {ctx}/task.md — the task and the full text of its scenarios
- {ctx}/proposal.md, {ctx}/design.md, {ctx}/specs/
- {ctx}/diff.patch — the last candidate against the task's starting point
- {ctx}/history.md — every attempt's blocking findings, oldest first, then why the last one failed

Decide:
- "accept" — {accept_rule}
- "retry" — the remaining findings are real defects within this task, or the
  attempts are going in circles for want of direction. Write `note` for the
  implementer: how to settle it, concretely: what to do, and what not to
  bother with. One more attempt follows; if it fails, a human decides.
- "human" — the spec itself is contradictory or silent on what the findings
  turn on, or you cannot tell.

`reason`: one or two sentences a human can check. `note`: empty unless "retry".
"""

ARBITER_ACCEPT = """\
the last candidate passed the harness's verification, and every remaining
  blocking finding is outside this task and its scenarios, asks for something the
  spec does not, or needs an input no real deployment can produce (for example,
  data that only code on this unmerged branch could have written). Accepting lands
  the candidate, and the findings go into the pull request for the human who
  merges. Never accept over a real defect within the task."""

ARBITER_NO_ACCEPT = "not available: the last candidate failed the harness's own verification."

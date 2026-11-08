"""One read-only review of the whole change before admission.

Its single job is to find tasks that are impossible as written given what the
manifest freezes and verifies. On 2026-10-20 a task required package tags that
a frozen checker rejected; it cost a blocked run and a human fix, and was
visible from the start.
Risks are reported; only contradictions refuse admission.
"""

import json
import shlex
import shutil
import subprocess
from pathlib import Path

from specloop import agents, verify
from specloop.manifest import Manifest

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["contradictions", "risks"],
    "properties": {
        "contradictions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["task", "problem", "evidence"],
            "properties": {"task": {"type": "string"}, "problem": {"type": "string"}, "evidence": {"type": "string"}}}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
}

PROMPT = """\
# Feasibility check of an OpenSpec change before it is admitted

Your working directory is a checkout of the branch the change will be built
on. Do not modify anything. The change is in openspec/changes/{change}/
(proposal.md, design.md, specs/, tasks.md).

Implementers may change only: {allowed}
They may never change: {frozen}
Every candidate must pass these commands, exactly as written:
{steps}

For each task in tasks.md, decide whether it can be completed as written under
those rules. Report a contradiction ONLY when a task is impossible as written,
for example:
- what the task must produce is rejected by a frozen file or a verify command
  (read the frozen files and the scripts the commands run);
- the task must change a file outside the allowed paths, or a frozen one;
- the task contradicts the design or another task;
- the change removes, hides or breaks behaviour that a requirement in
  openspec/specs/ guarantees, without a MODIFIED or REMOVED delta for that
  requirement (cite the requirement);
- a person, not an implementing agent working in this repository, would have
  to do the task (a manual, visual, hardware or live-service check). Such a
  check belongs in design.md, not tasks.md.
Cite the file and line that proves it in `evidence`. Put anything that is
merely risky, vague or likely to need a retry in `risks`.
"""


# Shared agent reading guidance, not a harness parser or history resolver.
SPECKIT_CONTEXT = """\
Your working directory is a fresh extraction of the exact candidate commit.
Do not modify anything. Read specs/{change}/spec.md, specs/{change}/plan.md,
specs/{change}/tasks.md, .specify/memory/constitution.md, relevant repository
code and tests, and repository instructions (including AGENTS.md when present).

Read named feature context using these bounded physical-line rules:
- In plan.md, sections start at exact column-zero `## Depends on` or
  `## Assumptions` and end at the next column-zero level-one/two heading.
  Also accept feature names on an ASCII-trimmed `Depends on:` or
  `**Depends on**:` field line.
- Read spec.md's `## Assumptions` too, with the same exact column-zero section
  start and next column-zero level-one/two heading boundary.
- References are explicit canonical `NNN-slug` names or `specs/NNN-slug/`
  paths, optionally ending in `spec.md`. Read specs/<named-feature>/spec.md
  for every feature named by these sections or fields.
- Check each named spec's physical `**Status**: Complete` before treating it
  as a delivered guarantee. Do not treat Draft features as Complete guarantees.
  Do not infer identities from titles, recurse through named features'
  references, or scan unrelated historical features.
- Literal matching physical lines inside examples receive no semantic Markdown
  exemption (including fences, HTML or indentation). Missing or ambiguous
  referenced identity, unreadable required context, or conflicting Status lines
  must produce a concrete inability-to-complete finding in contradictions,
  naming the affected task and file/line evidence (or the required missing path).
  Never silently omit required context and return a clean review.

Breaking a constitution principle or changing behavior specified by a named
Complete feature without explicitly identifying and justifying that change in
`## Assumptions` in the selected feature's spec.md or plan.md is a contradiction.
Name the task and cite concrete file/line evidence for the principle or guarantee
and the changed behavior. A generic dependency mention is not acknowledgement.
An explicit, justified Assumptions statement removes only that missing-disclosure
basis; it does not waive allowed/frozen paths, trusted verify commands, or other
impossibilities. Human-only checks belong in plan.md, not tasks.md.
"""

SPECKIT_PROMPT = (
    "# Feasibility check of a spec-kit feature before it is admitted\n\n"
    + SPECKIT_CONTEXT + "\n"
    + PROMPT[PROMPT.index("Implementers may change only:"):].replace(
        "- the change removes, hides or breaks behaviour that a requirement in\n"
        "  openspec/specs/ guarantees, without a MODIFIED or REMOVED delta for that\n"
        "  requirement (cite the requirement);\n", ""
    ).replace("check belongs in design.md", "check belongs in plan.md").replace(
        "Report a contradiction ONLY when a task is impossible as written,",
        "Besides the required-context and missing-disclosure findings above,\n"
        "report a contradiction ONLY when a task is impossible as written,"
    )
)


def check(repo: Path, rev: str, m: Manifest, out: Path, agent=None) -> dict:
    agent = agent or agents.run
    checkout = out / "checkout"
    verify.extract(repo, rev, checkout)
    subprocess.run(["chmod", "-R", "u+w", str(checkout)], check=False)
    values = {**m.vars, "src": ".", "build": "<build dir>", "sha": rev}
    steps = (
        "Trusted preparation runs under the harness with network access before offline verification.\n"
        "Steps run from the build directory with read-only extracted source.\n"
        "Source, build, and revision values below illustrate the recipe, not actual sandbox paths.\n"
    )
    steps += "\n".join(
        f"{label}:\n" + ("\n".join(
            f"- {s.id}: {shlex.join(verify.expand(a, values) for a in s.run)} (must exit {s.expect})"
            for s in group) or "(none)")
        for label, group in (("Prepare", m.prepare), ("Verify", m.verify))
    )
    template = SPECKIT_PROMPT if m.format == "speckit" else PROMPT
    prompt = template.format(change=m.change, allowed=", ".join(m.allowed), frozen=", ".join(m.frozen), steps=steps)
    profile = (m.chains.get("feasibility") or m.chains.get("review") or (m.roles["review"],))[0]
    call = agents.Call(profile, "read", prompt, checkout, SCHEMA, out / "feasibility.jsonl", 20 * 60,
                       m.idle_minutes * 60, agents.homes_dir() / profile.name)
    res = agent(call)
    shutil.rmtree(checkout, ignore_errors=True)
    result = res.output if res.disposition == "ok" else {"error": f"{res.disposition}: {res.detail[:300]}"}
    (out / "feasibility.json").write_text(json.dumps(result, indent=2))
    return result

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


def check(repo: Path, rev: str, m: Manifest, out: Path, agent=None) -> dict:
    agent = agent or agents.run
    checkout = out / "checkout"
    verify.extract(repo, rev, checkout)
    subprocess.run(["chmod", "-R", "u+w", str(checkout)], check=False)
    values = {**m.vars, "src": ".", "build": "<build dir>", "sha": rev}
    steps = "\n".join(f"- {s.id}: {shlex.join(verify.expand(a, values) for a in s.run)} (must exit {s.expect})"
                      for s in m.verify)
    prompt = PROMPT.format(change=m.change, allowed=", ".join(m.allowed), frozen=", ".join(m.frozen), steps=steps)
    profile = (m.chains.get("feasibility") or m.chains.get("review") or (m.roles["review"],))[0]
    call = agents.Call(profile, "read", prompt, checkout, SCHEMA, out / "feasibility.jsonl", 20 * 60,
                       m.idle_minutes * 60, agents.homes_dir() / profile.name)
    res = agent(call)
    shutil.rmtree(checkout, ignore_errors=True)
    result = res.output if res.disposition == "ok" else {"error": f"{res.disposition}: {res.detail[:300]}"}
    (out / "feasibility.json").write_text(json.dumps(result, indent=2))
    return result

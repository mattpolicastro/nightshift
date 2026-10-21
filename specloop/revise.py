"""Queue review feedback as harness-authored tasks on an admitted branch."""

import json
import re

from specloop import seal, tasks
from specloop.manifest import Manifest
from specloop.state import Busy, Run

# Human stops (stuck, blocked, secret, a phase gate, abort) are resolved with
# resume or accept, never cleared by new review feedback.
REVISABLE_PAUSES = ("ready_to_publish", "human_checks")


def revise(run: Run, m: Manifest, feedback: str, *, feedback_format: str = "text") -> str:
    if feedback_format == "json":
        blocks = json.loads(feedback)
        if not isinstance(blocks, list) or not all(isinstance(block, str) for block in blocks):
            raise ValueError("JSON feedback must be an array of strings")
        blocks = [block for block in blocks if block.strip()]
    elif feedback_format == "text":
        blocks = [block.strip() for block in re.split(r"(?m)^---\r?$", feedback) if block.strip()]
    else:
        raise ValueError(f"unknown feedback format: {feedback_format}")
    if not blocks:
        raise ValueError("feedback is empty; no revision tasks to append")
    with run.lock() as mine:
        if not mine:
            raise Busy(f"{m.change} is already being driven; retry when it is idle")
        run.reload()
        st = run.state
        reason = st.pause.reason if st.pause else ""
        if not (st.status == "running" or (st.status == "paused" and reason in REVISABLE_PAUSES)):
            raise ValueError(f"{m.change} is {st.status}" + (f" ({reason})" if reason else "")
                             + "; a review round is only added to a running run or one waiting to publish "
                               "or for its human checks")
        repo, ref = run.dir / "repo.git", f"refs/heads/{m.branch}"
        head = seal.git(repo, "rev-parse", ref).decode().strip()
        path = f"openspec/changes/{m.change}/tasks.md"
        text = seal.git(repo, "show", f"{head}:{path}").decode()
        rounds = [int(r.group(1)) for line in text.splitlines()
                  if (section := tasks.SECTION.match(line))
                  and (r := re.fullmatch(r"Review round (\d+)", section.group(2)))]
        number = max(rounds, default=0) + 1
        # Tasks are parsed one line at a time. JSON quoting preserves the whole
        # comment, including code/checklists, without creating extra tasks.
        # The ordinal also gives identical comments distinct task identities.
        items = [f"Address review feedback {i}: {json.dumps(block)}"
                 for i, block in enumerate(blocks, 1)]
        text = tasks.append_section(text, f"Review round {number}", items)
        commit = seal.commit_files(repo, head, {path: text}, f"specloop: append review round {number}")
        seal.git(repo, "update-ref", ref, commit, head)
        run.state.status, run.state.pause = "running", None
        run.save()
        run.event("revised", commit=commit, round=number, tasks=len(blocks))
        return commit

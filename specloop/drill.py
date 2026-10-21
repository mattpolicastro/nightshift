"""`specloop drill`: is the review gate still catching what it should?

Guardrails are claims about error rates, so they get measured like one. Each
drill declared in the manifest plants a one-line defect into a landed task's
candidate. A drill counts only if the planted candidate still passes the
harness's verification, because otherwise the tests caught it and the reviewer
was never tested. It is then reviewed through the loop's own review path, and
is caught only on a FAIL with a valid blocking finding.

Hand-run first on 2026-10-20: two integration defects unit tests cannot see (a
sample-rate change ignored behind a plausible comment, and `output[index ^ 1]`)
were both rejected, and a clean control passed.
"""

import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from specloop import loop, prompts, review, tasks, verify
from specloop.seal import IDENTITY, git
from specloop.state import Run, TaskState


@dataclass
class Drill:
    id: str
    task: str  # task label, e.g. "2.2"
    path: str
    old: str
    new: str


@dataclass
class Outcome:
    id: str
    result: str  # caught | missed | verified-out | invalid
    detail: str = ""


def landed_candidate(run: Run, ctx: loop.Ctx, label: str) -> tuple[str, str, tasks.Task] | None:
    """(base, candidate, task) for the task that landed under this label."""
    events = [json.loads(line) for line in (run.dir / "events.jsonl").read_text().splitlines()]
    tick = next((e["sha"] for e in reversed(events) if e["event"] == "landed" and e.get("label") == label), "")
    if not tick:
        return None
    candidate = git(ctx.repo, "rev-parse", f"{tick}^").decode().strip()
    base = git(ctx.repo, "rev-parse", f"{candidate}^").decode().strip()
    text = git(ctx.repo, "show", f"{base}:{ctx.tasks_path}").decode()
    task = next((t for t in tasks.parse(text) if t.label == label), None)
    return (base, candidate, task) if task else None


def run_drill(ctx: loop.Ctx, d: Drill, out: Path) -> Outcome:
    found = landed_candidate(ctx.run, ctx, d.task)
    if not found:
        return Outcome(d.id, "invalid", f"no landed task {d.task}")
    base, candidate, task = found
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp) / "w"
        subprocess.run(["git", "clone", "-q", str(ctx.repo), str(w)], check=True)
        git(w, "checkout", "-q", candidate)
        f = w / d.path
        text = f.read_text() if f.exists() else ""
        if text.count(d.old) != 1:
            return Outcome(d.id, "invalid", f"`{d.old[:60]}` occurs {text.count(d.old)} times in {d.path}")
        f.write_text(text.replace(d.old, d.new))
        git(w, "reset", "-q", "--soft", base)
        git(w, "commit", "-qam", f"specloop: task {task.title()}"[:200], env=IDENTITY)
        planted = git(w, "rev-parse", "HEAD").decode().strip()
        git(w, "push", "-q", str(ctx.repo), f"{planted}:refs/specloop/drills/{d.id}")
    receipts = verify.run_steps(ctx.repo, planted, ctx.m.verify, ctx.m, out / "verify")
    verify.cleanup(out / "verify")
    if not verify.ok(receipts, ctx.m.prepare + ctx.m.verify):
        return Outcome(d.id, "verified-out", "the harness's verification caught it before review")
    checkout, context = out / "review", out / "context"
    verify.extract(ctx.repo, planted, checkout)
    subprocess.run(["chmod", "-R", "u+w", str(checkout)], check=False)
    (out / task.key / "a1" / "verify").mkdir(parents=True, exist_ok=True)
    (out / task.key / "a1" / "verify" / "receipts.json").write_text(json.dumps([asdict(r) for r in receipts]))
    drill_ctx = loop.Ctx(Run(out), ctx.m, ctx.secrets, lambda t: None, ctx.agent)
    drill_ctx.repo = ctx.repo
    loop._review_context(drill_ctx, task, TaskState(), base, planted, checkout, context)
    prompt = prompts.REVIEW.format(label=task.label, text=task.text, prior_line="", prior_rule="", ctx=str(context))
    call = loop._call(drill_ctx, "review", "read", prompt, checkout, prompts.REVIEW_SCHEMA, out / "review.jsonl",
                      ctx.m.review_minutes)
    res = ctx.agent(call) if call else None
    shutil.rmtree(checkout, ignore_errors=True)
    if not res or res.disposition != "ok":
        return Outcome(d.id, "invalid", f"review did not complete: {res.disposition if res else 'no profile'}")
    decision = review.decide(res.output, 1, [])
    if decision.verdict in ("FAIL", "NEEDS_HUMAN"):
        return Outcome(d.id, "caught", decision.blocking[0]["text"][:300] if decision.blocking else "")
    return Outcome(d.id, "missed", json.dumps(res.output)[:300])


def run_all(ctx: loop.Ctx, drills: list[Drill], out: Path) -> list[Outcome]:
    results = []
    for d in drills:
        o = run_drill(ctx, d, out / d.id)
        results.append(o)
        ctx.run.event("drill", id=d.id, result=o.result, detail=o.detail[:300])
    (out / "drills.json").write_text(json.dumps([asdict(o) for o in results], indent=2))
    return results

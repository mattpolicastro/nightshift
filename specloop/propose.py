"""`specloop propose`: draft an OpenSpec change from an idea, for a human to approve.

Writing specific specs was the bottleneck the worker engine's September review
identified. A drafting session writes openspec/changes/<name>/ in a clone of the
target repository; the harness validates it (OpenSpec strict validation, the
scenario lint, every task's scenario IDs) and feeds problems back, then runs the
feasibility review. A clean draft is committed to a local branch for approval.

Each rule in `lint` started as a spec PR a human sent back: a check only a
person could make written as a task (it stalls the build), and a test file
another change was editing at the same time (it conflicts on merge). The
drafter is told which files unfinished runs on the same repository plan to
change; a task that edits one of those test files gets one round of feedback.

The drafter cannot run the OpenSpec CLI itself (its sandbox has no network,
and npx needs it), which is why validation is the harness's job. The manifest,
with its trusted verify commands, is never drafted here.
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from specloop import agents, feasibility, manifest, openspec, state, tasks
from specloop.manifest import Manifest
from specloop.seal import IDENTITY, git

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["status", "summary", "questions"],
    "properties": {
        "status": {"type": "string", "enum": ["drafted", "needs_input"]},
        "summary": {"type": "string"},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
}

PROMPT = """\
# Draft an OpenSpec change: `{change}`

Your working directory is a clone of the repository. Write the change, and only
the change, under openspec/changes/{change}/. Do not edit any other file.

## The idea

{idea}

## Read first

{sources}
Also read AGENTS.md, README.md, the code the change will touch, and the
requirements in openspec/specs/ for each capability it touches. Keep every one
of those requirements unless the change says why it must change, with a
MODIFIED or REMOVED delta.
{in_flight}
## What to write

- `proposal.md` with `## Why`, `## What Changes` (including what is explicitly
  out of scope) and `## Capabilities`.
- `design.md` with `## Context`, `## Goals / Non-Goals`, `## Decisions`
  (concrete, testable defaults; name files and interfaces), and
  `## Risks / Trade-offs`.
- `specs/<capability>/spec.md` for each capability: `## Purpose`, then
  `## ADDED Requirements` with `### Requirement: <short name>` (use SHALL), each
  with one or more `#### Scenario: <short name>` blocks written as `- **WHEN** ...`
  / `- **THEN** ...` bullets. Four `#` for scenarios, exactly.
- `tasks.md` with `## N. Title` sections and `- [ ] N.M ...` tasks.
  - Each task is a single session's work: about 300 changed lines at most, one
    concern.
  - Each task names the scenario IDs it covers, in backticks, as
    `capability/requirement-slug/scenario-slug`. A slug is the name lowercased,
    with every run of other characters replaced by `-`.
  - Every scenario must be covered by at least one task.
  - Order tasks so each builds on landed work. Put tests with the code they test.
  - Every task goes to an implementing agent working in this repository. A check
    only a person can make (real hardware, live services, how something looks)
    is never a task: describe it in the design, where it becomes the pull
    request's human checklist.
- Prefer behaviour that can be tested without the host application. Say in the
  design where that is not possible, so a human checks it instead.
{feedback}
Finish with "drafted", or with "needs_input" plus the questions that only a
human can answer (missing intent, not missing detail you can reasonably decide).
"""


# A task a person performs: "The operator verifies ...", "Manually ...". Named
# people ("Matt checks ...") are left to the feasibility review, which reads the
# task as language: a capitalised word and a verb-s also match "Append tests".
HUMAN_TASK = re.compile(
    r"^(?:(?:[Aa] (?:human|person)|[Tt]he (?:human|user|operator|maintainer|reviewer|owner)|[Ss]omeone)\s"
    r"|(?:Manually|By hand|Visually)\b)")
BACKTICKED = re.compile(r"`([^`\s]+)`")


def paths(text: str) -> set[str]:
    """Repository file paths named in backticks, such as `tests/test_x.py` or `src/a.py::f`."""
    found = set()
    for token in BACKTICKED.findall(text):
        path = token.split("::")[0].rstrip(":,.")
        if ("/" in path and not path.startswith(("openspec/", "/", "~", "http"))
                and re.search(r"\.\w+$|\*$", path)):
            found.add(path)
    return found


# "Do not edit `tests/x.py`": overlap feedback itself makes drafters write this.
# mutation-equivalent: {0,60} -> {1,60} preserves matches: word boundaries between the negation and edit/chang/touch/modif require at least one separator.
NEGATED = re.compile(r"\b(?:do not|don't|never|without)\b(?:[^.;]|\.(?=\S)){0,60}?\b(?:edit|chang|touch|modif)"
                     r"|\bleave\b(?:[^.;]|\.(?=\S)){0,60}?\b(?:unchanged|untouched|alone|as is)\b", re.I)


def edited_paths(text: str) -> set[str]:
    """Paths a task names, minus those in a clause telling the implementer to leave them alone."""
    return set().union(*(paths(c) for c in re.split(r"(?<=[.;])\s+", text) if not NEGATED.search(c)))


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or "/tests/" in path or name.startswith("test_") or ".test." in name


def in_flight(source: Path, change: str) -> dict[str, list[str]]:
    """Files the tasks of other unfinished runs on this repository name: path -> changes."""
    planned: dict[str, list[str]] = {}
    root = Path(source).expanduser().resolve()
    for d in sorted(state.runs_dir().glob("*")):
        if d.name == change or not (d / "state.json").exists():
            continue
        try:
            if state.Run(d).state.status not in ("running", "paused"):
                continue
            m = manifest.load(d / "manifest.toml")
            if Path(m.repo).expanduser().resolve() != root:
                continue
            text = git(d / "repo.git", "show", f"{m.branch}:openspec/changes/{m.change}/tasks.md").decode()
        except Exception:
            continue  # an unreadable run must not stop drafting
        for path in paths(text):
            planned.setdefault(path, []).append(d.name)
    return planned


def _in_flight_section(planned: dict[str, list[str]]) -> str:
    if not planned:
        return ""
    lines = "\n".join(f"- `{p}` ({', '.join(c)})" for p, c in sorted(planned.items()))
    return ("\n## Work in flight\n\nOther changes to this repository are being built now. Their tasks change "
            "these files:\n" + lines + "\n\nPlan no edits to them unless this change cannot be done otherwise. "
            "Put new tests in new test files rather than in any test file listed here. "
            "The implementer never sees this list: if the change must avoid a file, name it.\n")


def overlaps(work: Path, change: str, planned: dict[str, list[str]]) -> list[str]:
    """Tasks that edit a test file an unfinished run also plans to change."""
    task_file = openspec.change_dir(work, change) / "tasks.md"
    found = []
    for t in tasks.parse(task_file.read_text()) if task_file.exists() else []:
        for path in sorted(p for p in edited_paths(t.text) if is_test(p) and p in planned):
            found.append(f"task {t.label}: `{path}` is also being changed by {', '.join(planned[path])}; "
                         "put these tests in a new file")
    return found


@dataclass
class Draft:
    change: str
    work: Path
    problems: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    commit: str = ""
    branch: str = ""


def lint(work: Path, change: str) -> list[str]:
    problems = openspec.validate(work, change)
    try:
        known = openspec.scenarios(work, change)
    except openspec.SpecError as e:
        return problems + [str(e)]
    task_file = openspec.change_dir(work, change) / "tasks.md"
    parsed = tasks.parse(task_file.read_text()) if task_file.exists() else []
    if not parsed:
        problems.append("tasks.md has no tasks under '## N. Title' sections")
    cited = {s for t in parsed for s in t.scenarios}
    problems += [f"task {t.label}: unknown scenario `{s}`" for t in parsed for s in t.scenarios if s not in known]
    problems += [f"scenario `{s}` is not covered by any task" for s in sorted(set(known) - cited)]
    problems += [f"task {t.label}: a person does this, but only an implementing agent works through "
                 "tasks.md; describe the check in design.md instead" for t in parsed if HUMAN_TASK.match(t.text)]
    stray = [p for p in _changed(work) if not p.startswith(f"openspec/changes/{change}/")]
    problems += [f"{p}: only files under openspec/changes/{change}/ may change" for p in stray]
    return problems


def _changed(work: Path) -> list[str]:
    out = git(work, "status", "--porcelain", "--untracked-files=all").decode()
    return [line[3:].strip() for line in out.splitlines() if line.strip()]


def draft(source: Path, base: str, change: str, idea: str, sources: list[str], m: Manifest, out: Path,
          rounds: int = 4, agent=None, *, branch: str | None = None,
          from_branch: str | None = None) -> Draft:
    agent = agent or agents.run
    # Resolve in the source: a clone renames local branches to origin/* and
    # does not copy private refs used by a front end to fetch a reviewed tip.
    start = (git(source, "rev-parse", "--verify", f"{from_branch}^{{commit}}").decode().strip()
             if from_branch else base)
    shutil.rmtree(out, ignore_errors=True)
    work = out / "work"
    subprocess.run(["git", "clone", "-q", str(source), str(work)], check=True)
    branch = branch if branch is not None else f"spec/{change}"
    if from_branch:
        git(work, "fetch", "-q", str(source), start)
    git(work, "checkout", "-q", "-B" if from_branch else "-b", branch, start)
    d = Draft(change, work, branch=branch)
    profile = (m.chains.get("implement") or (m.roles["implement"],))[0]
    planned = in_flight(source, change)
    feedback = ""
    for n in range(1, rounds + 1):
        prompt = PROMPT.format(change=change, idea=idea, feedback=feedback, in_flight=_in_flight_section(planned),
                               sources="\n".join(f"- {s}" for s in sources) or "- (no extra sources named)")
        call = agents.Call(profile, "write", prompt, work, SCHEMA, out / f"draft-{n}.jsonl", m.implement_minutes * 60,
                           m.idle_minutes * 60, agents.homes_dir() / profile.name)
        res = agent(call)
        if res.disposition != "ok":
            d.problems = [f"drafting session failed: {res.disposition} {res.detail[:300]}"]
            return d
        if res.output["status"] == "needs_input":
            d.questions = res.output["questions"]
            return d
        # Overlap gets one round of feedback: a change may have to edit a shared test file.
        d.problems = lint(work, change) + (overlaps(work, change, planned) if n == 1 else [])
        if d.problems:
            feedback = ("\n## Your previous draft was rejected by the harness\n\nFix every problem:\n"
                        + "\n".join(f"- {p}" for p in d.problems) + "\n")
            continue
        git(work, "add", "-A", f"openspec/changes/{change}")
        unchanged = subprocess.run(["git", "-C", str(work), "diff", "--cached", "--quiet"]).returncode == 0
        if unchanged and d.commit:
            break  # the drafter made no change in answer to the review: the contradictions stand
        if unchanged:
            d.problems = ["The drafting session made no changes."]
            return d
        git(work, "commit", "-qm", f"Draft OpenSpec change {change} (specloop propose, round {n})", env=IDENTITY)
        head = git(work, "rev-parse", "HEAD").decode().strip()
        review = feasibility.check(work, head, m, out / f"feasibility-{n}", agent=agent)
        if review.get("error"):
            d.problems = [f"feasibility review failed: {review['error']}"]
            return d
        d.contradictions, d.risks, d.commit = review.get("contradictions", []), review.get("risks", []), head
        if not d.contradictions:
            break
        # A contradiction in a draft is a spec bug the drafter can fix.
        feedback = ("\n## A feasibility review found tasks impossible as written\n\nRevise the change so each is "
                    "achievable under the stated rules:\n" + "\n".join(
                        f"- {c['task']}: {c['problem']} (evidence: {c['evidence']})" for c in d.contradictions) + "\n")
    if d.problems:
        return d
    d.risks += [f"{o}, so the two may conflict on merge" for o in overlaps(work, change, planned)]
    (out / "draft.json").write_text(json.dumps({k: str(v) if isinstance(v, Path) else v
                                                for k, v in d.__dict__.items()}, indent=2))
    return d


def publish_branch(d: Draft, source: Path) -> str:
    """Retain the draft locally without changing a checkout; return its ref."""
    branch = d.branch or f"spec/{d.change}"
    target = f"refs/heads/{branch}"
    # A reviewer may have this branch checked out, including in a linked
    # worktree. Preserve their HEAD, index and files; the front end pushes the
    # returned commit, so a private ref is enough to retain it for delivery.
    worktrees = git(source, "worktree", "list", "--porcelain").decode()
    if f"branch {target}" in worktrees.splitlines():
        target = f"refs/specloop/proposals/{d.change}"
    git(source, "fetch", "-q", str(d.work), f"+refs/heads/{branch}:{target}")
    return target.removeprefix("refs/heads/")

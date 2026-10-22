"""The driver: one task at a time until a pause, or nothing is left.

Per task: fresh clone -> implement -> seal -> verify -> review -> land. A FAIL
feeds the next attempt; mechanical violations are reverted and reported, not
fatal. The run only stops at a defined pause, and every pause says why.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import time

from specloop import agents, evidence, messages, openspec, prompts, quota, review, seal, tasks, verify
from specloop.manifest import Manifest
from specloop.seal import git
from specloop.state import Run, TaskState


class Ctx:
    def __init__(self, run: Run, m: Manifest, secrets: dict, notify=lambda text: None, agent=agents.run):
        self.run, self.m, self.secrets, self.notify, self.agent = run, m, secrets, notify, agent
        self.repo = run.dir / "repo.git"
        self.ref = f"refs/heads/{m.branch}"
        self.tasks_path = f"openspec/changes/{m.change}/tasks.md"
        self.credentials = seal.credential_values(secrets)

    def head(self) -> str:
        return git(self.repo, "rev-parse", self.ref).decode().strip()

    def tasks_text(self, rev: str) -> str:
        return git(self.repo, "show", f"{rev}:{self.tasks_path}").decode()

    def pause(self, reason: str, task: tasks.Task | None = None, detail: str = "", key: str = "",
              resume_at: float = 0) -> None:
        self.run.pause(reason, task.key if task else key, detail, resume_at)
        ts = self.run.state.tasks.get(task.key) if task else None
        done = 0
        if reason == "ready_to_publish":
            done = sum(t.done for t in tasks.parse(self.tasks_text(self.head())))
        self.notify(messages.for_pause(self.m.change, reason, label=task.label if task else "", key=key,
                                       detail=detail, attempts=ts.attempts if ts else 0, done=done,
                                       pr=self.run.state.pr, resume_at=resume_at))


def _fetch_publish_base(ctx: Ctx, remote: str | None = None, *, timeout: float) -> str:
    """Fetch the source origin's base, never falling back to a previous fetch.

    Publish can supply its already validated origin URL; driver preparation
    resolves it here without requiring a GitHub target. Callers own pause/retry
    policy. Only the run repository is written, and transport keeps the user's
    credential-helper configuration (unlike seal.git).
    """
    requested = f"refs/heads/{ctx.m.pr_base}"
    destination = "refs/specloop/publish-base"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        if remote is None:
            remote = subprocess.run(
                ["git", "-C", str(ctx.m.repo), "remote", "get-url", "origin"],
                capture_output=True, text=True, check=True, timeout=timeout, env=env,
            ).stdout.strip()
        if not remote:
            raise RuntimeError("source clone has no origin URL")
        # Git's URL schemes and scp-style host:path syntax are not local paths.
        # Relative filesystem origins are relative to the source, not repo.git.
        if not re.match(r"^[^/]+:", remote):
            remote = str((ctx.m.repo / remote).resolve())
        subprocess.run(
            ["git", *seal.SAFE_FLAGS, "-C", str(ctx.repo), "fetch", "--no-tags",
             "--no-recurse-submodules", "--", remote, f"+{requested}:{destination}"],
            capture_output=True, text=True, check=True, timeout=timeout, env=env,
        )
        return git(ctx.repo, "rev-parse", "--verify", f"{destination}^{{commit}}").decode().strip()
    except subprocess.TimeoutExpired as exc:
        diagnostic = exc.stderr or exc.stdout or ""
        if isinstance(diagnostic, bytes):
            diagnostic = diagnostic.decode(errors="replace")
        raise RuntimeError(f"fetch {requested} from source origin timed out after {timeout:g}s: "
                           f"{diagnostic.strip()[:400]}") from exc
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        diagnostic = (exc.stderr or exc.stdout or f"git exited {exc.returncode}") \
            if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        if isinstance(diagnostic, bytes):
            diagnostic = diagnostic.decode(errors="replace")
        raise RuntimeError(f"fetch {requested} from source origin failed: {diagnostic.strip()[:400]}") from exc


class _MergeConflict(RuntimeError):
    def __init__(self, paths: list[str], diagnostic: str):
        super().__init__(diagnostic)
        self.paths = paths


def _is_ancestor(repo: Path, base: str, head: str) -> bool:
    # merge-base's exit 1 means non-ancestry, not an operational failure.
    result = subprocess.run(
        ["git", *seal.SAFE_FLAGS, "-C", str(repo), "merge-base", "--is-ancestor", base, head],
        capture_output=True, timeout=60, env={**os.environ, **seal.SAFE_GIT},
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result.returncode == 0


def _merge_commits(repo: Path, head: str, base: str) -> str:
    """Import a two-parent merge object, without changing any repository ref.

    The caller owns ancestry checks, intent persistence and CAS installation.
    Also usable for a synthetic evidence baseline. All indexes and merge state
    live in the disposable checkout, including when Git fails or conflicts.
    """
    with tempfile.TemporaryDirectory(prefix="base-merge-", dir=repo.parent) as tmp:
        work = Path(tmp)
        git(work, "init", "-q")
        git(work, "fetch", "--no-tags", "--no-recurse-submodules", "--", str(repo), head, base)
        git(work, "checkout", "-q", "--detach", head)
        try:
            git(work, "merge", "--no-ff", "--no-commit", base, env=seal.IDENTITY)
        except RuntimeError as exc:
            paths = sorted({os.fsdecode(p) for p in
                            git(work, "diff", "--name-only", "--diff-filter=U", "-z").split(b"\0") if p})
            if paths:
                raise _MergeConflict(paths, str(exc)) from exc
            raise
        tree = git(work, "write-tree").decode().strip()
        merged = git(work, "commit-tree", tree, "-p", head, "-p", base,
                     "-m", f"Merge current PR base {base}", env=seal.IDENTITY).decode().strip()
        git(repo, "fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules",
            "--", str(work), merged)
        return merged


def _merge_publish_base(ctx: Ctx, base: str, head: str) -> str | None:
    """Construct integration for the driver; None means a durable pause.

    A successful result still needs an intent saved before CAS advancement and
    trusted verification. An incorporated base leaves that obligation untouched.
    """
    try:
        if _is_ancestor(ctx.repo, base, head):
            return head
        return _merge_commits(ctx.repo, head, base)
    except _MergeConflict as exc:
        # Do not replace the last integrated base with one that conflicted.
        ctx.run.state.base_sync["retry_verify"] = True
        ctx.pause("base_conflict", detail=f"base {base} conflicts with run head {head}:\n"
                  + "\n".join(exc.paths) + f"\n{exc}\nRepair, then `specloop resume {ctx.m.change}`.")
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        ctx.pause("infra", detail=f"could not merge base {base} into run head {head}: {exc}\n"
                  f"Repair, then `specloop resume {ctx.m.change}`.")
    return None


def run(ctx: Ctx) -> str:
    """Advance until a pause or completion. Returns the final run status."""
    st = ctx.run.state
    while st.status == "running":
        if ctx.run.pause_requested():
            ctx.run.clear_pause_request()
            ctx.pause("requested", detail="stopped at a task boundary, as asked; `specloop resume` continues "
                                          "and takes fast-forward spec edits from the source branch")
            break
        head = ctx.head()
        all_tasks = tasks.parse(ctx.tasks_text(head))
        if ctx.m.evidence and (n := _evidence_due(all_tasks, st)) is not None:
            if not section_evidence(ctx, n, head, all_tasks):
                break
            continue
        if gate := _gate_due(all_tasks, ctx):
            if ctx.m.gate_review == "agent":
                if not gate_review(ctx, gate, head, all_tasks):
                    break
                continue
            summary = st.evidence_summary.get(str(gate), "")
            ctx.pause("phase_gate", detail=f"section {gate} is complete; review the branch, then `specloop resume`"
                      + (f"\n\nEvidence:\n{summary}" if summary else ""), key=f"section:{gate}")
            break
        pending = [t for t in all_tasks if not t.done]
        if pending:
            step(ctx, pending[0], head)
        elif not finals(ctx, head):
            break
    return st.status


def finals(ctx: Ctx, head: str) -> bool:
    """Coverage, then acceptance, once every task has landed.

    Returns True when it appended tasks (the loop continues), False when the
    run paused or finished. Each stage gets one round of appended tasks; a
    second failure pauses rather than churning.
    """
    st, m = ctx.run.state, ctx.m
    if m.coverage_step:
        ok, seen, receipts = _seen_scenarios(ctx, head, ctx.run.dir / "final" / f"coverage-{st.coverage_rounds}")
        if not ok:
            return _final_fix(ctx, head, "acceptance", "Make the harness's verification pass on the branch head:\n"
                              + verify.summary(receipts))
        missing = sorted(set(_scenarios_at(ctx, head)) - seen - set(m.coverage_exempt))
        ctx.run.event("coverage", seen=len(seen), missing=missing)
        if missing:
            if st.coverage_rounds >= 1:
                ctx.pause("coverage", detail="no executed test is tagged with:\n" + "\n".join(missing))
                return False
            st.coverage_rounds += 1
            return _append(ctx, head, "Coverage", _coverage_tasks(missing))
    if m.acceptance:
        out = ctx.run.dir / "final" / f"acceptance-{st.acceptance_rounds}"
        receipts = verify.run_steps(ctx.repo, head, m.acceptance, m, out)
        verify.cleanup(out)
        ctx.run.event("acceptance", passed=verify.ok(receipts, m.prepare + m.acceptance))
        if not verify.ok(receipts, m.prepare + m.acceptance):
            return _final_fix(ctx, head, "acceptance", "Make the acceptance checks pass:\n" + verify.summary(receipts))
    checks = "\n".join(f"- [ ] `{sid}`: {why}" for sid, why in sorted(m.coverage_exempt.items()))
    ctx.pause("ready_to_publish", detail=f"all tasks landed at {head[:12]}; human checks after publishing:\n{checks}\n"
              f"Next: `specloop publish {m.change}`")
    return False


def _final_fix(ctx: Ctx, head: str, stage: str, why: str) -> bool:
    st = ctx.run.state
    if st.acceptance_rounds >= 1:
        ctx.pause(stage, detail=why[:1500])
        return False
    st.acceptance_rounds += 1
    first = why.splitlines()[0]
    return _append(ctx, head, "Acceptance fixes", [f"{first} (details: {_first_failure(why)})"])


def _first_failure(why: str) -> str:
    bad = [l for l in why.splitlines() if "FAIL" in l]
    return bad[0].strip("- ") if bad else why.splitlines()[-1][:200]


def _coverage_tasks(missing: list[str]) -> list[str]:
    by_req: dict[str, list[str]] = {}
    for sid in missing:
        by_req.setdefault(sid.rsplit("/", 1)[0], []).append(sid)
    return [f"Add tagged tests covering {', '.join(f'`{s}`' for s in ids)}, fixing the code where a scenario does not hold"
            for ids in by_req.values()]


def _append(ctx: Ctx, head: str, title: str, items: list[str]) -> bool:
    text = tasks.append_section(ctx.tasks_text(head), title, items)
    _rewrite_tasks(ctx, head, text, f"specloop: append {title.lower()} tasks", None)
    ctx.notify(messages.fyi(ctx.m.change, f"added {len(items)} {title.lower()} task(s) and carried on."))
    return True


def _scenarios_at(ctx: Ctx, rev: str) -> dict[str, str]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        verify.extract(ctx.repo, rev, Path(tmp) / "src")
        try:
            return openspec.scenarios(Path(tmp) / "src", ctx.m.change)
        finally:
            subprocess.run(["chmod", "-R", "u+w", tmp], check=False)


def _evidence_due(all_tasks: list[tasks.Task], st) -> int | None:
    for n in sorted({t.section for t in all_tasks}):
        section = [t for t in all_tasks if t.section == n]
        if all(t.done for t in section) and n not in st.evidenced:
            return n
    return None


def _seen_scenarios(ctx: Ctx, head: str, out: Path) -> tuple[bool, set[str], list]:
    receipts = verify.run_steps(ctx.repo, head, ctx.m.verify, ctx.m, out)
    log = out / f"{ctx.m.coverage_step}.log"
    seen = set(tasks.SCENARIO_ID.findall(log.read_text(errors="replace"))) if log.exists() else set()
    verify.cleanup(out)
    return verify.ok(receipts, ctx.m.prepare + ctx.m.verify), seen, receipts


def section_evidence(ctx: Ctx, n: int, head: str, all_tasks: list[tasks.Task]) -> bool:
    """Coverage and mutation evidence for a finished section.

    Pass: record it, tell the human, carry on (a human gate still pauses, with
    the evidence attached). Fail: one round of strengthening tasks added to the
    same section, so they run next; a second failure pauses.
    """
    st, m = ctx.run.state, ctx.m
    known = _scenarios_at(ctx, head)
    out = ctx.run.dir / "gates" / f"section-{n}-{st.strengthen_rounds.get(str(n), 0)}"
    ok, seen, receipts = _seen_scenarios(ctx, head, out) if m.coverage_step else (True, set(), [])
    cited = sorted({s for t in all_tasks if t.section == n for s in t.known_scenarios(known)})
    if not m.coverage_step:
        seen = set(cited)
    ev = evidence.gather(ctx.repo, st.section_base.get(str(n), ""), head, cited, seen, m, out, n)
    summary = ev.summary()
    ctx.run.event("evidence", section=n, passed=ev.passed(m.mutation_min), score=ev.score,
                  timeouts=sum(x.result == "timeout" for x in ev.mutants),
                  uncovered=ev.uncovered, survived=[f"{x.path}:{x.line}" for x in ev.mutants if x.result == "survived"])
    if ok and ev.passed(m.mutation_min):
        st.evidenced.append(n)
        st.evidence_summary[str(n)] = summary
        ctx.run.save()
        ctx.notify(messages.fyi(m.change, messages.evidence_line(n, summary)))
        return True
    if ev.unmeasured:
        # A harness or manifest problem: strengthening tests cannot fix it.
        ctx.pause("gate_evidence", detail=summary, key=f"section:{n}")
        return False
    if st.strengthen_rounds.get(str(n), 0) >= 1 or not ok:
        why = summary if ok else "the harness's verification fails on the branch head:\n" + verify.summary(receipts)
        before = st.evidence_scores.get(str(n))
        if ok and ev.score is not None and before is not None and ev.score <= before:
            # Seen on a real run, 2026-10-21: mutation_steps ran only the
            # trusted checker, so the agent's strengthened tests could not
            # kill anything and the score could not move.
            why += ("\n\nThe mutation score did not improve after a round of strengthening tasks. If "
                    "mutation_steps runs only trusted checks, the candidate's own tests cannot kill a mutant: "
                    "add a verify step that runs them to mutation_steps.")
        ctx.pause("gate_evidence", detail=why, key=f"section:{n}")
        return False
    if ev.score is not None:
        st.evidence_scores[str(n)] = ev.score
    st.strengthen_rounds[str(n)] = st.strengthen_rounds.get(str(n), 0) + 1
    items = [f"Add tagged tests covering {', '.join(f'`{s}`' for s in ids)}, fixing the code where a scenario does not hold"
             for ids in _group(ev.uncovered)]
    survived = [x for x in ev.mutants if x.result == "survived"]
    if survived:
        listed = "; ".join(f"{x.path}:{x.line} `{x.before.strip()[:60]}` -> `{x.after.strip()[:60]}`" for x in survived)
        items.append(f"Strengthen the tests so each of these single-line mutations of code added in section {n} "
                     f"makes a test fail; where a mutation genuinely cannot change behaviour, say why in a comment on the "
                     f"line above it starting `mutation-equivalent:` (the reviewer judges it): {listed}")
    text = tasks.extend_section(ctx.tasks_text(head), n, items)
    _rewrite_tasks(ctx, head, text, f"specloop: strengthen section {n} after its evidence check", None)
    gap = (f"{len(ev.uncovered)} scenario(s) untested" if ev.uncovered else "") + \
          (", " if ev.uncovered and survived else "") + (f"{len(survived)} mutant(s) survived" if survived else "")
    ctx.notify(messages.fyi(m.change, f"section {n} fell short ({gap}); added {len(items)} strengthening "
                                      f"task(s) and carried on."))
    return True


def _group(ids: list[str]) -> list[list[str]]:
    by_req: dict[str, list[str]] = {}
    for sid in ids:
        by_req.setdefault(sid.rsplit("/", 1)[0], []).append(sid)
    return list(by_req.values())


def _gate_due(all_tasks: list[tasks.Task], ctx: Ctx) -> int | None:
    for n in ctx.m.phase_gates:
        section = [t for t in all_tasks if t.section == n]
        if section and all(t.done for t in section) and n not in ctx.run.state.gates_passed:
            later = [t for t in all_tasks if t.section > n and not t.done]
            if later:
                return n
    return None


def gate_review(ctx: Ctx, n: int, head: str, all_tasks: list[tasks.Task]) -> bool:
    """A fresh reviewer judges a finished section as a whole, in place of a human.

    PASS: the gate is passed and the run carries on. FAIL: one round of fix
    tasks is added to the section, which then gets a second review; a second
    FAIL (or anything the reviewer says needs a human) pauses at the gate as a
    human gate would. Returns True when the loop should continue.
    """
    st, key = ctx.run.state, f"section:{n}"
    ts = st.task(key)
    ts.sessions += 1
    ctx.run.save()
    gdir = ctx.run.dir / "gates" / f"review-{n}-{ts.sessions}"
    checkout, context = gdir / "review", gdir / "context"
    verify.extract(ctx.repo, head, checkout)
    subprocess.run(["chmod", "-R", "u+w", str(checkout)], check=False)
    section = [t for t in all_tasks if t.section == n]
    _gate_context(ctx, n, head, section, ts, checkout, context)
    prompt = prompts.GATE_REVIEW.format(
        n=n, title=section[0].section_title, ctx=str(context),
        prior_line=f"- {context}/prior-review.md — the first review's blocking findings\n" if ts.feedback else "",
        prior_rule=prompts.GATE_PRIOR_RULE.format(ctx=str(context)) if ts.feedback else "")
    ctx.run.event("gate_review", section=n, head=head, session=ts.sessions)
    call = _call(ctx, "gate", "read", prompt, checkout, prompts.REVIEW_SCHEMA, gdir / "review.jsonl",
                 ctx.m.review_minutes)
    if call is None:
        _all_exhausted(ctx, None, "gate", key)
        return False
    res = ctx.agent(call)
    ctx.run.event("gate_reviewed", section=n, disposition=res.disposition, session_id=res.session,
                  profile=call.profile.name, output=res.output, usage=res.usage)
    if _infra(ctx, None, ts, res, call, "gate", key):
        return st.status == "running"
    if res.disposition == "invalid_output":
        ts.review_invalid += 1
        if ts.review_invalid > ctx.m.review_invalid_retries:
            ctx.pause("review_invalid", detail=res.detail, key=key)
            return False
        ctx.run.save()
        return True
    d = review.decide(res.output, ts.attempts + 1, ts.waived, review.GATE, review.GATE)
    (gdir / "decision.json").write_text(json.dumps(d.__dict__, indent=2))
    _followups(ctx, f"section {n}", d.followups)
    if d.verdict == "PASS":
        st.gates_passed.append(n)
        ts.status, ts.feedback = "passed", ""
        ctx.run.save()
        ctx.run.event("gate_passed", section=n, by="agent", notes=len(d.followups))
        ctx.notify(messages.fyi(ctx.m.change, f"section {n} passed its gate review"
                                + (f" ({len(d.followups)} note(s) kept for the PR)" if d.followups else "")
                                + "; carrying on."))
        return True
    if d.verdict == "NEEDS_HUMAN":
        ctx.pause("phase_gate", detail=f"section {n}'s gate reviewer needs a human:\n" + "\n".join(d.needs_human),
                  key=key)
        return False
    if ts.attempts >= 1:
        ctx.pause("phase_gate", detail=f"section {n} failed its gate review again after a round of fixes.\n"
                  + review.feedback(d), key=key)
        return False
    ts.attempts += 1
    ts.feedback = review.feedback(d)
    items = [f"Fix this finding from section {n}'s review: {f['text']} ({f['basis']})" for f in d.blocking]
    text = tasks.extend_section(ctx.tasks_text(head), n, items)
    _rewrite_tasks(ctx, head, text, f"specloop: add fixes from section {n}'s gate review", None)
    ctx.notify(messages.fyi(ctx.m.change, f"section {n}'s gate review found {len(items)} problem(s); "
                                          f"added fix task(s) and carried on."))
    return True


def _gate_context(ctx: Ctx, n: int, head: str, section: list[tasks.Task], ts: TaskState, checkout: Path,
                  context: Path) -> None:
    context.mkdir(parents=True, exist_ok=True)
    _copy_change(checkout, ctx, context)
    try:
        known = openspec.scenarios(checkout, ctx.m.change)
    except openspec.SpecError as e:
        known = {}
        ctx.run.event("gate_scenarios_failed", section=n, error=str(e)[:300])
    cited = sorted({sid for t in section for sid in t.known_scenarios(known)})
    lines = [f"# Section {n}: {section[0].section_title}", ""]
    lines += [f"- [{'x' if t.done else ' '}] {t.label} {t.text}" for t in section]
    lines += ["", "## Scenarios cited", ""]
    lines += [f"### `{sid}`\n{known[sid]}\n" for sid in cited] or ["(none)"]
    (context / "section.md").write_text("\n".join(lines) + "\n")
    base = ctx.run.state.section_base.get(str(n), "")
    diff = git(ctx.repo, "diff", base, head).decode(errors="replace") if base else "(the section's starting point was not recorded)"
    (context / "diff.patch").write_text(diff)
    (context / "evidence.md").write_text(ctx.run.state.evidence_summary.get(str(n), "(no evidence gate ran for this section)") + "\n")
    notes = ctx.run.dir / "followups.md"
    mine = [l for l in notes.read_text().splitlines() if l.startswith(f"- ({n}.")] if notes.exists() else []
    (context / "followups.md").write_text("\n".join(mine or ["(none)"]) + "\n")
    if ts.feedback:
        (context / "prior-review.md").write_text(ts.feedback + "\n")


def step(ctx: Ctx, task: tasks.Task, head: str) -> None:
    ts = ctx.run.state.task(task.key)
    if ts.pending_review and ts.candidate_base == head:
        return _review(ctx, task, ts, head, ts.pending_review)
    ts.pending_review = ""
    if ts.sealed and ts.sealed_base == head:
        # The last driver sealed this and died before verification finished:
        # verify it again rather than repeat the agent session.
        ctx.run.event("reverify", task=task.key, sha=ts.sealed)
        return _verify_then_review(ctx, task, ts, head, ts.sealed, ctx.run.attempt_dir(task.key, ts.sessions))
    ts.sessions += 1
    ctx.run.state.section_base.setdefault(str(task.section), head)
    ctx.run.save()
    adir = ctx.run.attempt_dir(task.key, ts.sessions)
    work, context = adir / "work", adir / "context"
    _clone(ctx, head, work)
    seeded = _seed(ctx, ts, work)
    has_feedback = _context(ctx, task, ts, work, context, seeded)
    ctx.run.event("implement", task=task.key, label=task.label, session=ts.sessions, attempt=ts.attempts + 1)

    call = _call(ctx, "implement", "write", _implement_prompt(ctx, task, has_feedback, context), work,
                 prompts.IMPLEMENT_SCHEMA, adir / "implement.jsonl", ctx.m.implement_minutes)
    if call is None:
        return _all_exhausted(ctx, task, "implement")
    res = ctx.agent(call)
    ctx.run.event("implemented", task=task.key, disposition=res.disposition, session_id=res.session,
                  profile=call.profile.name, detail=res.detail[:400], output=res.output, usage=res.usage)
    if _infra(ctx, task, ts, res, call, "implement"):
        return
    ts.note = ""
    if res.disposition == "invalid_output":
        return _failed(ctx, task, ts, f"Your final structured result was invalid ({res.detail}). End with the required result.")
    out = res.output
    if out["status"] == "blocked":
        ts.status = "blocked"
        return ctx.pause("blocked", task, out["summary"])
    if out["status"] == "too_big":
        subs = [s for s in out["subtasks"] if s.strip()]
        if not 2 <= len(subs) <= 6:
            return _failed(ctx, task, ts, "too_big needs 2 to 6 non-empty subtasks.")
        return _rewrite_tasks(ctx, head, tasks.split(ctx.tasks_text(head), task, subs),
                              f"specloop: split task {task.label} into {len(subs)} subtasks", task)

    try:
        sealed = seal.seal(ctx.repo, work, head, ctx.m, f"specloop: task {task.title()}"[:200], ctx.credentials)
    except RuntimeError as e:
        # Raised before any attempt is recorded, so a crash here made the
        # supervisor relaunch the same attempt forever (32 sessions, 2026-10-21).
        return ctx.pause("seal", task, str(e))
    (adir / "seal.json").write_text(json.dumps(sealed.__dict__, indent=2))
    ctx.run.event("sealed", task=task.key, sha=sealed.sha, changed=len(sealed.changed), violations=sealed.violations)
    if sealed.secret_paths:
        return ctx.pause("secret", task, f"credential value found in {sealed.secret_paths}; nothing was committed")
    # Reverting a self-ticked tasks.md is harmless; only other repeats pause.
    repeated = {v for v in set(sealed.violations) & set(ts.violations) if not v.startswith(ctx.tasks_path)}
    ts.violations = sealed.violations
    if repeated:
        return ctx.pause("violation_repeat", task, "\n".join(sorted(repeated)))
    if not sealed.sha:
        return _failed(ctx, task, ts, "No admissible change was made." + _violation_note(sealed.violations))
    ts.sealed, ts.sealed_base = sealed.sha, head
    ctx.run.save()
    _verify_then_review(ctx, task, ts, head, sealed.sha, adir)


def _verify_then_review(ctx: Ctx, task: tasks.Task, ts: TaskState, head: str, sha: str, adir: Path) -> None:
    receipts = verify.run_steps(ctx.repo, sha, ctx.m.verify, ctx.m, adir / "verify")
    verify.cleanup(adir / "verify")
    passed = verify.ok(receipts, ctx.m.prepare + ctx.m.verify)
    ctx.run.event("verified", task=task.key, sha=sha, passed=passed)
    ts.candidate, ts.candidate_base, ts.sealed = sha, head, ""
    if not passed:
        return _failed(ctx, task, ts, "The harness's verification failed:\n" + verify.summary(receipts)
                       + _violation_note(ts.violations))
    shutil.rmtree(adir / "work", ignore_errors=True)
    ts.pending_review = sha
    ctx.run.save()
    _review(ctx, task, ts, head, sha)


def _review(ctx: Ctx, task: tasks.Task, ts: TaskState, head: str, sha: str) -> None:
    ts.sessions += 1
    ctx.run.save()
    adir = ctx.run.attempt_dir(task.key, ts.sessions)
    checkout, context = adir / "review", adir / "context"
    verify.extract(ctx.repo, sha, checkout)
    subprocess.run(["chmod", "-R", "u+w", str(checkout)], check=False)
    _review_context(ctx, task, ts, head, sha, checkout, context)
    attempt = ts.attempts + 1
    prompt = prompts.REVIEW.format(
        label=task.label, text=task.text,
        prior_line=f"- {context}/prior-review.md — the previous attempt's blocking findings\n" if ts.feedback else "",
        ctx=str(context),
        prior_rule=prompts.PRIOR_RULE.format(attempt=attempt) if ts.feedback else "")
    ctx.run.event("review", task=task.key, sha=sha, session=ts.sessions)
    call = _call(ctx, "review", "read", prompt, checkout, prompts.REVIEW_SCHEMA, adir / "review.jsonl",
                 ctx.m.review_minutes)
    if call is None:
        return _all_exhausted(ctx, task, "review")
    res = ctx.agent(call)
    ctx.run.event("reviewed", task=task.key, disposition=res.disposition, session_id=res.session,
                  profile=call.profile.name, output=res.output, usage=res.usage)
    if _infra(ctx, task, ts, res, call, "review"):
        return
    if res.disposition == "invalid_output":
        ts.review_invalid += 1
        if ts.review_invalid > ctx.m.review_invalid_retries:
            return ctx.pause("review_invalid", task, res.detail)
        ctx.run.save()
        return
    d = review.decide(res.output, attempt, ts.waived)
    (adir / "decision.json").write_text(json.dumps(d.__dict__, indent=2))
    _followups(ctx, task.label, d.followups)
    if d.verdict == "PASS":
        return _land(ctx, task, ts, head, sha)
    ts.pending_review = ""
    if d.verdict == "NEEDS_HUMAN":
        ts.feedback = review.feedback(d)
        return ctx.pause("needs_human", task, "\n".join(d.needs_human))
    _failed(ctx, task, ts, review.feedback(d), verified=True)


def _land(ctx: Ctx, task: tasks.Task, ts: TaskState, head: str, sha: str) -> None:
    """Candidate plus the tasks.md tick, onto the branch in one ref update."""
    ticked = tasks.tick(ctx.tasks_text(head), task)
    tick = _commit_with(ctx, sha, {ctx.tasks_path: ticked}, f"specloop: complete task {task.label}")
    if not _advance(ctx, head, tick):
        ts.pending_review = ""
        ctx.run.event("land_raced", task=task.key)
        ctx.run.save()
        return
    ts.status, ts.pending_review, ts.feedback = "passed", "", ""
    ctx.run.save()
    ctx.run.event("landed", task=task.key, label=task.label, sha=tick)


def _rewrite_tasks(ctx: Ctx, head: str, text: str, message: str, task: tasks.Task | None) -> None:
    commit = _commit_with(ctx, head, {ctx.tasks_path: text}, message)
    _advance(ctx, head, commit)
    ctx.run.event("tasks_rewritten", task=task.key if task else "", message=message)
    ctx.run.save()


def _commit_with(ctx: Ctx, parent: str, files: dict[str, str], message: str) -> str:
    return seal.commit_files(ctx.repo, parent, files, message)


def _advance(ctx: Ctx, old: str, new: str) -> bool:
    try:
        git(ctx.repo, "update-ref", ctx.ref, new, old)
        return True
    except RuntimeError:
        return False


INFRA_COOLDOWN = 15 * 60


def _infra(ctx: Ctx, task: tasks.Task | None, ts: TaskState, res: agents.Result, call: agents.Call, role: str,
           key: str = "") -> bool:
    if res.disposition == "usage_limit":
        p = call.profile
        limits = quota.codex_limits(call.home) if p.adapter == "codex" and not p.base_url else None
        until = quota.resume_at(limits)
        ctx.run.state.exhausted[p.name] = until
        ctx.run.event("exhausted", profile=p.name, until=until, detail=res.detail[:300])
        nxt = _pick(ctx, role)
        if nxt is None:
            _all_exhausted(ctx, task, role, key)
        else:
            ctx.run.save()
            ctx.notify(messages.fyi(ctx.m.change, f"{p.name} hit its usage limit; {role} continues on {nxt.name}."))
        return True
    if res.disposition == "infra":
        ts.infra += 1
        if ts.infra > ctx.m.max_infra:
            ctx.pause("infra", task, res.detail, key=key, resume_at=time.time() + INFRA_COOLDOWN)
        else:
            ctx.run.save()
        return True
    return False


def _chain(ctx: Ctx, role: str) -> tuple:
    return ctx.m.chains.get(role) or (ctx.m.roles[role],)


def _pick(ctx: Ctx, role: str):
    """The first profile in the role's chain that is not usage-limited right now."""
    now = time.time()
    for p in _chain(ctx, role):
        if ctx.run.state.exhausted.get(p.name, 0) <= now:
            ctx.run.state.exhausted.pop(p.name, None)
            return p
    return None


def _all_exhausted(ctx: Ctx, task: tasks.Task | None, role: str, key: str = "") -> None:
    until = min(ctx.run.state.exhausted.get(p.name, 0) for p in _chain(ctx, role))
    names = ", ".join(p.name for p in _chain(ctx, role))
    ctx.pause("usage_limit", task, f"every {role} profile is usage-limited ({names}); usable again "
              f"{time.strftime('%a %H:%M', time.localtime(until))}", key=key, resume_at=until)


def _failed(ctx: Ctx, task: tasks.Task, ts: TaskState, why: str, verified: bool = False) -> None:
    """`verified`: the failing candidate passed verification (it failed review)."""
    ts.attempts += 1
    ts.feedback = why
    ctx.run.event("attempt_failed", task=task.key, attempt=ts.attempts, why=why[:800])
    if ts.attempts < ctx.m.max_attempts:
        ctx.run.save()
    elif ctx.m.stuck == "agent" and not ts.arbitrated:
        arbitrate(ctx, task, ts, why, verified)
    else:
        ts.status = "stuck"
        ctx.pause("stuck", task, why[:1500])


def arbitrate(ctx: Ctx, task: tasks.Task, ts: TaskState, why: str, verified: bool) -> None:
    """One ruling on a stuck task, before a human is asked.

    accept: land the last candidate (only if it passed verification), keeping
    the overruled findings for the pull request. retry: one more attempt, with
    the arbiter's note for the implementer. human, or anything that goes wrong:
    the stuck pause a human would have had anyway, with the arbiter's reason.
    """
    ts.arbitrated += 1
    ts.sessions += 1
    ctx.run.save()
    adir = ctx.run.attempt_dir(task.key, ts.sessions)
    checkout, context = adir / "arbiter", adir / "context"
    head = ctx.head()
    verify.extract(ctx.repo, ts.candidate or head, checkout)
    subprocess.run(["chmod", "-R", "u+w", str(checkout)], check=False)
    context.mkdir(parents=True, exist_ok=True)
    _copy_change(checkout, ctx, context)
    (context / "task.md").write_text(f"# Task {task.title()}\n\n## Scenarios\n\n{_scenario_text(checkout, ctx, task)}\n")
    diff = git(ctx.repo, "diff", ts.candidate_base, ts.candidate).decode(errors="replace") if ts.candidate else ""
    (context / "diff.patch").write_text(diff or "(no candidate was sealed)\n")
    (context / "history.md").write_text(_history(ctx, task) + "\n\n## Why the last attempt failed\n\n" + why + "\n")
    prompt = prompts.ARBITER.format(label=task.label, text=task.text, attempts=ts.attempts, ctx=str(context),
                                    accept_rule=prompts.ARBITER_ACCEPT if verified else prompts.ARBITER_NO_ACCEPT)
    ctx.run.event("arbitrate", task=task.key, session=ts.sessions, verified=verified)
    call = _call(ctx, "arbiter", "read", prompt, checkout, prompts.ARBITER_SCHEMA, adir / "arbiter.jsonl",
                 ctx.m.review_minutes)
    res = ctx.agent(call) if call else None
    out = res.output if res and res.disposition == "ok" else {}
    ctx.run.event("arbitrated", task=task.key, disposition=res.disposition if res else "no_profile",
                  session_id=res.session if res else "", output=out, usage=res.usage if res else {})
    decision, reason = out.get("decision", ""), out.get("reason", "").strip()
    first = messages.first_line(reason)
    if decision == "accept" and verified and ts.candidate and ts.candidate_base == head:
        _followups(ctx, task.label, [{"basis": "overruled by the arbiter", "text": f"{why} (arbiter: {reason})"}])
        ctx.run.event("arbiter_accept", task=task.key, sha=ts.candidate, reason=reason)
        _land(ctx, task, ts, head, ts.candidate)
        if ts.status == "passed":
            ts.status = "accepted"
            ctx.run.save()
            ctx.notify(messages.fyi(ctx.m.change, f"task {task.label} was stuck; the arbiter accepted its last "
                                                  f"candidate: {first}"))
            return
    elif decision == "retry" and out.get("note", "").strip():
        ts.attempts = ctx.m.max_attempts - 1
        ts.note = out["note"].strip()
        ctx.run.save()
        ctx.notify(messages.fyi(ctx.m.change, f"task {task.label} was stuck; the arbiter gave it one more attempt: "
                                              f"{messages.first_line(ts.note)}"))
        return
    ts.status = "stuck"
    if decision == "accept":
        said = f"The arbiter would accept, but the candidate cannot land (unverified, or the branch moved): {reason}"
    elif reason:
        said = f"The arbiter handed this to you: {reason}"
    else:
        said = f"The arbiter could not rule ({res.disposition if res else 'no usable profile'})."
    ctx.pause("stuck", task, f"{said}\n\n{why}"[:1500])


def _history(ctx: Ctx, task: tasks.Task) -> str:
    """Every review's blocking findings for this task, oldest first."""
    parts = []
    decisions = sorted(ctx.run.dir.glob(f"{task.key}/a*/decision.json"), key=lambda p: int(p.parent.name[1:]))
    for n, path in enumerate(decisions, 1):
        d = json.loads(path.read_text())
        found = "\n".join(f"- [{f.get('basis', '')}] {f['text']}" for f in d.get("blocking", [])) or "(none blocking)"
        parts.append(f"## Review {n} ({d.get('verdict', '')})\n\n{found}")
    return "\n\n".join(parts) or "(no review reached a verdict; every attempt failed before review)"


def _violation_note(violations: list[str]) -> str:
    if not violations:
        return ""
    return "\n\nThese changes were reverted by the harness; do not repeat them:\n" + "\n".join(f"- {v}" for v in violations)


def _clone(ctx: Ctx, head: str, work: Path) -> None:
    shutil.rmtree(work, ignore_errors=True)
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(ctx.repo), str(work)], check=True)
    git(work, "checkout", "-q", "--detach", head)
    git(work, "remote", "remove", "origin")


def _seed(ctx: Ctx, ts: TaskState, work: Path) -> bool:
    """Start a retry from the previous candidate when it still applies."""
    if not (ts.candidate and ts.candidate_base and ts.feedback):
        return False
    diff = git(ctx.repo, "diff", "--binary", ts.candidate_base, ts.candidate)
    p = subprocess.run(["git", "-C", str(work), "apply", "--whitespace=nowarn", "-"], input=diff, capture_output=True)
    return p.returncode == 0


def _scenario_text(work: Path, ctx: Ctx, task: tasks.Task) -> str:
    try:
        known = openspec.scenarios(work, ctx.m.change)
    except openspec.SpecError as e:
        return f"(scenario lookup failed: {e})"
    return "\n\n".join(f"### `{sid}`\n{known[sid]}"
                       for sid in task.known_scenarios(known)) or "(this task names no scenario IDs)"


def _copy_change(work: Path, ctx: Ctx, context: Path) -> None:
    src = openspec.change_dir(work, ctx.m.change)
    for name in ("proposal.md", "design.md"):
        if (src / name).exists():
            shutil.copy(src / name, context / name)
    if (src / "specs").exists():
        shutil.copytree(src / "specs", context / "specs", dirs_exist_ok=True)


def _context(ctx: Ctx, task: tasks.Task, ts: TaskState, work: Path, context: Path, seeded: bool) -> bool:
    context.mkdir(parents=True, exist_ok=True)
    _copy_change(work, ctx, context)
    (context / "task.md").write_text(f"# Task {task.title()}\n\nSection {task.section}: {task.section_title}\n\n"
                                     f"## Scenarios\n\n{_scenario_text(work, ctx, task)}\n")
    parts = []
    if ts.feedback:
        parts.append(("The previous attempt's diff is already applied to your workspace; correct it."
                      if seeded else "The previous attempt's diff no longer applies; start from the current code.")
                     + "\n\n" + ts.feedback)
    if ts.note:
        parts.append(f"A human added this note for you:\n\n{ts.note}")
    if parts:
        (context / "feedback.md").write_text("\n\n---\n\n".join(parts) + "\n")
    return bool(parts)


def _review_context(ctx: Ctx, task: tasks.Task, ts: TaskState, head: str, sha: str, checkout: Path, context: Path) -> None:
    context.mkdir(parents=True, exist_ok=True)
    _copy_change(checkout, ctx, context)
    (context / "task.md").write_text(f"# Task {task.title()}\n\n## Scenarios\n\n{_scenario_text(checkout, ctx, task)}\n")
    diff = git(ctx.repo, "diff", head, sha).decode(errors="replace")
    (context / "diff.patch").write_text(diff)
    (context / "test-edits.md").write_text(review.test_edits(diff) + "\n")
    receipts = sorted(ctx.run.dir.glob(f"{task.key}/a*/verify/receipts.json"), key=lambda p: p.stat().st_mtime)
    if receipts:
        rs = [verify.Receipt(**r) for r in json.loads(receipts[-1].read_text())]
        (context / "receipts.md").write_text(verify.summary(rs) + "\n")
    if ts.feedback:
        (context / "prior-review.md").write_text(ts.feedback + "\n")


def _implement_prompt(ctx: Ctx, task: tasks.Task, has_feedback: bool, context: Path) -> str:
    values = {**ctx.m.vars, "src": ".", "build": "build", "sha": "HEAD"}
    recipe = (
        "  Trusted preparation runs under the harness with network access before offline verification.\n"
        "  Steps run from the build directory with read-only extracted source.\n"
        "  Source, build, and revision values below illustrate the recipe, not actual sandbox paths.\n"
    )
    recipe += "\n".join(
        f"  {label}:\n" + ("\n".join(
            f"    {s.id}: {shlex.join(verify.expand(a, values) for a in s.run)}  (expect exit {s.expect})"
            for s in steps) or "    (none)")
        for label, steps in (("Prepare", ctx.m.prepare), ("Verify", ctx.m.verify))
    )
    return prompts.IMPLEMENT.format(
        label=task.label, text=task.text, recipe=recipe,
        allowed=", ".join(ctx.m.allowed), frozen=", ".join(ctx.m.frozen),
        feedback_line=f"- {context}/feedback.md — why the previous attempt was not accepted\n" if has_feedback else "",
        ctx=str(context))


def _followups(ctx: Ctx, label: str, items: list[dict]) -> None:
    if not items:
        return
    with open(ctx.run.dir / "followups.md", "a") as f:
        for it in items:
            f.write(f"- ({label}) [{it.get('basis', '')}] {it['text']}\n")


def _call(ctx: Ctx, role: str, access: str, prompt: str, cwd: Path, schema: dict, transcript: Path, minutes: int):
    profile = _pick(ctx, role)
    if profile is None:
        return None
    return agents.Call(profile, access, prompt, cwd, schema, transcript, minutes * 60, ctx.m.idle_minutes * 60,
                       agents.homes_dir() / profile.name, ctx.secrets, read_dirs=(cwd.parent / "context",))

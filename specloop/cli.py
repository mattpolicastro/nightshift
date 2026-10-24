"""specloop admit | run | status | act | resume | revise | accept | waive | abort | publish | finish"""

import argparse
import contextlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from specloop import feasibility, loop, manifest, messages, openspec, tasks, verify
from specloop.seal import git
from specloop.state import SAFE_CHANGE, Busy, Run, TaskState, runs_dir

DEFAULT_ENV = Path.home() / ".config" / "specloop" / "env"


def load_env(path: Path | None) -> dict[str, str]:
    values = {}
    p = path or DEFAULT_ENV
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip().removeprefix("export ").strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def notifier(secrets: dict):
    hook = secrets.get("SPECLOOP_SLACK_WEBHOOK") or os.environ.get("SPECLOOP_SLACK_WEBHOOK", "")

    fyi_to_slack = (secrets.get("SPECLOOP_SLACK_FYI") or os.environ.get("SPECLOOP_SLACK_FYI", "1")) != "0"

    def send(message) -> None:
        from specloop import messages

        text = message.text() if isinstance(message, messages.Message) else str(message)
        print(text)
        if isinstance(message, messages.Message) and message.kind == messages.FYI and not fyi_to_slack:
            return
        if hook:
            from nightshift import notify

            if not notify.send(hook, text):
                print("specloop: Slack delivery FAILED for the message above", file=sys.stderr)
        else:
            print("specloop: no SPECLOOP_SLACK_WEBHOOK; this message went only to the log", file=sys.stderr)

    return send


def _manifest(run: Run):
    roles = json.loads((run.dir / "roles.json").read_text()) if (run.dir / "roles.json").exists() else {}
    return manifest.load(run.manifest_path, roles)


def _ctx(run: Run) -> loop.Ctx:
    m = _manifest(run)
    secrets = load_env(m.env_file)
    return loop.Ctx(run, m, secrets, notifier(secrets))


def _checkout(repo: Path, rev: str, dest: Path) -> None:
    archive = subprocess.run(["git", "-C", str(repo), "archive", rev], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)


def problems_with(repo: Path, rev: str, m: manifest.Manifest) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        _checkout(repo, rev, Path(tmp))
        found = openspec.validate(Path(tmp), m.change)
        try:
            known = openspec.scenarios(Path(tmp), m.change)
        except openspec.SpecError as e:
            return found + [str(e)]
        task_file = openspec.change_dir(Path(tmp), m.change) / "tasks.md"
        if not task_file.exists():
            return found + ["the change has no tasks.md"]
        parsed = tasks.parse(task_file.read_text())
        if not parsed:
            found.append("tasks.md has no tasks under '## N. Title' sections")
        keys = [t.key for t in parsed]
        if len(keys) != len(set(keys)):
            found.append("two tasks in one section have identical text")
        for t in parsed:
            found += [f"task {t.label}: unknown scenario `{s}`" for s in t.scenarios if s not in known]
        for n in m.phase_gates:
            if not any(t.section == n for t in parsed):
                found.append(f"phase gate {n} names no section in tasks.md")
    return found


@contextlib.contextmanager
def _diagnostics_to_stderr():
    """Reserve stdout for JSON, including output inherited by subprocesses."""
    sys.stdout.flush()
    saved_stdout = os.dup(1)
    try:
        # redirect_stdout alone cannot redirect git hooks or other children.
        os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            try:
                yield
            finally:
                sys.stdout.flush()
    finally:
        try:
            os.dup2(saved_stdout, 1)
        finally:
            os.close(saved_stdout)


def _outcome(args, **values) -> None:
    if hasattr(args, "document"):
        args.document.update(values)


def admit(args) -> int:
    roles = {"implement": args.implement, "review": args.review}
    m, text = manifest.template(Path(args.manifest), roles, change=args.change, branch=args.branch)
    run = Run.for_change(m.change)
    with run.lock() as mine:
        if not mine:
            reason = f"{m.change} is already being admitted or driven by another process; retry later"
            _outcome(args, reasons=[reason])
            print(reason, file=sys.stderr)
            return 1
        return _admit_locked(args, run, m, text, roles)


def _clear_admission(run: Run) -> None:
    """Remove partial admission data while holding run.lock().

    Keep the directory and lock inode: unlinking either would let another
    process acquire a new lock while this admission still holds the old one.
    """
    # Only ever a partial admission: the caller refuses when state.json exists,
    # and Run.for_change refuses names that resolve outside one run directory.
    for path in run.dir.iterdir():
        if path.name == "lock":
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def _admit_locked(args, run: Run, m: manifest.Manifest, text: str, roles: dict) -> int:
    # Recheck the commit point under the same lock as cleanup and admission.
    if (run.dir / "state.json").exists():
        reason = f"{run.dir} already exists; abort and remove it to re-admit"
        _outcome(args, reasons=[reason])
        print(reason, file=sys.stderr)
        return 1
    try:
        if args.head and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", args.head):
            raise ValueError("--head must be a full commit object ID")
        # State is the admission commit point, written only after validation.
        # A process killed before then can leave a partial clone or review;
        # rebuild it so the daemon's next status/admit cycle can recover.
        _clear_admission(run)
        run.manifest_path.write_text(text)
        (run.dir / "roles.json").write_text(json.dumps({k: v for k, v in roles.items() if v}))
        subprocess.run(["git", "clone", "-q", "--bare", str(m.repo), str(run.dir / "repo.git")], check=True)
        if args.head:
            # Fetch explicitly: bare clones do not copy custom source refs.
            # Only the run's branch moves; source checkouts stay untouched.
            git(run.dir / "repo.git", "fetch", "-q", str(m.repo),
                f"+{args.head}:refs/heads/{m.branch}")
        head = git(run.dir / "repo.git", "rev-parse", f"refs/heads/{m.branch}").decode().strip()
        _outcome(args, head=head)
        found = problems_with(run.dir / "repo.git", head, m)
        if found:
            raise ValueError("the change is not admissible:\n" + "\n".join(f"  - {p}" for p in found))
        if not args.skip_feasibility:
            review = feasibility.check(run.dir / "repo.git", head, m, run.dir / "admission")
            _outcome(args, risks=review.get("risks", []))
            for risk in review.get("risks", []):
                print(f"risk: {risk}")
            if review.get("error"):
                raise ValueError(f"the feasibility review did not complete ({review['error']}); "
                                 "retry, or pass --skip-feasibility")
            if review.get("contradictions"):
                raise ValueError("tasks that are impossible as written:\n" + "\n".join(
                    f"  - {c['task']}: {c['problem']}\n      evidence: {c['evidence']}" for c in review["contradictions"]))
    except Exception as e:
        _clear_admission(run)
        _outcome(args, reasons=[str(e)])
        print(f"admission refused: {e}", file=sys.stderr)
        return 1
    run.state.pr = args.pr
    run.save()
    run.event("admitted", head=head, implement=m.roles["implement"].name, review=m.roles["review"].name)
    _outcome(args, outcome="admitted")
    print(f"admitted {m.change} at {head[:12]} ({m.roles['implement'].name} implements, "
          f"{m.roles['review'].name} reviews). Next: specloop run {m.change}")
    return 0


def _sync(ctx: loop.Ctx) -> None:
    """Fast-forward from the source repo's branch, e.g. after a human tasks.md edit."""
    try:
        git(ctx.repo, "fetch", "-q", str(ctx.m.repo), f"+{ctx.ref}:refs/specloop/source")
    except RuntimeError as e:
        print(f"(no sync from {ctx.m.repo}: {e})")
        return
    head, src = ctx.head(), git(ctx.repo, "rev-parse", "refs/specloop/source").decode().strip()
    if head == src:
        return
    if subprocess.run(["git", "-C", str(ctx.repo), "merge-base", "--is-ancestor", head, src]).returncode == 0:
        git(ctx.repo, "update-ref", ctx.ref, src, head)
        ctx.run.event("synced", head=src)
        print(f"took {src[:12]} from {ctx.m.repo}")


def run_cmd(args) -> int:
    run = Run.for_change(args.change)
    if not run.manifest_path.exists():
        print(f"no admitted change {args.change}", file=sys.stderr)
        return 1
    with run.lock() as mine:
        if not mine:
            print(f"{args.change} is already being driven", file=sys.stderr)
            return 1
        run.reload()
        if run.state.status != "running":
            print(f"{args.change} is {run.state.status}" + (f" ({run.state.pause.reason})" if run.state.pause else ""))
            return 1
        status = loop.run(_ctx(run))
    return 0 if status in ("done", "paused") else 1


def _locked_command(args, command) -> int:
    """Reload and apply a direct command under the same lock used by act."""
    run = Run.for_change(args.change)
    with run.lock() as mine:
        if not mine:
            reason = (f"{args.change} is being driven elsewhere (e.g. specloop serve); retry later, "
                      f"or `specloop pause {args.change}` first")
            if getattr(args, "json", False) and hasattr(args, "document"):
                # A held lock is retryable, not a refusal a front end should escalate.
                args.document = {"version": 1, "error": reason}
            else:
                _outcome(args, reasons=[reason])
            print(reason, file=sys.stderr)
            return 1
        run.reload()
        return command(args, run)


def resume(args) -> int:
    return _locked_command(args, _resume)


def _resume(args, run: Run) -> int:
    """Synchronize and resume while the caller holds the run's driver lock."""
    st = run.state
    if st.status != "paused" or not st.pause:
        print(f"{args.change} is {st.status}, not paused", file=sys.stderr)
        return 1
    # Import human corrections before making the run eligible for serve, even
    # when execution is deferred with --no-run (including act resume).
    ctx = _ctx(run)
    _sync(ctx)
    p = st.pause
    if p.reason == "phase_gate" and p.task.startswith("section:"):
        st.gates_passed.append(int(p.task.split(":")[1]))
    elif p.task in st.tasks:
        ts = st.tasks[p.task]
        ts.attempts = ts.infra = ts.review_invalid = 0
        ts.status, ts.violations = "pending", []
    if p.task in st.tasks and args.note:
        st.tasks[p.task].note = args.note
    st.status, st.pause = "running", None
    run.clear_pause_request()
    run.save()
    run.event("resumed", note=args.note or "")
    if args.no_run:
        return 0
    return 0 if loop.run(ctx) in ("done", "paused") else 1


def revise(args) -> int:
    from specloop import revise as revision

    try:
        run = Run.for_change(args.change)
        if not run.manifest_path.exists() or not (run.dir / "state.json").exists():
            raise ValueError(f"no admitted change {args.change}")
        result = revision.revise_result(run, _manifest(run), Path(args.feedback).read_text(),
                                        feedback_format=args.feedback_format, round_id=args.round_id)
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        _outcome(args, reasons=[str(e)])
        print(f"revision refused: {e}", file=sys.stderr)
        return 1
    _outcome(args, outcome="revised", commit=result.commit)
    if result.round_id is None:
        print(f"{args.change}: review tasks committed at {result.commit[:12]}; run is running")
    else:
        _outcome(args, round_id=result.round_id, replayed=result.replayed,
                 status=result.status, pause=result.pause)
        action = "replayed" if result.replayed else "committed"
        status = "finished" if result.status == "done" else result.status
        detail = f" ({result.pause['reason']})" if result.pause else ""
        print(f"{args.change}: review round {result.round_id} {action} at {result.commit[:12]}; "
              f"run is {status}{detail}")
    return 0


def accept(args) -> int:
    return _locked_command(args, _accept)


def _candidate_landed(ctx: loop.Ctx, task: tasks.Task, ts: TaskState, head: str) -> bool:
    """Recognize _land's ref update even if its state save was interrupted."""
    if not task.done or not ts.candidate_base:
        return False
    if git(ctx.repo, "show", "-s", "--format=%P", head).decode().strip() != ts.candidate:
        return False
    if git(ctx.repo, "diff", "--name-only", ts.candidate, head).decode().splitlines() != [ctx.tasks_path]:
        return False
    return ctx.tasks_text(head) == tasks.tick(ctx.tasks_text(ts.candidate_base), task)


def _accept(args, run: Run) -> int:
    if run.state.status in ("aborted", "done"):
        print(f"{args.change} is {run.state.status}; no candidate can be accepted", file=sys.stderr)
        return 1
    ctx = _ctx(run)
    key = args.task or (run.state.pause.task if run.state.pause else "")
    ts = run.state.tasks.get(key)
    if not ts or not ts.candidate:
        print("no candidate to accept for that task", file=sys.stderr)
        return 1
    head = ctx.head()
    task = next((t for t in tasks.parse(ctx.tasks_text(head)) if t.key == key), None)
    if not task:
        print("that task is no longer in tasks.md", file=sys.stderr)
        return 1
    if ts.candidate_base == head:
        run.event("human_accept", task=key, sha=ts.candidate)
        loop._land(ctx, task, ts, head, ts.candidate)
    else:
        pause = run.state.pause
        # Completing acceptance saves "accepted" and clears the pause together.
        # A later pause must never be cleared by replaying that acceptance.
        unfinished = (run.state.status == "paused" and pause is not None
                      and pause.reason in ("stuck", "needs_human") and pause.task == key
                      and ts.status != "accepted")
        if not unfinished or not _candidate_landed(ctx, task, ts, head):
            print("the branch has moved since that candidate was built; resume instead", file=sys.stderr)
            return 1
    # Git is the durable landing record; finish the transition without landing
    # again when a previous accept stopped before it could clear the pause.
    ts.status, ts.pending_review, ts.feedback = "accepted", "", ""
    run.state.status, run.state.pause = "running", None
    run.save()
    return 0 if args.no_run or loop.run(ctx) in ("done", "paused") else 1


def waive(args) -> int:
    return _locked_command(args, _waive)


def _waive(args, run: Run) -> int:
    run.state.task(args.task).waived.append(args.text)
    run.save()
    run.event("waived", task=args.task, text=args.text)
    print(f"waived findings matching {args.text!r} for task {args.task}; `specloop resume {args.change}` to continue")
    return 0


def pause_cmd(args) -> int:
    """Stop cleanly: now if nothing is driving the run, else at the next task boundary."""
    run = Run.for_change(args.change)
    with run.lock() as mine:
        if mine:
            run.reload()
            if run.state.status != "running":
                print(f"{args.change} is {run.state.status}; nothing to pause", file=sys.stderr)
                return 1
            run.pause("requested", detail="paused while nothing was driving it")
            print(f"{args.change} paused")
            return 0
    # A busy driver owns state.json. Only signal it; never save our snapshot.
    run.request_pause()
    print(f"{args.change} will pause at its next task boundary; the current step finishes first")
    return 0


def abort(args) -> int:
    return _locked_command(args, _abort)


def _abort(args, run: Run) -> int:
    run.state.status = "aborted"
    run.save()
    run.event("aborted")
    print(f"{args.change} aborted; evidence kept in {run.dir}")
    return 0


def _gh(*args: str) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {p.stderr.strip()[:400]}")
    return p.stdout.strip()


class PublishTarget(ValueError):
    """Pushing to the source clone's origin would not reach the manifest's repository."""


def _repository(url: str) -> str:
    """host/owner/repo of a hosted remote URL; "" for a local path."""
    m = re.match(r"(?:[a-z][a-z0-9+.-]*://(?:[^@/]+@)?|[^@/:]+@)([^/:]+)(?::\d+)?[/:](.+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _remote(ctx: loop.Ctx) -> str:
    # Publishing reaches GitHub through the clone's origin, so a clone of
    # someone else's repository would send the run's branch there.
    github = ctx.m.github.rstrip("/")
    if not github:
        raise PublishTarget(f"the manifest names no GitHub repository; add github = \"owner/repo\" "
                            f"under [run] in {ctx.run.dir / 'manifest.toml'}")
    url = subprocess.run(["git", "-C", str(ctx.m.repo), "remote", "get-url", "origin"],
                         capture_output=True, text=True, check=True).stdout.strip()
    parts = github.split("/")
    expected = "/".join([parts[0] if len(parts) == 3 else "github.com", *parts[-2:]])
    actual = _repository(url)
    if actual and actual.casefold() != expected.casefold():
        raise PublishTarget(f"origin of {ctx.m.repo} is {actual}, but the manifest publishes to {github}; "
                            f"repoint origin before publishing")
    return url


def _pr_body(ctx: loop.Ctx) -> str:
    m = ctx.m
    checks = "\n".join(f"- [ ] `{sid}` — {why}" for sid, why in sorted(m.coverage_exempt.items()))
    follow = (ctx.run.dir / "followups.md").read_text() if (ctx.run.dir / "followups.md").exists() else "(none)\n"
    done = [t for t in tasks.parse(ctx.tasks_text(ctx.head())) if t.done]
    return (f"OpenSpec change `{m.change}`, driven by specloop: {len(done)} tasks, each sealed, verified by the "
            f"harness and independently reviewed.\n\n## Checks a human must make\n\n{checks or '(none)'}\n\n"
            f"## Non-blocking review findings\n\n{follow}\n"
            f"🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")


def _push(repo: Path, url: str, ref: str) -> None:
    # Deliberately not seal.git: that helper drops the global config, and with
    # it the user's credential helper. Pushing is the user's own act.
    subprocess.run(["git", "-C", str(repo), "push", "-q", url, f"{ref}:{ref}"], check=True, timeout=300)


def publish(args) -> int:
    """Push the branch and reuse its PR or open a draft on an explicit command."""
    return _locked_command(args, _publish)


def _existing_pr(ctx: loop.Ctx) -> str:
    # gh accepts [HOST/]OWNER/REPO. REST repository names omit the host;
    # select it explicitly when supplied, retaining gh's default otherwise.
    parts = ctx.m.github.rstrip("/").split("/")
    repository = "/".join(parts[-2:])
    host = ("--hostname", parts[0]) if len(parts) == 3 else ()
    page = 1
    while True:
        prs = json.loads(_gh("api", f"repos/{repository}/pulls", *host, "--method", "GET",
                             "-f", "state=open", "-f", f"base={ctx.m.pr_base}",
                             "-f", "per_page=30", "-f", f"page={page}"))
        # Fork PRs can share both branch names. Walk every page until the
        # run's own head repository and branches match or results run out.
        for pr in prs:
            if (pr["head"]["ref"] == ctx.m.branch and pr["base"]["ref"] == ctx.m.pr_base
                    and (pr["head"].get("repo") or {}).get("full_name", "").casefold()
                    == repository.casefold()):
                return pr["html_url"]
        if len(prs) < 30:
            return ""
        page += 1


def _publish(args, run: Run) -> int:
    st = run.state
    if not (st.status == "paused" and st.pause and st.pause.reason == "ready_to_publish"):
        reason = f"{args.change} is not ready to publish"
        _outcome(args, reasons=[reason])
        print(reason, file=sys.stderr)
        return 1
    ctx = _ctx(run)
    try:
        remote = _remote(ctx)
    except PublishTarget as e:
        _outcome(args, reasons=[str(e)])
        print(e, file=sys.stderr)
        return 1
    try:
        base = loop._fetch_publish_base(ctx, remote, timeout=20)
        head = ctx.head()
        needs_prepare = loop._publish_needs_prepare(ctx, base, head)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        # Keep transport/check failures retryable, even with a pending intent.
        # No successful freshness result means readiness and checks stay intact.
        reason = (f"Could not check publication base refs/heads/{ctx.m.pr_base}: {exc}; "
                  f"nothing was published. Retry `specloop publish {args.change}` "
                  "when the current base can be checked.")
    else:
        reason = ""
        if needs_prepare:
            st.status, st.pause = "running", None
            run.save()
            reason = ("Base preparation was scheduled; nothing was published. Retry publication after "
                      "the driver restores readiness. Without `specloop serve`, run "
                      f"`specloop run {args.change}`, then retry `specloop publish {args.change}`.")
            run.event("publish_preparation_scheduled", base=base, head=head, reason=reason)
    if reason:
        if getattr(args, "json", False) and hasattr(args, "document"):
            args.document = {"version": 1, "error": reason}
        else:
            _outcome(args, reasons=[reason])
        print(reason, file=sys.stderr)
        return 1
    _push(ctx.repo, remote, ctx.ref)
    url = st.pr
    if not url:
        # Creation may have succeeded before its response or local state was
        # saved. Reconcile first; a failed lookup must not trigger creation.
        url = _existing_pr(ctx)
    if not url:
        url = _gh("pr", "create", "--draft", "-R", ctx.m.github, "--head", ctx.m.branch, "--base", ctx.m.pr_base,
                  "--title", f"{ctx.m.change}: OpenSpec change via specloop", "--body", _pr_body(ctx))
    st.pr = url
    run.event("published", pr=url, head=ctx.head())
    ctx.pause("human_checks", detail=f"{url}\nHosted CI runs on the PR. Make the PR's human checks, then "
              f"`specloop finish {args.change}`.")
    _outcome(args, outcome="published", pr=url)
    return 0


def finish(args) -> int:
    """Archive the change into openspec/specs, push, and mark the PR ready."""
    return _locked_command(args, _finish)


def _tasks_path(repo: Path, head: str, change: str) -> str:
    """Locate tasks at this commit, including an interrupted finish's archive."""
    live = f"openspec/changes/{change}/tasks.md"
    paths = git(repo, "ls-tree", "-r", "--name-only", head,
                live, "openspec/changes/archive/").decode().splitlines()
    if live in paths:
        return live
    archived = sorted(p for p in paths if re.fullmatch(
        rf"openspec/changes/archive/\d{{4}}-\d{{2}}-\d{{2}}-{re.escape(change)}/tasks\.md", p))
    return archived[-1] if archived else live


def _finish(args, run: Run) -> int:
    st = run.state
    if not (st.status == "paused" and st.pause and st.pause.reason == "human_checks"):
        print(f"{args.change} has not been published", file=sys.stderr)
        return 1
    ctx = _ctx(run)
    head = ctx.head()
    task_path = _tasks_path(ctx.repo, head, ctx.m.change)
    text = git(ctx.repo, "show", f"{head}:{task_path}").decode()
    if any(not t.done for t in tasks.parse(text)):
        print("tasks are unfinished; refusing to archive", file=sys.stderr)
        return 1
    if task_path == ctx.tasks_path:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "w"
            subprocess.run(["git", "clone", "-q", "--branch", ctx.m.branch, str(ctx.repo), str(work)], check=True)
            openspec.archive(work, ctx.m.change, all_tasks_done=True)
            git(work, "add", "-A")
            git(work, "commit", "-qm", f"specloop: archive {ctx.m.change} into openspec/specs", env=loop.seal.IDENTITY)
            git(work, "push", "-q", str(ctx.repo), f"HEAD:{ctx.ref}")
    # The branch itself records the archive durably. Retry external operations
    # from that commit without requiring a separate state write to have survived.
    _push(ctx.repo, _remote(ctx), ctx.ref)
    try:
        _gh("pr", "ready", st.pr, "-R", ctx.m.github)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        # A lost response (or a prior interrupted finish) can leave it ready.
        ready = False
        try:
            ready = _gh("pr", "view", st.pr, "-R", ctx.m.github,
                        "--json", "isDraft", "--jq", ".isDraft") == "false"
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            pass
        if not ready:
            raise
    st.status, st.pause = "done", None
    run.save()
    run.event("finished", head=ctx.head(), pr=st.pr)
    print(f"{args.change}: archived and marked ready for review — {st.pr}")
    return 0


def act(args) -> int:
    """Validate and apply one interface action under the run's driver lock."""
    document = {"version": 1, "outcome": "refused", "verb": args.verb, "detail": ""}
    code = 1
    with _diagnostics_to_stderr():
        try:
            if len(args.note) > 2000:
                raise ValueError("the note must be at most 2,000 characters")
            run = Run.for_change(args.change)
            # Do not create a directory for an unknown or partially admitted run.
            if not (run.dir / "state.json").exists():
                raise ValueError(f"no admitted change {args.change}")
            with run.lock() as mine:
                if not mine:
                    raise ValueError(f"{args.change} is locked by another process; retry later")
                run.reload()
                st = run.state
                allowed = messages.actions_for_pause(st.pause.reason) if st.status == "paused" and st.pause else []
                if args.verb not in allowed:
                    raise ValueError(f"{args.verb} is not allowed for {args.change}'s current pause")
                if args.expect and args.expect != f"{st.pause.reason}:{st.pause.task}":
                    # A stale page must not act on a newer pause of the same kind.
                    raise ValueError(f"{args.change}'s pause changed since it was shown; refresh and retry")
                command = {"resume": _resume, "accept": _accept, "publish": _publish,
                           "finish": _finish, "abort": _abort}[args.verb]
                # Reuse command transitions on the reloaded run; serve drives it later.
                command_args = argparse.Namespace(change=args.change, note=args.note, no_run=True, task=None,
                                                  document={})
                code = command(command_args, run)
                reasons = command_args.document.get("reasons") if code else None
                document.update(outcome="done" if code == 0 else "refused",
                                detail="; ".join(reasons) if reasons else
                                f"{args.verb} {'completed' if code == 0 else 'could not be completed'} "
                                f"for {args.change}")
        except Exception as e:
            code = 1
            document["detail"] = str(e)
    if args.json:
        print(json.dumps(document))
    else:
        print(document["detail"])
    return code


def propose(args) -> int:
    """Draft an OpenSpec change with an agent; stop for a human to approve it."""
    from specloop import propose as drafting

    # Keep the original positional spelling and its spec/<change> default.
    # Template-style invocations inherit each value unless explicitly overridden.
    change = args.change or args.legacy_change
    branch = args.branch or (f"spec/{change}" if args.legacy_change else None)
    m, _ = manifest.template(Path(args.manifest), change=change, branch=branch)
    _outcome(args, branch=m.branch)
    idea = Path(args.idea_file).read_text() if args.idea_file else args.idea
    if not idea:
        _outcome(args, problems=["give --idea or --idea-file"])
        print("give --idea or --idea-file", file=sys.stderr)
        return 1
    out = runs_dir().parent / "proposals" / m.change
    d = drafting.draft(m.repo, args.base, m.change, idea, args.source or [], m, out,
                       branch=m.branch, from_branch=args.from_branch)
    _outcome(args, commit=d.commit, questions=d.questions, contradictions=d.contradictions,
             risks=d.risks, problems=d.problems)
    notify = notifier(load_env(m.env_file))
    if d.questions:
        _outcome(args, outcome="needs_input")
        notify(messages.Message(messages.ACTION, m.change,
                                f"Answer {len(d.questions)} question(s) so the draft can continue",
                                messages.first_line(d.questions[0]),
                                f"specloop propose {args.manifest} {m.change} --idea-file <idea + answers>"))
        return 2
    if d.problems:
        print("draft rejected after its rounds:\n" + "\n".join(f"  - {p}" for p in d.problems), file=sys.stderr)
        return 1
    for r in d.risks:
        print(f"risk: {r}")
    if d.contradictions:
        print("feasibility review found tasks impossible as written:\n" + "\n".join(
            f"  - {c['task']}: {c['problem']} ({c['evidence']})" for c in d.contradictions), file=sys.stderr)
        return 1
    published_ref = drafting.publish_branch(d, m.repo)
    notify(messages.Message(messages.ACTION, m.change, "Review the drafted change, then admit it",
                            f"draft ready on {published_ref} in {m.repo} ({d.commit[:12]}); "
                            f"review openspec/changes/{m.change}/ there",
                            f"specloop admit --head {d.commit}"))
    _outcome(args, outcome="drafted", branch=published_ref)
    return 0


def drill_cmd(args) -> int:
    from specloop import drill

    run = Run.for_change(args.change)
    ctx = _ctx(run)
    drills = [drill.Drill(**d) for d in ctx.m.drills]
    if not drills:
        print("the manifest declares no [[drill]] entries", file=sys.stderr)
        return 1
    results = drill.run_all(ctx, drills, run.dir / "drills")
    for o in results:
        print(f"{o.id:28s} {o.result:13s} {o.detail[:110]}")
    caught = sum(o.result == "caught" for o in results)
    scored = sum(o.result in ("caught", "missed") for o in results)
    if caught == scored:
        ctx.notify(messages.fyi(args.change, f"reviewer drill: caught {caught}/{scored} seeded defects."))
    else:
        ctx.notify(messages.Message(messages.ACTION, args.change,
                                    f"Look at the review gate: it missed {scored - caught} of {scored} seeded defects",
                                    "", f"specloop drill {args.change}"))
    return 0 if caught == scored else 1


def serve_cmd(args) -> int:
    from specloop import serve

    return serve.serve(args.poll, notifier(load_env(None)), once=args.once)


def _context_summary(repo: Path, head: str, task_path: str) -> str:
    proposal = str(Path(task_path).with_name("proposal.md"))
    if not git(repo, "ls-tree", "--name-only", head, "--", proposal).strip():
        return ""
    text = git(repo, "show", f"{head}:{proposal}").decode()
    why = re.search(r"^##[ \t]+Why[ \t]*\r?$", text, re.MULTILINE)
    if not why:
        return ""
    section = re.split(r"^#{1,6}[ \t]+", text[why.end():], maxsplit=1, flags=re.MULTILINE)[0].strip()
    paragraph = re.split(r"\n[ \t\r]*\n", section, maxsplit=1)[0]
    summary = " ".join(paragraph.split())
    return summary if len(summary) <= 400 else summary[:399].rstrip() + "…"


#: Pause reasons as a person coming in cold would say them.
PAUSE_WORDS = {
    "stuck": "stuck", "blocked": "blocked", "needs_human": "needs a human answer",
    "phase_gate": "waiting at a phase gate", "gate_evidence": "evidence gate fell short",
    "ready_to_publish": "ready to publish", "human_checks": "waiting for human checks",
    "requested": "paused on request", "infra": "provider trouble", "usage_limit": "waiting on usage limits",
    "coverage": "untested scenarios", "acceptance": "final checks failing",
    "violation_repeat": "repeatedly changing files it may not", "review_invalid": "reviewer output invalid",
    "secret": "credential value found",
}


def _context_recent(run: Run, all_tasks: list[tasks.Task]) -> list[dict]:
    path = run.dir / "events.jsonl"
    if not path.exists():
        return []
    labels = {t.key: t.label for t in all_tasks}
    recent = deque(maxlen=5)
    for line in path.read_text().splitlines():
        # Skip a damaged or half-written line (the log is appended while read).
        try:
            event = json.loads(line)
            kind = event["event"]
        except (ValueError, KeyError, TypeError):
            continue
        # Implement events retain labels after a split removes the task key.
        # Replay in order so later renumbering cannot relabel earlier events.
        if event.get("task") and event.get("label"):
            labels[event["task"]] = event["label"]
        label = event.get("label") or labels.get(event.get("task"))
        # Infrastructure failures legitimately have no structured output.
        output = event.get("output") or {}
        if kind == "landed" and label:
            text = f"task {label} landed"
        elif kind == "attempt_failed" and label:
            text = f"task {label} attempt {event['attempt']} failed"
        elif kind == "gate_reviewed" and output.get("verdict") in ("PASS", "FAIL"):
            text = f"section {event['section']} gate review: {output['verdict']}"
        elif kind == "arbitrated" and label and output.get("decision"):
            text = f"arbiter ruled {output['decision']} on task {label}"
        elif kind == "pause":
            reason = event.get("reason", "")
            text = f"paused: {PAUSE_WORDS.get(reason, reason.replace('_', ' '))}"
        elif kind == "resumed":
            text = "resumed"
        elif kind == "human_accept" and label:
            text = f"task {label} accepted by a human"
        elif kind == "published":
            text = "published to its pull request"
        elif kind == "finished":
            text = "finished"
        elif kind == "aborted":
            text = "aborted"
        else:
            continue
        at = datetime.fromtimestamp(event["at"], timezone.utc).isoformat().replace("+00:00", "Z")
        recent.append({"at": at, "text": text})
    return list(reversed(recent))


def _status_context(run: Run, m: manifest.Manifest, head: str, task_path: str,
                    all_tasks: list[tasks.Task]) -> dict:
    try:
        pause = run.state.pause if run.state.status == "paused" else None
        task = next((t for t in all_tasks if pause and t.key == pause.task), None)
        if task is None:
            task = next((t for t in all_tasks if not t.done), None)
        # Publication accepts [HOST/]OWNER/REPO; display links support github.com only.
        parts = m.github.rstrip("/").split("/")
        host = parts.pop(0).lower() if len(parts) == 3 else "github.com"
        github = ""
        if run.state.pr and host == "github.com" and len(parts) == 2 and all(parts):
            github = (f"https://github.com/{quote('/'.join(parts), safe='/')}"
                      f"/tree/{quote(m.branch, safe='/')}"
                      f"/{quote(Path(task_path).parent.as_posix(), safe='/')}")
        return {
            "summary": _context_summary(run.dir / "repo.git", head, task_path),
            "repo": str(m.repo.absolute()),
            "branch": m.branch,
            "task": {"label": task.label, "section": task.section_title, "text": task.text[:300]}
                    if task else None,
            "detail": pause.detail[:1500] if pause else "",
            "recent": _context_recent(run, all_tasks),
            "github": github,
        }
    except Exception:
        # Display context is best effort; one damaged run must not hide status.
        return {"summary": "", "repo": "", "branch": "", "task": None,
                "detail": "", "recent": [], "github": ""}


def _pause_since(directory: Path) -> int | float | None:
    """Best-effort time of the last appended pause, independent of context limits."""
    try:
        lines = (directory / "events.jsonl").read_text().splitlines()
    except (OSError, UnicodeError):
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(event, dict) and event.get("event") == "pause":
            at = event.get("at")
            # A recognizable pause with no usable time supersedes older pauses.
            # Exact types exclude bool, which Python otherwise treats as an int.
            return at if type(at) is int or (type(at) is float and math.isfinite(at)) else None
    return None


def _status_entry(name: str) -> dict:
    from specloop import serve

    run = Run.for_change(name, require_existing=True)
    st = run.state
    # Status needs only the trusted manifest and git, not credentials
    # or the notification machinery of an execution context.
    m = _manifest(run)
    repo = run.dir / "repo.git"
    head = git(repo, "rev-parse", f"refs/heads/{m.branch}").decode().strip()
    task_path = _tasks_path(repo, head, m.change)
    text = git(repo, "show", f"{head}:{task_path}").decode()
    all_tasks = tasks.parse(text)
    auto_resumes_remaining = 0
    if st.pause and st.pause.resume_at and st.pause.reason in serve.AUTO_RESUME:
        cap = serve.AUTO_RESUME[st.pause.reason]
        ts = st.tasks.get(st.pause.task)
        # Like lift_due, a missing task state leaves recovery uncapped.
        auto_resumes_remaining = (max(0, cap - ts.auto_resumes)
                                  if cap is not None and ts is not None else None)
    done = sum(t.done for t in all_tasks)
    entry = {
        "change": name,
        "status": st.status,
        "pause": {"reason": st.pause.reason, "task": st.pause.task, "detail": st.pause.detail,
                  "auto_resumes_remaining": auto_resumes_remaining, "since": _pause_since(run.dir)}
                 if st.pause else None,
        "tasks": {"done": done, "total": len(all_tasks)},
        "head": head,
        "pr": st.pr,
        "context": _status_context(run, m, head, task_path, all_tasks),
    }
    if st.status == "paused" and st.pause:
        pause = st.pause
        task = next((t for t in all_tasks if t.key == pause.task), None)
        ts = st.tasks.get(pause.task)
        message = messages.for_pause(
            m.change, pause.reason, label=task.label if task else "", key=pause.task,
            detail=pause.detail, attempts=ts.attempts if ts else 0,
            done=done, pr=st.pr, resume_at=pause.resume_at,
        )
        entry["action"] = {
            "kind": message.kind,
            "headline": message.headline,
            "why": message.why,
            "command": message.command,
            "actions": messages.actions_for_pause(pause.reason),
            # Names this pause, so an action can refuse to land on a later one.
            "pause_id": f"{pause.reason}:{pause.task}",
        }
    return entry


def _status_error_entry(name: str, error: Exception) -> dict:
    summary = "Run status could not be read."
    detail = f"{summary} Inspect specloop status --json for details."
    # Compatibility placeholders only: never persist this pause or offer actions.
    return {
        "change": name,
        "status": "paused",
        "pause": {"reason": "status_error", "task": "", "detail": detail,
                  "auto_resumes_remaining": 0, "since": _pause_since(runs_dir() / name)},
        "tasks": {"done": 0, "total": 0},
        "head": "",
        "pr": "",
        "context": {"summary": summary, "repo": "", "branch": "", "task": None,
                    "detail": detail, "recent": [], "github": ""},
        "error": f"{type(error).__name__}: {error}"[:500],
    }


def _status_json(change: str | None) -> int:
    from specloop import serve

    try:
        if change is not None:
            # Admission is checked before collection; disappearance after this
            # check is a per-run read failure, not an unknown named change.
            if not SAFE_CHANGE.fullmatch(change) or change in (".", ".."):
                raise ValueError(f"unsafe change name {change!r}: use letters, digits, '.', '_' or '-'")
            if not (runs_dir() / change / "state.json").exists():
                raise ValueError(f"{change}: no such change (not admitted, or admission was refused)")
            names = [change]
        else:
            names = sorted(p.name for p in runs_dir().glob("*") if (p / "state.json").exists())
        entries = []
        for name in names:
            try:
                entry = _status_entry(name)
            except Exception as e:
                entry = _status_error_entry(name, e)
            entries.append(entry)
        document = {"version": 1, "supervisor": {"state": serve.liveness()}, "runs": entries}
    except Exception as e:
        print(json.dumps({"version": 1, "error": str(e) or type(e).__name__}))
        return 1
    print(json.dumps(document))
    return 0


def status(args) -> int:
    from specloop import serve

    if args.json:
        return _status_json(args.change)
    names = [args.change] if args.change else sorted(p.name for p in runs_dir().glob("*") if (p / "state.json").exists())
    print(f"supervisor: {serve.liveness()}")
    for name in names:
        run = Run.for_change(name)
        if not (run.dir / "state.json").exists():
            print(f"{name}: no such change (not admitted, or admission was refused)")
            continue
        st = run.state
        print(f"{name}: {st.status}")
        if st.pause:
            when = (f"; auto-resume after {time.strftime('%a %H:%M', time.localtime(st.pause.resume_at))}"
                    if st.pause.resume_at else "")
            print(f"  paused: {st.pause.reason}" + (f" (task {st.pause.task})" if st.pause.task else "") + when)
            for line in st.pause.detail.splitlines()[:12]:
                print(f"    {line}")
        try:
            ctx = _ctx(run)
            head = ctx.head()
            task_path = _tasks_path(ctx.repo, head, ctx.m.change)
            all_tasks = tasks.parse(git(ctx.repo, "show", f"{head}:{task_path}").decode())
            done = sum(t.done for t in all_tasks)
            print(f"  tasks: {done}/{len(all_tasks)} done on {ctx.m.branch} @ {ctx.head()[:12]}")
            nxt = next((t for t in all_tasks if not t.done), None)
            if nxt:
                ts = st.tasks.get(nxt.key)
                extra = f" — attempt {ts.attempts + 1}, {ts.infra} infra retries" if ts else ""
                print(f"  next: [{nxt.key}] {nxt.title()[:100]}{extra}")
        except Exception as e:
            print(f"  (could not read tasks: {e})")
        events = (run.dir / "events.jsonl")
        if events.exists():
            for line in events.read_text().splitlines()[-args.events:]:
                e = json.loads(line)
                rest = {k: v for k, v in e.items() if k not in ("at", "event", "output")}
                print(f"  · {e['event']} {json.dumps(rest)[:140]}")
        if (run.dir / "followups.md").exists():
            n = len((run.dir / "followups.md").read_text().splitlines())
            print(f"  followups: {n} in {run.dir / 'followups.md'}")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    option_end = argv.index("--") if "--" in argv else len(argv)
    json_errors = "--json" in argv[:option_end]

    class ParseError(Exception):
        pass

    class ArgumentParser(argparse.ArgumentParser):
        def error(self, message):
            if json_errors:
                raise ParseError(message)
            super().error(message)

    # argparse uses the parent's class for subparsers, sharing this error mode.
    ap = ArgumentParser(prog="specloop")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("admit")
    a.add_argument("manifest")
    a.add_argument("--implement")
    a.add_argument("--review")
    a.add_argument("--skip-feasibility", action="store_true")
    a.add_argument("--change", help="override the manifest's run.change")
    a.add_argument("--branch", help="override the manifest's run.branch")
    a.add_argument("--head", help="admit this full commit ID from the local source onto the run's branch")
    a.add_argument("--pr", default="", help="adopt an existing PR URL for publish to reuse")
    a.add_argument("--json", action="store_true", help="print one version-1 outcome document")
    r = sub.add_parser("run")
    r.add_argument("change")
    s = sub.add_parser("status")
    s.add_argument("change", nargs="?")
    s.add_argument("--events", type=int, default=6)
    s.add_argument("--json", action="store_true", help="print one version-1 status document")
    act_parser = sub.add_parser("act")
    act_parser.add_argument("change")
    act_parser.add_argument("verb")
    act_parser.add_argument("--note", default="")
    act_parser.add_argument("--expect", default="", help="the pause_id the caller saw; refuse if it changed")
    act_parser.add_argument("--json", action="store_true", help="print one version-1 outcome document")
    rs = sub.add_parser("resume")
    rs.add_argument("change")
    rs.add_argument("--note", default="")
    rs.add_argument("--no-run", action="store_true")
    rv = sub.add_parser("revise")
    rv.add_argument("change")
    rv.add_argument("--feedback", required=True, help="file of feedback blocks separated by a line containing ---")
    rv.add_argument("--feedback-format", choices=("text", "json"), default="text",
                    help="text uses --- separators; json is an array of complete feedback strings")
    rv.add_argument("--round-id", help="run-local key for replaying the same feedback without appending again")
    rv.add_argument("--json", action="store_true", help="print one version-1 outcome document")
    ac = sub.add_parser("accept")
    ac.add_argument("change")
    ac.add_argument("task", nargs="?")
    ac.add_argument("--no-run", action="store_true")
    w = sub.add_parser("waive")
    w.add_argument("change")
    w.add_argument("task")
    w.add_argument("text")
    ab = sub.add_parser("abort")
    ab.add_argument("change")
    pa = sub.add_parser("pause")
    pa.add_argument("change")
    pb = sub.add_parser("publish")
    pb.add_argument("change")
    pb.add_argument("--json", action="store_true", help="print one version-1 outcome document")
    fi = sub.add_parser("finish")
    fi.add_argument("change")
    pr = sub.add_parser("propose")
    pr.add_argument("manifest", help="a manifest for the target repo (its repo, paths, verify and profiles are used)")
    pr.add_argument("legacy_change", nargs="?", metavar="change")
    pr.add_argument("--change", help="override the manifest's run.change (and positional change, if given)")
    pr.add_argument("--branch", help="override the manifest's run.branch")
    pr.add_argument("--json", action="store_true", help="print one version-1 outcome document")
    pr.add_argument("--idea")
    pr.add_argument("--idea-file")
    pr.add_argument("--source", action="append", help="repo-relative documents the drafter must read")
    pr.add_argument("--base", default="main")
    pr.add_argument("--from-branch", help="revise from this existing local branch/ref instead of the base")
    dr = sub.add_parser("drill")
    dr.add_argument("change")
    sv = sub.add_parser("serve")
    sv.add_argument("--poll", type=float, default=60)
    sv.add_argument("--once", action="store_true")
    try:
        args = ap.parse_args(argv)
    except ParseError as e:
        print(json.dumps({"version": 1, "error": str(e)}))
        return 2
    command = {"admit": admit, "run": run_cmd, "status": status, "act": act, "resume": resume, "revise": revise,
            "accept": accept, "waive": waive, "abort": abort, "publish": publish, "finish": finish,
            "serve": serve_cmd, "propose": propose, "drill": drill_cmd, "pause": pause_cmd}[args.cmd]
    if args.cmd not in ("admit", "propose", "revise", "publish") or not args.json:
        return command(args)
    if args.cmd == "propose":
        args.document = {"version": 1, "outcome": "rejected", "branch": "", "commit": "",
                         "questions": [], "contradictions": [], "risks": [], "problems": []}
    elif args.cmd == "admit":
        args.document = {"version": 1, "outcome": "refused", "head": "", "reasons": [], "risks": []}
    else:
        args.document = {"version": 1, "outcome": "refused"}
    # Notifications and progress remain visible without contaminating stdout.
    with _diagnostics_to_stderr():
        try:
            code = command(args)
        except Exception as e:
            if args.cmd == "publish" or isinstance(e, Busy):
                # An interrupted push is retryable, not a domain refusal that
                # the front end can cache as delivered human input.
                args.document = {"version": 1, "error": str(e)}
            else:
                key = "problems" if args.cmd == "propose" else "reasons"
                args.document[key] = [str(e)]
            code = 1
    print(json.dumps(args.document))
    return code


if __name__ == "__main__":
    sys.exit(main())

"""Draft, admit and publish spec PRs, separate from worker claims.

Durable CLI receipts and GitHub checks let a restarted daemon finish delivery
without spending another drafting session or duplicating a PR or comment.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tomllib
import uuid
from functools import partial
from pathlib import Path
from urllib.parse import quote

from . import queue, specloop_client, specloop_job
from .config import Repo


log = logging.getLogger(__name__)


class Paused(RuntimeError):
    """Stop polling immediately, including item processing and error reporting."""


class DeliveryError(RuntimeError):
    """An item needs human intervention before delivery can continue."""


_PENDING = object()


# Delivery runs in the poll thread. Keep each subprocess well below the
# default 240-second poll interval, including publish's nested git push.
POLL_COMMAND_TIMEOUT = 30


def _bounded_run(args, *, runner=None, **kwargs):
    try:
        return (runner or subprocess.run)(args, timeout=POLL_COMMAND_TIMEOUT, **kwargs)
    except subprocess.TimeoutExpired as exc:
        # An ambiguous timeout is retryable, not a rejected push. Preserve the
        # receipt and arming labels so the next poll can reconcile remote state.
        raise RuntimeError(
            f"{args[0]} command timed out after {POLL_COMMAND_TIMEOUT}s; retry on the next poll"
        ) from exc


def _each(items, action, *, on_error=None, on_pending=None) -> list[dict]:
    """Finish independent items before reporting errors to a direct caller.

    The daemon supplies on_error to record each failure and escalate an item
    when needed. Pending jobs are not successful retries. A pause is control
    flow, never an item failure.
    """
    results, errors = [], []
    for item in items:
        try:
            result = action(item)
        except Paused:
            raise
        except Exception as exc:
            if on_error is not None:
                on_error(item, exc)
            else:
                errors.append(exc)
        else:
            if result is _PENDING:
                if on_pending is not None:
                    on_pending(item)
            elif result is not None:
                results.append(result)
    if errors:
        raise errors[0]
    return results


def report_failure(repo, labels, number, stage, kind, exc, *, runner):
    """Best-effort item escalation must not prevent processing the next item."""
    try:
        _comment(repo.name, number, str(number),
                 f"Specloop {stage} failed. Human input is needed.\n\n{exc}\n\n"
                 f"Resolve the failure, then remove `{labels.needs_human}` to retry.",
                 runner=runner, action=f"{stage}-error", kind=kind)
        # Label last so a failed comment leaves delivery armed for retry.
        runner([kind, "edit", str(number), "--repo", repo.name,
                "--add-label", labels.needs_human])
    except Paused:
        raise
    except Exception as error:
        return [error]
    return []


def propose_pending(
    repo: Repo, labels: queue.Labels, state_dir: Path, *, runner=None, specloop_runner=None,
    git_runner=None, launcher=None, on_error=None, on_pending=None,
) -> list[dict]:
    """Draft each armed issue and publish or escalate its durable receipt.

GitHub reads deliberately propagate failures: an unavailable issue must never
look like an absent label or an empty set of comments.
"""
    if repo.engine != "specloop":
        return []
    gh = runner or queue._gh
    pending = {}
    for label in (labels.ready, labels.spec_draft):
        for issue in queue._list(repo.name, label, runner=gh):
            pending[issue.number] = issue

    def propose(number):
        raw = json.loads(gh([
            "issue", "view", str(number), "--repo", repo.name,
            "--json", "state,title,body,labels,comments",
        ]))
        current = {label["name"] for label in raw["labels"]}
        if raw["state"] != "OPEN" or not current.intersection((labels.ready, labels.spec_draft)):
            return None
        if current.intersection((labels.working, labels.needs_human, labels.revise)):
            return None
        path = state_dir / "proposals" / repo.name.replace("/", "__") / f"{number}.json"
        slug = re.sub(r"[^a-z0-9]+", "-", raw["title"].lower()).strip("-")[:40].rstrip("-")
        change = f"{repo.name.split('/')[-1]}-issue-{number}-{slug or 'issue'}"
        receipt = json.loads(path.read_text()) if path.exists() else None
        drafting_labels = current.intersection((labels.ready, labels.spec_draft))
        selected_events = None
        drafting_events = None
        authorized = False

        def authorize():
            nonlocal selected_events, drafting_events, authorized
            if selected_events is None:
                selected_events = _latest_label_events(repo.name, number, drafting_labels, runner=gh)
                drafting_events = {label: event["id"] for label, event in selected_events.items()}
            if not authorized:
                authorized = _authorize_label_events(repo.name, number, selected_events, runner=gh)
            return authorized

        # Collection can write the receipt and job records. Hold both intact
        # on denial, without cancelling the worker or consulting blockers.
        if receipt and "job" in receipt and "result" not in receipt and not authorize():
            return None
        if receipt and not _collect(path, receipt, "propose"):
            return _PENDING
        if receipt and "result" not in receipt:
            receipt = None  # A failed job was reported on the preceding poll.
        # Only collaborators' comments steer a draft (queue.TRUSTED_ASSOCIATIONS).
        comments = [comment.get("body") or "" for comment in raw.get("comments", []) if queue.trusted(comment)]
        idea_parts = [raw["title"], raw.get("body") or ""]
        idea = "\n\n".join([*idea_parts, *comments])
        # Our delivery comment must not count as an answer on a retry.
        issue_input = "\n\n".join([
            *idea_parts, *(body for body in comments if not body.startswith(queue.SELF_COMMENT_PREFIX)),
        ])
        if receipt and receipt["result"]["outcome"] != "drafted":
            # A label write may succeed even when its response is lost. A new
            # label application then starts a new round without an input edit
            # or a locally recorded `delivered` flag.
            if selected_events is None:
                selected_events = _latest_label_events(repo.name, number, drafting_labels, runner=gh)
                drafting_events = {label: event["id"] for label, event in selected_events.items()}
            if (receipt.get("delivered") or issue_input != receipt.get("input", receipt["idea"])
                    or drafting_events != receipt.get("drafting_events", drafting_events)):
                receipt = None
        if receipt:
            change = receipt["change"]
            receipt.setdefault("title", raw["title"])
        branch = f"spec/{change}"
        pr = queue._latest_pr_number(repo.name, branch, runner=gh, strict=True)
        replaces_pr = None
        if pr is not None:
            previous = json.loads(gh([
                "pr", "view", str(pr), "--repo", repo.name, "--json", "state,headRefOid",
            ]))
            if previous["state"] == "CLOSED":
                # A re-armed issue starts a new draft after a human closes its
                # PR. Keep replacement receipts on delivery retries, even
                # while GitHub still lists the old PR as the latest one.
                replaces_pr = {"number": pr, "head": previous["headRefOid"]}
                if not receipt or receipt.get("replaces_pr", {}).get("number") != pr:
                    receipt = None
                pr = None
            elif previous["state"] != "OPEN":
                return None  # A merged change must not be drafted again.
        # Receipts (including legacy ID-only records) and open PRs establish
        # recoverable work, never trust. Recovery still bypasses blockers.
        if (pr is not None or receipt) and not authorize():
            return None
        if pr is not None:
            _deliver(repo, labels, number, change, None, current, pr=pr, runner=gh)
            return receipt
        if receipt:
            _deliver(repo, labels, number, change, receipt, current, runner=gh, git_runner=git_runner)
            receipt["delivered"] = True
            _save(path, receipt)
            return receipt
        blocked = queue.open_blockers(repo.name, queue.blockers(raw["body"]), runner=gh)
        if blocked:
            log.info("#%s is blocked by %s — not drafting", number,
                     ", ".join(f"#{blocker}" for blocker in blocked))
            return None
        # New rounds stay silent while blocked. Once eligible, authorize the
        # same applications used for rearming before replacing any receipt.
        if not authorize():
            return None
        receipt = {"repo": repo.name, "issue": number, "change": change,
                   "title": raw["title"], "idea": idea, "input": issue_input,
                   "round_id": uuid.uuid4().hex, "drafting_events": drafting_events}
        if replaces_pr:
            receipt["replaces_pr"] = replaces_pr
        if not _issue_author_trusted(repo.name, number, gh):
            # The issue's own text can change after a collaborator labels it,
            # so an outside author's issue is never drafted as written.
            receipt["result"] = {"outcome": "rejected", "contradictions": [], "risks": [], "problems": [
                "This issue was opened by someone outside the repository's collaborators, and its text can "
                "change after it is labelled, so it is not drafted as written. A collaborator can open an "
                "issue with the request (or restate it in a comment here and re-open it as their own)."]}
            _save(path, receipt)
            _deliver(repo, labels, number, change, receipt, current, runner=gh, git_runner=git_runner)
            receipt["delivered"] = True
            _save(path, receipt)
            return receipt
        if not _start(path, receipt, [
            "propose", str(repo.manifest), "--change", change,
            "--branch", branch, "--base", repo.base,
        ], idea=idea, runner=specloop_runner, launcher=launcher):
            return _PENDING
        _save(path, receipt)
        _deliver(repo, labels, number, change, receipt, current, runner=gh, git_runner=git_runner)
        receipt["delivered"] = True
        _save(path, receipt)
        return receipt

    return _each(sorted(pending), propose, on_error=on_error, on_pending=on_pending)


def _issue_author_trusted(repo: str, number: int, gh) -> bool:
    """The issue author's association, from REST (gh issue view does not carry it).

    A failed read raises: an unknown author must not be treated as trusted.
    """
    issue = json.loads(gh(["api", f"repos/{repo}/issues/{number}"]))
    return queue.trusted(issue, "author_association")


def admit_pending(
    repo: Repo, labels: queue.Labels, state_dir: Path, *, runner=None, specloop_runner=None,
    git_runner=None, launcher=None, status_runner=None, on_error=None, on_pending=None,
) -> list[dict]:
    """Admit approved spec branches and recover interrupted PR notifications."""
    if repo.engine != "specloop":
        return []
    gh = runner or queue._gh
    pending = json.loads(gh([
        "pr", "list", "--repo", repo.name, "--label", labels.spec_approved,
        "--state", "open", "--json", "number", "--limit", "100",
    ]))

    def admit(number):
        pr = json.loads(gh([
            "pr", "view", str(number), "--repo", repo.name, "--json",
            "state,labels,headRefName,headRefOid,isCrossRepository,url",
        ]))
        current = {label["name"] for label in pr["labels"]}
        branch = pr["headRefName"]
        if (pr["state"] != "OPEN" or labels.spec_approved not in current
                or pr["isCrossRepository"] or not branch.startswith("spec/")):
            return None
        change = branch.removeprefix("spec/")
        # Drafting preserves the repository name, including dots, underscores
        # and hyphens. Keep it a single safe path/ref component.
        if (not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", change)
                or ".." in change or change.endswith((".", ".lock"))):
            return None
        approval_event = _approval_event(repo.name, number, labels.spec_approved, runner=gh)
        # Checked before the revise/needs-human skip, so an approval applied
        # alongside agent:revise records the head the human actually saw.
        if not _approval_covers_head(state_dir, repo, number, change, approval_event, pr, labels, gh):
            return None
        if current.intersection((labels.needs_human, labels.revise)):
            return None
        path = state_dir / "admissions" / repo.name.replace("/", "__") / f"{number}.json"
        receipt = json.loads(path.read_text()) if path.exists() else None
        if receipt and not _collect(path, receipt, "admit"):
            return _PENDING
        if receipt and "result" not in receipt:
            receipt = None
        status = specloop_client.call(["status"], runner=status_runner or specloop_runner)
        if not isinstance(status.get("runs"), list):
            raise specloop_client.SpecloopError("status returned no runs list")
        existing = next((run for run in status["runs"] if run["change"] == change), None)
        if existing is not None:
            if existing["pr"] == pr["url"]:
                result = {"outcome": "admitted"}
            else:
                result = {"outcome": "refused", "reasons": [
                    f"Change {change} already belongs to a run with PR {existing['pr'] or '(none)'}."
                ]}
        elif (receipt and not receipt.get("delivered")
              and receipt["result"]["outcome"] in ("admitted", "refused")
              and receipt.get("approval_event") == approval_event
              and receipt["head"] == pr["headRefOid"] and receipt["change"] == change):
            result = receipt["result"]
        else:
            # Keep the reviewed tip reachable without moving a source branch:
            # it may be checked out, with local edits, or have been rebased.
            # Admission installs this exact commit only in its own bare repo.
            source = Path(tomllib.loads(repo.manifest.read_text())["run"]["repo"]).expanduser()
            fetched_ref = f"refs/nightshift/admit/{change}"
            git = partial(_bounded_run, runner=git_runner)
            fetched = git([
                "git", "-C", str(source), "fetch", "origin", f"+refs/heads/{branch}:{fetched_ref}",
            ], capture_output=True, text=True, check=False)
            if fetched.returncode:
                raise RuntimeError(f"could not fetch {branch}: {fetched.stderr.strip()}")
            tip = git(["git", "-C", str(source), "rev-parse", fetched_ref],
                      capture_output=True, text=True, check=False)
            if tip.returncode or tip.stdout.strip() != pr["headRefOid"]:
                raise RuntimeError(f"{branch} changed during approval; retry on the next poll")
            receipt = {"change": change, "head": pr["headRefOid"],
                       "approval_event": approval_event}
            if not _start(path, receipt, [
                "admit", str(repo.manifest), "--change", change, "--branch", branch,
                "--head", pr["headRefOid"], "--pr", pr["url"],
            ], runner=specloop_runner, launcher=launcher):
                return _PENDING
            result = receipt["result"]
        receipt = {"change": change, "head": pr["headRefOid"],
                   "approval_event": approval_event, "result": result}
        _save(path, receipt)
        if result["outcome"] == "admitted":
            body = f"The build has started for {change}."
            add, remove = [], [labels.spec_approved]
            if labels.spec_review in current:
                remove.append(labels.spec_review)
        else:
            body = "Admission refused. Human input is needed.\n\n" + "\n".join(
                "- " + (json.dumps(reason, ensure_ascii=False) if isinstance(reason, dict) else reason)
                for reason in result.get("reasons", [])
            )
            add, remove = [labels.needs_human], [labels.spec_approved]
        _comment(repo.name, number, change, body, runner=gh, action="admit", kind="pr",
                 round_id=f"{approval_event}:{receipt['head']}:{result['outcome']}")
        edit = ["pr", "edit", str(number), "--repo", repo.name]
        for label in add:
            edit += ["--add-label", label]
        for label in remove:
            edit += ["--remove-label", label]
        gh(edit)
        receipt["delivered"] = True
        _save(path, receipt)
        return result

    return _each((item["number"] for item in pending), admit,
                 on_error=on_error, on_pending=on_pending)


def revise_pending(
    repo: Repo, labels: queue.Labels, state_dir: Path, *, runner=None, specloop_runner=None,
    git_runner=None, launcher=None, status_runner=None, on_error=None, on_pending=None,
) -> list[dict]:
    """Re-draft specs or queue review tasks for admitted changes on their PR."""
    if repo.engine != "specloop":
        return []
    gh = runner or queue._gh
    pending = json.loads(gh([
        "pr", "list", "--repo", repo.name, "--label", labels.revise,
        "--state", "open", "--json", "number", "--limit", "100",
    ]))

    def revise(number):
        pr = json.loads(gh([
            "pr", "view", str(number), "--repo", repo.name, "--json",
            "state,title,body,labels,headRefName,headRefOid,isCrossRepository,url",
        ]))
        current = {label["name"] for label in pr["labels"]}
        branch = pr["headRefName"]
        if (pr["state"] != "OPEN" or labels.revise not in current
                or labels.needs_human in current or pr["isCrossRepository"]
                or not branch.startswith("spec/")):
            return None
        change = branch.removeprefix("spec/")
        if (not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", change)
                or ".." in change or change.endswith((".", ".lock"))):
            return None
        directory = repo.name.replace("/", "__")
        path = state_dir / "revisions" / directory / f"{number}.json"
        receipt = json.loads(path.read_text()) if path.exists() else None
        if receipt and not _collect(path, receipt, receipt.get("command", "propose")):
            return _PENDING
        event = _label_events(repo.name, number, {labels.revise}, runner=gh)[labels.revise]
        if receipt and (receipt["change"] != change or receipt["event"] != event
                        or "result" not in receipt
                        or (receipt.get("delivered")
                            and receipt["result"]["outcome"] in ("needs_input", "rejected", "refused"))):
            receipt = None
        git = partial(_bounded_run, runner=git_runner)
        if receipt is None or receipt["result"]["outcome"] in ("needs_input", "rejected", "refused"):
            tip = json.loads(gh(["api", f"repos/{repo.name}/commits/{pr['headRefOid']}"]))
            since = tip["commit"]["committer"]["date"]
            if not since:
                raise RuntimeError("could not read the PR branch tip date")
            feedback = queue.pr_feedback(repo.name, branch, since, runner=gh, strict=True, number=number)
            feedback = [block for block in feedback if queue.has_instructions([block])]
            # The needs-human write may have landed despite a lost response,
            # leaving delivered unset. A reviewer answer after clearing the
            # label must retry drafting rather than replay the old questions.
            if receipt and feedback != receipt["feedback"]:
                receipt = None
        if receipt is None:
            receipt = {"change": change, "head": pr["headRefOid"], "event": event,
                       "feedback": feedback, "round_id": str(event)}
            if not queue.has_instructions(feedback):
                receipt["result"] = {"outcome": "empty"}
            else:
                # Empty feedback needs no CLI call, including status. Only
                # instructions require choosing between spec and build stages.
                status = specloop_client.call(["status"], runner=status_runner or specloop_runner)
                if not isinstance(status.get("runs"), list):
                    raise specloop_client.SpecloopError("status returned no runs list")
                admitted = next((run for run in status["runs"] if run["change"] == change), None)
                if admitted is not None:
                    if admitted.get("pr") != pr["url"]:
                        return None  # Never steer another repository's run.
                    receipt["command"] = "revise"
                    # The background CLI may outlive this poll or the daemon.
                    # Keep the blocks on disk until its durable result arrives.
                    feedback_path = path.with_suffix(f".{event}.feedback.json").resolve()
                    feedback_path.parent.mkdir(parents=True, exist_ok=True)
                    # Review bodies may contain Markdown separators or YAML
                    # document boundaries. JSON preserves each complete block.
                    feedback_path.write_text(json.dumps(feedback) + "\n")
                    if not _start(path, receipt, [
                        "revise", change, "--feedback", str(feedback_path),
                        "--feedback-format", "json",
                    ], runner=specloop_runner, launcher=launcher):
                        return _PENDING
            if "result" not in receipt:
                receipt["command"] = "propose"
                source = Path(tomllib.loads(repo.manifest.read_text())["run"]["repo"]).expanduser()
                # Prefer the original idea, including issue comments. A spec
                # PR created outside intake still has its title/body and the
                # existing change in the drafting clone as context.
                idea = f"{pr['title']}\n\n{pr.get('body') or ''}"
                for proposal in (state_dir / "proposals" / directory).glob("*.json"):
                    original = json.loads(proposal.read_text())
                    if original["change"] == change:
                        idea = original["idea"]
                        break
                idea += "\n\n## New PR review feedback\n\n" + "\n\n---\n\n".join(feedback)
                fetched_ref = f"refs/nightshift/revise/{change}"
                fetched = git([
                    "git", "-C", str(source), "fetch", "origin", f"+refs/heads/{branch}:{fetched_ref}",
                ], capture_output=True, text=True, check=False)
                if fetched.returncode:
                    raise RuntimeError(f"could not fetch {branch}: {fetched.stderr.strip()}")
                head = git(["git", "-C", str(source), "rev-parse", fetched_ref],
                           capture_output=True, text=True, check=False)
                if head.returncode or head.stdout.strip() != pr["headRefOid"]:
                    raise RuntimeError(f"{branch} changed during revision; retry on the next poll")
                if not _start(path, receipt, [
                    "propose", str(repo.manifest), "--change", change, "--branch", branch,
                    "--from-branch", fetched_ref,
                ], idea=idea, runner=specloop_runner, launcher=launcher):
                    return _PENDING
            _save(path, receipt)

        result = receipt["result"]
        if result["outcome"] == "drafted":
            source = Path(tomllib.loads(repo.manifest.read_text())["run"]["repo"]).expanduser()
            if pr["headRefOid"] not in (receipt["head"], result["commit"]):
                raise DeliveryError(f"{branch} moved while its revision was drafted; review the branch before retrying")
            pushed = git([
                "git", "-C", str(source), "push", "origin", f"{result['commit']}:refs/heads/{branch}",
            ], capture_output=True, text=True, check=False)
            if pushed.returncode:
                raise DeliveryError(f"could not push {branch}: {pushed.stderr.strip()}")
            body = (f"Revised the spec in {result['commit']} from this review feedback:\n\n"
                    + "\n\n---\n\n".join(receipt["feedback"]))
            if result.get("risks"):
                body += "\n\nFeasibility risks:\n" + "\n".join(f"- {risk}" for risk in result["risks"])
        elif result["outcome"] == "revised":
            body = f"Queued review tasks for {change} in {result['commit']}; the build has resumed."
        elif result["outcome"] == "empty":
            body = "There is nothing to revise: no new review feedback since the branch tip."
        else:
            body = "Revision needs human input.\n\n" + "\n".join(
                "- " + (json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item)
                for field in ("questions", "contradictions", "problems", "risks", "reasons")
                for item in result.get(field, [])
            )
        _comment(repo.name, number, change, body, runner=gh, action="revise", kind="pr",
                 round_id=receipt["round_id"])
        edit = ["pr", "edit", str(number), "--repo", repo.name]
        if result["outcome"] in ("drafted", "revised", "empty"):
            edit += ["--remove-label", labels.revise]
        else:
            edit += ["--add-label", labels.needs_human]
        gh(edit)
        receipt["delivered"] = True
        _save(path, receipt)
        return result

    return _each((item["number"] for item in pending), revise,
                 on_error=on_error, on_pending=on_pending)


def publish_pending(
    repo: Repo, labels: queue.Labels, state_dir: Path, *, runner=None, specloop_runner=None,
    on_error=None,
) -> list[dict]:
    """Publish only admitted daemon changes, preserving checks before the CLI
    replaces ready_to_publish with human_checks. Recover notification delivery
    from that transition even if the publish response was lost.
    """
    if repo.engine != "specloop":
        return []
    directory = repo.name.replace("/", "__")
    admissions = sorted((state_dir / "admissions" / directory).glob("*.json"))
    if not admissions:
        return []
    status = specloop_client.call(["status"], runner=specloop_runner)
    if not isinstance(status.get("runs"), list):
        raise specloop_client.SpecloopError("status returned no runs list")
    runs = {run["change"]: run for run in status["runs"]}
    gh = runner or queue._gh

    def publish(admission_path):
        admission = json.loads(admission_path.read_text())
        if admission.get("result", {}).get("outcome") != "admitted":
            return None
        change = admission["change"]
        run = runs.get(change)
        if not run or run["status"] != "paused":
            return None
        pause = run.get("pause") or {}
        if pause.get("reason") not in ("ready_to_publish", "human_checks") or not run.get("pr"):
            return None
        path = state_dir / "publications" / directory / admission_path.name
        receipt = json.loads(path.read_text()) if path.exists() else None
        if receipt and (receipt["change"], receipt["head"], receipt["pr"]) != (
                change, run["head"], run["pr"]):
            receipt = None
        if receipt and receipt.get("delivered"):
            return None
        if pause["reason"] == "human_checks" and receipt is None:
            return None  # No daemon publication to recover.
        number = int(admission_path.stem)
        pr = json.loads(gh([
            "pr", "view", str(number), "--repo", repo.name, "--json",
            "state,labels,headRefName,isCrossRepository,url",
        ]))
        current = {label["name"] for label in pr["labels"]}
        if (pr["state"] != "OPEN" or pr["isCrossRepository"]
                or pr["headRefName"] != f"spec/{change}" or pr["url"] != run["pr"]
                or labels.revise in current):
            return None
        if (pause["reason"] == "ready_to_publish" and labels.needs_human in current
                and not (receipt and receipt.get("result"))):
            return None
        if receipt is None:
            receipt = {"change": change, "head": run["head"], "pr": run["pr"],
                       "checks": pause["detail"], "round_id": uuid.uuid4().hex}
            _save(path, receipt)
        if pause["reason"] == "human_checks":
            result = {"outcome": "published", "pr": run["pr"]}
        elif "result" in receipt:
            result = receipt["result"]
        else:
            result = specloop_client.call(
                ["publish", change], runner=partial(_bounded_run, runner=specloop_runner),
            )
            if result.get("outcome") not in ("published", "refused"):
                raise specloop_client.SpecloopError("publish returned no recognized outcome")
            if result["outcome"] == "published" and result.get("pr") != run["pr"]:
                raise specloop_client.SpecloopError("publish returned a different PR")
        receipt["result"] = result
        _save(path, receipt)
        if result["outcome"] == "published":
            checks = receipt["checks"].removesuffix(f"\nNext: `specloop publish {change}`").strip()
            body = (f"Published {change} to {run['pr']}.\n\nRemaining human checks:\n\n"
                    f"{checks or '(none)'}\n\nReview hosted CI on the PR and complete the human checks, "
                    f"then run `specloop finish {change}`.")
        else:
            body = "Publication refused. Human input is needed.\n\n" + "\n".join(
                "- " + (json.dumps(reason, ensure_ascii=False) if isinstance(reason, dict) else reason)
                for reason in result.get("reasons", [])
            )
        _comment(repo.name, number, change, body, runner=gh, action="publish", kind="pr",
                 round_id=receipt.get("round_id"))
        if result["outcome"] == "refused":
            gh(["pr", "edit", str(number), "--repo", repo.name, "--add-label", labels.needs_human])
        receipt["delivered"] = True
        _save(path, receipt)
        return result

    def failed(path, exc):
        on_error(int(path.stem), exc)

    return _each(admissions, publish, on_error=failed if on_error else None)


def _approval_covers_head(state_dir: Path, repo: Repo, number: int, change: str, event: int, pr: dict,
                          labels: queue.Labels, gh) -> bool:
    """An approval covers the head it was first seen with, and nothing pushed later.

    Without this, approving a spec PR that also carries agent:revise (or
    pushing after approving) built a revision no human had reviewed.
    """
    path = state_dir / "approvals" / repo.name.replace("/", "__") / f"{number}.json"
    seen = json.loads(path.read_text()) if path.exists() else None
    if not seen or seen.get("event") != event:
        _save(path, {"event": event, "head": pr["headRefOid"]})
        return True
    if seen["head"] == pr["headRefOid"]:
        return True
    _comment(repo.name, number, change,
             f"The spec changed after it was approved ({seen['head'][:12]} -> {pr['headRefOid'][:12]}), so it was "
             f"not admitted. Review the new revision, then reapply `{labels.spec_approved}`.",
             runner=gh, action="approval", kind="pr", round_id=str(event))
    gh(["pr", "edit", str(number), "--repo", repo.name, "--remove-label", labels.spec_approved])
    return False


def _approval_event(repo: str, number: int, label: str, *, runner) -> int:
    """Identify this approval even when removal succeeded but its response was lost.

    A later label application permits a new admission with the same PR head
    (for example after a manifest correction). Reads must fail closed so an
    unavailable event history cannot masquerade as renewed approval.
    """
    return _label_events(repo, number, {label}, runner=runner)[label]


def _label_events(repo: str, number: int, labels: set[str], *, runner) -> dict[str, int]:
    """Retain the ID-only receipt interface for current label applications."""
    return {label: event["id"] for label, event in
            _latest_label_events(repo, number, labels, runner=runner).items()}


def _latest_label_events(repo: str, number: int, labels: set[str], *, runner) -> dict[str, dict]:
    """Confirm current applications and retain their actor evidence.

    GitHub returns events in chronological page order. Select the final event
    before validating it, so an older application cannot authorize a newer one.
    """
    pages = json.loads(runner([
        "api", f"repos/{repo}/issues/{number}/events", "--paginate", "--slurp",
    ]))
    malformed = f"malformed label event history for #{number}; retry next poll"
    if not isinstance(pages, list):
        raise RuntimeError(malformed)
    latest = {}
    for page in pages:
        if not isinstance(page, list):
            raise RuntimeError(malformed)
        for event in page:
            if not isinstance(event, dict):
                raise RuntimeError(malformed)
            if event.get("event") not in ("labeled", "unlabeled"):
                continue
            label = event.get("label")
            if not isinstance(label, dict) or not isinstance(label.get("name"), str):
                raise RuntimeError(malformed)
            if label["name"] in labels:
                latest[label["name"]] = event
    for label in labels:
        event = latest.get(label, {})
        event_id = event.get("id")
        if (event.get("event") != "labeled" or not isinstance(event_id, int)
                or isinstance(event_id, bool) or event_id <= 0):
            raise RuntimeError(f"could not confirm {label} event for #{number}; retry next poll")
    return latest


def _valid_actor_login(login) -> bool:
    # GitHub usernames, including enterprise managed users (alice_acme) and
    # app bot logins. Never accept URL syntax, whitespace, or the placeholder
    # GitHub uses for deleted accounts.
    return (isinstance(login, str) and login.lower() != "ghost"
            and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9]|[-_](?=[A-Za-z0-9])){0,38}(?:\[bot\])?",
                             login) is not None)


def _membership_status(endpoint: str, *, runner) -> int:
    """Read a membership endpoint, preserving failures other than HTTP 404.

    queue._gh returns stdout on success but puts only stderr in GhError on
    failure. gh reports a negative membership check as `gh: ... (HTTP 404)`.
    A missing status or an unexpected response must remain a lookup failure.
    """
    try:
        response = runner(["api", endpoint, "--include"])
    except queue.GhError as exc:
        error = str(exc).rsplit(" failed: ", 1)[-1].strip()
        if not exc.transient and re.search(r"(?:^HTTP 404:|\(HTTP 404\)$)", error):
            return 404
        raise
    if isinstance(response, str):
        response = response.replace("\r\n", "\n")
        match = re.match(r"HTTP/(?:1\.[01]|2(?:\.0)?) (\d{3})(?: [^\n]*)?\n", response)
        if match and "\n\n" in response:
            status = int(match[1])
            if status in (204, 404):
                return status
    raise RuntimeError(f"unconfirmed membership response from {endpoint}; retry next poll")


def _label_actor_association(repo: str, event: dict, *, runner) -> str | None:
    """Resolve only the label actor using current positive membership evidence."""
    actor = event.get("actor")
    login = actor.get("login") if isinstance(actor, dict) else None
    if not _valid_actor_login(login):
        return None

    repository = json.loads(runner(["api", f"repos/{repo}"]))
    owner = repository.get("owner") if isinstance(repository, dict) else None
    if (not isinstance(owner, dict) or not _valid_actor_login(owner.get("login"))
            or owner.get("type") not in ("User", "Organization")):
        raise RuntimeError(f"unconfirmed repository owner for {repo}; retry next poll")

    association = None
    if owner["type"] == "User" and owner["login"].lower() == login.lower():
        association = "OWNER"
    elif _membership_status(f"repos/{repo}/collaborators/{quote(login, safe='')}", runner=runner) == 204:
        association = "COLLABORATOR"
    elif owner["type"] == "Organization" and _membership_status(
            f"orgs/{quote(owner['login'], safe='')}/members/{quote(login, safe='')}", runner=runner) == 204:
        association = "MEMBER"
    return association if association in queue.TRUSTED_ASSOCIATIONS else None


def _authorize_label_events(repo: str, number: int, events: dict[str, dict], *, runner,
                            kind="issue") -> bool:
    """Authorize every selected application, explaining each definitive denial.

    Resolve all actors before writing: a failed lookup leaves the item on the
    retry path, rather than partially explaining an unconfirmed decision.
    Recheck membership on each poll; neither receipts nor comments grant trust.
    """
    rejected = [
        (label, event) for label, event in events.items()
        if _label_actor_association(repo, event, runner=runner) not in queue.TRUSTED_ASSOCIATIONS
    ]
    for label, event in rejected:
        _comment_label_rejection(repo, number, label, event, runner=runner, kind=kind)
    return not rejected


def _comment_label_rejection(repo: str, number: int, label: str, event: dict, *, runner,
                             kind="issue") -> None:
    """Create one explanation per application, without editing it on later polls.

    The draft namespace opts into queue._gh's ambiguous-create reconciliation.
    Only that transport retry may PATCH an existing comment with the same body.
    """
    marker = f"<!-- nightshift:draft:label-authorization-{number}:{event['id']} -->"
    pages = json.loads(runner([
        "api", f"repos/{repo}/issues/{number}/comments", "--paginate", "--slurp",
    ]))
    malformed = f"malformed comment history for #{number}; retry next poll"
    if not isinstance(pages, list):
        raise RuntimeError(malformed)
    bodies = []
    for page in pages:
        if not isinstance(page, list):
            raise RuntimeError(malformed)
        for comment in page:
            if (not isinstance(comment, dict)
                    or (comment.get("body") is not None and not isinstance(comment["body"], str))):
                raise RuntimeError(malformed)
            bodies.append(comment.get("body") or "")
    if any(marker in body for body in bodies):
        return

    actor = event.get("actor")
    login = actor.get("login") if isinstance(actor, dict) else None
    identity = f"`{login}`" if _valid_actor_login(login) else "an unavailable actor identity"
    body = (
        f"{queue.SELF_COMMENT_PREFIX} Ignoring the `{label}` label applied by {identity}. "
        "Only label actors with an OWNER, MEMBER, or COLLABORATOR relationship to this "
        "repository may trigger this work.\n\n"
        f"To retry, a trusted actor must remove and reapply `{label}`. "
        "If both intake labels are present, remove the unauthorized alias or have a trusted "
        "actor remove and reapply it; both remaining labels must be authorized.\n\n"
        f"{marker}"
    )
    runner([kind, "comment", str(number), "--repo", repo, "--body", body])


_save = specloop_job.save


def _validate(result: dict, command: str, receipt: dict) -> None:
    outcomes = {"propose": ("drafted", "needs_input", "rejected"),
                "admit": ("admitted", "refused"), "revise": ("revised", "refused")}[command]
    if result.get("outcome") not in outcomes:
        raise specloop_client.SpecloopError(f"{command} returned no recognized outcome")
    if command == "propose" and result["outcome"] == "drafted" and (
            not result.get("commit") or result.get("branch") != f"spec/{receipt['change']}"):
        raise specloop_client.SpecloopError("propose returned no commit on the requested branch")
    if command == "revise" and result["outcome"] == "revised" and not result.get("commit"):
        raise specloop_client.SpecloopError("revise returned no landed commit")


def _start(path: Path, receipt: dict, args: list[str], *, runner, launcher,
           idea: str | None = None) -> bool:
    if idea is not None:
        # Retain a distinct input for each invocation, including detached readers.
        payload = (path.parent / f"{path.stem}.jobs" / f"idea-{uuid.uuid4().hex}.txt").resolve()
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_text(idea, encoding="utf-8")
        args = [*args, "--idea-file", str(payload)]
    # An injected completed-process runner keeps CLI protocol/delivery tests
    # deterministic. Production launches a durable subprocess and returns now.
    if runner is None:
        specloop_job.start(path, receipt, args, launcher=launcher)
        return False
    result = specloop_client.call(args, runner=runner)
    _validate(result, args[0], receipt)
    receipt["result"] = result
    return True


def _collect(path: Path, receipt: dict, command: str) -> bool:
    if "result" in receipt or "job" not in receipt:
        return True
    result = specloop_job.collect(path, receipt)
    if result is None:
        return False
    try:
        _validate(result, command, receipt)
    except specloop_client.SpecloopError:
        receipt.pop("job")
        _save(path, receipt)
        raise
    receipt["result"] = result
    _save(path, receipt)
    return True


def _comment(repo: str, number: int, change: str, body: str, *, runner,
             action="draft", kind="issue", round_id: str | None = None) -> None:
    """Upsert one delivery round; queue._gh also reconciles internal retries.

    Legacy receipts without a round ID retain their original marker
    when retried. New receipts save the ID before attempting delivery.
    """
    suffix = f":{round_id}" if round_id is not None else ""
    marker = f"<!-- nightshift:{action}:{change}{suffix} -->"
    body = f"{queue.SELF_COMMENT_PREFIX} {body}\n\n{marker}"
    pages = json.loads(runner([
        "api", f"repos/{repo}/issues/{number}/comments", "--paginate", "--slurp",
    ]))
    comments = [comment for page in pages for comment in page]
    previous = next((c for c in comments if marker in (c.get("body") or "")), None)
    if previous:
        runner(["api", f"repos/{repo}/issues/comments/{previous['id']}",
                "--method", "PATCH", "-f", f"body={body}"])
    else:
        runner([kind, "comment", str(number), "--repo", repo, "--body", body])


def _deliver(repo: Repo, labels: queue.Labels, number: int, change: str,
             receipt: dict | None, current: set[str], *, pr: int | None = None,
             runner, git_runner=None) -> None:
    drafting_labels = [label for label in (labels.ready, labels.spec_draft) if label in current]
    result = receipt["result"] if receipt else {}
    if pr is None and result["outcome"] != "drafted":
        parts = ["The spec draft needs human input."]
        for field in ("questions", "contradictions", "problems", "risks"):
            if result.get(field):
                parts.append(field.capitalize() + ":\n" + "\n".join(
                    "- " + (json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item)
                    for item in result[field]))
        _comment(repo.name, number, change, "\n\n".join(parts), runner=runner,
                 round_id=receipt.get("round_id"))
        queue._relabel(repo.name, number, add=[labels.needs_human], remove=drafting_labels, runner=runner)
        return
    if pr is None:
        branch = f"spec/{change}"
        # Propose places its commit in the manifest's source repository, which
        # need not be the worker clone configured by Repo.dir.
        source = Path(tomllib.loads(repo.manifest.read_text())["run"]["repo"]).expanduser()
        git = partial(_bounded_run, runner=git_runner)
        push_options = []
        if receipt.get("replaces_pr"):
            # A fresh draft starts at the base, so replacing the abandoned
            # branch may not fast-forward. A human may also have deleted it.
            # Lease the old tip (or absence), never a newer human commit.
            remote = git([
                "git", "-C", str(source), "ls-remote", "--exit-code", "--refs",
                "origin", f"refs/heads/{branch}",
            ], capture_output=True, text=True, check=False)
            if remote.returncode not in (0, 2):
                raise RuntimeError(f"could not read {branch}: {remote.stderr.strip()}")
            expected = receipt["replaces_pr"]["head"] if remote.returncode == 0 else ""
            push_options = [f"--force-with-lease=refs/heads/{branch}:{expected}"]
        proc = git([
            "git", "-C", str(source), "push", *push_options,
            "origin", f"{result['commit']}:refs/heads/{branch}",
        ], capture_output=True, text=True, check=False)
        if proc.returncode:
            raise DeliveryError(f"could not push {branch}: {proc.stderr.strip()}")
        body = f"Closes #{number}\n"
        if result.get("risks"):
            body += "\nFeasibility risks:\n" + "\n".join(f"- {risk}" for risk in result["risks"])
        url = runner([
            "pr", "create", "--repo", repo.name, "--head", branch, "--base", repo.base,
            "--draft", "--title", f"Spec: {receipt['title']}", "--body", body,
        ]).strip()
        if not url:
            raise RuntimeError("GitHub returned no URL for the draft PR")
    else:
        url = str(pr)
    # Recheck even a newly created PR before writing: it may have been closed
    # since the lookup or while a create response was being recovered.
    live = json.loads(runner(["pr", "view", url, "--repo", repo.name, "--json", "state,url"]))
    if live["state"] != "OPEN":
        raise RuntimeError(f"draft PR {url} is no longer open; retry on the next poll")
    url = live["url"]
    runner(["pr", "edit", url, "--repo", repo.name, "--add-label", labels.spec_review])
    # The PR identifies the successful draft even after a lost create response
    # or when recovering an existing PR without a local proposal receipt.
    _comment(repo.name, number, change, f"Spec draft ready for review: {url}", runner=runner,
             round_id=f"pr-{url.rsplit('/', 1)[-1]}")
    # Last: a failed label/comment write leaves the issue armed for recovery.
    queue._relabel(repo.name, number, add=[], remove=drafting_labels, runner=runner)

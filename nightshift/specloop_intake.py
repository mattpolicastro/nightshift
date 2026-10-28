"""Draft, admit and publish spec PRs, separate from worker claims.

Durable CLI receipts and GitHub checks let a restarted daemon finish delivery
without spending another drafting session or duplicating a PR or comment.
"""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import signal
import subprocess
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import TypeGuard
from urllib.parse import quote

from specloop import manifest as manifests
from specloop import state as run_state, tasks as run_tasks

from . import queue, specloop_client, specloop_job
from .config import Repo
from .forge import Forge
from .forge.github import GitHubForge


log = logging.getLogger(__name__)


class Paused(RuntimeError):
    """Stop polling immediately, including item processing and error reporting."""


class DeliveryError(RuntimeError):
    """An item needs human intervention before delivery can continue."""


class ConfigurationError(RuntimeError):
    """Enrollment needs repair; retain triggers without escalating items."""

    transient = False


class UnsupportedName(ConfigurationError):
    """New identities are unsafe; recorded safe identities remain usable."""


def _safe_change(change: str) -> bool:
    """The shared admission/revision path and branch component predicate."""
    return (re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", change) is not None
            and ".." not in change and not change.endswith((".", ".lock")))


def _change_name(repository: str, number: int, title: str) -> str:
    parts = repository.split("/", 1)
    if len(parts) != 2 or not all(parts) or "/" in parts[1]:
        raise UnsupportedName(
            f"{repository}: unsupported repository spelling; expected owner/repository. "
            "Correct the repository name in configuration and restart. "
            "Recorded safe branches remain usable."
        )
    owner, name = parts
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].rstrip("-")
    return f"{owner}__{name}-issue-{number}-{slug or 'issue'}"


def _require_supported_name(repo: Repo, change: str) -> None:
    if not _safe_change(change):
        raise UnsupportedName(
            f"{repo.name}: unsupported repository spelling for new branch spec/{change}. "
            "Use a repository spelling that passes branch validation and update enrollment; "
            "restart after configuration changes. Recorded safe branches remain usable."
        )


def manifest_readiness(repo: Repo, *, new_work: bool = True) -> manifests.Manifest | ConfigurationError:
    """Validate an enrollment template without changing it or executing work."""
    if new_work:
        try:
            _require_supported_name(repo, _change_name(repo.name, 7, "Add a gate"))
        except UnsupportedName as exc:
            return exc
    location = str(repo.manifest) if repo.manifest is not None else "(not configured)"
    repair = ("Set the manifest path in configuration and restart." if repo.manifest is None
              else "Repair the manifest at this path; the next poll will retry.")

    def failure(exc: Exception) -> ConfigurationError:
        # Parser diagnostics may contain template values. Report the failure
        # category and repair path, never the operator-managed file contents.
        return ConfigurationError(
            f"{repo.name}: unusable manifest {location} ({type(exc).__name__}). {repair}"
        )

    try:
        if repo.manifest is None:
            raise ValueError("missing manifest")
        path = repo.manifest.expanduser().resolve()
        if not path.is_file():
            raise ValueError("manifest must be a regular file")
    except (OSError, RuntimeError, ValueError) as exc:
        return failure(exc)
    try:
        manifest, _ = manifests.template(
            path, change="readiness-probe", branch="spec/readiness-probe",
        )
        # Finish normalization deferred by Repo when home/filesystem lookup
        # failed at config load. CLI dispatch must use the same resolved path;
        # template() itself does not expand a literal ~user path.
        object.__setattr__(repo, "manifest", path)
        return manifest
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        # Wrong TOML field/table types can also fail mapping/string operations.
        return failure(exc)
    except RuntimeError as exc:
        # pathlib uses RuntimeError for an unexpandable run.repo home path.
        if str(exc) != "Could not determine home directory.":
            raise
        return failure(exc)


def _require_manifest(
    repo: Repo, readiness: manifests.Manifest | ConfigurationError | None = None,
    *, new_work: bool = True,
) -> manifests.Manifest:
    # Saved-result delivery needs a valid template, but creates no identity.
    if not new_work and isinstance(readiness, UnsupportedName):
        readiness = None
    result = manifest_readiness(repo, new_work=new_work) if readiness is None else readiness
    if isinstance(result, ConfigurationError):
        raise result
    return result


_PENDING = object()


# Delivery runs in the poll thread. Keep each subprocess well below the
# default 240-second poll interval, including publish's nested git push.
POLL_COMMAND_TIMEOUT = 30


def run_git(args, *, timeout, capture_output=False, text=False, check=False, **kwargs):
    """Run daemon git in its own group, including ordinary transport children."""
    input_data = kwargs.pop("input", None)
    if input_data is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used")
        kwargs["stdin"] = subprocess.PIPE
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output")
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child = subprocess.Popen(args, start_new_session=True, text=text, **kwargs)
    try:
        try:
            stdout, stderr = child.communicate(input_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The group exited between the timeout and signal.
            except OSError as exc:
                raise RuntimeError(f"git timeout: failed to kill process group {child.pid}: {exc}") from exc
            # Reap independently of pipes held by descendants that escaped the
            # group. Reaping and draining share one additional second.
            deadline = time.monotonic() + 1
            child.wait(timeout=1)
            try:
                child.communicate(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
            raise
        result = subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        for pipe in (child.stdin, child.stdout, child.stderr):
            if pipe is not None:
                pipe.close()


def _bounded_run(args, *, runner=None, **kwargs):
    if runner is None:
        runner = run_git if args[0] == "git" else subprocess.run
    try:
        return runner(args, timeout=POLL_COMMAND_TIMEOUT, **kwargs)
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


def report_failure(repo, labels, number, stage, kind, exc, *, runner=None, forge: Forge | None = None):
    """Best-effort item escalation must not prevent processing the next item."""
    try:
        forge = forge if forge is not None else GitHubForge(runner)
        _comment(repo.name, number, str(number),
                 f"Specloop {stage} failed. Human input is needed.\n\n{exc}\n\n"
                 f"Resolve the failure, then remove `{labels.needs_human}` to retry.",
                 forge=forge, action=f"{stage}-error", kind=kind)
        # Label last so a failed comment leaves delivery armed for retry.
        relabel = forge.relabel_issue if kind == "issue" else forge.relabel_pr
        relabel(repo.name, number, add=[labels.needs_human], remove=[])
    except Paused:
        raise
    except Exception as error:
        return [error]
    return []


def propose_pending(
    repo: Repo, labels: queue.Labels, state_dir: Path, *, runner=None, specloop_runner=None,
    git_runner=None, launcher=None, on_error=None, on_pending=None, readiness=None,
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
        # Released and failed attempts still identify any PR they created.
        # Keep that identity for recovery even without a deliverable result.
        recorded_change = None
        if receipt and "result" not in receipt:
            recorded_change = receipt.get("change")
            receipt = None
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
        else:
            change = recorded_change or _change_name(repo.name, number, raw["title"])
        branch = f"spec/{change}"
        pr = queue._latest_pr_number(repo.name, branch, runner=gh, strict=True)
        if not receipt and not recorded_change and pr is None:
            # Bounded recovery after receipt loss: exact branches in this
            # repository, using the current title. Errors must not look like
            # absence, and recorded identities never depend on today's title.
            legacy = change.removeprefix(repo.name.split("/", 1)[0] + "__")
            pr = queue._latest_pr_number(repo.name, f"spec/{legacy}", runner=gh, strict=True)
            if pr is not None:
                change = legacy
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
            matching = receipt and "job" in receipt and receipt["result"]["outcome"] == "drafted"
            if matching:
                _validate(receipt["result"], "propose", receipt)
                # A reopened PR can share the branch with an unpushed replacement.
                # Only its exact draft commit proves delivery of this attempt.
                matching = previous.get("headRefOid") == receipt["result"]["commit"]
            if matching and not _recover_item_cleanup(path):
                return _PENDING
            _deliver(repo, labels, number, change, None, current, pr=pr, runner=gh)
            if matching:
                _finish_delivery(path, receipt)
            return receipt
        if receipt:
            if receipt["result"]["outcome"] == "drafted":
                _require_manifest(repo, readiness, new_work=False)
            if not _recover_item_cleanup(path):
                return _PENDING
            _deliver(repo, labels, number, change, receipt, current, runner=gh,
                     git_runner=git_runner, readiness=readiness)
            _finish_delivery(path, receipt)
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
        _require_manifest(repo, readiness)
        # Closing a legacy PR ends that identity's round. Only genuinely new
        # work gets today's qualified name; delivery retries keep the receipt.
        change = _change_name(repo.name, number, raw["title"])
        _require_supported_name(repo, change)
        if not _recover_item_cleanup(path):
            return _PENDING
        branch = f"spec/{change}"
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
            _deliver(repo, labels, number, change, receipt, current, runner=gh,
                     git_runner=git_runner, readiness=readiness)
            _finish_delivery(path, receipt)
            return receipt
        if not _start(path, receipt, [
            "propose", str(repo.manifest), "--change", change,
            "--branch", branch, "--base", repo.base,
        ], idea=idea, runner=specloop_runner, launcher=launcher):
            return _PENDING
        _save(path, receipt)
        _deliver(repo, labels, number, change, receipt, current, runner=gh,
                 git_runner=git_runner, readiness=readiness)
        _finish_delivery(path, receipt)
        return receipt

    return _each(sorted(pending), propose, on_error=on_error, on_pending=on_pending)


def _issue_author_trusted(repo: str, number: int, gh=None, *, forge: Forge | None = None) -> bool:
    """The issue author's association, from REST (gh issue view does not carry it).

    A failed read raises: an unknown author must not be treated as trusted.
    """
    forge = forge if forge is not None else GitHubForge(gh)
    issue = forge.issue_author(repo, number)
    return queue.trusted(issue, "author_association")


def admit_pending(
    repo: Repo, labels: queue.Labels, state_dir: Path, *, runner=None, specloop_runner=None,
    git_runner=None, launcher=None, status_runner=None, on_error=None, on_pending=None, readiness=None,
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
        if not _safe_change(change):
            return None
        events = _latest_label_events(repo.name, number, {labels.spec_approved}, runner=gh)
        if not _authorize_label_events(repo.name, number, events, runner=gh, kind="pr"):
            return None
        # Bind the receipt and reviewed head to the same authorized application.
        approval_event = events[labels.spec_approved]["id"]
        if current.intersection((labels.needs_human, labels.revise)):
            # Healthy approvals alongside agent:revise still bind the reviewed
            # head, but a configuration hold must not record a new attempt.
            _require_manifest(repo, readiness)
            _approval_covers_head(state_dir, repo, number, change, approval_event, pr, labels, gh)
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
        committed = existing is not None and existing["pr"] == pr["url"]
        recovering = (receipt and not receipt.get("delivered")
                      and receipt["result"]["outcome"] in ("admitted", "refused")
                      and receipt.get("approval_event") == approval_event
                      and receipt["head"] == pr["headRefOid"] and receipt["change"] == change)
        if not committed:
            # A colliding change belongs to another admission, not this PR's
            # recovery. Hold a new refusal just like a new admit until repair.
            if existing is not None or not recovering:
                source = _require_manifest(repo, readiness).repo
            if not _approval_covers_head(state_dir, repo, number, change, approval_event, pr, labels, gh):
                return None
        # Durable run state completes admission even if its response was lost.
        # Notification recovery needs neither enrollment nor a new approval head.
        if existing is not None:
            if existing["pr"] == pr["url"]:
                result = {"outcome": "admitted"}
            else:
                result = {"outcome": "refused", "reasons": [
                    f"Change {change} already belongs to a run with PR {existing['pr'] or '(none)'}."
                ]}
        elif recovering:
            result = receipt["result"]
        else:
            # Keep the reviewed tip reachable without moving a source branch:
            # it may be checked out, with local edits, or have been rebased.
            # Admission installs this exact commit only in its own bare repo.
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
            if not _recover_item_cleanup(path):
                return _PENDING
            receipt = {"change": change, "head": pr["headRefOid"],
                       "approval_event": approval_event}
            if not _start(path, receipt, [
                "admit", str(repo.manifest), "--change", change, "--branch", branch,
                "--head", pr["headRefOid"], "--pr", pr["url"],
            ], runner=specloop_runner, launcher=launcher):
                return _PENDING
            result = receipt["result"]
        if not _recover_item_cleanup(path):
            return _PENDING
        matching = (receipt and (existing is None or committed)
                    and receipt["change"] == change
                    and receipt.get("approval_event") == approval_event
                    and receipt["result"]["outcome"] == result["outcome"]
                    and (receipt["head"] == pr["headRefOid"] or committed))
        if matching:
            # Committed builds can advance the PR. Retain the original marker,
            # exact result and job only for this approval's matching attempt.
            result = receipt["result"]
            receipt = {**receipt, "result": result}
        else:
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
        _finish_delivery(path, receipt)
        return result

    return _each((item["number"] for item in pending), admit,
                 on_error=on_error, on_pending=on_pending)


def _revision_instant(value) -> datetime:
    """Migration cutoffs require an explicit timezone, never local time."""
    try:
        instant = datetime.fromisoformat(value) if isinstance(value, str) else None
        if instant is not None and instant.utcoffset() is not None:
            return instant.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        pass
    raise ValueError(f"invalid revision timestamp: {value!r}")


def _initialize_revision_feedback(path: Path, receipt: dict | None, repo: str,
                                  number: int, head: str, *, runner=queue._gh) -> dict | None:
    """After authorization, initialize legacy state before collection or delivery.

    `head` must be the current PR head, not the old receipt's head. Return a
    durable copy; read/validation/save failures leave the caller's state intact.
    No receipt means no legacy baseline. Initialized receipts never reread it.
    """
    if receipt is None:
        return None
    if not isinstance(receipt, dict):
        raise ValueError("revision feedback receipt must be an object")
    metadata = {"feedback_state_version", "consumed_feedback_ids", "feedback_ids",
                "feedback_migration", "legacy_snapshot"}
    if metadata.intersection(receipt):
        _validate_revision_feedback(receipt)
        return receipt
    if "saved_at" in receipt or "updated_at" in receipt:
        cutoff = _revision_instant(receipt["saved_at"] if "saved_at" in receipt
                                   else receipt["updated_at"])
        source = "receipt_saved_at"
    else:
        tip = json.loads(runner(["api", f"repos/{repo}/commits/{head}"]))
        cutoff = _revision_instant(tip["commit"]["committer"]["date"])
        source = "branch_tip"
    consumed = set()
    for kind, record in queue._pr_feedback_records(repo, number, runner=runner):
        identity = queue._feedback_id(kind, record)
        created = _revision_instant(record.get("submitted_at" if kind == "review" else "created_at"))
        if created <= cutoff:
            consumed.add(identity)
    updated = {**receipt, "feedback_state_version": 1,
               "consumed_feedback_ids": sorted(consumed), "legacy_snapshot": True,
               "feedback_migration": {"source": source,
                                      "cutoff": cutoff.isoformat().replace("+00:00", "Z")}}
    _validate_revision_feedback(updated)
    if "job" in receipt:
        with _job_receipt_lock(path, receipt) as current:
            # Remote inputs were fetched without locks. Any intervening change
            # invalidates this migration snapshot, including a released job.
            if current != receipt:
                raise _ReceiptChanged
            _save(path, updated)
    else:
        _save(path, updated)
    return updated


def _validate_revision_feedback(receipt: dict) -> None:
    """Validate an initialized ledger; legacy receipts must be migrated first."""
    if not isinstance(receipt, dict):
        raise ValueError("revision feedback receipt must be an object")
    version = receipt.get("feedback_state_version")
    if type(version) is not int or version != 1:
        raise ValueError("unsupported or missing revision feedback_state_version")
    if "feedback_migration" in receipt:
        migration = receipt["feedback_migration"]
        if (not isinstance(migration, dict)
                or migration.get("source") not in ("receipt_saved_at", "branch_tip")):
            raise ValueError("invalid revision feedback_migration")
        _revision_instant(migration.get("cutoff"))
    if "legacy_snapshot" in receipt and type(receipt["legacy_snapshot"]) is not bool:
        raise ValueError("invalid revision legacy_snapshot")
    legacy = receipt.get("legacy_snapshot", False)
    for field in ("consumed_feedback_ids", "feedback_ids"):
        if field == "feedback_ids" and legacy and field not in receipt:
            continue
        ids = receipt.get(field)
        if (not isinstance(ids, list) or any(
                not isinstance(identity, str)
                or not re.fullmatch(r"(?:conversation|review|inline):[1-9][0-9]*", identity)
                for identity in ids) or len(set(ids)) != len(ids)):
            raise ValueError(f"invalid revision {field}")
    feedback = receipt.get("feedback")
    if not isinstance(feedback, list) or any(not isinstance(block, str) for block in feedback):
        raise ValueError("invalid revision feedback")
    if "feedback_ids" in receipt and (legacy or len(receipt["feedback_ids"]) != len(feedback)):
        raise ValueError("revision feedback_ids must align with a non-legacy snapshot")


def _save_revision_snapshot(path: Path, previous: dict | None,
                            items: list[queue.FeedbackItem], **round_fields) -> dict:
    """Replace a finished round, preserving its ledger and unknown fields.

    Callers authorize and recover pending work first, then use this before
    dispatch (including synchronous runners). No receipt means no baseline.
    """
    if previous is not None:
        _validate_revision_feedback(previous)
    consumed = set(previous["consumed_feedback_ids"]) if previous is not None else set()
    selected = [item for item in items if item.id not in consumed]
    receipt = dict(previous) if previous is not None else {}
    for field in ("change", "head", "event", "round_id", "command", "job", "result",
                  "delivered", "feedback", "feedback_ids", "legacy_snapshot",
                  "revision_key_version", "round_job"):
        receipt.pop(field, None)
    receipt.update(round_fields)
    receipt.update(feedback_state_version=1, consumed_feedback_ids=sorted(consumed),
                   feedback_ids=[item.id for item in selected],
                   feedback=[item.block for item in selected])
    _validate_revision_feedback(receipt)
    _save(path, receipt)
    return receipt


def _acknowledge_revision_feedback(path: Path, receipt: dict) -> None:
    """After validated success (and spec push), consume only the saved IDs.

    Legacy snapshots have no exact IDs and add nothing beyond their baseline.
    A failed save leaves the caller's receipt unchanged so delivery cannot
    mistake an in-memory acknowledgement for durable consumption.
    """
    _validate_revision_feedback(receipt)
    updated = {**receipt, "consumed_feedback_ids": sorted(
        set(receipt["consumed_feedback_ids"]) | set(receipt.get("feedback_ids", [])))}
    _save(path, updated)
    receipt.update(updated)


def _build_revision_input(path: Path, feedback: list[str]) -> Path:
    """Finish and close each immutable attempt input before saving or dispatch."""
    source = (path.with_suffix(".jobs") / f"feedback-{uuid.uuid4().hex}.json").resolve()
    source.parent.mkdir(parents=True, exist_ok=True)
    with source.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(feedback) + "\n")
    return source


def _build_revision_args(receipt: dict, source: Path) -> list[str]:
    return ["revise", receipt["change"], "--feedback", str(source),
            "--feedback-format", "json", "--round-id", receipt["round_id"]]


def revise_pending(
    repo: Repo, labels: queue.Labels, state_dir: Path, *, runner=None, specloop_runner=None,
    git_runner=None, launcher=None, status_runner=None, on_error=None, on_pending=None, readiness=None,
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
        if not _safe_change(change):
            return None
        events = _latest_label_events(repo.name, number, {labels.revise}, runner=gh)
        if not _authorize_label_events(repo.name, number, events, runner=gh, kind="pr"):
            return None
        # Authorization and receipt identity use the same current application.
        # Even collecting a finished job must wait until its trigger is trusted.
        event = events[labels.revise]["id"]
        directory = repo.name.replace("/", "__")
        path = state_dir / "revisions" / directory / f"{number}.json"
        receipt = json.loads(path.read_text()) if path.exists() else None
        try:
            receipt = _initialize_revision_feedback(
                path, receipt, repo.name, number, pr["headRefOid"], runner=gh)
        except _ReceiptChanged:
            return _PENDING
        # A newer authorized event cannot replace a running job's snapshot.
        if receipt and not _collect(path, receipt, receipt.get("command", "propose")):
            return _PENDING
        previous = receipt
        # Finish successful work under its original event and marker first;
        # the newer application selects remaining IDs on a later poll.
        awaiting_success = (receipt and not receipt.get("delivered")
                            and receipt.get("result", {}).get("outcome") in ("drafted", "revised"))
        # Legacy results have no exact snapshot IDs. Deliver their saved
        # outcome before comparing live feedback or replacing their event,
        # including human-input outcomes that must retain the original marker.
        awaiting_legacy = (receipt and receipt.get("legacy_snapshot")
                           and not receipt.get("delivered") and "result" in receipt)
        selecting = not receipt or not (awaiting_success or awaiting_legacy) and (
            receipt["change"] != change or receipt["event"] != event
            or "result" not in receipt or receipt["result"]["outcome"] in (
                "needs_input", "rejected", "refused"))
        # Cleanup takes its own control locks; finish it before coordination.
        if selecting and not _recover_item_cleanup(path):
            return _PENDING
        recovered_attempt = False
        try:
            with ExitStack() as coordination:
                # Inspect only decisions that can replace or replay saved work.
                # Keep the boundary open through successor snapshot/start writes.
                if receipt and selecting and ("revision_key_version" in receipt
                                               or "round_job" in receipt
                                               or receipt.get("command") == "revise"
                                               and not receipt.get("legacy_snapshot")):
                    disposition = coordination.enter_context(
                        specloop_job.revision_attempt(path, receipt))
                    if disposition == "pending":
                        return _PENDING
                    if "result" not in receipt:
                        if "revision_key_version" in receipt and disposition in ("retry", "unattempted"):
                            status = specloop_client.call(["status"], runner=status_runner or specloop_runner)
                            if not isinstance(status.get("runs"), list):
                                raise specloop_client.SpecloopError("status returned no runs list")
                            admitted = next((run for run in status["runs"]
                                             if run["change"] == receipt["change"]), None)
                            if receipt["change"] != change or admitted is None or admitted.get("pr") != pr["url"]:
                                return None
                            source = _build_revision_input(path, receipt["feedback"])
                            if not _start(path, receipt, _build_revision_args(receipt, source),
                                          runner=specloop_runner, launcher=launcher):
                                return _PENDING
                            _save(path, receipt)
                            awaiting_success = True  # Deliver this attempt under its saved event.
                            recovered_attempt = True
                if receipt and not (awaiting_success or awaiting_legacy) and (
                        receipt["change"] != change or receipt["event"] != event or "result" not in receipt
                        or (receipt.get("delivered")
                            and receipt["result"]["outcome"] in ("needs_input", "rejected", "refused"))):
                    receipt = None
                git = partial(_bounded_run, runner=git_runner)
                if receipt is None or (not (awaiting_legacy or recovered_attempt) and receipt["result"]["outcome"] in (
                        "needs_input", "rejected", "refused")):
                    items = queue.pr_feedback_items(repo.name, number, runner=gh)
                    consumed = set(previous["consumed_feedback_ids"]) if previous else set()
                    items = [item for item in items if item.id not in consumed]
                    feedback = [item.block for item in items]
                    # The needs-human write may have landed despite a lost response,
                    # leaving delivered unset. A reviewer answer after clearing the
                    # label must retry drafting rather than replay the old questions.
                    if receipt and (feedback != receipt["feedback"]
                                    or [item.id for item in items] != receipt.get("feedback_ids")):
                        receipt = None
                if receipt is None:
                    receipt = {"change": change, "head": pr["headRefOid"], "event": event,
                               "feedback": feedback, "round_id": str(event)}
                    if not queue.has_instructions(feedback):
                        receipt["result"] = {"outcome": "empty"}
                        receipt = _save_revision_snapshot(path, previous, items, **receipt)
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
                            receipt.update(round_id=uuid.uuid4().hex, revision_key_version=1)
                            feedback_path = _build_revision_input(path, feedback)
                            receipt = _save_revision_snapshot(path, previous, items, **receipt)
                            if not _start(path, receipt, _build_revision_args(receipt, feedback_path),
                                          runner=specloop_runner, launcher=launcher):
                                return _PENDING
                    if "result" not in receipt:
                        receipt["command"] = "propose"
                        source = _require_manifest(repo, readiness).repo
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
                        receipt = _save_revision_snapshot(path, previous, items, **receipt)
                        if not _start(path, receipt, [
                            "propose", str(repo.manifest), "--change", change, "--branch", branch,
                            "--from-branch", fetched_ref,
                        ], idea=idea, runner=specloop_runner, launcher=launcher):
                            return _PENDING
                    _save(path, receipt)
        except specloop_job.ReceiptChanged:
            return _PENDING

        result = receipt["result"]
        if result["outcome"] == "drafted":
            source = _require_manifest(repo, readiness, new_work=False).repo
            if pr["headRefOid"] not in (receipt["head"], result["commit"]):
                raise DeliveryError(f"{branch} moved while its revision was drafted; review the branch before retrying")
            if not _recover_item_cleanup(path):
                return _PENDING
            pushed = git([
                "git", "-C", str(source), "push", "origin", f"{result['commit']}:refs/heads/{branch}",
            ], capture_output=True, text=True, check=False)
            if pushed.returncode:
                raise DeliveryError(f"could not push {branch}: {pushed.stderr.strip()}")
            _acknowledge_revision_feedback(path, receipt)
            body = (f"Revised the spec in {result['commit']} from this review feedback:\n\n"
                    + "\n\n---\n\n".join(receipt["feedback"]))
            if result.get("risks"):
                body += "\n\nFeasibility risks:\n" + "\n".join(f"- {risk}" for risk in result["risks"])
        elif result["outcome"] == "revised":
            # Saved results also cross this gate after a daemon restart.
            _validate(result, "revise", receipt)
            _acknowledge_revision_feedback(path, receipt)
            body = f"Queued review tasks for {change} in {result['commit']}; the build has resumed."
            if "revision_key_version" in receipt and (result["replayed"] or result["status"] != "running"):
                state = "finished" if result["status"] == "done" else result["status"]
                if state == "paused":
                    state += f" ({result['pause']['reason']})"
                action = "Acknowledged replay of review tasks" if result["replayed"] else "Queued review tasks"
                body = (f"{action} for {change} in {result['commit']}; "
                        f"the build was observed {state} when acknowledged.")
        elif result["outcome"] == "empty":
            body = "There is nothing to revise: no unconsumed review feedback is available."
        else:
            body = "Revision needs human input.\n\n" + "\n".join(
                "- " + (json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item)
                for field in ("questions", "contradictions", "problems", "risks", "reasons")
                for item in result.get(field, [])
            )
        if not _recover_item_cleanup(path):
            return _PENDING
        _comment(repo.name, number, change, body, runner=gh, action="revise", kind="pr",
                 round_id=receipt.get("round_id"))
        edit = ["pr", "edit", str(number), "--repo", repo.name]
        if result["outcome"] in ("drafted", "revised", "empty"):
            # Execution or comment delivery may have outlived the application.
            latest = _latest_label_events(repo.name, number, {labels.revise}, runner=gh)
            if latest.get(labels.revise, {}).get("id") == receipt["event"]:
                edit += ["--remove-label", labels.revise]
        else:
            edit += ["--add-label", labels.needs_human]
        # mutation-equivalent: edit has 5 arguments or 7 after a flag/value pair, so > 5 and > 6 agree.
        if len(edit) > 5:
            gh(edit)
        _finish_delivery(path, receipt)
        return result

    return _each((item["number"] for item in pending), revise,
                 on_error=on_error, on_pending=on_pending)


def _reconcile_merge_readiness(repo: Repo, labels: queue.Labels, state_dir: Path,
                               number: int, row: dict, publication: dict, *, runner=None) -> None:
    """Poll one PR snapshot; reuse only assessments bound to this publication/head."""
    gh = runner or queue._gh
    pr = json.loads(gh([
        "pr", "view", str(number), "--repo", repo.name, "--json",
        "state,labels,headRefName,headRefOid,isCrossRepository,url,body",
    ]))
    if (pr["state"] != "OPEN" or pr["isCrossRepository"] is not False
            or pr["headRefName"] != f"spec/{row['change']}" or pr["url"] != row["pr"]):
        return
    path = state_dir / "merge-readiness" / repo.name.replace("/", "__") / f"{number}.json"
    previous = _load_readiness(path)
    head = pr.get("headRefOid")
    head = head if isinstance(head, str) and head.strip() else ""
    identity = dict(change=row["change"], pr=row["pr"], run_head=row["head"], head=head,
                    publication_round=publication.get("round_id", [row[k] for k in ("change", "head", "pr")]))
    matching = previous is not None and all(previous.get(k) == v for k, v in identity.items())
    current = {label["name"] for label in pr["labels"]}
    consistent = previous is not None and current & {"merge:ready", "merge:blocked"} == {previous["label"]}
    if matching and consistent and previous is not None and previous["delivered"]:
        # A CI blocker is a snapshot taken while checks run (always, right after publication);
        # reassess it each poll until CI no longer blocks, or the label would never clear.
        if not any(reason.startswith("CI:") for reason in previous.get("reasons", [])):
            return
    if ("merge:ready" in current and (head != row["head"]
            or previous is not None and previous["head"] != head)):
        # Invalidate even an incomplete old intention before any assessment I/O.
        gh(["pr", "edit", str(number), "--repo", repo.name, "--remove-label", "merge:ready"])
        current.remove("merge:ready")
        pr["labels"] = [label for label in pr["labels"] if label["name"] != "merge:ready"]
    if matching and consistent and previous is not None and not previous["delivered"]:
        _deliver_readiness(repo, state_dir, number, row, publication, pr, previous, runner=gh)
        return
    assessment = _readiness_assessment(repo, labels, state_dir, number, row, pr, runner=gh,
                                       publication=publication)
    _deliver_readiness(repo, state_dir, number, row, publication, pr, assessment, runner=gh)


def _readiness_ci(repo: Repo, number: int, *, runner=None) -> list[str]:
    """Classify one ordinary PR-check snapshot through the string-returning runner."""
    try:
        raw = (runner or queue._gh)([
            "pr", "checks", str(number), "--repo", repo.name, "--json", "name,bucket,state",
        ])
    except Paused:
        raise
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return ["CI: checks unavailable; green CI could not be established"]
    try:
        checks = json.loads(raw)
    except (ValueError, TypeError):
        return ["CI: malformed check results"]
    if not isinstance(checks, list) or any(
            not isinstance(check, dict) or any(
                not isinstance(check.get(field), str) or not check[field].strip()
                for field in ("name", "bucket", "state")) for check in checks):
        return ["CI: malformed check results"]
    buckets = {check["bucket"] for check in checks}
    if "pass" in buckets and buckets <= {"pass", "skipping"}:
        return []
    return ["CI: checks are not green; require at least one pass and only pass or skipping buckets"]


def _readiness_assessment(repo: Repo, labels: queue.Labels, state_dir: Path, number: int,
                          row: dict, pr: dict, *, runner=None, git_runner=None,
                          publication: dict | None = None) -> dict:
    """Compose all conditions in stable order without delivering or modifying state."""
    head = pr.get("headRefOid")
    head = head if isinstance(head, str) and head.strip() else ""
    reasons = []
    if not head:
        reasons.append("head: PR head is missing or unknown")
    elif head != row.get("head"):
        reasons.append(f"head: PR head {head} does not match run head {row.get('head') or '(unknown)'}")
    reasons.extend(_readiness_ci(repo, number, runner=runner))
    reasons.extend(_readiness_evidence(row, git_runner=git_runner))
    current = {label["name"] for label in pr["labels"]}
    reasons.extend(_readiness_revision(repo, labels, state_dir, number, row, current))
    reasons.extend(_readiness_human_checks(row, pr.get("body"), git_runner=git_runner,
                                           publication_checks=(publication or {}).get("checks")))
    label = "merge:blocked" if reasons else "merge:ready"
    advisory = (f"Advisory snapshot for {head or '(unknown)'}. Recheck CI and current main when landing; "
                "this label does not authorize merge.")
    explanation = ("\n".join(f"- {reason}" for reason in reasons) if reasons else
                   "PR head matches the run; CI checks are green; section evidence passed with zero "
                   "timeouts (or an allowed measurement exemption); no build revision is pending; "
                   "human checks are empty or explicitly post-deploy.")
    return {"head": head, "run_head": row.get("head"), "label": label, "reasons": reasons,
            "body": f"Merge readiness: `{label}`.\n\n{advisory}\n\n{explanation}"}


def _load_readiness(path: Path) -> dict | None:
    """Reject damaged delivery state instead of overwriting its recovery evidence."""
    if not path.exists():
        return None
    receipt = json.loads(path.read_text())
    _validate_readiness(receipt)
    return receipt


def _validate_readiness(receipt: dict) -> None:
    if (not isinstance(receipt, dict)
            or any(not isinstance(receipt.get(key), str) or not receipt[key].strip()
                   for key in ("change", "pr", "run_head", "body"))
            or not _safe_change(receipt["change"])
            or not isinstance(receipt.get("head"), str)
            or type(receipt.get("delivered")) is not bool
            or receipt.get("label") not in ("merge:ready", "merge:blocked")
            or not isinstance(receipt.get("reasons"), list)
            or any(not isinstance(reason, str) or not reason.strip() for reason in receipt["reasons"])
            or (receipt["label"] == "merge:blocked") != bool(receipt["reasons"])
            or (receipt["label"] == "merge:ready" and receipt["head"] != receipt["run_head"])):
        raise ValueError("invalid merge-readiness receipt")
    round_id = receipt.get("publication_round")
    if not (isinstance(round_id, str) and round_id.strip()
            or round_id == [receipt[key] for key in ("change", "run_head", "pr")]):
        raise ValueError("invalid merge-readiness publication identity")


def _deliver_readiness(repo: Repo, state_dir: Path, number: int, row: dict,
                       publication: dict, pr: dict, assessment: dict, *, runner=None) -> None:
    """Deliver against the caller's fresh PR snapshot, never a saved intention alone.

    Eligibility, head-change removal before assessment and polling budgets belong
    to the caller. This receipt is independent of publication and revision state.
    """
    from .daemon import paused

    path = state_dir / "merge-readiness" / repo.name.replace("/", "__") / f"{number}.json"
    previous = _load_readiness(path) or {}
    head = pr.get("headRefOid")
    head = head if isinstance(head, str) and head.strip() else ""
    if assessment["head"] != head or assessment["run_head"] != row["head"]:
        raise ValueError("stale merge-readiness assessment; reassess the observed head")
    identity = [row["change"], row["head"], row["pr"]]
    if ([publication.get(key) for key in ("change", "head", "pr")] != identity
            or publication.get("delivered") is not True
            or publication.get("result", {}).get("outcome") != "published"):
        raise ValueError("merge-readiness requires a matching delivered publication")
    chosen = {key: assessment[key] for key in ("head", "run_head", "label", "reasons", "body")}
    chosen.update(change=row["change"], pr=row["pr"],
                  publication_round=publication.get("round_id", identity))
    receipt = {**previous, **chosen, "delivered": False}
    _validate_readiness(receipt)
    current = {label["name"] for label in pr["labels"]}
    selected = receipt["label"]
    opposite = "merge:blocked" if selected == "merge:ready" else "merge:ready"
    if (previous.get("delivered") is True and all(previous.get(k) == v for k, v in chosen.items())
            and selected in current and opposite not in current):
        return
    _save(path, receipt)  # Atomic intent precedes every external effect.
    gh = runner or queue._gh
    if opposite in current:
        gh(["pr", "edit", str(number), "--repo", repo.name, "--remove-label", opposite])
    if selected not in current:
        gh(["pr", "edit", str(number), "--repo", repo.name, "--add-label", selected])
    _comment(repo.name, number, row["change"], receipt["body"], runner=gh, action="approval",
             kind="pr", round_id="merge-readiness")
    if paused():
        raise Paused()
    _save(path, {**receipt, "delivered": True})


PUBLICATION_HEADER = re.compile(r"all tasks landed at [0-9a-f]+; human checks after publishing:")


def _readiness_human_checks(row: dict, body: object, *, git_runner=None,
                            publication_checks: object = None) -> list[str]:
    """Apply the literal checklist/spec line rules, using only committed run-head specs.

    A PR specloop opened carries the list in its body. A front-door PR's body is the
    spec PR's; its list is the publication's `checks`, the run's ready_to_publish detail.
    """
    unknown = ["human checks: unknown checklist or scenario annotation"]
    if not isinstance(body, str):
        return unknown
    lines = body.splitlines()
    starts = [i for i, line in enumerate(lines) if line == "## Checks a human must make"]
    if len(starts) == 1:
        start = starts[0] + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("#")), len(lines))
        checks = [line for line in lines[start:end] if line.strip()]
    elif not starts and isinstance(publication_checks, str):
        checks = [line for line in publication_checks.splitlines() if line.strip()
                  and not PUBLICATION_HEADER.fullmatch(line.strip())
                  and not line.startswith("Next: `specloop publish ")]
    else:
        return unknown
    if not checks or checks == ["(none)"]:
        return []
    ids = []
    for line in checks:
        if not line.startswith(("- [ ] ", "- [x] ")):
            return unknown
        parts = line.split("`")
        citations = [part for part in parts[1::2] if "/" in part]
        if (len(parts) % 2 != 1 or any(not part for part in parts[1::2])
                or len(citations) != 1 or not re.fullmatch(
                    r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*){2}", citations[0])):
            return unknown
        ids.append(citations[0])
    try:
        change, head = row["change"], row["head"]
        if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
            return unknown
        run = run_state.Run.for_change(change, require_existing=True)
        if run.state.change != change or run.state.pr != row["pr"]:
            return unknown

        def git(*args):
            return (git_runner or run_git)(["git", "--git-dir", str(run.dir / "repo.git"), *args],
                                          timeout=POLL_COMMAND_TIMEOUT, capture_output=True, text=True, check=True).stdout

        def slug(name):
            return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

        prefix = f"openspec/changes/{change}/specs/"
        paths = git("ls-tree", "-r", "--name-only", "-z", head, "--", prefix).split("\0")
        scenarios: dict[str, list[bool]] = {}
        for path in paths:
            relative = path.removeprefix(prefix).split("/")
            if len(relative) != 2 or relative[1] != "spec.md":
                continue
            spec = git("show", f"{head}^{{commit}}:{path}").splitlines()
            requirement = ""
            for i, line in enumerate(spec):
                if line.startswith("### Requirement: "):
                    requirement = slug(line.removeprefix("### Requirement: "))
                elif line.startswith("#### Scenario: ") and requirement:
                    sid = "/".join((slug(relative[0]), requirement, slug(line.removeprefix("#### Scenario: "))))
                    end = next((j for j in range(i + 1, len(spec)) if spec[j].startswith("#")), len(spec))
                    scenarios.setdefault(sid, []).append("<!-- nightshift:post-deploy -->" in spec[i + 1:end])
                elif line.startswith("#"):
                    requirement = ""
        return [f"human checks: outstanding or unknown post-deploy annotation for `{sid}`"
                for sid in dict.fromkeys(ids) if scenarios.get(sid) != [True]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        return unknown


def _readiness_revision(repo: Repo, labels: queue.Labels, state_dir: Path, number: int,
                        row: dict, current: set[str]) -> list[str]:
    """Observe revision state without authorization, collection, migration or delivery."""
    if labels.revise in current:
        return [f"revision: pending trigger `{labels.revise}`"]
    path = state_dir / "revisions" / repo.name.replace("/", "__") / f"{number}.json"
    try:
        try:
            receipt = json.loads(path.read_text())
        except FileNotFoundError:
            return []
        if (not isinstance(receipt, dict) or not isinstance(receipt.get("change"), str)
                or not _safe_change(receipt["change"])):
            raise ValueError("invalid receipt identity")
        if receipt["change"] != row["change"]:
            return []
        command = receipt.get("command")
        if command == "propose":
            return []
        # An empty-feedback round dispatches no command or job.
        if ("command" not in receipt and "job" not in receipt
                and receipt.get("delivered") is True and receipt.get("result") == {"outcome": "empty"}):
            return []
        if command != "revise":
            raise ValueError("unknown revision command")
        result = receipt.get("result")
        if (receipt.get("delivered") is not True or not isinstance(result, dict)
                or result.get("outcome") not in ("revised", "refused")
                or "error" in result
                or "version" in result and (type(result["version"]) is not int or result["version"] != 1)
                or result.get("outcome") == "revised" and not isinstance(result.get("commit"), str)
                or result.get("outcome") == "revised" and not result.get("commit")):
            return ["revision: pending or unknown build-revision result/delivery"]
        if "job" in receipt:
            job = receipt["job"]
            if not isinstance(job, str) or not re.fullmatch(r"[0-9a-f]{32}", job):
                raise ValueError("invalid attached job")
            directory = path.parent / f"{number}.jobs" / job
            if (directory / "stop.json").exists():
                return ["revision: attached job is stopped or stopping"]
            collected = json.loads((directory / "collected.json").read_text())
            if (not isinstance(collected, dict) or not isinstance(collected.get("collected_at"), str)
                    or not collected["collected_at"]):
                raise ValueError("uncollected or unknown attached job")
        return []
    except (OSError, ValueError, KeyError, TypeError):
        return ["revision: unknown local build-revision state"]


def _readiness_evidence(row: dict, *, git_runner=None) -> list[str]:
    """Read-only local evidence assessment; an empty reason list permits readiness."""
    source = "run identity/state.json"
    try:
        change, head, pr = row["change"], row["head"], row["pr"]
        if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
            raise ValueError("missing or invalid run head")
        run = run_state.Run.for_change(change, require_existing=True)
        saved = run.state
        if not isinstance(pr, str) or not pr or saved.change != change or saved.pr != pr:
            raise ValueError("saved change/PR does not match status")
        if (not isinstance(saved.evidenced, list)
                or any(type(n) is not int or n < 1 for n in saved.evidenced)):
            raise ValueError("invalid evidenced sections")
        for field in (saved.section_base, saved.evidence_summary):
            if not isinstance(field, dict) or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in field.items()):
                raise ValueError("invalid section_base/evidence_summary")
        source = "manifest.toml"
        manifest = manifests.load(run.manifest_path)
        if manifest.change != change or manifest.branch != f"spec/{change}":
            raise ValueError("manifest identity does not match status")

        def git(*args):
            return (git_runner or run_git)(["git", "--git-dir", str(run.dir / "repo.git"), *args],
                                          timeout=POLL_COMMAND_TIMEOUT, capture_output=True, text=True, check=True).stdout

        source = "committed tasks.md/repo.git"
        tasks_path = f"openspec/changes/{change}/tasks.md"
        document = git("show", f"{head}^{{commit}}:{tasks_path}")
        sections = list(dict.fromkeys(t.section for t in run_tasks.parse(document)))
        headings = [int(m[1]) for line in document.splitlines() if (m := run_tasks.SECTION.match(line))]
        if not sections or sections != headings:
            raise ValueError("missing, empty or duplicate task sections")
        source = "events.jsonl"
        latest = {}
        events = (run.dir / source).read_text()
        if events and not events.endswith("\n"):
            raise ValueError("incomplete final event line")
        for line in events.splitlines():
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("event"), str) or not event["event"]:
                raise ValueError("invalid event record")
            if event["event"] == "evidence":
                n = event.get("section")
                if type(n) is not int or n < 1:
                    raise ValueError("invalid evidence section")
                latest[n] = event
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, subprocess.SubprocessError) as exc:
        return [f"evidence: check {source} ({type(exc).__name__}: {exc})"]

    reasons = []
    for index, n in enumerate(sections):
        prefix = f"evidence section {n}: "
        event = latest.get(n)
        if event is None:
            start = saved.section_base.get(str(n), "")
            end = saved.section_base.get(str(sections[index + 1]), "") if index + 1 < len(sections) else head
            try:
                if not all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", ref) for ref in (start, end)):
                    raise ValueError("missing section boundaries")
                git("merge-base", "--is-ancestor", start, end)
                git("merge-base", "--is-ancestor", end, head)
                # Disabling rename detection exposes both sides of cross-boundary renames.
                paths = set(git("diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", start, end, "--").split("\0")) - {"", tasks_path}
                if paths and all(p.startswith("tests/") for p in paths):
                    continue
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            reasons.append(prefix + "missing event; no bounded test-only diff proof")
            continue
        failed = []
        if event.get("passed") is not True:
            failed.append("passed must be true")
        if event.get("unmeasured", False) is not False:
            failed.append("unmeasured or invalid measurement flag")
        score = event.get("score")
        skip = "mutation: skipped (no mutable lines added under mutation_paths)"
        if not (isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= 1):
            if not ("score" in event and score is None and skip in saved.evidence_summary.get(str(n), "").splitlines()):
                failed.append("score missing, invalid or unmeasured without no-mutable-lines summary")
        timeouts = event.get("timeouts")
        if type(timeouts) is not int or timeouts < 0:
            failed.append("timeouts missing or invalid")
        elif timeouts:
            failed.append(f"{timeouts} timeouts")
        if event.get("uncovered") != []:
            failed.append("uncovered scenarios missing, invalid or nonempty")
        if n not in saved.evidenced:
            failed.append("saved evidence completion missing")
        reasons.extend(prefix + reason for reason in failed)
    return reasons


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
            if (pause["reason"] == "human_checks" and receipt["delivered"] is True
                    and receipt.get("result", {}).get("outcome") == "published"):
                _reconcile_merge_readiness(repo, labels, state_dir, int(admission_path.stem),
                                           run, receipt, runner=gh)
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


def _approval_event(repo: str, number: int, label: str, *, runner=None, forge: Forge | None = None) -> int:
    """Identify this approval even when removal succeeded but its response was lost.

    A later label application permits a new admission with the same PR head
    (for example after a manifest correction). Reads must fail closed so an
    unavailable event history cannot masquerade as renewed approval.
    """
    return _label_events(repo, number, {label}, runner=runner, forge=forge)[label]


def _label_events(repo: str, number: int, labels: set[str], *, runner=None,
                  forge: Forge | None = None) -> dict[str, int]:
    """Retain the ID-only receipt interface for current label applications."""
    return {label: event["id"] for label, event in
            _latest_label_events(repo, number, labels, runner=runner, forge=forge).items()}


def _latest_label_events(repo: str, number: int, labels: set[str], *, runner=None,
                         forge: Forge | None = None) -> dict[str, dict]:
    """Confirm current applications and retain their actor evidence.

    GitHub returns events in chronological page order. Select the final event
    before validating it, so an older application cannot authorize a newer one.
    """
    forge = forge if forge is not None else GitHubForge(runner)
    pages = forge.label_event_pages(repo, number)
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


def _valid_actor_login(login) -> TypeGuard[str]:
    # GitHub usernames, including enterprise managed users (alice_acme) and
    # app bot logins. Never accept URL syntax, whitespace, or the placeholder
    # GitHub uses for deleted accounts.
    return (isinstance(login, str) and login.lower() != "ghost"
            and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9]|[-_](?=[A-Za-z0-9])){0,38}(?:\[bot\])?",
                             login) is not None)


def _membership_status(repo: str, login: str, *, runner=None,
                       forge: Forge | None = None, organization=False) -> int:
    """Read a membership endpoint, preserving failures other than HTTP 404.

    queue._gh returns stdout on success but puts only stderr in GhError on
    failure. gh reports a negative membership check as `gh: ... (HTTP 404)`.
    A missing status or an unexpected response must remain a lookup failure.
    """
    forge = forge if forge is not None else GitHubForge(runner)
    endpoint = (f"orgs/{quote(repo, safe='')}/members/{quote(login, safe='')}" if organization
                else f"repos/{repo}/collaborators/{quote(login, safe='')}")
    try:
        response = (forge.organization_member_response(repo, login) if organization
                    else forge.collaborator_response(repo, login)).stdout
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


def _label_actor_association(repo: str, event: dict, *, runner=None,
                             forge: Forge | None = None) -> str | None:
    """Resolve only the label actor using current positive membership evidence."""
    actor = event.get("actor")
    login = actor.get("login") if isinstance(actor, dict) else None
    if not _valid_actor_login(login):
        return None

    forge = forge if forge is not None else GitHubForge(runner)
    repository = forge.repository(repo)
    owner = repository.get("owner") if isinstance(repository, dict) else None
    if (not isinstance(owner, dict) or not _valid_actor_login(owner.get("login"))
            or owner.get("type") not in ("User", "Organization")):
        raise RuntimeError(f"unconfirmed repository owner for {repo}; retry next poll")

    association = None
    if owner["type"] == "User" and owner["login"].lower() == login.lower():
        association = "OWNER"
    elif _membership_status(repo, login, forge=forge) == 204:
        association = "COLLABORATOR"
    elif owner["type"] == "Organization" and _membership_status(
            owner["login"], login, forge=forge, organization=True) == 204:
        association = "MEMBER"
    return association if association in queue.TRUSTED_ASSOCIATIONS else None


def _authorize_label_events(repo: str, number: int, events: dict[str, dict], *, runner=None,
                            kind="issue", forge: Forge | None = None) -> bool:
    """Authorize every selected application, explaining each definitive denial.

    Resolve all actors before writing: a failed lookup leaves the item on the
    retry path, rather than partially explaining an unconfirmed decision.
    Recheck membership on each poll; neither receipts nor comments grant trust.
    """
    forge = forge if forge is not None else GitHubForge(runner)
    rejected = [
        (label, event) for label, event in events.items()
        if _label_actor_association(repo, event, forge=forge) not in queue.TRUSTED_ASSOCIATIONS
    ]
    for label, event in rejected:
        _comment_label_rejection(repo, number, label, event, forge=forge, kind=kind)
    return not rejected


def _comment_label_rejection(repo: str, number: int, label: str, event: dict, *, runner=None,
                             kind="issue", forge: Forge | None = None) -> None:
    """Create one explanation per application, without editing it on later polls.

    The draft namespace opts into queue._gh's ambiguous-create reconciliation.
    Only that transport retry may PATCH an existing comment with the same body.
    """
    marker = f"<!-- nightshift:draft:label-authorization-{number}:{event['id']} -->"
    forge = forge if forge is not None else GitHubForge(runner)
    pages = forge.comment_pages(repo, number)
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
    forge.create_comment(repo, number, kind, body)


_save = specloop_job.save


def _validate(result: dict, command: str, receipt: dict) -> None:
    outcomes = {"propose": ("drafted", "needs_input", "rejected"),
                "admit": ("admitted", "refused"), "revise": ("revised", "refused")}[command]
    if result.get("outcome") not in outcomes:
        raise specloop_client.SpecloopError(f"{command} returned no recognized outcome")
    if command == "propose" and result["outcome"] == "drafted" and (
            not result.get("commit") or result.get("branch") not in (
                f"spec/{receipt['change']}", f"refs/specloop/proposals/{receipt['change']}")):
        raise specloop_client.SpecloopError("propose returned no commit on the requested branch")
    if command == "revise" and result["outcome"] == "revised" and not result.get("commit"):
        raise specloop_client.SpecloopError("revise returned no landed commit")
    if command == "revise" and result["outcome"] == "revised" and "revision_key_version" in receipt:
        key = receipt.get("round_id")
        if (type(receipt["revision_key_version"]) is not int or receipt["revision_key_version"] != 1
                or not isinstance(key, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key)
                or result.get("round_id") != key):
            raise specloop_client.SpecloopError("revise returned a missing or mismatched round identity")
        if not isinstance(result["commit"], str) or not result["commit"].strip():
            raise specloop_client.SpecloopError("revise returned no landed commit")
        if type(result.get("replayed")) is not bool:
            raise specloop_client.SpecloopError("revise returned an invalid replay flag")
        if result.get("status") not in ("running", "paused", "aborted", "done"):
            raise specloop_client.SpecloopError("revise returned an invalid observed status")
        pause = result.get("pause")
        if "pause" not in result or pause is None and result["status"] == "paused":
            raise specloop_client.SpecloopError("revise returned no observed pause")
        # Abort can retain the preceding pause. Validate its shape without
        # imposing a new control-state transition or rejecting future fields.
        if pause is not None:
            if (not isinstance(pause, dict) or not isinstance(pause.get("reason"), str)
                    or not pause["reason"].strip()
                    or any(field in pause and not isinstance(pause[field], str)
                           for field in ("task", "detail"))
                    or type(pause.get("resume_at", 0)) not in (int, float)):
                raise specloop_client.SpecloopError("revise returned an invalid observed pause")
            try:
                finite = math.isfinite(pause.get("resume_at", 0))
            except OverflowError:
                finite = False
            if not finite:
                raise specloop_client.SpecloopError("revise returned an invalid observed pause")


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


class _ReceiptChanged(Exception):
    """Defer a cached job-bearing receipt invalidated by another writer."""


@contextmanager
def _job_receipt_lock(path: Path, receipt: dict):
    """Refresh under job control, then the release lock if it already exists."""
    directory = path.parent / f"{path.stem}.jobs" / receipt["job"]
    with specloop_job.control_lock(directory):
        try:
            release = path.with_suffix(".release.lock").open("r")
        except FileNotFoundError:
            release = None
        try:
            if release is not None:
                fcntl.flock(release, fcntl.LOCK_EX)
            current = json.loads(path.read_text())
            if not isinstance(current, dict):
                raise ValueError("job receipt must be an object")
            if current.get("job") != receipt["job"]:
                raise _ReceiptChanged
            yield current
        finally:
            if release is not None:
                release.close()


def _recover_item_cleanup(path: Path) -> bool:
    """Recover only this eligible, authorized item; defer unsafe replacement."""
    from .daemon import paused

    def check_pause():
        if paused():
            raise Paused()

    try:
        root = path.with_suffix(".jobs")
        specloop_job._cleanup_file(path)
        receipt = specloop_job._read_object(path) if path.exists() else {}
        job_id = receipt.get("job")
        candidate = (receipt.get("delivered") and isinstance(job_id, str)
                     and re.fullmatch(r"[0-9a-f]{32}", job_id))
        if not root.exists() and not candidate:
            return True  # No state creation merely to discover cleanup work.
        if candidate:
            check_pause()
            # Validate under coordination before a new round can replace the
            # sole surviving proof of delivery, even if local selection lost it.
            diagnostics = specloop_job.compact_delivered(path, receipt, prepare_only=True)
            for diagnostic in diagnostics:
                log.warning("%s", diagnostic)
            if diagnostics:
                return False
        specloop_job._cleanup_file(root / "cleanup.json")  # Reject linked roots.
        if root.is_dir():
            for directory in root.iterdir():
                journal = directory / "cleanup.json"
                if directory.is_dir() and journal.exists():
                    specloop_job._cleanup_file(journal)
                    if specloop_job._read_object(journal).get("complete") is True:
                        continue
                    check_pause()
                    for diagnostic in specloop_job.resume_cleanup(path, directory.name):
                        log.warning("%s", diagnostic)
    except (OSError, ValueError, TypeError) as exc:
        log.warning("Cannot recover cleanup at %s: %s", path, exc)
        return False
    return True


def _finish_delivery(path: Path, receipt: dict) -> None:
    """Persist completed notification before compacting its matching attempt."""
    from .daemon import paused

    if paused():
        raise Paused()
    if "job" not in receipt:
        _save(path, {**receipt, "delivered": True})
        receipt["delivered"] = True
        return
    try:
        with _job_receipt_lock(path, receipt) as current:
            if current != receipt:
                raise _ReceiptChanged
            _save(path, {**current, "delivered": True})
            receipt["delivered"] = True
    except _ReceiptChanged:
        return  # A release or replacement cannot inherit this delivery.
    if not re.fullmatch(r"[0-9a-f]{32}", receipt["job"]):
        log.debug("No durable attempt identity to compact at %s", path)
        return
    if paused():
        raise Paused()
    for diagnostic in specloop_job.compact_delivered(path, receipt):
        log.warning("%s", diagnostic)


def _collect(path: Path, receipt: dict, command: str) -> bool:
    if "job" not in receipt:
        return True
    specloop_job.reap()
    try:
        with _job_receipt_lock(path, receipt) as current:
            receipt.clear()
            receipt.update(current)
            if "result" in receipt:
                return True
            result = specloop_job._collect_locked(path, receipt)
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
    except _ReceiptChanged:
        return False
    except specloop_job.JobOverdue as exc:
        raise DeliveryError(str(exc)) from exc


def _comment(repo: str, number: int, change: str, body: str, *, runner=None,
             action="draft", kind="issue", round_id: str | None = None,
             forge: Forge | None = None) -> None:
    """Upsert one delivery round; queue._gh also reconciles internal retries.

    Legacy receipts without a round ID retain their original marker
    when retried. New receipts save the ID before attempting delivery.
    """
    suffix = f":{round_id}" if round_id is not None else ""
    marker = f"<!-- nightshift:{action}:{change}{suffix} -->"
    body = f"{queue.SELF_COMMENT_PREFIX} {body}\n\n{marker}"
    forge = forge if forge is not None else GitHubForge(runner)
    pages = forge.comment_pages(repo, number)
    comments = [comment for page in pages for comment in page]
    previous = next((c for c in comments if marker in (c.get("body") or "")), None)
    if previous:
        forge.update_comment(repo, previous['id'], body)
    else:
        forge.create_comment(repo, number, kind, body)


def _deliver(repo: Repo, labels: queue.Labels, number: int, change: str,
             receipt: dict | None, current: set[str], *, pr: int | None = None,
             runner, git_runner=None, readiness=None) -> None:
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
        source = _require_manifest(repo, readiness, new_work=False).repo
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

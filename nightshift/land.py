"""Explicit operator entry point for landing an enrolled, reviewed PR.

Human checks, run completion, and bounded CI/base synchronization precede a
head-conditioned merge, whose result must be confirmed by GitHub.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from html import escape
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Literal, Protocol
from urllib.error import HTTPError
from urllib.parse import SplitResult, unquote, urlsplit
from urllib.request import build_opener

from . import config, queue
from .forge import ForgeError
from .forge.gitea import GiteaForge
from .forge.http import GiteaHTTPError, GiteaTransport


Runner = Callable[..., subprocess.CompletedProcess[str]]
READ_TIMEOUT = 30
FINISH_TIMEOUT = 300
CI_POLL_INTERVAL = 10
PR_FIELDS = "number,url,state,isDraft,headRefOid,baseRefName,body"
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+")
_PR_URL = re.compile(r"https://github\.com/([^/]+/[^/]+)/pull/([0-9]+)/?")
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_TASK_LINE = re.compile(r"^[ \t>]*[-*+] \[(?P<state>[ xX])\] (?P<text>[^\r\n]*)", re.MULTILINE)


class Refusal(Exception):
    """An operational failure that is safe to show to the operator."""


class _CIDeadlineExpired(Refusal):
    """Distinguish shared-budget expiry from command and validation failures."""


class _UncertainBranchUpdate(Refusal):
    """A single dispatched update needs read-only provenance reconciliation."""


@dataclass(frozen=True)
class Identity:
    repo: str
    number: int
    origin: str = "https://github.com"
    forge_name: str | None = None

    @property
    def url(self) -> str:
        route = "pull" if self.forge_name is None else "pulls"
        return f"{self.origin}/{self.repo}/{route}/{self.number}"


@dataclass(frozen=True)
class PullRequest:
    identity: Identity
    head: str
    draft: bool
    body: str


@dataclass(frozen=True)
class HumanCheck:
    text: str
    checked: bool


@dataclass(frozen=True)
class CheckSource:
    """Keep the original text intact, including prose, markers, and line endings."""

    body: str
    comment_id: int | None = None

    @property
    def reference(self) -> str:
        return "PR body" if self.comment_id is None else f"publication comment {self.comment_id}"

    @property
    def checks(self) -> tuple[HumanCheck, ...]:
        # Deliberately count examples in fences, HTML, and blockquotes. Markdown
        # parsing could hide a real check, so only the design's line rule applies.
        return tuple(HumanCheck(match["text"], match["state"] != " ")
                     for match in _TASK_LINE.finditer(self.body))

    def completed(self) -> CheckSource:
        def tick(match: re.Match[str]) -> str:
            if match["state"] != " ":
                return match[0]
            offset = match.start("state") - match.start()
            return match[0][:offset] + "x" + match[0][offset + 1:]

        return CheckSource(_TASK_LINE.sub(tick, self.body), self.comment_id)


@dataclass(frozen=True)
class CheckSnapshot:
    """Expected head and post-edit sources for later revalidation.

    Keep the authenticated actor even when every box was already ticked, so
    attestation can recover independently of the source edits. Attestation uses
    the pre-finish head; validated finish advances the head for subsequent gates.
    """

    head: str
    sources: tuple[CheckSource, ...]
    actor: str | None


@dataclass(frozen=True)
class LandingRun:
    change: str
    pr: str
    head: str
    status: str
    pause_reason: str | None


@dataclass
class Operations:
    """Single process and clock boundary, shared by command helpers.

    The clocks are injected for bounded polling. Identity reads do
    not wait or retry; an unresolved read fails closed. In particular, never
    retry a comment POST here: only marker reconciliation can resolve a lost
    response, and that belongs to the attestation helper, even in production.
    """

    runner: Runner
    sleep: Callable[[float], None]
    monotonic: Callable[[], float]
    deadline: float | None = None
    forge: LandingForge = field(default_factory=lambda: GitHubLandingForge())
    gitea_forges: tuple[config.ForgeConfig, ...] = ()

    def remaining(self) -> float:
        """One absolute budget, retained across CI rounds and update observation."""
        if self.deadline is None:
            return math.inf
        remaining = self.deadline - self.monotonic()
        if remaining <= 0:
            raise _CIDeadlineExpired("shared CI deadline expired; inspect checks and rerun land")
        return remaining

    def poll_sleep(self) -> None:
        self.sleep(min(CI_POLL_INTERVAL, self.remaining()))
        self.remaining()

    def run(
        self, argv: list[str], *, stage: str, timeout: float = READ_TIMEOUT,
        allowed_returncodes: tuple[int, ...] | None = (0,),
    ) -> subprocess.CompletedProcess[str]:
        timeout = min(timeout, self.remaining())
        try:
            result = self.runner(
                argv, capture_output=True, text=True, check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise Refusal(f"{stage}: command timed out after {timeout}s") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise Refusal(f"{stage}: {exc}") from exc
        # A command that used up its budget cannot authorize further progress,
        # even if it returned a successful result just as the deadline elapsed.
        self.remaining()
        if allowed_returncodes is not None and result.returncode not in allowed_returncodes:
            detail = (result.stderr or result.stdout or "command failed").strip()
            raise Refusal(f"{stage}: {detail} (exit {result.returncode})")
        return result

    def _read_json(self, argv: list[str], *, stage: str) -> Any:
        result = self.run(argv, stage=stage)
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise Refusal(f"{stage}: malformed JSON response") from exc
        return value

    def read_json(self, argv: list[str], *, stage: str) -> dict[str, Any]:
        value = self._read_json(argv, stage=stage)
        if not isinstance(value, dict):
            raise Refusal(f"{stage}: expected a JSON object")
        return value

    def read_json_array(self, argv: list[str], *, stage: str) -> list[Any]:
        value = self._read_json(argv, stage=stage)
        if not isinstance(value, list):
            raise Refusal(f"{stage}: expected a JSON array")
        return value


class LandingForge(Protocol):
    """Named landing transport operations; policy remains in the gate helpers.

    Receive the current Operations on every call so deadline replacements apply
    to every dispatch. There is no arbitrary-command operation on this boundary.
    """

    def resolve_repo(self, operations: Operations) -> dict[str, Any]: ...

    def read_pr(self, identity: Identity, operations: Operations) -> dict[str, Any]: ...

    def read_main(self, identity: Identity, operations: Operations) -> dict[str, Any]: ...

    def request_update(
        self, identity: Identity, expected_head: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]: ...

    def read_commit(self, identity: Identity, head: str, operations: Operations) -> dict[str, Any]: ...

    def read_comments(self, identity: Identity, operations: Operations, *, stage: str) -> list[Any]: ...

    def read_actor(self, operations: Operations) -> dict[str, Any]: ...

    def trusted_comment(self, comment: dict[str, Any]) -> bool: ...

    def edit_source(
        self, identity: Identity, source: CheckSource, operations: Operations,
    ) -> subprocess.CompletedProcess[str]: ...

    def create_attestation(self, identity: Identity, body: str, operations: Operations) -> dict[str, Any]: ...

    def edit_attestation(
        self, identity: Identity, comment_id: int, body: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]: ...

    def incorporates_base(self, identity: Identity, head: str, base: str, operations: Operations) -> bool: ...

    def checks(
        self, identity: Identity, head: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]: ...

    def merge(
        self, identity: Identity, head: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]: ...

    def confirm_merge(self, identity: Identity, operations: Operations) -> dict[str, Any]: ...


class GitHubLandingForge:
    """The existing GitHub transcript, executed by the caller's injected runner."""

    def resolve_repo(self, operations: Operations) -> dict[str, Any]:
        return operations.read_json(
            ["gh", "repo", "view", "--json", "nameWithOwner"],
            stage="repository resolution",
        )

    def read_pr(self, identity: Identity, operations: Operations) -> dict[str, Any]:
        return operations.read_json(
            ["gh", "pr", "view", str(identity.number), "--repo", identity.repo,
             "--json", PR_FIELDS],
            stage=f"PR read for {identity.url}",
        )

    def read_main(self, identity: Identity, operations: Operations) -> dict[str, Any]:
        return operations.read_json(
            ["gh", "api", f"repos/{identity.repo}/git/ref/heads/main"],
            stage=f"read existing main branch in {identity.repo}",
        )

    def request_update(
        self, identity: Identity, expected_head: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]:
        return operations.run(
            ["gh", "api", "--method", "PUT",
             f"repos/{identity.repo}/pulls/{identity.number}/update-branch",
             "-f", f"expected_head_sha={expected_head}"],
            stage="branch update", allowed_returncodes=None,
        )

    def read_commit(self, identity: Identity, head: str, operations: Operations) -> dict[str, Any]:
        return operations.read_json(
            ["gh", "api", f"repos/{identity.repo}/commits/{head}"],
            stage="branch update parent inspection",
        )

    def read_comments(self, identity: Identity, operations: Operations, *, stage: str) -> list[Any]:
        return operations.read_json_array(
            ["gh", "api", f"repos/{identity.repo}/issues/{identity.number}/comments",
             "--paginate", "--slurp"],
            stage=stage,
        )

    def read_actor(self, operations: Operations) -> dict[str, Any]:
        return operations.read_json(["gh", "api", "user"], stage="authenticated actor lookup")

    @staticmethod
    def trusted_comment(comment: dict[str, Any]) -> bool:
        return (isinstance(comment.get("author_association"), str)
                and queue.trusted(comment, key="author_association"))

    def edit_source(
        self, identity: Identity, source: CheckSource, operations: Operations,
    ) -> subprocess.CompletedProcess[str]:
        endpoint = (f"repos/{identity.repo}/pulls/{identity.number}"
                    if source.comment_id is None
                    else f"repos/{identity.repo}/issues/comments/{source.comment_id}")
        return operations.run(
            ["gh", "api", "--method", "PATCH", endpoint, "-f", f"body={source.body}"],
            stage=f"record human checks in {source.reference}; rerun land to recover",
        )

    def create_attestation(self, identity: Identity, body: str, operations: Operations) -> dict[str, Any]:
        return operations.read_json(
            ["gh", "api", "--method", "POST",
             f"repos/{identity.repo}/issues/{identity.number}/comments", "-f", f"body={body}"],
            stage="attestation create",
        )

    def edit_attestation(
        self, identity: Identity, comment_id: int, body: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]:
        return operations.run(
            ["gh", "api", "--method", "PATCH", f"repos/{identity.repo}/issues/comments/{comment_id}",
             "-f", f"body={body}"],
            stage=f"attestation update for comment {comment_id}; rerun land to recover",
        )

    def compare(self, identity: Identity, head: str, base: str, operations: Operations) -> dict[str, Any]:
        return operations.read_json(
            ["gh", "api", f"repos/{identity.repo}/compare/{base}...{head}"],
            stage="initial base ancestry check",
        )

    def incorporates_base(self, identity: Identity, head: str, base: str, operations: Operations) -> bool:
        comparison = self.compare(identity, head, base, operations)
        ancestor = comparison.get("merge_base_commit")
        status = comparison.get("status")
        if (status not in ("ahead", "identical", "behind", "diverged")
                or not isinstance(ancestor, dict) or not isinstance(ancestor.get("sha"), str)
                or not _SHA.fullmatch(ancestor["sha"])
                or (ancestor["sha"] == base) != (status in ("ahead", "identical"))):
            raise Refusal("initial base ancestry check: malformed or inconsistent comparison")
        return ancestor["sha"] == base

    def checks(
        self, identity: Identity, head: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]:
        return operations.run(
            ["gh", "pr", "checks", str(identity.number), "--repo", identity.repo,
             "--json", "name,bucket,state,link"],
            stage="CI checks", allowed_returncodes=(0, 1, 8),
        )

    def merge(
        self, identity: Identity, head: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]:
        return operations.run(
            ["gh", "pr", "merge", str(identity.number), "--repo", identity.repo,
             "--merge", "--match-head-commit", head],
            stage="merge request",
        )

    def confirm_merge(self, identity: Identity, operations: Operations) -> dict[str, Any]:
        return operations.read_json(
            ["gh", "pr", "view", str(identity.number), "--repo", identity.repo,
             "--json", "number,url,state"],
            stage="merged-state confirmation",
        )


class _BudgetOpener:
    """Apply the current logical operation's budget to every HTTP attempt."""

    def __init__(self, opener: Any, operations: Operations):
        self.opener, self.operations = opener, operations

    def add_handler(self, handler: Any) -> None:
        self.opener.add_handler(handler)

    def open(self, request: Any, *, timeout: float) -> Any:
        try:
            response = self.opener.open(request, timeout=min(timeout, self.operations.remaining()))
        except Exception as error:
            # A failed dispatch also consumes time. Do not back off or retry
            # after expiry, and close an HTTP error stream if expiry masks it.
            try:
                self.operations.remaining()
            except Refusal:
                if isinstance(error, HTTPError):
                    error.close()
                raise
            raise
        try:
            self.operations.remaining()
        except Refusal:
            response.close()
            raise
        return response


class _LandingGiteaEvidence(GiteaForge):
    def _record(self, row: dict) -> dict:
        # Intake normalizes absent bodies to empty text. Landing must not turn
        # unreadable evidence into an apparent absence of checks or markers.
        if not isinstance(row.get("body"), str):
            raise ForgeError("Invalid Gitea comment body", transient=False)
        return super()._record(row)


class GiteaLandingForge:
    """Landing-specific reads at the configured root, using only the human token."""

    def __init__(self, definition: config.ForgeConfig, token: str, *, opener: Any = None):
        self.definition = definition
        self._token = token
        self._opener = build_opener() if opener is None else opener

    def _transport(self, operations: Operations) -> GiteaTransport:
        # Bind each call, not the initial unbounded Operations: finish replaces
        # it with a shared deadline, and merge later restores ordinary timeouts.
        return GiteaTransport(
            self.definition.url, self._token,
            opener=_BudgetOpener(self._opener, operations), guard=operations.remaining,
        )

    def _read_json(self, path: str, operations: Operations, *, stage: str) -> dict[str, Any]:
        try:
            value = self._transport(operations).get_json(path)
        except (GiteaHTTPError, ValueError):
            raise Refusal(f"{stage}: Gitea request failed") from None
        finally:
            operations.remaining()
        if not isinstance(value, dict):
            raise Refusal(f"{stage}: expected a JSON object")
        return value

    def read_pr(self, identity: Identity, operations: Operations) -> dict[str, Any]:
        data = self._read_json(
            f"/api/v1/repos/{identity.repo}/pulls/{identity.number}", operations, stage="PR read",
        )
        if (_returned_identity(data.get("html_url"), identity) != identity
                or type(data.get("number")) is not int or data["number"] != identity.number):
            raise Refusal("Gitea returned a contradictory PR identity")
        if data.get("state") != "open":
            raise Refusal("Gitea PR must be open")
        base, head = data.get("base"), data.get("head")
        if not isinstance(base, dict) or base.get("ref") != "main":
            raise Refusal("Gitea PR must target main")
        sha = head.get("sha") if isinstance(head, dict) else None
        title, body = data.get("title"), data.get("body")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha) or not isinstance(title, str):
            raise Refusal("malformed Gitea PR metadata: expected a full head SHA and title")
        if not isinstance(body, str):
            raise Refusal("malformed Gitea PR body: expected text")
        return {"number": identity.number, "url": identity.url, "state": "OPEN",
                "baseRefName": "main", "headRefOid": sha, "body": body,
                "isDraft": title.startswith(self.definition.wip_prefix)}

    def read_main(self, identity: Identity, operations: Operations) -> dict[str, Any]:
        data = self._read_json(
            f"/api/v1/repos/{identity.repo}/branches/main", operations, stage="read existing main branch",
        )
        commit = data.get("commit")
        sha = commit.get("id") if isinstance(commit, dict) else None
        if data.get("name") != "main" or not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise Refusal("cannot establish an existing main branch: malformed Gitea branch response")
        return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}}

    def _adapter(self, operations: Operations) -> GiteaForge:
        def guard() -> None:
            operations.remaining()

        return _LandingGiteaEvidence(
            self.definition.url, self._token, trusted_logins=self.definition.trusted_logins,
            bot_login=self.definition.bot_login, wip_prefix=self.definition.wip_prefix,
            opener=_BudgetOpener(self._opener, operations), guard=guard,
        )

    def read_comments(self, identity: Identity, operations: Operations, *, stage: str) -> list[Any]:
        try:
            return self._adapter(operations).comment_pages(identity.repo, identity.number)
        except (ForgeError, ValueError):
            raise Refusal(f"{stage}: Gitea comment evidence is unreadable") from None
        finally:
            operations.remaining()

    def trusted_comment(self, comment: dict[str, Any]) -> bool:
        # Landing intentionally permits listed publication bots. Neither the
        # server association nor the adapter's feedback trust grants authority.
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        return (isinstance(login, str) and login.casefold() in
                {name.casefold() for name in self.definition.trusted_logins})

    def read_actor(self, operations: Operations) -> dict[str, Any]:
        data = self._read_json("/api/v1/user", operations, stage="authenticated actor lookup")
        login = data.get("login")
        if (not self.trusted_comment({"user": data})
                or not isinstance(login, str)
                or login.casefold() == self.definition.bot_login.casefold()):
            raise Refusal("authenticated actor lookup: require a listed non-bot human login")
        return data

    def _write_body(
        self, method: str, path: str, body: str, operations: Operations, *, stage: str,
    ) -> Any:
        # Human landing writes must bypass the adapter's bot ownership helpers.
        # The transport attempts writes once; only attestation policy reconciles.
        try:
            _, _, response = self._transport(operations).request(method, path, json_body={"body": body})
            return response
        except (GiteaHTTPError, ValueError):
            raise Refusal(f"{stage}: Gitea request failed; rerun land to recover") from None
        finally:
            operations.remaining()

    def edit_source(
        self, identity: Identity, source: CheckSource, operations: Operations,
    ) -> subprocess.CompletedProcess[str]:
        endpoint = (f"/api/v1/repos/{identity.repo}/pulls/{identity.number}"
                    if source.comment_id is None
                    else f"/api/v1/repos/{identity.repo}/issues/comments/{source.comment_id}")
        self._write_body("PATCH", endpoint, source.body, operations,
                         stage=f"record human checks in {source.reference}")
        return subprocess.CompletedProcess([], 0, "", "")

    def create_attestation(self, identity: Identity, body: str, operations: Operations) -> dict[str, Any]:
        response = self._write_body(
            "POST", f"/api/v1/repos/{identity.repo}/issues/{identity.number}/comments",
            body, operations, stage="attestation create",
        )
        if not isinstance(response, dict):
            raise Refusal("attestation create: incomplete comment acknowledgement")
        return response

    def edit_attestation(
        self, identity: Identity, comment_id: int, body: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]:
        self._write_body("PATCH", f"/api/v1/repos/{identity.repo}/issues/comments/{comment_id}",
                         body, operations, stage=f"attestation update for comment {comment_id}")
        return subprocess.CompletedProcess([], 0, "", "")

    def read_commit(self, identity: Identity, head: str, operations: Operations) -> dict[str, Any]:
        try:
            commit = self._adapter(operations).commit(identity.repo, head)
        except (ForgeError, ValueError):
            raise Refusal("Gitea commit inspection: unreadable commit facts") from None
        finally:
            operations.remaining()
        if commit["sha"] != head:
            raise Refusal("Gitea commit inspection: mismatched commit SHA")
        return commit

    def incorporates_base(self, identity: Identity, head: str, base: str, operations: Operations) -> bool:
        pending, visited = [head], set()
        while pending:
            operations.remaining()
            sha = pending.pop()
            if sha == base:
                return True
            if sha in visited:
                continue
            visited.add(sha)
            try:
                commit = self.read_commit(identity, sha, operations)
            except _CIDeadlineExpired:
                raise
            except Refusal as exc:
                raise Refusal(f"initial base ancestry check: {exc}") from exc
            pending.extend(parent["sha"] for parent in commit["parents"])
        return False

    def request_update(
        self, identity: Identity, expected_head: str, operations: Operations,
    ) -> subprocess.CompletedProcess[str]:
        # The controller checked H and captured B immediately before this call.
        # Gitea has no expected-head CAS; exact ordered parents gate acceptance.
        try:
            self._transport(operations).request(
                "POST", f"/api/v1/repos/{identity.repo}/pulls/{identity.number}/update",
                query={"style": "merge"},
            )
        except GiteaHTTPError as exc:
            if ((exc.status is None and exc.transient)
                    or (exc.status is not None and (200 <= exc.status < 300 or 500 <= exc.status < 600))):
                raise _UncertainBranchUpdate("branch update: uncertain Gitea response") from None
            raise Refusal("branch update: Gitea request rejected") from None
        except ValueError:
            raise Refusal("branch update: invalid Gitea request") from None
        finally:
            operations.remaining()
        return subprocess.CompletedProcess([], 0, "", "")

    def _unavailable(self, *args: Any, **kwargs: Any) -> Any:
        # Subsequent implementation tasks add CI and merge operations.
        # Until then no inherited GitHub operation may authorize a Gitea action.
        raise Refusal("Gitea landing operation is not yet implemented")

    resolve_repo = checks = _unavailable
    merge = confirm_merge = _unavailable


def read_confirmed_remote_head(pr: str, read_json: Callable[[list[str]], Any]) -> str:
    """Factory's standalone identity read, retaining its caller's 20s boundary."""
    data = read_json(["gh", "pr", "view", pr, "--json", "url,headRefOid"])
    if (not isinstance(data, dict) or data.get("url") != pr
            or not isinstance(data.get("headRefOid"), str) or not _SHA.fullmatch(data["headRefOid"])):
        raise Refusal("fresh PR identity is unreadable or contradictory")
    return data["headRefOid"].lower()


ConfirmedCommand = Literal["pr", "status", "read", "mutation", "finish", "unknown"]


def classify_confirmed_command(
    argv: list[str], *, repo: str, number: str, change: str, head: str,
) -> ConfirmedCommand:
    """Recognize only exact pre-finish shapes for the confirmed GitHub target.

    Classification grants no permission: Factory must still inspect the actual
    responses and revalidate all five confirmation fields before each mutation.
    """
    view = ["gh", "pr", "view", number, "--repo", repo, "--json", PR_FIELDS]
    status = [sys.executable, "-m", "nightshift.cli", "status", "--json"]
    finish = [sys.executable, "-m", "nightshift.cli", "finish", change, "--expect-head", head]
    if argv == view:
        return "pr"
    if argv == status:
        return "status"
    if argv in (["gh", "api", f"repos/{repo}/git/ref/heads/main"],
                ["gh", "api", f"repos/{repo}/issues/{number}/comments", "--paginate", "--slurp"],
                ["gh", "api", "user"]):
        return "read"
    if len(argv) == len(finish) and argv[:-1] == finish[:-1] and argv[-1].lower() == head:
        return "finish"
    if (len(argv) == 7 and argv[:3] == ["gh", "api", "--method"]
            and argv[5] == "-f" and argv[6].startswith("body=")):
        endpoint = argv[4]
        if (argv[3] == "POST" and endpoint == f"repos/{repo}/issues/{number}/comments"
                or argv[3] == "PATCH" and (
                    endpoint == f"repos/{repo}/pulls/{number}"
                    or re.fullmatch(rf"repos/{re.escape(repo)}/issues/comments/[1-9][0-9]*", endpoint)
                    is not None)):
            return "mutation"
    return "unknown"


def _positive_timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return seconds


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="python -m nightshift.land",
        description="Land a reviewed PR in an enrolled repository.",
        allow_abbrev=False,
    )
    result.add_argument("pr", help="GitHub.com HTTPS pull URL or positive PR number")
    result.add_argument("--repo", help="repository as owner/name (inferred for a bare number if omitted)")
    result.add_argument("--config", type=Path, default=config.DEFAULT_PATH, help="enrollment config path")
    result.add_argument("--checks-done", action="store_true", help="assert completion of listed human checks")
    result.add_argument("--timeout", type=_positive_timeout, default=1800, metavar="SECONDS",
                        help="overall CI wait budget (positive seconds; default: 1800)")
    return result


def _repo_name(value: Any) -> str:
    if (not isinstance(value, str) or not _REPO.fullmatch(value)
            or value.split("/")[1] in {".", ".."}):
        raise Refusal("invalid repository identity: expected owner/name")
    return value.lower()


def _gitea_repo_name(value: Any) -> str:
    """Validate literal API path components without GitHub's owner grammar."""
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value)
            or any(part in {".", ".."} for part in value.split("/"))):
        raise Refusal("invalid repository identity: expected owner/name")
    return value.casefold()


def _number(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise Refusal("invalid PR identity: expected a positive PR number") from exc
    if number <= 0:
        raise Refusal("invalid PR identity: expected a positive PR number")
    return number


def _url_identity(value: Any) -> Identity:
    match = _PR_URL.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise Refusal("invalid PR identity: expected a GitHub.com HTTPS pull URL or positive PR number")
    return Identity(_repo_name(match[1]), _number(match[2]))


def resolve_identity(pr: str, repo: str | None, operations: Operations) -> Identity:
    explicit_repo = _repo_name(repo) if repo is not None else None
    if re.fullmatch(r"[0-9]+", pr):
        number = _number(pr)
        if explicit_repo is None:
            data = operations.forge.resolve_repo(operations)
            explicit_repo = _repo_name(data.get("nameWithOwner"))
        return Identity(explicit_repo, number)
    identity = _url_identity(pr)
    if explicit_repo is not None and explicit_repo != identity.repo:
        raise Refusal(f"PR URL repository {identity.repo} contradicts --repo {explicit_repo}")
    return identity


def _safe_url(value: Any) -> SplitResult:
    """Reject syntax that URL parsers normalize away before matching authority."""
    try:
        if (not isinstance(value, str) or not value
                or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 or c == "\\"
                       for c in value)):
            raise ValueError
        parts = urlsplit(value)
        decoded_path = unquote(parts.path, errors="strict")
        if (parts.scheme not in ("https", "http") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.netloc.endswith(":") or parts.port == 0
                or "?" in value or "#" in value
                or re.search(r"%(?![0-9a-fA-F]{2})", value)
                or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 or c == "\\"
                       for c in decoded_path)
                or any(p in (".", "..") for p in decoded_path.split("/"))):
            raise ValueError
        return parts
    except ValueError:
        raise Refusal("invalid PR identity: unsafe or malformed URL") from None


def _gitea_url_identity(value: Any, origin: str, forge_name: str) -> Identity:
    parts = _safe_url(value)
    root = _safe_url(origin)
    def authority(parsed: SplitResult) -> tuple[str, str | None, int]:
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

    prefix = root.path.rstrip("/")
    match = re.fullmatch(re.escape(prefix) + r"/([^/]+/[^/]+)/pulls/([0-9]+)/?", parts.path)
    if authority(parts) != authority(root) or match is None:
        raise Refusal("invalid PR identity: URL contradicts configured Gitea origin or prefix")
    _gitea_repo_name(match[1])
    return Identity(match[1], _number(match[2]), origin.rstrip("/"), forge_name)


def _returned_identity(value: Any, expected: Identity) -> Identity:
    if expected.forge_name is None:
        return _url_identity(value)
    parsed = _gitea_url_identity(value, expected.origin, expected.forge_name)
    if parsed.repo.casefold() == expected.repo.casefold():
        parsed = replace(parsed, repo=expected.repo)
    return parsed


def resolve_target(
    pr: str, repo: str | None, cfg: config.Config, operations: Operations,
) -> tuple[Identity, config.ForgeConfig]:
    # Preserve GitHub's number inference and exact URL grammar and argv.
    try:
        hostname = urlsplit(pr).hostname
    except ValueError:
        raise Refusal("invalid PR identity: malformed URL") from None
    if re.fullmatch(r"[0-9]+", pr) or hostname == "github.com":
        identity = resolve_identity(pr, repo, operations)
        enrollment = enrolled_repo(cfg, identity)
        return identity, cfg.forge_for(enrollment)
    parts = _safe_url(pr)
    definitions = [f for f in cfg.forges if f.kind == "gitea"
                   and urlsplit(f.url).hostname == parts.hostname]
    if len(definitions) != 1:
        raise Refusal("invalid PR identity: absent or ambiguous configured Gitea hostname")
    definition = definitions[0]
    identity = _gitea_url_identity(pr, definition.url, definition.name)
    if repo is not None and _gitea_repo_name(repo) != identity.repo.casefold():
        raise Refusal("PR URL repository contradicts --repo")
    enrollment = enrolled_repo(cfg, identity)
    return replace(identity, repo=enrollment.name), definition


def load_land_token(definition: config.ForgeConfig) -> str:
    """File presence wins, even for empty values; never export loaded secrets."""
    variable = definition.land_token_env
    path = Path.home() / ".config" / "nightshift" / "land.env"
    try:
        # Path.exists() may hide permission errors on newer Python versions.
        # Only a genuinely missing file permits the process-only fallback.
        try:
            path.stat()
        except FileNotFoundError:
            pass
        values = config.parse_env_file(path)
    except (OSError, ValueError):
        raise Refusal(f"human credential loading: cannot read land.env for {variable}") from None
    token = values[variable] if variable in values else os.environ.get(variable, "")
    if not token or "\r" in token or "\n" in token:
        raise Refusal(f"human credential loading: missing or invalid {variable}")
    return token


def load_config(path: Path) -> config.Config:
    try:
        return config.load(path)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise Refusal(f"cannot read configuration {path}: {exc}") from exc


def enrolled_repo(cfg: config.Config, identity: Identity) -> config.Repo:
    validate_repo = _repo_name if identity.forge_name is None else _gitea_repo_name
    matches = [repo for repo in cfg.repos
               if ((identity.forge_name is None and cfg.forge_for(repo).kind == "github")
                    or (identity.forge_name is not None and cfg.forge_for(repo).kind == "gitea"
                        and repo.forge == identity.forge_name))
               and validate_repo(repo.name) == identity.repo.casefold()]
    if not matches:
        raise Refusal(f"repository {identity.repo} is not enrolled in configuration")
    if len(matches) != 1:
        raise Refusal(f"ambiguous enrollment for {identity.repo}: {len(matches)} matching repositories")
    return matches[0]


def read_pr(identity: Identity, operations: Operations) -> PullRequest:
    data = operations.forge.read_pr(identity, operations)
    if (_returned_identity(data.get("url"), identity) != identity
            or type(data.get("number")) is not int or data["number"] != identity.number):
        raise Refusal("GitHub returned a contradictory PR identity")
    if data.get("state") != "OPEN":
        raise Refusal(f"PR must be open; current state: {data.get('state', 'missing')}")
    if data.get("baseRefName") != "main":
        raise Refusal(f"PR must target main; current base: {data.get('baseRefName', 'missing')}")
    head = data.get("headRefOid")
    draft = data.get("isDraft")
    if not isinstance(head, str) or not _SHA.fullmatch(head) or not isinstance(draft, bool):
        raise Refusal("malformed PR metadata: expected a full head SHA and draft status")
    body = data.get("body")
    if not isinstance(body, str):
        raise Refusal("malformed PR body: expected text")
    return PullRequest(identity, head, draft, body)


def read_main(identity: Identity, operations: Operations) -> str:
    data = operations.forge.read_main(identity, operations)
    obj = data.get("object")
    if (data.get("ref") != "refs/heads/main" or not isinstance(obj, dict)
            or obj.get("type") != "commit" or not isinstance(obj.get("sha"), str)
            or not _SHA.fullmatch(obj["sha"])):
        raise Refusal("cannot establish an existing main branch: malformed ref response")
    return obj["sha"]


def update_branch(
    identity: Identity, expected_head: str, operations: Operations,
) -> tuple[PullRequest, str]:
    """Return the exact update head and its retained, immediately pre-request base.

    The caller supplies the shared CI deadline and must obtain fresh CI for the
    returned head. A queued acknowledgement is not provenance: only the observed
    commit's ordered parents can authorize advancing the expected head. Never
    retry the update, even when its response is lost.
    """
    if operations.deadline is None:
        raise Refusal("branch update requires the shared CI deadline")
    pr = read_pr(identity, operations)
    if pr.head != expected_head:
        raise Refusal(f"PR head changed before branch update: expected {expected_head}, "
                      f"found {pr.head}; review the current head and rerun land")
    if pr.draft:
        raise Refusal("PR became draft before branch update")
    base = read_main(identity, operations)
    lost_response: Refusal | None = None
    try:
        result = operations.forge.request_update(identity, expected_head, operations)
    except _UncertainBranchUpdate as exc:
        lost_response = exc
    except Refusal as exc:
        if not isinstance(exc.__cause__, (OSError, subprocess.SubprocessError)):
            raise
        lost_response = exc
    else:
        if result.returncode:
            stdout, stderr = result.stdout or "", result.stderr or ""
            detail = (stderr or stdout or "request rejected").strip()
            failure = Refusal(f"branch update: {detail} (exit {result.returncode})")
            # Definite failures override every uncertain-response exception.
            if re.search(r"\bHTTP(?:/[0-9.]+)?\s+[1-5][0-9]{2}\b", stderr, re.IGNORECASE):
                raise failure
            for stream in (stdout, stderr):
                try:
                    json.loads(stream)
                except ValueError:
                    continue
                raise failure
            if result.returncode == 4:
                raise failure
            transport_loss = re.search(
                r"\b(EOF|connection reset|connection lost|broken pipe|timed out|timeout)\b",
                detail, re.IGNORECASE,
            )
            if not ("unexpected end of JSON input" in stderr
                    or (stdout == "" and stderr == "") or transport_loss):
                raise failure
            lost_response = failure

    try:
        while True:
            observed = read_pr(identity, operations)
            if observed.draft:
                raise Refusal("PR became draft during branch update")
            if observed.head != expected_head:
                commit = operations.forge.read_commit(identity, observed.head, operations)
                parents = commit.get("parents")
                if (commit.get("sha") != observed.head or not isinstance(parents, list)
                        or len(parents) != 2
                        or any(not isinstance(parent, dict) for parent in parents)
                        or [parent.get("sha") for parent in parents] != [expected_head, base]):
                    raise Refusal(f"branch update provenance refused for {observed.head}: "
                                  f"expected exactly ordered parents [{expected_head}, {base}]; "
                                  "review the current head and rerun land")
                return observed, base
            operations.poll_sleep()
    except Refusal as exc:
        if lost_response is not None:
            raise Refusal(f"{lost_response}; branch update reconciliation failed: {exc}") from exc
        raise


def discover_run(identity: Identity, operations: Operations) -> LandingRun | None:
    """A missing match is meaningful only after every row is readable.

    Validate the version-1 fields used for classification and lifecycle gates;
    status presentation fields (tasks, context, action) are not landing inputs.
    """
    data = operations.read_json(
        [sys.executable, "-m", "nightshift.cli", "status", "--json"], stage="run discovery",
    )
    if type(data.get("version")) is not int or data["version"] != 1:
        raise Refusal("run discovery: unsupported status version; expected version 1")
    if "error" in data:
        raise Refusal(f"run discovery: status error: {data['error']}")
    rows = data.get("runs")
    if not isinstance(rows, list):
        raise Refusal("run discovery: malformed status; expected a runs list")
    matches = []
    for index, row in enumerate(rows):
        stage = f"run discovery: status row {index + 1}"
        if not isinstance(row, dict):
            raise Refusal(f"{stage}: malformed run object")
        if "error" in row:
            raise Refusal(f"{stage} ({row.get('change', 'unknown')}): error: {row['error']}")
        change, pr, head = row.get("change"), row.get("pr"), row.get("head")
        status, pause = row.get("status"), row.get("pause")
        if not isinstance(change, str) or not change.strip():
            raise Refusal(f"{stage}: malformed or missing change name")
        if not isinstance(pr, str):
            raise Refusal(f"{stage} ({change}): malformed or missing PR URL")
        try:
            run_identity = None
            if pr:
                parsed = urlsplit(pr)
                configured_http = False
                if parsed.scheme == "http":
                    for definition in operations.gitea_forges:
                        if urlsplit(definition.url).hostname == parsed.hostname:
                            _gitea_url_identity(pr, definition.url, definition.name)
                            configured_http = True
                            break
                if ((not pr.startswith("https://") and not configured_http) or not parsed.hostname
                        or re.search(r"[\s\x00-\x1f\x7f\\]", pr)):
                    raise ValueError("invalid HTTPS URL")
                # Accessing port also validates malformed or out-of-range ports.
                _ = parsed.port
                if parsed.hostname == urlsplit(identity.url).hostname:
                    run_identity = _returned_identity(pr, identity)
        except (Refusal, ValueError) as exc:
            raise Refusal(f"{stage} ({change}): malformed PR URL: {pr!r}") from exc
        if not isinstance(head, str) or not _SHA.fullmatch(head):
            raise Refusal(f"{stage} ({change}): malformed or missing head SHA")
        if status not in ("running", "paused", "done", "aborted"):
            raise Refusal(f"{stage} ({change}): malformed run state: {status!r}")
        if ("pause" not in row or (pause is not None and (
                not isinstance(pause, dict) or not isinstance(pause.get("reason"), str)
                or not pause["reason"].strip())) or (status == "paused" and pause is None)):
            raise Refusal(f"{stage} ({change}): malformed pause for state {status}")
        if run_identity == identity:
            matches.append(LandingRun(change, identity.url, head, status,
                                      pause["reason"] if pause is not None else None))
    if len(matches) > 1:
        raise Refusal(f"run discovery: multiple matching runs for {identity.url}: "
                      + ", ".join(run.change for run in matches))
    return matches[0] if matches else None


def validate_run_eligibility(pr: PullRequest, run: LandingRun | None) -> None:
    if run is None:
        if pr.draft:
            raise Refusal("direct PR is draft; mark it ready before retrying land")
        return
    if run.status == "done" or (run.status == "paused" and run.pause_reason == "human_checks"):
        return
    state = run.status + (f" ({run.pause_reason})" if run.pause_reason else "")
    raise Refusal(f"run {run.change} is {state}; retry land only when paused at human_checks "
                  "or done")


def read_comments(
    identity: Identity, operations: Operations, *, stage: str = "publication comment discovery",
) -> list[dict[str, Any]]:
    """Slurp all REST pages into one JSON document; never use a partial history."""
    pages = operations.forge.read_comments(identity, operations, stage=stage)
    comments = []
    for page in pages:
        if not isinstance(page, list):
            raise Refusal(f"{stage}: malformed comment page")
        for comment in page:
            if not isinstance(comment, dict) or not isinstance(comment.get("body"), str):
                raise Refusal(f"{stage}: malformed comment body")
            comments.append(comment)
    return comments


def select_publication(
    comments: list[dict[str, Any]], change: str, *,
    trusted: Callable[[dict[str, Any]], bool] = GitHubLandingForge.trusted_comment,
) -> CheckSource | None:
    # One optional round token, with literal marker whitespace/delimiters.
    # Escape the change name so neither regex syntax nor a similarly named
    # change can select another run's publication.
    marker = re.compile(r"<!-- nightshift:publish:" + re.escape(change)
                        + r"(?::[^\s:<>]+)? -->")
    candidates = []
    for comment in comments:
        body = comment["body"]
        if (not trusted(comment)
                or not body.startswith(queue.SELF_COMMENT_PREFIX)
                or not marker.search(body)):
            continue
        comment_id, created_at = comment.get("id"), comment.get("created_at")
        if type(comment_id) is not int or comment_id <= 0 or not isinstance(created_at, str):
            raise Refusal("publication comment discovery: malformed publication ID or creation time")
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise Refusal("publication comment discovery: malformed publication creation time") from exc
        if created.tzinfo is None:
            raise Refusal("publication comment discovery: publication creation time has no timezone")
        candidates.append((created, comment_id, body))
    if not candidates:
        return None
    _, comment_id, body = max(candidates, key=lambda candidate: candidate[:2])
    return CheckSource(body, comment_id)


def read_check_sources(
    pr: PullRequest, run: LandingRun | None, operations: Operations,
) -> tuple[CheckSource, ...]:
    sources = [CheckSource(pr.body)]
    if run is not None:
        publication = select_publication(read_comments(pr.identity, operations), run.change,
                                         trusted=operations.forge.trusted_comment)
        if publication is not None:
            sources.append(publication)
    return tuple(sources)


def validate_human_checks(sources: tuple[CheckSource, ...], *, checks_done: bool) -> None:
    checks = [(source.reference, check) for source in sources for check in source.checks]
    if not checks:
        print("No human checks listed")
        return
    remaining = [f"{reference}: {check.text}" for reference, check in checks if not check.checked]
    if remaining and not checks_done:
        raise Refusal("unticked human checks remain:\n" + "\n".join(remaining))


def read_actor(operations: Operations) -> str:
    data = operations.forge.read_actor(operations)
    login = data.get("login")
    if not isinstance(login, str) or not login or any(char.isspace() for char in login):
        raise Refusal("authenticated actor lookup: malformed or missing login")
    return login


def prepare_human_checks(
    pr: PullRequest, sources: tuple[CheckSource, ...], operations: Operations, *, checks_done: bool,
) -> CheckSnapshot:
    validate_human_checks(sources, checks_done=checks_done)
    if not checks_done or not any(source.checks for source in sources):
        return CheckSnapshot(pr.head, sources, None)

    # Resolve identity before the first write, including on retries where the
    # boxes are complete but a subsequent attestation still needs recording.
    actor = read_actor(operations)
    completed = []
    for source in sources:
        updated = source.completed()
        if updated.body != source.body:
            # One full-source PATCH, with raw text fields preserving newlines.
            # Stop on failure without rollback or blind retry: the next operator
            # invocation reads actual remote state, including ambiguous writes.
            operations.forge.edit_source(pr.identity, updated, operations)
        completed.append(updated)
    return CheckSnapshot(pr.head, tuple(completed), actor)


def select_attestation(
    comments: list[dict[str, Any]], marker: str, *,
    trusted: Callable[[dict[str, Any]], bool] = GitHubLandingForge.trusted_comment,
) -> int | None:
    """An untrusted match is uncertainty, never permission to create another."""
    matches = [comment for comment in comments if marker in comment["body"]]
    if not matches:
        return None
    if len(matches) != 1 or matches[0]["body"].count(marker) != 1:
        raise Refusal("attestation lookup: duplicate marker matches; resolve duplicates before retrying")
    comment = matches[0]
    if (not trusted(comment)
            or not comment["body"].startswith(queue.SELF_COMMENT_PREFIX)):
        raise Refusal("attestation lookup: untrusted marker match; inspect the comment before retrying")
    comment_id = comment.get("id")
    if type(comment_id) is not int or comment_id <= 0:
        raise Refusal("attestation lookup: malformed comment ID")
    return comment_id


def record_attestation(
    identity: Identity, snapshot: CheckSnapshot, operations: Operations,
) -> None:
    if snapshot.actor is None or not any(source.checks for source in snapshot.sources):
        return
    marker = f"<!-- nightshift:land-checks:{identity.number}:{snapshot.head} -->"
    lines = [f"{queue.SELF_COMMENT_PREFIX} Human checks", "",
             f"@{snapshot.actor} recorded completion through `land`.",
             f"PR: {identity.url}", f"Head: `{snapshot.head}`", ""]
    for source in snapshot.sources:
        if source.checks:
            # Checklist text is quoted content, not comment metadata. Escape
            # HTML so copied publication/attestation markers cannot make this
            # comment supersede a publication or corrupt marker reconciliation.
            lines.extend([f"Source: {source.reference}",
                          *[f"- [x] {escape(check.text, quote=False)}" for check in source.checks], ""])
    body = "\n".join([*lines, marker])

    def lookup() -> int | None:
        return select_attestation(read_comments(
            identity, operations, stage="attestation lookup",
        ), marker, trusted=operations.forge.trusted_comment)

    comment_id = lookup()
    if comment_id is None:
        try:
            response = operations.forge.create_attestation(identity, body, operations)
            # Even exit zero can leave an unusable acknowledgement. Resolve
            # that through the same full-history lookup as a transport error.
            if (not isinstance(response.get("body"), str)
                    or select_attestation([response], marker,
                                          trusted=operations.forge.trusted_comment) is None):
                raise Refusal("attestation create: incomplete comment acknowledgement")
            return
        except Refusal as exc:
            try:
                comment_id = lookup()
            except Refusal as lookup_error:
                raise Refusal(f"{exc}; reconciliation failed: {lookup_error}; rerun land to recover") from exc
            if comment_id is None:
                raise Refusal(f"{exc}; attestation could not be reconciled; rerun land to recover") from exc
            # The remote create may have succeeded. Reuse its known ID; never
            # issue a second POST in this invocation, even if it was rejected.
    operations.forge.edit_attestation(identity, comment_id, body, operations)


def refresh_run(
    identity: Identity, expected: LandingRun | None, operations: Operations,
) -> LandingRun | None:
    current = discover_run(identity, operations)
    if ((expected is None) != (current is None)
            or (expected is not None and current is not None
                and (current.change, current.pr) != (expected.change, expected.pr))):
        raise Refusal("run identity changed during landing; inspect status and rerun land")
    return current


def revalidate_check_sources(
    pr: PullRequest, run: LandingRun | None, snapshot: CheckSnapshot, operations: Operations,
) -> None:
    if pr.head != snapshot.head:
        raise Refusal(f"PR head changed during landing: expected {snapshot.head}, found {pr.head}; "
                      "review the current head and rerun land")
    sources = read_check_sources(pr, run, operations)
    # Prose may change, but source identity, checklist text, order and ticks
    # must still match this invocation's completed human-check pass.
    if ([(s.comment_id, s.checks) for s in sources]
            != [(s.comment_id, s.checks) for s in snapshot.sources]):
        raise Refusal("human check sources changed during landing; inspect checks and rerun land")


def revalidate_landing_inputs(
    identity: Identity, run: LandingRun | None, snapshot: CheckSnapshot, operations: Operations,
    *, require_done: bool = False,
) -> tuple[PullRequest, LandingRun | None]:
    """Read-only gate before finish and before each round's final main read.

    Compare with the post-edit snapshot, never prepare or attest checks again:
    additions need a new operator decision even with --checks-done. Only the
    validated finish and branch-update paths may advance the snapshot's head.
    """
    current = refresh_run(identity, run, operations)
    pr = read_pr(identity, operations)
    validate_run_eligibility(pr, current)
    if (current is not None and current.status != "done"
            and (require_done or (run is not None and run.status == "done"))):
        raise Refusal(f"run {current.change} is no longer done; inspect status before retrying land")
    if require_done and pr.draft:
        raise Refusal("PR became draft during landing")
    revalidate_check_sources(pr, current, snapshot, operations)
    return pr, current


def finish_run(
    pr: PullRequest, run: LandingRun | None, snapshot: CheckSnapshot, operations: Operations,
) -> tuple[PullRequest, LandingRun | None, CheckSnapshot]:
    """Finish only the checked run; a done-run retry authorizes the current PR.

    Never reset state or repair a partial finish here. The plain finish command
    owns archive/push recovery, including after a lost acknowledgement.
    """
    refreshed_pr, current = revalidate_landing_inputs(pr.identity, run, snapshot, operations)
    if current is not None and run is not None and run.status != "done":
        if current.status == "paused":
            operations.run(
                [sys.executable, "-m", "nightshift.cli", "finish", current.change,
                 "--expect-head", refreshed_pr.head],
                timeout=FINISH_TIMEOUT,
                stage=f"finish {current.change}; rerun land to recover",
            )
            current = refresh_run(pr.identity, current, operations)
            if current is None or current.status != "done":
                state = "missing" if current is None else f"{current.status} ({current.pause_reason})"
                raise Refusal(f"finish {run.change}: expected the same run done, found {state}; "
                              "inspect status and rerun land")
            refreshed_pr = read_pr(pr.identity, operations)
        # Only initial done discovery permits a local/remote head difference.
        # A concurrent finish between discovery and refresh must also establish
        # its finished head before this invocation can adopt it.
        if refreshed_pr.head != current.head:
            raise Refusal(f"finish {run.change}: finished run head {current.head} does not equal "
                          f"remote PR head {refreshed_pr.head}; inspect status and rerun land")
    if refreshed_pr.draft:
        raise Refusal("PR remains draft after run completion; inspect status before retrying land")
    return refreshed_pr, current, replace(snapshot, head=refreshed_pr.head)


def continue_landing(
    pr: PullRequest, run: LandingRun | None, snapshot: CheckSnapshot, operations: Operations,
    *, timeout: float,
) -> int:
    """Start the shared budget only after finish/continuation validation.

    Later base-update rounds must reuse this bounded Operations instance; the
    polling helper never creates or resets a deadline of its own.
    """
    bounded = replace(operations, deadline=operations.monotonic() + timeout)
    movements = []
    for round_number in range(1, 4):
        baseline = read_main(pr.identity, bounded)
        if round_number > 1 or not incorporates_base(pr.identity, snapshot.head, baseline, bounded):
            pr, baseline = update_branch(pr.identity, snapshot.head, bounded)
            snapshot = replace(snapshot, head=pr.head)
        # Retain the immediately pre-update base even if main moved while the
        # asynchronous update was being observed. Only a new round adopts it.
        wait_for_ci(pr.identity, snapshot.head, bounded, timeout=timeout)
        pr, run = revalidate_landing_inputs(
            pr.identity, run, snapshot, bounded, require_done=True,
        )
        observed_base = read_main(pr.identity, bounded)
        if observed_base == baseline:
            return after_ci(pr, run, snapshot, bounded)
        movements.append(f"round {round_number}: {baseline} -> {observed_base}")
    raise Refusal("main moved during all three CI rounds; refusing a fourth round: "
                  + "; ".join(movements) + "; inspect main and rerun land")


def incorporates_base(identity: Identity, head: str, base: str, operations: Operations) -> bool:
    """Establish pinned-head ancestry using the selected forge's commit facts."""
    return operations.forge.incorporates_base(identity, head, base, operations)


def _checks_not_registered(result: subprocess.CompletedProcess[str]) -> bool:
    return (result.returncode == 1 and result.stdout == ""
            and "no checks reported" in (result.stderr or ""))


def ci_is_green(result: subprocess.CompletedProcess[str]) -> bool:
    """Decode gh's JSON together with its special failure/pending exit codes."""
    if _checks_not_registered(result):
        return False
    try:
        checks = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        detail = (result.stderr or "").strip()
        raise Refusal(f"CI checks: malformed JSON response (exit {result.returncode})"
                      + (f": {detail}" if detail else "")) from exc
    if not isinstance(checks, list):
        raise Refusal("CI checks: expected a JSON array")
    for check in checks:
        if (not isinstance(check, dict)
                or any(not isinstance(check.get(key), str) or not check[key].strip()
                       for key in ("name", "bucket", "state"))
                or not isinstance(check.get("link"), str)):
            raise Refusal(f"CI checks: malformed check result: {check!r}")
        if check["bucket"] not in {"pass", "fail", "pending", "skipping", "cancel"}:
            raise Refusal(f"CI check {check['name']}: unknown classification {check['bucket']!r}")
    for check in checks:
        if check["bucket"] in {"fail", "cancel"}:
            raise Refusal(f"CI check {check['name']}: {check['bucket']} ({check['state']})"
                          + (f" — {check['link']}" if check["link"] else ""))
    buckets = {check["bucket"] for check in checks}
    # Exit 1 is meaningful only with a failing/cancelled check, and exit 8
    # only with pending checks. Neither may turn contradictory JSON green.
    if result.returncode == 1 or (result.returncode == 8 and "pending" not in buckets):
        detail = (result.stderr or "inconsistent exit status and check results").strip()
        raise Refusal(f"CI checks: {detail} (exit {result.returncode})")
    return "pass" in buckets and buckets <= {"pass", "skipping"}


def wait_for_ci(
    identity: Identity, expected_head: str, operations: Operations, *, timeout: float | None = None,
) -> None:
    """Require green checks bracketed by reads of the same reviewed head."""
    if timeout is None:
        timeout = operations.remaining()
    awaiting_registration = False

    def check_head() -> None:
        pr = read_pr(identity, operations)
        if pr.head != expected_head:
            raise Refusal(f"PR head changed during CI: expected {expected_head}, found {pr.head}; "
                          "review the current head and rerun land")
        if pr.draft:
            raise Refusal("PR became draft during CI; inspect it before retrying land")

    try:
        while True:
            check_head()
            result = operations.forge.checks(identity, expected_head, operations)
            check_head()
            if ci_is_green(result):
                return
            awaiting_registration = _checks_not_registered(result)
            operations.poll_sleep()
    except _CIDeadlineExpired as exc:
        if awaiting_registration:
            raise Refusal(f"no CI checks were reported for head {expected_head} within {timeout:g}s; "
                          "inspect checks and rerun land") from exc
        raise


def after_ci(
    pr: PullRequest, run: LandingRun | None, snapshot: CheckSnapshot, operations: Operations,
) -> int:
    """Merge the verified head once, then reconcile even a lost acknowledgement.

    The CI budget ends at the final base comparison. Merge and confirmation
    each have the ordinary bounded call timeout, so a slow/lost merge response
    cannot consume the subsequent read's opportunity to establish the result.
    Head conditioning does not lock main against another merge in this window.
    """
    operations.remaining()
    operations = replace(operations, deadline=None)
    identity = pr.identity
    merge_error: Refusal | None = None
    try:
        operations.forge.merge(identity, snapshot.head, operations)
    except Refusal as exc:
        # Never repeat the mutation: the server may have completed it already.
        merge_error = exc

    try:
        result = operations.forge.confirm_merge(identity, operations)
        if (type(result.get("number")) is not int or result["number"] != identity.number
                or _returned_identity(result.get("url"), identity) != identity):
            raise Refusal("merged-state confirmation: contradictory PR identity")
        if result.get("state") != "MERGED":
            raise Refusal("merged-state confirmation: "
                          f"PR state is {result.get('state', 'missing')!r}; "
                          "queue admission is not a confirmed merge")
    except Refusal as exc:
        reason = f"{merge_error}; {exc}" if merge_error is not None else str(exc)
        raise Refusal(f"merge not confirmed for {identity.url} at head {snapshot.head}: "
                      f"{reason}; inspect the PR and protection/check results, then rerun land "
                      "if it remains open; do not reset a done run") from exc
    print(f"Merged {identity.url}")
    return 0


def main(
    argv: list[str] | None = None, *, runner: Runner | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    forge: LandingForge | None = None,
    opener: Any = None,
) -> int:
    args = parser().parse_args(argv)
    operations = Operations(subprocess.run if runner is None else runner, sleep, monotonic)
    if forge is not None:
        operations.forge = forge
    try:
        cfg = load_config(args.config)
        operations.gitea_forges = tuple(f for f in cfg.forges if f.kind == "gitea")
        identity, definition = resolve_target(args.pr, args.repo, cfg, operations)
        if definition.kind == "gitea":
            token = load_land_token(definition)
            operations.forge = (GiteaLandingForge(definition, token, opener=opener)
                                if forge is None else forge)
        pr = read_pr(identity, operations)
        read_main(identity, operations)
        run = discover_run(identity, operations)
        validate_run_eligibility(pr, run)
        sources = read_check_sources(pr, run, operations)
        snapshot = prepare_human_checks(pr, sources, operations, checks_done=args.checks_done)
        record_attestation(identity, snapshot, operations)
        pr, run, snapshot = finish_run(pr, run, snapshot, operations)
        return continue_landing(pr, run, snapshot, operations, timeout=args.timeout)
    except Refusal as exc:
        print(f"land: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

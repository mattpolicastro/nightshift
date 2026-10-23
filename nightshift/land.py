"""Explicit operator entry point for landing an enrolled, reviewed PR.

Identity, run validation, and in-place human-check recording are wired so far.
Finish and merge remain disabled until attestation and subsequent gates are implemented.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from . import config, queue


Runner = Callable[..., subprocess.CompletedProcess[str]]
READ_TIMEOUT = 30
PR_FIELDS = "number,url,state,isDraft,headRefOid,baseRefName,body"
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+")
_PR_URL = re.compile(r"https://github\.com/([^/]+/[^/]+)/pull/([0-9]+)/?")
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_TASK_LINE = re.compile(r"^[ \t>]*[-*+] \[(?P<state>[ xX])\] (?P<text>[^\r\n]*)", re.MULTILINE)


class Refusal(Exception):
    """An operational failure that is safe to show to the operator."""


@dataclass(frozen=True)
class Identity:
    repo: str
    number: int

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/pull/{self.number}"


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
    """Expected post-edit sources and pre-finish head for later revalidation.

    Keep the authenticated actor even when every box was already ticked, so
    a later attestation can recover independently of the source edits.
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

    The clocks are injected here for later bounded polling. Identity reads do
    not wait or retry; an unresolved read fails closed.
    """

    runner: Runner
    sleep: Callable[[float], None]
    monotonic: Callable[[], float]

    def run(self, argv: list[str], *, stage: str) -> subprocess.CompletedProcess[str]:
        try:
            result = self.runner(
                argv, capture_output=True, text=True, check=False,
                timeout=READ_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise Refusal(f"{stage}: command timed out after {READ_TIMEOUT}s") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise Refusal(f"{stage}: {exc}") from exc
        if result.returncode:
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
        description="Land a reviewed PR in an enrolled repository (finish and merge currently disabled).",
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
            data = operations.read_json(
                ["gh", "repo", "view", "--json", "nameWithOwner"],
                stage="repository resolution",
            )
            explicit_repo = _repo_name(data.get("nameWithOwner"))
        return Identity(explicit_repo, number)
    identity = _url_identity(pr)
    if explicit_repo is not None and explicit_repo != identity.repo:
        raise Refusal(f"PR URL repository {identity.repo} contradicts --repo {explicit_repo}")
    return identity


def load_config(path: Path) -> config.Config:
    try:
        return config.load(path)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise Refusal(f"cannot read configuration {path}: {exc}") from exc


def enrolled_repo(cfg: config.Config, identity: Identity) -> config.Repo:
    matches = [repo for repo in cfg.repos if _repo_name(repo.name) == identity.repo]
    if not matches:
        raise Refusal(f"repository {identity.repo} is not enrolled in configuration")
    if len(matches) != 1:
        raise Refusal(f"ambiguous enrollment for {identity.repo}: {len(matches)} matching repositories")
    return matches[0]


def read_pr(identity: Identity, operations: Operations) -> PullRequest:
    data = operations.read_json(
        ["gh", "pr", "view", str(identity.number), "--repo", identity.repo,
         "--json", PR_FIELDS],
        stage=f"PR read for {identity.url}",
    )
    if (_url_identity(data.get("url")) != identity
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
    data = operations.read_json(
        ["gh", "api", f"repos/{identity.repo}/git/ref/heads/main"],
        stage=f"read existing main branch in {identity.repo}",
    )
    obj = data.get("object")
    if (data.get("ref") != "refs/heads/main" or not isinstance(obj, dict)
            or obj.get("type") != "commit" or not isinstance(obj.get("sha"), str)
            or not _SHA.fullmatch(obj["sha"])):
        raise Refusal("cannot establish an existing main branch: malformed ref response")
    return obj["sha"]


def discover_run(identity: Identity, operations: Operations) -> LandingRun | None:
    """A missing match is meaningful only after every row is readable.

    Validate the version-1 fields used for classification and lifecycle gates;
    status presentation fields (tasks, context, action) are not landing inputs.
    """
    data = operations.read_json(["specloop", "status", "--json"], stage="run discovery")
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
            run_identity = _url_identity(pr) if pr else None
        except Refusal as exc:
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


def read_comments(identity: Identity, operations: Operations) -> list[dict[str, Any]]:
    """Slurp all REST pages into one JSON document; never use a partial history."""
    pages = operations.read_json_array(
        ["gh", "api", f"repos/{identity.repo}/issues/{identity.number}/comments",
         "--paginate", "--slurp"],
        stage="publication comment discovery",
    )
    comments = []
    for page in pages:
        if not isinstance(page, list):
            raise Refusal("publication comment discovery: malformed comment page")
        for comment in page:
            if not isinstance(comment, dict) or not isinstance(comment.get("body"), str):
                raise Refusal("publication comment discovery: malformed comment body")
            comments.append(comment)
    return comments


def select_publication(comments: list[dict[str, Any]], change: str) -> CheckSource | None:
    # One optional round token, with literal marker whitespace/delimiters.
    # Escape the change name so neither regex syntax nor a similarly named
    # change can select another run's publication.
    marker = re.compile(r"<!-- nightshift:publish:" + re.escape(change)
                        + r"(?::[^\s:<>]+)? -->")
    candidates = []
    for comment in comments:
        body = comment["body"]
        association = comment.get("author_association")
        if (not isinstance(association, str)
                or not queue.trusted(comment, key="author_association")
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
        publication = select_publication(read_comments(pr.identity, operations), run.change)
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
    data = operations.read_json(["gh", "api", "user"], stage="authenticated actor lookup")
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
            endpoint = (f"repos/{pr.identity.repo}/pulls/{pr.identity.number}"
                        if source.comment_id is None
                        else f"repos/{pr.identity.repo}/issues/comments/{source.comment_id}")
            # One full-source PATCH, with raw text fields preserving newlines.
            # Stop on failure without rollback or blind retry: the next operator
            # invocation reads actual remote state, including ambiguous writes.
            operations.run(
                ["gh", "api", "--method", "PATCH", endpoint, "-f", f"body={updated.body}"],
                stage=f"record human checks in {source.reference}; rerun land to recover",
            )
        completed.append(updated)
    return CheckSnapshot(pr.head, tuple(completed), actor)


def main(
    argv: list[str] | None = None, *, runner: Runner | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    args = parser().parse_args(argv)
    operations = Operations(subprocess.run if runner is None else runner, sleep, monotonic)
    try:
        cfg = load_config(args.config)
        identity = resolve_identity(args.pr, args.repo, operations)
        enrolled_repo(cfg, identity)
        pr = read_pr(identity, operations)
        read_main(identity, operations)
        run = discover_run(identity, operations)
        validate_run_eligibility(pr, run)
        sources = read_check_sources(pr, run, operations)
        snapshot = prepare_human_checks(pr, sources, operations, checks_done=args.checks_done)
        # Retain the expected post-edit snapshot for attestation and later
        # revalidation. Finish/CI/merge must wait for their remaining gates.
        raise Refusal(f"{identity.url}: mutations disabled beyond check edits until all landing "
                      f"gates are wired (checks read at head {snapshot.head})")
    except Refusal as exc:
        print(f"land: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

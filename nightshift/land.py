"""Explicit operator entry point for landing an enrolled, reviewed PR.

Only read-only identity and run validation are wired so far. Mutations remain disabled
until the human-check, lifecycle, CI, and merge gates are implemented.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from . import config


Runner = Callable[..., subprocess.CompletedProcess[str]]
READ_TIMEOUT = 30
PR_FIELDS = "number,url,state,isDraft,headRefOid,baseRefName"
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+")
_PR_URL = re.compile(r"https://github\.com/([^/]+/[^/]+)/pull/([0-9]+)/?")
_SHA = re.compile(r"[0-9a-fA-F]{40}")


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

    def read_json(self, argv: list[str], *, stage: str) -> dict[str, Any]:
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
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise Refusal(f"{stage}: malformed JSON response") from exc
        if not isinstance(value, dict):
            raise Refusal(f"{stage}: expected a JSON object")
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
        description="Land a reviewed PR in an enrolled repository (mutations currently disabled).",
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
    return PullRequest(identity, head, draft)


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
        # Fail closed until every subsequent safety gate is wired. In
        # particular, --checks-done must not authorize mutations on its own.
        raise Refusal(f"{identity.url}: mutations disabled until all landing gates are wired")
    except Refusal as exc:
        print(f"land: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

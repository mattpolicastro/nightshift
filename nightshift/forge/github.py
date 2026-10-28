"""GitHub operations using the existing queue helpers and transport."""

from collections.abc import Callable
import json
from typing import Literal
from urllib.parse import quote

from nightshift import queue
from nightshift.forge.types import ResponseText


class GitHubForge:
    def __init__(self, runner: Callable[[list[str]], str] | None = None) -> None:
        self._runner = runner if runner is not None else queue._gh

    def list_issues(self, repo: str, label: str) -> list[queue.Issue]:
        return queue._list(repo, label, runner=self._runner)

    def issue_labels(self, repo: str, number: int) -> set[str]:
        return queue.labels_of(repo, number, runner=self._runner)

    def relabel_issue(self, repo: str, number: int, *, add: list[str], remove: list[str]) -> None:
        return queue._relabel(repo, number, add=add, remove=remove, runner=self._runner)

    def latest_pr_number(self, repo: str, branch: str, *, strict: bool = True) -> int | None:
        return queue._latest_pr_number(repo, branch, strict=strict, runner=self._runner)

    def pr_feedback_records(self, repo: str, number: int) -> list[tuple[str, dict]]:
        return queue._pr_feedback_records(repo, number, runner=self._runner)

    def pr_feedback_items(self, repo: str, number: int) -> list[queue.FeedbackItem]:
        return queue.pr_feedback_items(repo, number, runner=self._runner)

    def open_blockers(self, repo: str, numbers: list[int]) -> list[int]:
        return queue.open_blockers(repo, numbers, runner=self._runner)

    def issue_for_draft(self, repo: str, number: int) -> dict:
        return json.loads(self._runner(["issue", "view", str(number), "--repo", repo,
                                       "--json", "state,title,body,labels,comments"]))

    def issue_author(self, repo: str, number: int) -> dict:
        return json.loads(self._runner(["api", f"repos/{repo}/issues/{number}"]))

    def list_open_prs(self, repo: str, label: str) -> list[dict]:
        return json.loads(self._runner(["pr", "list", "--repo", repo, "--label", label,
                                       "--state", "open", "--json", "number", "--limit", "100"]))

    def pr_for_recovery(self, repo: str, number: int) -> dict:
        return json.loads(self._runner(["pr", "view", str(number), "--repo", repo,
                                       "--json", "state,headRefOid"]))

    def pr_for_admission(self, repo: str, number: int) -> dict:
        return json.loads(self._runner(["pr", "view", str(number), "--repo", repo,
                                       "--json", "state,labels,headRefName,headRefOid,isCrossRepository,url"]))

    def pr_for_revision(self, repo: str, number: int) -> dict:
        return json.loads(self._runner(["pr", "view", str(number), "--repo", repo,
                                       "--json", "state,title,body,labels,headRefName,headRefOid,isCrossRepository,url"]))

    def pr_for_publication(self, repo: str, number: int) -> dict:
        return json.loads(self._runner(["pr", "view", str(number), "--repo", repo,
                                       "--json", "state,labels,headRefName,isCrossRepository,url"]))

    def pr_for_readiness(self, repo: str, number: int) -> dict:
        return json.loads(self._runner(["pr", "view", str(number), "--repo", repo,
                                       "--json", "state,labels,headRefName,headRefOid,isCrossRepository,url,body"]))

    def pr_state_url(self, repo: str, target: str | int) -> dict:
        return json.loads(self._runner(["pr", "view", str(target), "--repo", repo, "--json", "state,url"]))

    def label_event_pages(self, repo: str, number: int) -> list[list[dict]]:
        return json.loads(self._runner(["api", f"repos/{repo}/issues/{number}/events", "--paginate", "--slurp"]))

    def repository(self, repo: str) -> dict:
        return json.loads(self._runner(["api", f"repos/{repo}"]))

    def collaborator_response(self, repo: str, login: str) -> ResponseText:
        return ResponseText(self._runner(["api", f"repos/{repo}/collaborators/{quote(login, safe='')}", "--include"]))

    def organization_member_response(self, owner: str, login: str) -> ResponseText:
        return ResponseText(self._runner(["api", f"orgs/{quote(owner, safe='')}/members/{quote(login, safe='')}", "--include"]))

    def commit(self, repo: str, head: str) -> dict:
        return json.loads(self._runner(["api", f"repos/{repo}/commits/{head}"]))

    def comment_pages(self, repo: str, number: int) -> list[list[dict]]:
        return json.loads(self._runner(["api", f"repos/{repo}/issues/{number}/comments", "--paginate", "--slurp"]))

    def pr_checks(self, repo: str, number: int) -> ResponseText:
        return ResponseText(self._runner(["pr", "checks", str(number), "--repo", repo, "--json", "name,bucket,state"]))

    def relabel_pr(self, repo: str, target: str | int, *, add: list[str], remove: list[str]) -> None:
        args = ["pr", "edit", str(target), "--repo", repo]
        for label in add:
            args += ["--add-label", label]
        for label in remove:
            args += ["--remove-label", label]
        self._runner(args)

    def create_comment(self, repo: str, number: int, kind: Literal["issue", "pr"], body: str) -> None:
        self._runner([kind, "comment", str(number), "--repo", repo, "--body", body])

    def update_comment(self, repo: str, comment_id: int, body: str) -> None:
        self._runner(["api", f"repos/{repo}/issues/comments/{comment_id}", "--method", "PATCH", "-f", f"body={body}"])

    def create_draft_pr(self, repo: str, branch: str, base: str, title: str, body: str) -> ResponseText:
        return ResponseText(self._runner(["pr", "create", "--repo", repo, "--head", branch, "--base", base,
                                          "--draft", "--title", title, "--body", body]))

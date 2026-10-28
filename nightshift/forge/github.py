"""GitHub operations using the existing queue helpers and transport."""

from collections.abc import Callable

from nightshift import queue


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

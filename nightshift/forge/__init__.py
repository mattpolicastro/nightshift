"""Forge transport primitives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from nightshift.queue import FeedbackItem, Issue


class ForgeError(RuntimeError):
    """A forge failure with the transport's existing retry classification."""

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


@runtime_checkable
class Forge(Protocol):
    """Factual operations; authorization and lifecycle decisions stay in core."""

    def list_issues(self, repo: str, label: str) -> list[Issue]: ...
    def issue_labels(self, repo: str, number: int) -> set[str]: ...
    def relabel_issue(self, repo: str, number: int, *, add: list[str], remove: list[str]) -> None: ...
    def latest_pr_number(self, repo: str, branch: str, *, strict: bool = True) -> int | None: ...
    def pr_feedback_records(self, repo: str, number: int) -> list[tuple[str, dict]]: ...
    def pr_feedback_items(self, repo: str, number: int) -> list[FeedbackItem]: ...
    def open_blockers(self, repo: str, numbers: list[int]) -> list[int]: ...

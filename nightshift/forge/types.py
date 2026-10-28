"""Immutable facts, without authorization or workflow judgments.

Optional fields retain absent/null evidence; labels are immutable when present.
Queue helper results keep their existing types rather than converting to these.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    repo: str
    number: int
    title: str | None = None
    body: str | None = None
    labels: tuple[str, ...] | None = None
    state: str | None = None
    author_login: str | None = None
    author_association: str | None = None


@dataclass(frozen=True)
class PullRequest(Issue):
    head_ref_name: str | None = None
    head_ref_oid: str | None = None
    url: str | None = None
    is_cross_repository: bool | None = None


@dataclass(frozen=True)
class Comment:
    id: int
    body: str | None = None
    author_login: str | None = None
    author_association: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    submitted_at: str | None = None
    source: str | None = None
    state: str | None = None
    path: str | None = None
    line: int | None = None
    original_line: int | None = None


@dataclass(frozen=True)
class LabelEvent:
    id: int
    event: str | None = None
    label: str | None = None
    actor_login: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class CICheck:
    name: str | None = None
    bucket: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class ResponseText:
    stdout: str

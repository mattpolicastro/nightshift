"""Bounded operator-approved task and review policy data, without worker history.

Approval is the trusted caller's responsibility; these types validate shape and
size, not authorization or the truth of their contents. Text remains untrusted.
"""
from dataclasses import dataclass


def _text(value, limit: int, label: str) -> int:
    if type(value) is not str or len(value) > limit or not value.strip():
        raise ValueError(label + " must be a nonempty plain string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError(label + " must be valid UTF-8") from exc
    if size > limit or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValueError(label + " exceeds its size or text constraints")
    return size


def _items(values, label: str) -> int:
    if type(values) is not tuple or len(values) > 32:
        raise ValueError(label + " must be a tuple of at most 32 plain strings")
    return sum(_text(value, 2048, label) for value in values)


@dataclass(frozen=True)
class ApprovedTask:
    task_id: str
    title: str
    body: str
    acceptance_criteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewPolicy:
    instructions: str
    required_checks: tuple[str, ...] = ()


def validate(task: ApprovedTask, policy: ReviewPolicy) -> None:
    if type(task) is not ApprovedTask or type(policy) is not ReviewPolicy:
        raise ValueError("Exact ApprovedTask and ReviewPolicy data types are required")
    task_bytes = (_text(task.task_id, 256, "Task identity")
                  + _text(task.title, 512, "Task title")
                  + _text(task.body, 32768, "Task body")
                  + _items(task.acceptance_criteria, "Acceptance criteria"))
    policy_bytes = (_text(policy.instructions, 8192, "Review instructions")
                    + _items(policy.required_checks, "Required checks"))
    if task_bytes > 65536 or policy_bytes > 16384:
        raise ValueError("Task or review policy exceeds its total UTF-8 budget")


def payload(task: ApprovedTask, policy: ReviewPolicy) -> dict:
    validate(task, policy)
    return {"approved_task": {"task_id": task.task_id, "title": task.title, "body": task.body,
                              "acceptance_criteria": list(task.acceptance_criteria)},
            "review_policy": {"instructions": policy.instructions,
                              "required_checks": list(policy.required_checks)}}

"""Exact-SHA verification inside the experimental owned container executor.

No production dispatch integration. Dependencies must already exist in the
operator-selected immutable image; this module never installs or downloads them.
Repository commands execute only through the container session.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..verification import parse_commands
from . import snapshot


@dataclass(frozen=True)
class ClauseResult:
    argv: tuple[str, ...]
    status: str
    exit_code: int | None
    output: str
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.status == "succeeded" and self.exit_code == 0


@dataclass
class IsolatedVerificationResult:
    candidate_sha: str
    command: str
    image_id: str
    status: str = "failed"
    source_fingerprint: str = ""
    final_fingerprint: str = ""
    clauses: list[ClauseResult] = field(default_factory=list)
    cleanup_succeeded: bool = False
    duration_s: float = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        try:
            expected = parse_commands(self.command)
        except (ValueError, TypeError):
            return False
        return (self.status == "succeeded" and self.cleanup_succeeded
                and bool(self.source_fingerprint)
                and self.final_fingerprint == self.source_fingerprint
                and [list(clause.argv) for clause in self.clauses] == expected
                and all(clause.ok for clause in self.clauses))


class _Failure(Exception):
    def __init__(self, status: str, detail: str):
        self.status, self.detail = status, detail


def run(repository: Path, candidate_sha: str, command: str, *, image_id: str,
        docker_host: str, recovery_dir: Path, timeout_s: float = 600,
        max_output_bytes: int = 1024 * 1024) -> IsolatedVerificationResult:
    """Verify one committed snapshot with one shared source/container deadline.

    The mutable source copy must export identically after every clause and at
    finish. This is an integrity check, not a read-only mount; immutable review
    is separate work.
    The owned session has a separate bounded cleanup allowance after its budget.
    Recovery records live only in the explicit host-owned recovery directory.
    """
    from .container_session import Session

    started = time.monotonic()
    result = IsolatedVerificationResult(candidate_sha, command, image_id)
    session = None
    deadline = None
    try:
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise ValueError("A positive finite timeout is required")
        if type(max_output_bytes) is not int or max_output_bytes <= 0:
            raise ValueError("A positive output budget is required")
        deadline = started + timeout_s
        clauses = parse_commands(command)
        source = snapshot.from_git(repository, candidate_sha, deadline=deadline)
        result.source_fingerprint = snapshot.fingerprint(source)
        if time.monotonic() >= deadline:
            raise _Failure("timed_out", "Source transfer exhausted the verification deadline")
        session = Session(image_id, source, docker_host=docker_host,
                          recovery_dir=recovery_dir, deadline=deadline,
                          max_output_bytes=max_output_bytes)
        with session:
            for argv in clauses:
                if time.monotonic() >= deadline:
                    raise _Failure("timed_out", "Verification deadline exhausted before clause")
                clause_start = time.monotonic()
                try:
                    completed = session.run(argv)
                except (Exception, KeyboardInterrupt) as exc:
                    result.clauses.append(ClauseResult(
                        tuple(argv), "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                        None, "", time.monotonic() - clause_start))
                    raise
                clause = ClauseResult(tuple(argv), completed.status, completed.exit_code,
                                      completed.output, time.monotonic() - clause_start)
                result.clauses.append(clause)
                if not clause.ok:
                    raise _Failure(clause.status if clause.status != "succeeded" else "failed",
                                   "Verification clause did not complete successfully")
                if time.monotonic() >= deadline:
                    raise _Failure("timed_out", "Verification deadline exhausted before checkpoint")
                result.final_fingerprint = snapshot.fingerprint(session.checkpoint())
                if result.final_fingerprint != result.source_fingerprint:
                    raise _Failure("candidate_changed", "Verification clause modified the source snapshot")
            if time.monotonic() >= deadline:
                raise _Failure("timed_out", "Verification deadline exhausted before final snapshot")
            final = session.finish()
            result.final_fingerprint = snapshot.fingerprint(final)
            if result.final_fingerprint != result.source_fingerprint:
                raise _Failure("candidate_changed", "Verification modified the source snapshot")
        # finish() must export and remove the entire owned session before its
        # source can count as successful verification evidence.
        result.cleanup_succeeded = session.cleanup_succeeded
        if not result.cleanup_succeeded:
            raise _Failure("cleanup_failed", "Owned verification session cleanup was not confirmed")
        result.status = "succeeded"
    except _Failure as exc:
        result.status, result.detail = exc.status, exc.detail
    except KeyboardInterrupt:
        result.status, result.detail = "interrupted", "Verification was interrupted"
    except Exception as exc:
        result.status = "timed_out" if deadline is not None and time.monotonic() >= deadline else "failed"
        result.detail = str(exc)
    finally:
        if session is not None:
            result.cleanup_succeeded = session.cleanup_succeeded
            if not result.cleanup_succeeded:
                original_status = result.status
                result.status = "cleanup_failed"
                result.detail = f"Owned session cleanup unconfirmed after {original_status}: {result.detail}"
        result.duration_s = time.monotonic() - started
    return result

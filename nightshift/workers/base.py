"""Provider-independent values. Credentials and transcripts never belong here."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

WorkerStatus = Literal[
    "succeeded", "failed", "interrupted", "budget_exhausted", "auth_failed",
    "rate_limited", "needs_input", "protocol_error", "unsupported",
]


@dataclass(frozen=True)
class WorkerBudgets:
    max_runtime_s: float = 1800
    max_tool_calls: int = 100
    max_output_tokens_total: int = 32000
    max_stream_bytes: int = 8 * 1024 * 1024
    max_line_bytes: int = 1024 * 1024
    interrupt_grace_s: float = 0.25

    def __post_init__(self):
        import math
        values = (self.max_runtime_s, self.max_tool_calls,
                  self.max_output_tokens_total, self.max_stream_bytes,
                  self.max_line_bytes, self.interrupt_grace_s)
        if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("Worker budgets must be positive and finite")
        if any(not isinstance(v, int) for v in values[1:5]):
            raise ValueError("Count and byte budgets must be integers")


@dataclass(frozen=True)
class WorkerRequest:
    role: Literal["implement", "review"]
    cwd: Path
    prompt: str
    model: str
    budgets: WorkerBudgets = field(default_factory=WorkerBudgets)
    reasoning_effort: str | None = None
    transcript_path: Path | None = None
    normalized_transcript_path: Path | None = None

    def __post_init__(self):
        if self.role not in {"implement", "review"} or not self.model.strip():
            raise ValueError("A supported role and explicit model are required")
        if not self.cwd.is_absolute():
            raise ValueError("Worker cwd must be absolute")


@dataclass(frozen=True)
class CompletedCommand:
    item_id: str
    command: str
    cwd: str
    exit_code: int | None
    status: str
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class ReviewerVerdict:
    verdict: Literal["PASS", "FAIL"]
    blocking: tuple[str, ...]
    non_blocking: tuple[str, ...]


@dataclass
class WorkerResult:
    status: WorkerStatus = "protocol_error"
    text: str = ""
    runtime: str = "codex-app-server"
    runtime_version: str | None = None
    requested_model: str | None = None
    observed_model: str | None = None
    duration_s: float = 0
    commands: list[CompletedCommand] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    thread_id: str | None = None
    turn_id: str | None = None
    reviewer_verdict: ReviewerVerdict | None = None
    diagnostics: list[str] = field(default_factory=list)
    denied_actions: list[str] = field(default_factory=list)
    file_changes: list[dict] = field(default_factory=list)
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    @property
    def output_tokens(self) -> int | None:
        value = self.usage.get("outputTokens")
        return value if type(value) is int and value >= 0 else None

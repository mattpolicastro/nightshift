"""Parse a `--output-format stream-json` transcript.

Everything the scheduler needs is on the terminal `result` event. Read it from
there rather than from the agent's prose: a worker that hits a permission wall
mentions it in its closing message, but `permission_denials` says so in a field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class RateLimit:
    """Subscription window state. Present on successful runs, not just failures.

    This is continuous telemetry, which means the scheduler can read the
    authoritative window off every run instead of modelling consumption and
    inferring exhaustion. Only `five_hour` has been observed so far — the
    weekly cap is the real binding constraint, so treat `type` as a discriminant
    rather than assuming.
    """

    status: str  # "allowed" | ...
    type: str  # "five_hour" | ...
    resets_at: int  # unix seconds
    using_overage: bool


@dataclass
class Denial:
    tool: str
    command: str


@dataclass
class Result:
    ok: bool
    stop_reason: str | None
    turns: int
    duration_s: float
    # List-price arithmetic on the token counts, NOT money: these runs are on a
    # Max subscription and consume quota. Also priced at standard rates, which
    # ignores any introductory pricing in effect. A consumption proxy only.
    cost_usd: float
    output_tokens: int
    cache_read_tokens: int
    text: str
    # Every Bash command the worker actually executed. The daemon checks this
    # rather than the closing message: a worker that claims it ran the tests
    # and a worker that ran them look identical in prose.
    commands: list[str] = field(default_factory=list)
    denials: list[Denial] = field(default_factory=list)
    # A `--model sonnet` run also bills small auxiliary haiku calls, so per-model
    # accounting cannot assume one model per worker.
    model_usage: dict = field(default_factory=dict)
    rate_limit: RateLimit | None = None
    # The result event's own verdict on why the run ended: "success",
    # "error_max_turns", … This is the field that actually carries turn
    # exhaustion. `stop_reason` does NOT — issue #23 exhausted its turns and
    # reported `stop_reason: "tool_use"`, because that describes the last
    # message rather than the run.
    subtype: str | None = None

    @property
    def subscription_billed(self) -> bool:
        """Did this run consume subscription quota?

        Read off the run's own telemetry rather than from what the config
        asserted about its endpoint — the same principle as checking the Bash
        commands a worker ran instead of believing its prose. Two signals, and
        both must say no: `rate_limit_info` is absent (a subscription run
        reports window state even when it succeeds), and every priced model
        carries `costBasis: "unknown"`, which is the CLI saying it does not
        know what the run cost because it did not price it.

        A run with no `modelUsage` at all is treated as billed. The safe
        direction is to over-count against the cap, not to hand out free slots
        on the strength of missing telemetry.
        """
        if self.rate_limit is not None:
            return True
        priced = [m for m in self.model_usage.values() if isinstance(m, dict)]
        if not priced:
            return True
        return not all(m.get("costBasis") == "unknown" for m in priced)

    @property
    def truncated(self) -> bool:
        """Hit --max-turns. A partial diff must never be mistaken for finished work."""
        return (
            self.subtype == "error_max_turns"
            or self.stop_reason == "max_turns"
            or self.turns_exhausted
        )

    turns_exhausted: bool = False


def parse(events: str) -> Result | None:
    """Return the terminal result, or None if the run produced no result event."""
    result_event = None
    rate_limit = None
    commands: list[str] = []

    for line in events.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    commands.append(str(block.get("input", {}).get("command", "")))
        elif event.get("type") == "result":
            result_event = event
        elif event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            rate_limit = RateLimit(
                status=info.get("status", "unknown"),
                type=info.get("rateLimitType", "unknown"),
                resets_at=info.get("resetsAt", 0),
                using_overage=bool(info.get("isUsingOverage")),
            )

    if result_event is None:
        return None

    usage = result_event.get("usage") or {}
    model_usage = result_event.get("modelUsage") or {}
    # The CLI prices every run at list rates, including one against a local
    # Ollama that cost nothing — measured 2026-09-04: $0.33 for 56k tokens of
    # glm-4.7-flash. Those entries carry `costBasis: "unknown"`, which is the
    # CLI saying it does not know what the run cost; subscription runs carry
    # no costBasis at all. Invented money must not reach the tally, where it
    # would be summed with real consumption and throttle work that was free.
    unpriced = sum(
        m.get("costUSD") or 0.0
        for m in model_usage.values()
        if isinstance(m, dict) and m.get("costBasis") == "unknown"
    )
    cost = max(0.0, (result_event.get("total_cost_usd") or 0.0) - unpriced)
    return Result(
        ok=not result_event.get("is_error", False),
        stop_reason=result_event.get("stop_reason"),
        turns=result_event.get("num_turns", 0),
        duration_s=(result_event.get("duration_ms") or 0) / 1000,
        cost_usd=cost,
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        text=result_event.get("result", ""),
        commands=commands,
        denials=[
            Denial(
                tool=d.get("tool_name", "?"),
                command=str((d.get("tool_input") or {}).get("command", "")),
            )
            for d in (result_event.get("permission_denials") or [])
        ],
        model_usage=model_usage,
        rate_limit=rate_limit,
        subtype=result_event.get("subtype"),
    )


@dataclass
class Progress:
    """What a run has done SO FAR, read from a transcript still being written.

    Deliberately separate from `parse`, which answers "how did this end" and
    returns None without a terminal `result` event. A run in flight has no
    result event and is not a failure — it is the normal case for the question
    `status` asks, which is "is this moving, and on what".
    """

    turns: int = 0
    last_tool: str = ""
    last_command: str = ""
    #: Any events at all, including the `system`/`init` header. Distinguishes
    #: "the CLI has started and is thinking" from "nothing has happened",
    #: which look identical if only assistant turns are counted — and the
    #: first is what the first minute of every run actually looks like.
    events_seen: int = 0

    @property
    def line(self) -> str:
        if not self.turns and not self.last_tool:
            return "started, no turns yet" if self.events_seen else "no events yet"
        where = self.last_tool or "?"
        if self.last_command:
            where += f" {self.last_command}"
        return f"turn {self.turns}, last: {where}"


def progress(events: str) -> Progress:
    """Summarise a partial transcript. Tolerates a half-written final line.

    The file is flushed per line by `worker._run`, but a reader can still
    arrive mid-write, so the last line may be truncated JSON — which
    `json.loads` raises on and this swallows, exactly as `parse` does.
    """
    turns = 0
    seen = 0
    last_tool = last_command = ""
    for line in events.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        seen += 1
        if event.get("type") != "assistant":
            continue
        turns += 1
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                last_tool = str(block.get("name", ""))
                command = (block.get("input") or {}).get("command", "")
                last_command = str(command)[:60]
    return Progress(turns=turns, last_tool=last_tool, last_command=last_command,
                    events_seen=seen)


@dataclass
class Skim:
    """The reviewer's own précis: what changed, and what only a human can settle.

    Written LAST by the reviewer so it cannot pre-commit a verdict, and read
    FIRST by hoisting it to the top of the PR body. Both halves matter: a
    summary written before the analysis is a guess, and one buried under two
    thousand words of rubric is not read at all.
    """

    summary: str = ""
    needs_human: list[str] = field(default_factory=list)


def skim(text: str) -> Skim:
    """Parse the SUMMARY / NEEDS-HUMAN block. Absent fields come back empty.

    Lenient on purpose: a missing block degrades the PR body to what it was
    before, and must never be mistaken for "the reviewer said nothing needs a
    human" — those are different, and `verdict` is what gates shipping.
    """
    summary = ""
    needs: list[str] = []
    collecting = False
    for raw in text.splitlines():
        line = raw.strip()
        upper = line.upper()
        if upper.startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()
            collecting = False
        elif upper.startswith("NEEDS-HUMAN:"):
            collecting = True
            rest = line.split(":", 1)[1].strip()
            if rest and rest.lower() not in ("nothing", "none"):
                needs.append(rest)
        elif collecting:
            if line.startswith(("-", "*")):
                item = line.lstrip("-* ").strip()
                if item and item.lower() not in ("nothing", "none"):
                    needs.append(item)
            elif line:
                collecting = False
    return Skim(summary=summary, needs_human=needs)


def verdict(text: str) -> bool | None:
    """Extract the reviewer's verdict. None means it never rendered one.

    None is not a pass. A reviewer that ran out of turns mid-analysis produces
    no verdict line, and treating that as approval defeats the gate.
    """
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line == "VERDICT: PASS":
            return True
        if line == "VERDICT: FAIL":
            return False
    return None

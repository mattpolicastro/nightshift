"""What specloop tells a human, and how it says it.

Two kinds. An ACTION means a run is waiting on you: it leads with what to do,
in one line, then at most one line of why, then the command to run. An FYI is
one line and never needs a reply; `SPECLOOP_SLACK_FYI=0` keeps FYIs out of
Slack. The full detail stays in `nightshift status <change>`, not in the message.

(2026-10-20: the first version sent "paused — <reason>" plus up to 600
characters of raw detail, and the request was buried in it.)
"""

import time
from dataclasses import dataclass

ACTION, FYI = "action", "fyi"


@dataclass(frozen=True)
class Message:
    kind: str
    change: str
    headline: str
    why: str = ""
    command: str = ""
    link: str = ""

    def text(self) -> str:
        if self.kind == FYI:
            return f"FYI ({self.change}): {self.headline}"
        icon = "🔴" if self.headline.startswith("Security:") else "🟡"
        lines = [f"{icon} *{self.headline}*  ({self.change})"]
        lines += [x for x in (self.why, f"`{self.command}`" if self.command else "", self.link) if x]
        return "\n".join(lines)


def first_line(text: str, limit: int = 160) -> str:
    for line in text.splitlines():
        line = line.strip(" -*`#")
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def actions_for_pause(reason: str) -> list[str]:
    """The interface verbs allowed for a pause; every pause permits abort."""
    table = {
        "phase_gate": ["resume"],
        "gate_evidence": ["resume"],
        "coverage": ["resume"],
        "infra": ["resume"],
        "violation_repeat": ["resume"],
        "review_invalid": ["resume"],
        "requested": ["resume"],
        "stuck": ["resume", "accept"],
        "needs_human": ["resume", "accept"],
        "blocked": ["resume"],
        "seal": ["resume"],
        "ready_to_publish": ["publish"],
        "human_checks": ["finish"],
    }
    return [*table.get(reason, []), "abort"]


def for_pause(change: str, reason: str, *, label: str = "", key: str = "", detail: str = "",
              attempts: int = 0, done: int = 0, pr: str = "", resume_at: float = 0) -> Message:
    """The message for a pause: what to do first, then why, then the command."""
    c, task = change, f"task {label}" if label else "a task"
    section = key.split(":", 1)[1] if key.startswith("section:") else ""
    why = first_line(detail)
    resume, note = f"nightshift resume {c}", f'nightshift resume {c} --note "…"'
    table = {
        "phase_gate": (f"Review section {section} on the branch", "Its tasks landed; the run waits for your look.", resume),
        "stuck": (f"Decide how to unstick {task} ({attempts} attempts failed)", f"Last finding: {why}",
                  f"{note}   or   nightshift accept {c}"),
        "blocked": (f"Clarify the spec: {task} is blocked", f"The agent says: {why}", note),
        "needs_human": (f"Answer a spec question for {task}", why, note),
        "violation_repeat": (f"Fix the task or the manifest: {task} keeps changing files it may not", why, resume),
        "review_invalid": (f"Check the reviewer: its output on {task} keeps failing validation", why, resume),
        "seal": (f"Inspect {task}'s work tree: the harness could not seal it", why, resume),
        "secret": (f"Security: inspect {task}, a credential value appeared in its changes", "Nothing was committed.",
                   f"nightshift status {c}"),
        "gate_evidence": (f"Look at section {section}'s tests: they still fall short after strengthening", why, resume),
        "coverage": ("Decide on scenarios that are still untested", why, resume),
        "acceptance": ("Look at the final checks: they still fail", why, f"nightshift status {c}"),
        "infra": (f"Check the provider: {task}'s agent keeps failing to run", f"{why} (auto-retried; this needs a look)",
                  resume),
        "ready_to_publish": (f"Publish: all {done} tasks landed and passed the final checks", "", f"nightshift publish {c}"),
        "human_checks": ("Make the human checks on the PR, then finish", "", f"nightshift finish {c}"),
    }
    if reason == "requested":
        return Message(FYI, c, "paused at a task boundary, as asked.")
    if reason == "usage_limit":
        when = time.strftime("%a %H:%M", time.localtime(resume_at)) if resume_at else "when the window resets"
        return Message(FYI, c, f"waiting on usage limits; resumes automatically at {when}.")
    headline, why_line, command = table.get(reason, (f"Look at the run: paused ({reason})", why, f"nightshift status {c}"))
    return Message(ACTION, c, headline, why_line, command, pr)


def fyi(change: str, text: str) -> Message:
    return Message(FYI, change, text)


def evidence_line(section: int, summary: str) -> str:
    """'section 2 passed its evidence gate — 6 scenarios covered, 10/10 mutants caught.'"""
    parts = []
    for line in summary.splitlines():
        line = line.strip()
        if "scenario(s) covered" in line:
            parts.append(line.split(": ", 1)[-1].replace("scenario(s)", "scenarios"))
        elif line.startswith("mutation:"):
            parts.append(line.split(": ", 1)[1].replace(" caught", " mutants caught"))
    return f"section {section} passed its evidence gate — {', '.join(parts) or 'ok'}."

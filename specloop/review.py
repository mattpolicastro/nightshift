"""Review context and the rules that turn a review into a decision.

The rules, not the reviewer's verdict, decide: a blocking finding without a
valid basis is downgraded, a waived finding is dropped, and a PASS that still
carries a valid blocking finding is a FAIL.
"""

import re
from dataclasses import dataclass, field

FIRST = ("scenario:", "task", "weakened-test", "scope", "input:")
LATER = ("scenario:", "weakened-test", "input:")
GATE = ("scenario:", "requirement:", "integration:", "input:")  # a whole-section review, every round


@dataclass
class Decision:
    verdict: str  # PASS | FAIL | NEEDS_HUMAN
    blocking: list[dict] = field(default_factory=list)
    followups: list[dict] = field(default_factory=list)
    needs_human: list[str] = field(default_factory=list)


def _valid(basis: str, attempt: int, prior_unresolved: bool, first=FIRST, later=LATER) -> bool:
    allowed = first if attempt <= 1 or prior_unresolved else later
    return any(basis.strip().lower().startswith(a) for a in allowed)


def decide(review: dict, attempt: int, waived: list[str], first=FIRST, later=LATER) -> Decision:
    unresolved = [p["finding"] for p in review.get("prior", []) if not p.get("resolved")]
    d = Decision("PASS", needs_human=[s for s in review.get("needs_human", []) if s.strip()])
    for f in review.get("findings", []):
        if any(w.lower() in f["text"].lower() for w in waived if w):
            continue
        reasserts = any(_overlap(f["text"], u) for u in unresolved)
        if f.get("blocking") and _valid(f.get("basis", ""), attempt, reasserts, first, later):
            d.blocking.append(f)
        else:
            d.followups.append(f)
    if d.blocking:
        d.verdict = "NEEDS_HUMAN" if d.needs_human else "FAIL"
    return d


def _overlap(a: str, b: str) -> bool:
    """A loose match between a new finding and a prior unresolved one."""
    wa, wb = set(re.findall(r"[a-z0-9_]{4,}", a.lower())), set(re.findall(r"[a-z0-9_]{4,}", b.lower()))
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.5


def feedback(d: Decision) -> str:
    lines = ["The independent reviewer rejected the previous attempt. Blocking findings:"]
    lines += [f"- [{f['basis']}] {f['text']}" for f in d.blocking]
    return "\n".join(lines)


def test_edits(diff: str, is_test=lambda p: "test" in p.lower()) -> str:
    """Existing test lines the diff removes or rewrites, per file."""
    out, path, removed = [], None, []

    def flush():
        if path and removed and is_test(path):
            out.append(f"## {path}\n" + "\n".join(removed))

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            path, removed = line.split(" b/", 1)[-1], []
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line)
    flush()
    return "\n\n".join(out) or "(no existing test lines were changed or removed)"

"""Evidence at a section boundary: did this section's tests cover its scenarios,
and would they catch a plausible bug in the code it added?

The mutation check is deliberately crude and language-agnostic: single-line
textual mutations (comparison, equality, boolean, arithmetic operators, integer
literals) on lines the section added under `mutation_paths`, each rebuilt and
tested with the manifest's `mutation_steps` under the verify sandbox. A mutant
that does not compile is discarded, not counted. Equivalent mutants exist, so
the threshold is below 1.

Hand-run on 2026-10-20 against the a sample module core, the same idea separated
the inherited tests (13/15 planted bugs caught) from the phase-1 tests (15/15).
"""

import json
import random
import re
import signal
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from specloop import verify
from specloop.manifest import Manifest, matches
from specloop.seal import git

OPERATORS = [
    ("<=", re.compile(r"(?<![<>=!])<=(?!=)"), "<"),
    (">=", re.compile(r"(?<![<>=!-])>=(?!=)"), ">"),
    ("<", re.compile(r"(?<![<=-])\s<\s(?![<=])"), " <= "),
    (">", re.compile(r"(?<![>=-])\s>\s(?![>=])"), " >= "),
    ("==", re.compile(r"=="), "!="),
    ("!=", re.compile(r"!="), "=="),
    ("&&", re.compile(r"&&"), "||"),
    ("||", re.compile(r"\|\|"), "&&"),
    ("and", re.compile(r"(?<=\S) and (?=\S)"), " or "),
    ("or", re.compile(r"(?<=\S) or (?=\S)"), " and "),
    ("True", re.compile(r"\bTrue\b"), "False"),
    ("False", re.compile(r"\bFalse\b"), "True"),
    ("+", re.compile(r"(?<=\w|\))\s\+\s(?=[\w(])"), " - "),
    ("-", re.compile(r"(?<=\w|\))\s-\s(?=[\w(])"), " + "),
    ("int", re.compile(r"(?<![\w.])([0-9]+)(?![\w.])"), None),  # k -> k+1
]
SKIP = re.compile(r"^\s*(#|//|/\*|\*|import |from |using |template|static_assert)")
# A human- or agent-written claim, judged by the reviewer, that mutating this
# line cannot change behaviour. Such mutants are excluded from the score.
EQUIVALENT = "mutation-equivalent:"


@dataclass
class Mutant:
    path: str
    line: int
    op: str
    before: str
    after: str
    result: str = ""  # killed | survived | invalid | equivalent


@dataclass
class Evidence:
    section: int
    covered: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    mutants: list[Mutant] = field(default_factory=list)
    skipped: str = ""
    baseline_failed: bool = False  # the unmutated head failed mutation_steps: no evidence at all

    @property
    def score(self) -> float | None:
        valid = [x for x in self.mutants if x.result in ("killed", "survived")]
        return sum(x.result == "killed" for x in valid) / len(valid) if valid else None

    def passed(self, minimum: float) -> bool:
        if self.uncovered or self.baseline_failed:
            return False
        return self.score is None or self.score >= minimum

    def summary(self) -> str:
        lines = [f"section {self.section}: {len(self.covered)} scenario(s) covered"
                 + (f", UNCOVERED: {', '.join(self.uncovered)}" if self.uncovered else "")]
        if self.score is not None:
            valid = [x for x in self.mutants if x.result != "invalid"]
            equivalent = sum(x.result == "equivalent" for x in self.mutants)
            lines.append(f"mutation: {sum(x.result == 'killed' for x in valid if x.result != 'equivalent')}/"
                         f"{len([x for x in valid if x.result != 'equivalent'])} caught"
                         + (f" ({equivalent} marked equivalent)" if equivalent else ""))
            lines += [f"  survived: {x.path}:{x.line} {x.op}  `{x.after.strip()[:90]}`"
                      for x in self.mutants if x.result == "survived"]
        elif self.baseline_failed:
            lines.append("mutation: the unmutated head fails mutation_steps, so nothing could be measured")
        elif self.skipped:
            lines.append(f"mutation: skipped ({self.skipped})")
        return "\n".join(lines)


def added_lines(diff: str, paths: tuple[str, ...]) -> list[tuple[str, int, str]]:
    out, path, line = [], None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("@@"):
            line = int(re.search(r"\+(\d+)", raw).group(1))
        elif path is None:
            continue
        elif raw.startswith("+"):
            if matches(path, paths) and not SKIP.match(raw[1:]) and raw[1:].strip():
                out.append((path, line, raw[1:]))
            line += 1
        elif not raw.startswith("-"):
            line += 1
    return out


def generate(lines: list[tuple[str, int, str]], count: int, seed: int) -> list[Mutant]:
    options = []
    for path, n, text in lines:
        for name, rx, repl in OPERATORS:
            for m in rx.finditer(text):
                if name == "int":
                    new = text[:m.start()] + str(int(m.group(1)) + 1) + text[m.end():]
                else:
                    new = text[:m.start()] + repl + text[m.end():]
                options.append(Mutant(path, n, name, text, new))
    random.Random(seed).shuffle(options)
    seen, picked = set(), []
    for mut in options:  # at most one mutant per line, spread across the diff
        if (mut.path, mut.line) not in seen:
            seen.add((mut.path, mut.line))
            picked.append(mut)
        if len(picked) == count:
            break
    return picked


BUILD_STEPS = ("configure", "build")
# A mutant gets this many times the baseline's duration per step (at least
# MIN_MUTANT_SECONDS): a mutant that hangs is caught by its timeout instead of
# holding the gate for the step's full ceiling (15 minutes for Nightshift's
# suite, seen on 2026-10-20).
MUTANT_TIME_FACTOR = 3
MIN_MUTANT_SECONDS = 30.0


def _run_steps(steps, values, sb: Path, env: dict, cwd: Path, budget: dict | None = None) -> str | None:
    """None when every step met its expectation, else the id of the first that did not."""
    for step in steps:
        limit = min(step.timeout, (budget or {}).get(step.id, step.timeout))
        argv = [verify.expand(a, values) for a in step.run]
        p = subprocess.Popen(verify.wrap(argv, sb), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=env, cwd=cwd, start_new_session=True)
        try:
            code = p.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            return step.id
        if code < 0 or code != step.expect:
            return step.id
    return None


def mutate(repo: Path, head: str, mutants: list[Mutant], m: Manifest, out: Path) -> bool:
    """Classify each mutant. False when the unmutated head itself fails."""
    steps = tuple(s for s in m.verify if s.id in m.mutation_steps)
    src, build = out / "src", out / "build"
    receipts = verify.run_steps(repo, head, steps, m, out)
    if not verify.ok(receipts, m.prepare + steps):
        for mut in mutants:
            mut.result = "invalid"
        verify.cleanup(out)
        return False
    sb = out / "mutation.sb"
    sb.write_text(verify.profile(build, m))
    values = {**m.vars, "src": str(src), "build": str(build), "sha": head}
    env = {"PATH": verify.PATH, "HOME": str(build / "home"), "TMPDIR": str(build / "tmp"),
           "LANG": "en_US.UTF-8", "COPYFILE_DISABLE": "1"}
    # Mutants are time-boxed against the unmutated baseline's own step times.
    budget = {r.id: max(MIN_MUTANT_SECONDS, MUTANT_TIME_FACTOR * r.seconds) for r in receipts if r.seconds}
    for number, mut in enumerate(mutants, 1):
        f = src / mut.path
        original = f.read_text()
        lines = original.splitlines(keepends=True)
        idx = mut.line - 1
        if idx >= len(lines) or lines[idx].rstrip("\n") != mut.before:
            mut.result = "invalid"
            continue
        if EQUIVALENT in lines[idx] or (idx > 0 and EQUIVALENT in lines[idx - 1]):
            mut.result = "equivalent"
            continue
        lines[idx] = mut.after + ("\n" if lines[idx].endswith("\n") else "")
        mode = f.stat().st_mode
        f.chmod(mode | 0o200)
        f.write_text("".join(lines))
        f.chmod(mode)
        try:
            if m.prepare:
                # Preparation may copy or transform source into the build tree.
                # Rebuild that tree from this mutant, without stale baseline or
                # previous-mutant artifacts, and keep each run's receipts.
                mutant_out = out / f"mutant-{number}"
                try:
                    receipts = verify.run_extracted(src, head, steps, m, mutant_out)
                    failed = next((r.id for r in receipts if not r.passed), None)
                finally:
                    verify.cleanup(mutant_out)
            else:
                failed = _run_steps(steps, values, sb, env, build, budget=budget)
            invalid_steps = set(BUILD_STEPS) | {s.id for s in m.prepare}
            mut.result = "survived" if failed is None else "invalid" if failed in invalid_steps else "killed"
        finally:
            f.chmod(mode | 0o200)
            f.write_text(original)
            f.chmod(mode)
    verify.cleanup(out)
    return True


def gather(repo: Path, base: str, head: str, section_scenarios: list[str], seen: set[str],
           m: Manifest, out: Path, section: int) -> Evidence:
    ev = Evidence(section)
    wanted = [s for s in section_scenarios if s not in m.coverage_exempt]
    ev.covered = [s for s in wanted if s in seen]
    ev.uncovered = [s for s in wanted if s not in seen]
    if not m.mutation_steps:
        ev.skipped = "no mutation_steps in the manifest"
    elif not base:
        ev.skipped = "section started before evidence was recorded"
    else:
        diff = git(repo, "diff", "--unified=0", base, head).decode(errors="replace")
        ev.mutants = generate(added_lines(diff, m.mutation_paths), m.mutation_count, seed=section)
        if not ev.mutants:
            ev.skipped = "no mutable lines added under mutation_paths"
        else:
            ev.baseline_failed = not mutate(repo, head, ev.mutants, m, out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence.json").write_text(json.dumps({**asdict(ev), "score": ev.score}, indent=2))
    return ev

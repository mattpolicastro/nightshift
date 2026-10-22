"""Evidence at a section boundary: did this section's tests cover its scenarios,
and would they catch a plausible bug in the code it added?

The mutation check uses single-line textual mutations (comparison, equality,
boolean, arithmetic operators, integer literals) on lines the section added
under `mutation_paths`, excluding Python docstrings and comments using committed
source context. Each mutant is rebuilt and tested with the manifest's
`mutation_steps` under the verify sandbox. A mutant
that does not compile is discarded, not counted. Equivalent mutants exist, so
the threshold is below 1.

Hand-run on 2026-10-20 against the a sample module core, the same idea separated
the inherited tests (13/15 planted bugs caught) from the phase-1 tests (15/15).
"""

import ast
import io
import json
import random
import re
import shutil
import tokenize
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from specloop import verify
from specloop.manifest import Manifest, Step, matches
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
PYTHON_SKIP = re.compile(r"^\s*(import |from |using |template|static_assert)")
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
    result: str = ""  # killed | survived | timeout | invalid | equivalent


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

    @property
    def unmeasured(self) -> bool:
        """Mutants were generated but none could be judged: no evidence, not a pass.

        (2026-10-21: a prepare sandbox that could not read a mutant's source made
        every mutant invalid, and two changes passed their gates on nothing.)
        """
        return bool(self.mutants) and not any(x.result in ("killed", "survived", "equivalent")
                                                for x in self.mutants)

    def passed(self, minimum: float) -> bool:
        if self.uncovered or self.baseline_failed or self.unmeasured:
            return False
        return self.score is None or self.score >= minimum

    def summary(self) -> str:
        lines = [f"section {self.section}: {len(self.covered)} scenario(s) covered"
                 + (f", UNCOVERED: {', '.join(self.uncovered)}" if self.uncovered else "")]
        timeouts = sum(x.result == "timeout" for x in self.mutants)
        if self.score is not None:
            valid = [x for x in self.mutants if x.result in ("killed", "survived")]
            equivalent = sum(x.result == "equivalent" for x in self.mutants)
            lines.append(f"mutation: {sum(x.result == 'killed' for x in valid)}/{len(valid)} caught"
                         + (f" ({equivalent} marked equivalent)" if equivalent else ""))
            lines += [f"  survived: {x.path}:{x.line} {x.op}  `{x.after.strip()[:90]}`"
                      for x in self.mutants if x.result == "survived"]
        elif self.baseline_failed:
            lines.append("mutation: the unmutated head fails mutation_steps, so nothing could be measured")
        elif self.unmeasured:
            outcomes = "invalid or timed out" if timeouts else "invalid"
            lines.append(f"mutation: none of the {len(self.mutants)} mutants could be judged (every one was "
                         f"{outcomes}), so nothing was measured; check mutation_steps and any prepare steps")
        elif self.skipped:
            lines.append(f"mutation: skipped ({self.skipped})")
        if timeouts:
            lines.append(f"{timeouts} of {len(self.mutants)} mutants timed out and were not counted")
        return "\n".join(lines)


def _is_python(path: str) -> bool:
    return Path(path).suffix in (".py", ".pyi")


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
            skip = PYTHON_SKIP if _is_python(path) else SKIP
            if matches(path, paths) and not skip.match(raw[1:]) and raw[1:].strip():
                out.append((path, line, raw[1:]))
            line += 1
        elif not raw.startswith("-"):
            line += 1
    return out


def _python_protected_spans(path: str, source: str) -> dict[int, list[tuple[int, int]]]:
    """Map docstrings and comments to physical lines and character offsets."""
    source_lines = source.split("\n")
    spans: dict[int, list[tuple[int, int]]] = {}

    def protect(start: tuple[int, int], end: tuple[int, int]) -> None:
        for line in range(start[0], end[0] + 1):
            left = start[1] if line == start[0] else 0
            right = end[1] if line == end[0] else len(source_lines[line - 1])
            spans.setdefault(line, []).append((left, right))

    def character_column(line: int, byte_column: int) -> int:
        # AST columns count UTF-8 bytes; tokenize and regex matches count characters.
        return len(source_lines[line - 1].encode("utf-8")[:byte_column].decode("utf-8"))

    try:
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    value = first.value
                    protect((value.lineno, character_column(value.lineno, value.col_offset)),
                            (value.end_lineno, character_column(value.end_lineno, value.end_col_offset)))
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                protect(token.start, token.end)
    except (SyntaxError, ValueError, tokenize.TokenError) as exc:
        raise ValueError(f"cannot analyze Python source {path}: {exc}") from exc
    return spans


def generate(lines: list[tuple[str, int, str]], count: int, seed: int, *,
             sources: dict[str, str],
             mutation_skip: tuple[str, ...] = ()) -> list[Mutant]:
    protected = {}
    # Analyze each complete file before exclusions, so even an excluded line
    # cannot turn a source-analysis failure into a successful skip.
    for path in dict.fromkeys(path for path, _, _ in lines):
        if path not in sources:
            raise ValueError(f"missing committed source for {path}")
        protected[path] = _python_protected_spans(path, sources[path]) if _is_python(path) else {}
    options = []
    for path, n, text in lines:
        if any(pattern in text for pattern in mutation_skip):
            continue
        for name, rx, repl in OPERATORS:
            for m in rx.finditer(text):
                if any(m.start() < end and m.end() > start for start, end in protected[path].get(n, ())):
                    continue
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


def _mutate_one(number: int, mut: Mutant, baseline_src: Path, head: str,
                steps: tuple[Step, ...], m: Manifest, out: Path) -> str:
    # mutation-equivalent: Docstring wording only; execution never reads this function's __doc__.
    """Run one mutation in a private source and build, retaining its diagnostics."""
    mutant_out = out / f"mutant-{number}"
    try:
        lines = (baseline_src / mut.path).read_text().splitlines(keepends=True)
        idx = mut.line - 1
        if idx < 0 or idx >= len(lines) or lines[idx].rstrip("\n") != mut.before:
            return "invalid"
        if EQUIVALENT in lines[idx] or (idx > 0 and EQUIVALENT in lines[idx - 1]):
            return "equivalent"

        src = mutant_out / "src"
        # copytree copies file contents, never hard-links the immutable baseline.
        shutil.copytree(baseline_src, src, symlinks=True)
        f = src / mut.path
        lines[idx] = mut.after + ("\n" if lines[idx].endswith("\n") else "")
        mode = f.stat().st_mode
        f.chmod(mode | 0o200)
        f.write_text("".join(lines))
        f.chmod(mode)
        receipts = verify.run_extracted(src, head, steps, m, mutant_out)
        failed = next((r for r in receipts if not r.passed), None)
        invalid_steps = set(BUILD_STEPS) | {s.id for s in m.prepare}
        if failed is None:
            return "survived"
        if failed.id in invalid_steps:
            return "invalid"
        return "timeout" if failed.exit is None else "killed"
    finally:
        verify.cleanup(mutant_out)


def mutate(repo: Path, head: str, mutants: list[Mutant], m: Manifest, out: Path) -> bool:
    # mutation-equivalent: False describes the return contract here; this docstring is not executed or read.
    """Classify each mutant. False when the unmutated head itself fails."""
    steps = tuple(s for s in m.verify if s.id in m.mutation_steps)
    try:
        receipts = verify.run_steps(repo, head, steps, m, out)
        if not verify.ok(receipts, m.prepare + steps):
            for mut in mutants:
                mut.result = "invalid"
            return False
        # Snapshot the baseline's per-step limits once, before any mutant runs.
        # Copies keep the baseline and shared manifest at their original ceilings.
        budget = {r.id: max(MIN_MUTANT_SECONDS, MUTANT_TIME_FACTOR * r.seconds)
                  for r in receipts if r.seconds is not None and r.seconds > 0}
        steps = tuple(replace(s, timeout=min(s.timeout, budget.get(s.id, s.timeout)))
                      for s in steps)
        mutant_manifest = replace(m, prepare=tuple(
            replace(s, timeout=min(s.timeout, budget.get(s.id, s.timeout)))
            for s in m.prepare))
        if mutants:
            # The pool drains even on an unexpected worker error, before the
            # outer finally releases the baseline source shared by all workers.
            with ThreadPoolExecutor(max_workers=min(m.mutation_parallel, len(mutants))) as pool:
                futures = {
                    pool.submit(_mutate_one, index + 1, mut, out / "src", head,
                                steps, mutant_manifest, out): index
                    for index, mut in enumerate(mutants)
                }
                for future in as_completed(futures):
                    mutants[futures[future]].result = future.result()
        return True
    finally:
        verify.cleanup(out)


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
        lines = added_lines(diff, m.mutation_paths)
        sources = {}
        for path in dict.fromkeys(path for path, _, _ in lines):
            try:
                sources[path] = git(repo, "show", f"{head}:{path}").decode()
            except (RuntimeError, OSError, UnicodeError) as exc:
                raise ValueError(f"cannot read committed source {path} at {head}: {exc}") from exc
        ev.mutants = generate(lines, m.mutation_count, seed=section,
                              sources=sources, mutation_skip=m.mutation_skip)
        if not ev.mutants:
            ev.skipped = "no mutable lines added under mutation_paths"
        else:
            ev.baseline_failed = not mutate(repo, head, ev.mutants, m, out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence.json").write_text(json.dumps({**asdict(ev), "score": ev.score}, indent=2))
    return ev

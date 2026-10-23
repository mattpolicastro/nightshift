"""Evidence at a section boundary: did this section's tests cover its scenarios,
and would they catch a plausible bug in the code it added?

The mutation check uses single-line textual mutations (comparison, equality,
boolean, arithmetic operators, integer literals) on lines the section added
under `mutation_paths`, excluding Python docstrings and comments and supported
non-Python comment blocks using committed source context. Each mutant is rebuilt
and tested with the manifest's `mutation_steps` under the verify sandbox. A mutant
that does not compile is discarded, not counted. Equivalent mutants exist, so
the threshold is below 1.

Hand-run on 2026-10-20 against the a sample module core, the same idea separated
the inherited tests (13/15 planted bugs caught) from the phase-1 tests (15/15).
"""

import ast
import fnmatch
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
        """Mutants were generated but none were killed or survived: no measurement.

        (2026-10-21: a prepare sandbox that could not read a mutant's source made
        every mutant invalid, and two changes passed their gates on nothing.)
        """
        return bool(self.mutants) and not any(x.result in ("killed", "survived")
                                                for x in self.mutants)

    def passed(self, minimum: float) -> bool:
        if self.uncovered or self.baseline_failed or self.unmeasured:
            return False
        return self.score is None or self.score >= minimum

    def summary(self) -> str:
        lines = [f"section {self.section}: {len(self.covered)} scenario(s) covered"
                 + (f", UNCOVERED: {', '.join(self.uncovered)}" if self.uncovered else "")]
        timeouts = sum(x.result == "timeout" for x in self.mutants)
        equivalent = sum(x.result == "equivalent" for x in self.mutants)
        pending = sum(x.result == "" for x in self.mutants)
        if self.score is not None:
            valid = [x for x in self.mutants if x.result in ("killed", "survived")]
            lines.append(f"mutation: {sum(x.result == 'killed' for x in valid)}/{len(valid)} caught"
                         + (f" ({equivalent} marked equivalent)" if equivalent else ""))
            lines += [f"  survived: {x.path}:{x.line} {x.op}  `{x.after.strip()[:90]}`"
                      for x in self.mutants if x.result == "survived"]
        elif self.baseline_failed:
            lines.append("mutation: the unmutated head fails mutation_steps, so nothing could be measured")
        elif self.unmeasured:
            if equivalent or pending:
                outcomes = []
                if equivalent:
                    outcomes.append(f"{equivalent} marked equivalent")
                if pending:
                    outcomes.append(f"{pending} pending")
                lines.append(f"mutation: none of the {len(self.mutants)} mutants were killed or survived "
                             f"({', '.join(outcomes)}), so nothing was measured; "
                             + ("review equivalence annotations and " if equivalent else "")
                             + "check mutation_steps and any prepare steps")
            else:
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


@dataclass
class _PythonAnalysis:
    protected: dict[int, list[tuple[int, int]]]
    conditions: dict[int, tuple[int, int]]
    returns: dict[int, tuple[int, int]]


def _python_protected_spans(path: str, source: str) -> _PythonAnalysis:
    """Map docstrings and comments to physical lines and character offsets."""
    source_lines = source.split("\n")
    spans: dict[int, list[tuple[int, int]]] = {}
    conditions: dict[int, tuple[int, int]] = {}
    returns: dict[int, tuple[int, int]] = {}

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
        tokens_by_line = {}
        standalone_returns = set()
        previous_type = None
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.string == "return" and previous_type in (
                    tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                standalone_returns.add(token.start[0])
            if token.type not in (tokenize.COMMENT, tokenize.NL):
                previous_type = token.type
            if token.type == tokenize.COMMENT:
                protect(token.start, token.end)
            elif token.type not in (tokenize.INDENT, tokenize.DEDENT, tokenize.NL):
                tokens_by_line.setdefault(token.start[0], []).append(token)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    value = first.value
                    protect((value.lineno, character_column(value.lineno, value.col_offset)),
                            (value.end_lineno, character_column(value.end_lineno, value.end_col_offset)))
            if (isinstance(node, (ast.If, ast.While)) and isinstance(node.test, ast.Name)
                    and node.lineno == node.test.lineno == node.test.end_lineno
                    and node.body[0].lineno > node.lineno):
                header = tokens_by_line.get(node.lineno, [])
                # The real keyword excludes elif; a colon followed by NEWLINE
                # on this physical line excludes continued and inline suites.
                if (len(header) >= 3 and header[0].string in ("if", "while")
                        and header[0].start[1] == character_column(node.lineno, node.col_offset)
                        and header[-2].string == ":" and header[-1].type == tokenize.NEWLINE):
                    conditions[node.lineno] = (
                        character_column(node.lineno, node.test.col_offset),
                        character_column(node.lineno, node.test.end_col_offset))
            # Each suite supplies only its own immediate continuation. An elif
            # belongs to the initial if; an ordinary if inside else does not.
            for _, suite in ast.iter_fields(node):
                if not isinstance(suite, list):
                    continue
                for guard in suite[:-1]:
                    if not isinstance(guard, ast.If):
                        continue
                    branches = [guard.body]
                    while (len(guard.orelse) == 1 and isinstance(guard.orelse[0], ast.If)
                           and tokens_by_line[guard.orelse[0].lineno][0].string == "elif"):
                        guard = guard.orelse[0]
                        branches.append(guard.body)
                    branches.append(guard.orelse)
                    for branch in branches:
                        ret = branch[-1] if branch else None
                        if (not isinstance(ret, ast.Return) or ret.lineno != ret.end_lineno
                                or ret.lineno not in standalone_returns):
                            continue
                        start = character_column(ret.lineno, ret.col_offset)
                        end = character_column(ret.lineno, ret.end_col_offset)
                        line = source_lines[ret.lineno - 1]
                        suffix = line[end:].lstrip()
                        if (not line[:start].strip() and (not suffix or suffix.startswith("#"))
                                and tokens_by_line[ret.lineno][-1].type == tokenize.NEWLINE):
                            returns[ret.lineno] = (start, end)
    except (SyntaxError, ValueError, tokenize.TokenError) as exc:
        raise ValueError(f"cannot analyze Python source {path}: {exc}") from exc
    return _PythonAnalysis(spans, conditions, returns)


def _non_python_protected_spans(source: str) -> dict[int, list[tuple[int, int]]]:
    """Protect only complete blocks with comment-only opening and closing lines.

    This is line-level tracking, with no string or inline-comment parsing.
    The existing prefix exclusions remain in added_lines.
    """
    source_lines = source.split("\n")
    spans = {}
    start = None
    for line, text in enumerate(source_lines, 1):
        stripped = text.lstrip()
        if start is None:
            if not stripped.startswith("/*"):
                continue
            start = line
            end = stripped.find("*/", 2)
        else:
            end = stripped.find("*/")
        if end == -1:
            continue
        # The first terminator ends tracking even when code follows it;
        # a later comment-only terminator must not validate that mixed block.
        if not stripped[end + 2:].strip():
            for protected_line in range(start, line + 1):
                spans[protected_line] = [(0, len(source_lines[protected_line - 1]))]
        start = None
    return spans


def generate(lines: list[tuple[str, int, str]], count: int, seed: int, *,
             sources: dict[str, str],
             mutation_skip: tuple[str, ...] = ()) -> list[Mutant]:
    protected = {}
    conditions = {}
    returns = {}
    # Analyze each complete file before exclusions, so even an excluded line
    # cannot turn a source-analysis failure into a successful skip.
    for path in dict.fromkeys(path for path, _, _ in lines):
        if path not in sources:
            raise ValueError(f"missing committed source for {path}")
        if _is_python(path):
            analysis = _python_protected_spans(path, sources[path])
            protected[path], conditions[path] = analysis.protected, analysis.conditions
            returns[path] = analysis.returns
        else:
            protected[path] = _non_python_protected_spans(sources[path])
    options = []
    for path, n, text in lines:
        if any(pattern in text for pattern in mutation_skip):
            continue
        for name, rx, repl in OPERATORS:
            for m in rx.finditer(text):
                # Comments and non-Python spans end at EOL; valid Python needs a
                # separator after a docstring before a current operator can match.
                # mutation-equivalent: No match can start at a span's end, so < and <= reject the same candidates.
                if any(m.start() < end and m.end() > start for start, end in protected[path].get(n, ())):
                    continue
                if name == "int":
                    new = text[:m.start()] + str(int(m.group(1)) + 1) + text[m.end():]
                else:
                    new = text[:m.start()] + repl + text[m.end():]
                options.append(Mutant(path, n, name, text, new))
        if n in conditions.get(path, {}):
            start, end = conditions[path][n]
            if not any(start < right and end > left for left, right in protected[path].get(n, ())):
                options.append(Mutant(path, n, "negate-condition", text,
                                      text[:start] + "not " + text[start:]))
        if n in returns.get(path, {}):
            start, end = returns[path][n]
            if not any(start < right and end > left for left, right in protected[path].get(n, ())):
                options.append(Mutant(path, n, "drop-return", text, text[:start] + "pass" + text[end:]))
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


@dataclass(frozen=True)
class _MutationBudgets:
    prepare: tuple[Step, ...]
    steps: tuple[Step, ...]


def _mutation_budgets(prepare: tuple[Step, ...], steps: tuple[Step, ...],
                      receipts: list[verify.Receipt]) -> _MutationBudgets:
    """Snapshot limits from one sequence's full baseline without changing ceilings.

    Shared step IDs are local to this baseline. Frozen steps retain these limits
    independently of receipt changes, sibling execution, or time in the queue.
    """
    budget = {r.id: max(MIN_MUTANT_SECONDS, MUTANT_TIME_FACTOR * r.seconds)
              for r in receipts if r.seconds is not None and r.seconds > 0}

    def limited(group: tuple[Step, ...]) -> tuple[Step, ...]:
        return tuple(replace(s, timeout=min(s.timeout, budget.get(s.id, s.timeout)))
                     for s in group)

    return _MutationBudgets(limited(prepare), limited(steps))


def resolve_mutation_steps(path: str, m: Manifest) -> tuple[int | None, tuple[Step, ...]]:
    """Resolve a repository-relative POSIX path against a validated manifest.

    Return the one-based route index (None for fallback) and immutable steps.
    Route globs use case-sensitive fnmatch: * crosses / and ** adds no special
    directory semantics. Routes preserve their listed step order; fallback
    preserves verify declaration order, then mutation-only declaration order.
    The ordered tuple of step IDs can be used as a baseline key independently
    of the route index. This helper neither executes steps nor changes m.
    """
    definitions = m.verify + m.mutation_step
    for index, route in enumerate(m.mutation_routes, 1):
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in route.paths):
            by_id = {step.id: step for step in definitions}
            return index, tuple(by_id[step_id] for step_id in route.steps)
    return None, tuple(step for step in definitions if step.id in m.mutation_steps)


def _mutate_one(number: int, mut: Mutant, baseline_src: Path, head: str,
                steps: tuple[Step, ...], m: Manifest, out: Path) -> str:
    """Run one mutation in a private source and build, retaining its diagnostics."""
    mutant_out = out / f"mutant-{number}"
    run_dirs = [mutant_out]
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
        narrowed = any("{mutant_path}" in arg for s in steps for arg in s.run)
        uses_path = narrowed or any("{mutant_path}" in arg for s in m.prepare for arg in s.run)
        phases = [(mutant_out, mut.path), (mutant_out / "confirmation", "")] if narrowed else [(mutant_out, "")]
        invalid_steps = set(BUILD_STEPS) | {s.id for s in m.prepare}
        for run_out, path in phases:
            if run_out != mutant_out:
                run_dirs.append(run_out)
            run_m = replace(m, vars={**m.vars, "mutant_path": path}) if uses_path else m
            # Keep the same mutation and baseline budgets in this worker slot.
            # A separate output directory gives confirmation fresh preparation
            # and builds without overwriting the narrowed diagnostics.
            receipts = verify.run_extracted(src, head, steps, run_m, run_out)
            failed = next((r for r in receipts if not r.passed), None)
            if failed is not None:
                if failed.id in invalid_steps:
                    return "invalid"
                return "timeout" if failed.exit is None else "killed"
        return "survived"
    finally:
        for run_out in reversed(run_dirs):
            verify.cleanup(run_out)


def mutate(repo: Path, head: str, mutants: list[Mutant], m: Manifest, out: Path) -> bool:
    """Classify each mutant. False when the unmutated head itself fails."""
    resolved = [resolve_mutation_steps(mut.path, m)[1] for mut in mutants]
    sequences = {tuple(s.id for s in steps): steps for steps in resolved}
    if not mutants:
        # Preserve the direct-call legacy baseline without starting a pool.
        steps = tuple(s for s in m.verify + m.mutation_step if s.id in m.mutation_steps)
        sequences[tuple(s.id for s in steps)] = steps
    baseline_dirs = []
    assignments = {}
    try:
        for number, (key, steps) in enumerate(sequences.items(), 1):
            # Legacy commands keep their original manifest and variables. Direct
            # Manifest construction may omit the reserved placeholder default.
            full_manifest = m
            if any("{mutant_path}" in arg for s in m.prepare + steps for arg in s.run):
                full_manifest = replace(m, vars={**m.vars, "mutant_path": ""})
            baseline_out = out if number == 1 else out / f"baseline-{number}"
            baseline_dirs.append(baseline_out)
            receipts = verify.run_steps(repo, head, steps, full_manifest, baseline_out)
            if not verify.ok(receipts, m.prepare + steps):
                for mut in mutants:
                    mut.result = "invalid"
                return False
            budgets = _mutation_budgets(m.prepare, steps, receipts)
            mutant_manifest = replace(full_manifest, prepare=budgets.prepare)
            assignments[key] = (baseline_out / "src", budgets.steps, mutant_manifest)
        if mutants:
            # The pool drains even on an unexpected worker error, before the
            # outer finally releases any baseline source shared by workers.
            with ThreadPoolExecutor(max_workers=min(m.mutation_parallel, len(mutants))) as pool:
                futures = {}
                for index, (mut, steps) in enumerate(zip(mutants, resolved)):
                    src, limited_steps, mutant_manifest = assignments[tuple(s.id for s in steps)]
                    future = pool.submit(_mutate_one, index + 1, mut, src, head,
                                         limited_steps, mutant_manifest, out)
                    futures[future] = index
                for future in as_completed(futures):
                    mutants[futures[future]].result = future.result()
        return True
    finally:
        for baseline_out in reversed(baseline_dirs):
            verify.cleanup(baseline_out)


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

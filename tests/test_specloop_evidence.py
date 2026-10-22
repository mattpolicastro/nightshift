import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Barrier, Event, Lock

import pytest

from specloop import evidence, manifest, tasks, verify

# Every mutant of this code is observable: boundaries of `within` and both
# terms of `scale`. (A clamp written with early returns has equivalent mutants:
# `x < lo` -> `x <= lo` returns the same value at the boundary.)
CODE = '''def within(x, lo, hi):
    return lo <= x and x <= hi


def scale(x, k):
    return x * k + 1
'''
WEAK = '''import unittest
import calc
class T(unittest.TestCase):
    def test_inside(self):
        self.assertTrue(calc.within(5, 0, 10))
        self.assertEqual(calc.scale(2, 3), 7)
'''
STRONG = WEAK + '''    def test_edges(self):
        self.assertTrue(calc.within(0, 0, 10))
        self.assertTrue(calc.within(10, 0, 10))
        self.assertFalse(calc.within(11, 0, 10))
        self.assertFalse(calc.within(-1, 0, 10))
'''


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def project(tmp_path, tests):
    src = tmp_path / "p"
    (src / "tests").mkdir(parents=True)
    (src / "calc.py").write_text("")
    (src / "tests" / "__init__.py").write_text("")
    sh(tmp_path, "git", "init", "-q", "-b", "main", str(src))
    sh(src, "git", "add", "-A")
    sh(src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    base = sh(src, "git", "rev-parse", "HEAD")
    (src / "calc.py").write_text(CODE)
    (src / "tests" / "test_calc.py").write_text(tests)
    sh(src, "git", "add", "-A")
    sh(src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "section")
    return src, base, sh(src, "git", "rev-parse", "HEAD")


M = manifest.parse({
    "run": {"repo": "/x", "change": "c", "branch": "main"},
    "roles": {"implement": "p", "review": "p"},
    "profiles": {"p": {"adapter": "codex", "model": "m"}},
    "paths": {"allowed": ["calc.py", "tests/**"]},
    "verify": [{"id": "test", "run": ["/usr/bin/env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-m", "unittest",
                                      "discover", "-s", "{src}/tests", "-t", "{src}"], "timeout": 60}],
    "gates": {"evidence": True, "mutation_steps": ["test"], "mutation_paths": ["calc.py"], "mutation_count": 8},
})


def _section_with_evidence(tmp_path, monkeypatch, ev):
    from specloop import loop
    from specloop.state import Run

    run = Run(tmp_path / "run")
    run.state.section_base["1"] = "base"
    # A failing event fixture reaches the existing pause instead of rewriting tasks.
    if not ev.passed(0.5):
        run.state.strengthen_rounds["1"] = 1
    m = replace(M, mutation_min=0.5, coverage_step="")
    ctx = loop.Ctx(run, m, {})
    section = tasks.parse("## 1. Core\n\n- [x] 1.1 Cover `c/r/s`\n")
    calls = []

    def gather(repo, base, head, cited, seen, manifest, out, number):
        calls.append(number)
        assert (repo, base, head) == (ctx.repo, "base", "head")
        assert cited == ["c/r/s"] and seen == {"c/r/s"}
        assert manifest == m and number == ev.section
        return ev

    monkeypatch.setattr(evidence, "gather", gather)
    assert loop.section_evidence(ctx, 1, "head", section) is ev.passed(0.5)
    assert calls == [1]
    return ctx, section


@pytest.mark.scenario("parallel-evidence-mutants/timeout-reporting/event-counts-timeout-mutants")
@pytest.mark.parametrize("uncovered", [False, True])
def test_evidence_event_counts_only_test_timeout_mutants(tmp_path, monkeypatch, uncovered):
    source = tmp_path / "baseline"
    source.mkdir()
    (source / "calc.py").write_text("x = 1\n")
    steps = {name: manifest.Step(name, ("/bin/sh", "-c", "true"))
             for name in ("dependencies", "configure", "build", "check")}
    m = replace(M, prepare=(steps["dependencies"],),
                verify=tuple(steps[name] for name in ("configure", "build", "check")))
    outcomes = [("check", 3), ("check", 0), ("check", None), ("check", None),
                ("dependencies", None), ("configure", None), ("build", None)]
    ev = evidence.Evidence(1, covered=["c/r/s"], uncovered=["c/r/missing"] if uncovered else [])
    for number, (stage, exit_code) in enumerate(outcomes, 1):
        receipt = verify.Receipt(stage, [], 0, exit_code, exit_code == 0, 1, "")
        monkeypatch.setattr(verify, "run_extracted", lambda *args, receipt=receipt: [receipt])
        mut = evidence.Mutant("calc.py", 1, "int", "x = 1", f"x = {number + 1}")
        mut.result = evidence._mutate_one(number, mut, source, "head", m.verify, m, tmp_path / "out")
        ev.mutants.append(mut)
    assert [mut.result for mut in ev.mutants] == [
        "killed", "survived", "timeout", "timeout", "invalid", "invalid", "invalid"]

    ctx, _ = _section_with_evidence(tmp_path, monkeypatch, ev)
    events = [json.loads(line) for line in (ctx.run.dir / "events.jsonl").read_text().splitlines()]
    recorded = [event for event in events if event["event"] == "evidence"]
    assert len(recorded) == 1
    event = recorded[0]
    assert isinstance(event.pop("at"), (int, float))
    assert type(event["timeouts"]) is int
    assert event == {"event": "evidence", "section": 1, "passed": not uncovered, "score": 0.5,
                     "uncovered": ev.uncovered, "survived": ["calc.py:1"], "timeouts": 2}
    assert ev.summary().splitlines().count("2 of 7 mutants timed out and were not counted") == 1


@pytest.mark.scenario("parallel-evidence-mutants/timeout-reporting/zero-timeouts-are-explicit")
@pytest.mark.parametrize("case", ["judged", "skipped", "baseline-timeout"])
def test_evidence_event_reports_explicit_zero_timeouts(tmp_path, monkeypatch, case):
    if case == "judged":
        ev = _evidence_with_results("killed", "survived", "invalid", "equivalent")
    elif case == "skipped":
        ev = evidence.Evidence(1, covered=["c/r/s"], skipped="no mutation_steps in the manifest")
    else:
        ev = _evidence_with_results("", "")

        def baseline(*args):
            return [verify.Receipt("test", [], 0, None, False, 1, "baseline timed out")]

        def unexpected_worker(*args, **kwargs):
            pytest.fail("a baseline timeout must not dispatch mutants")

        monkeypatch.setattr(verify, "run_steps", baseline)
        monkeypatch.setattr(evidence, "ThreadPoolExecutor", unexpected_worker)
        ev.baseline_failed = not evidence.mutate(tmp_path, "head", ev.mutants, M, tmp_path / "out")
        assert ev.baseline_failed and [mut.result for mut in ev.mutants] == ["invalid", "invalid"]
    ctx, _ = _section_with_evidence(tmp_path, monkeypatch, ev)
    events = [json.loads(line) for line in (ctx.run.dir / "events.jsonl").read_text().splitlines()]
    recorded = [event for event in events if event["event"] == "evidence"]
    assert len(recorded) == 1
    event = recorded[0]
    assert isinstance(event.pop("at"), (int, float))
    assert type(event["timeouts"]) is int
    assert event == {"event": "evidence", "section": 1, "passed": case != "baseline-timeout",
                     "score": 0.5 if case == "judged" else None, "uncovered": [],
                     "survived": ["calc.py:2"] if case == "judged" else [], "timeouts": 0}
    assert "timed out and were not counted" not in ev.summary()


@pytest.mark.scenario("parallel-evidence-mutants/timeout-reporting/reviewer-receives-timeout-count")
def test_reviewer_receives_saved_timeout_summary(tmp_path, monkeypatch):
    from specloop import loop
    from specloop.state import Run, TaskState

    ev = _evidence_with_results("killed", "survived", "timeout", "timeout", "invalid", "equivalent")
    ctx, section = _section_with_evidence(tmp_path, monkeypatch, ev)
    # Reload the saved state so the reviewer consumes the durable summary.
    ctx.run = Run(ctx.run.dir)
    assert ctx.run.state.evidenced == [1] and ctx.run.state.pause is None
    summary = ctx.run.state.evidence_summary["1"]
    assert summary == ev.summary()
    monkeypatch.setattr(loop, "_copy_change", lambda *args: None)
    monkeypatch.setattr(loop.openspec, "scenarios", lambda *args: {"c/r/s": "Covered scenario"})
    monkeypatch.setattr(loop, "git", lambda *args: b"section diff\n")
    context = tmp_path / "context"
    loop._gate_context(ctx, 1, "head", section, TaskState(), tmp_path / "checkout", context)
    reviewer = (context / "evidence.md").read_text()
    assert reviewer == summary + "\n"
    for text in (summary, reviewer):
        assert "mutation: 1/2 caught (1 marked equivalent)" in text.splitlines()
        assert text.splitlines().count("2 of 6 mutants timed out and were not counted") == 1


@pytest.mark.scenario("parallel-evidence-mutants/stable-evidence-results/parallel-results-match-serial")
@pytest.mark.parametrize("prepared", [False, True])
def test_timeout_results_match_serial_despite_parallel_completion_order(tmp_path, monkeypatch, prepared):
    code = "x = 1\n"
    selected = [evidence.Mutant("calc.py", 1, "int", "x = 1", f"x = {number + 1}")
                for number in range(1, 6)]
    results = ["timeout", "survived", "killed", "timeout", "invalid"]
    prepare = (manifest.Step("dependencies", ("/bin/sh", "-c", "true")),) if prepared else ()
    checks = tuple(manifest.Step(name, ("/bin/sh", "-c", "true")) for name in ("build", "check"))
    m = replace(M, prepare=prepare, verify=checks, mutation_steps=("build", "check"))

    def git(repo, *args):
        if args == ("show", "head:calc.py"):
            return code.encode()
        assert args == ("diff", "--unified=0", "base", "head")
        return b"+++ b/calc.py\n@@ -0,0 +1 @@\n+x = 1\n"

    monkeypatch.setattr(evidence, "git", git)
    monkeypatch.setattr(evidence, "generate", lambda *args, **kwargs: [replace(mut) for mut in selected])

    def baseline(repo, head, steps, run_m, out):
        (out / "src").mkdir(parents=True)
        (out / "src/calc.py").write_text(code)
        return [verify.Receipt(step.id, list(step.run), 0, 0, True, 1, "")
                for step in run_m.prepare + steps]

    monkeypatch.setattr(verify, "run_steps", baseline)
    real_cleanup = verify.cleanup
    snapshots = []
    for parallel in (1, 2):
        out = tmp_path / f"parallel-{parallel}"
        first_pair = Barrier(2)
        second_cleaned = Event()
        completed = []
        lock = Lock()

        def run(src, head, steps, run_m, run_out):
            number = int(run_out.name.split("-")[1])
            assert (src / "calc.py").read_text() == selected[number - 1].after + "\n"
            assert bool(run_m.prepare) is prepared
            if parallel > 1 and number <= 2:
                first_pair.wait(timeout=10)
            if parallel > 1 and number == 1:
                assert second_cleaned.wait(10)
            receipts = []
            for step in run_m.prepare + steps:
                exit_code = 0
                if step.id == "build" and number == 5:
                    exit_code = None
                elif step.id == "check":
                    exit_code = {1: None, 2: 0, 3: 9, 4: None}[number]
                receipts.append(verify.Receipt(step.id, list(step.run), 0, exit_code,
                                               exit_code == 0, 1, ""))
                if exit_code != 0:
                    break
            (run_out / "receipts.json").write_text(json.dumps([asdict(r) for r in receipts]))
            (run_out / "identity.txt").write_text(selected[number - 1].after)
            with lock:
                completed.append(number)
            return receipts

        def cleanup(run_out):
            real_cleanup(run_out)
            if run_out == out / "mutant-2":
                second_cleaned.set()

        monkeypatch.setattr(verify, "run_extracted", run)
        monkeypatch.setattr(verify, "cleanup", cleanup)
        ev = evidence.gather(tmp_path, "base", "head", ["c/r/s"], {"c/r/s"},
                             replace(m, mutation_parallel=parallel), out, 1)
        assert sorted(completed) == list(range(1, 6))
        if parallel == 1:
            assert completed == list(range(1, 6))
        else:
            assert completed.index(2) < completed.index(1)
        assert [replace(mut, result="") for mut in ev.mutants] == selected
        assert [mut.result for mut in ev.mutants] == results
        assert ev.score == 0.5 and ev.passed(0.5) and not ev.passed(0.8)
        assert ev.summary().splitlines().count("2 of 5 mutants timed out and were not counted") == 1
        saved = (out / "evidence.json").read_text()
        assert json.loads(saved) == {**asdict(ev), "score": 0.5}
        snapshots.append((saved, ev.summary(), ev.passed(0.5), ev.passed(0.8)))
        for number, mut in enumerate(ev.mutants, 1):
            worker = out / f"mutant-{number}"
            assert (worker / "identity.txt").read_text() == mut.after
            receipts = json.loads((worker / "receipts.json").read_text())
            assert [r["id"] for r in receipts] == (["dependencies"] if prepared else []) + (
                ["build"] if number == 5 else ["build", "check"])
            assert receipts[-1]["exit"] == {1: None, 2: 0, 3: 9, 4: None, 5: None}[number]
            assert not (worker / "src").exists()
        assert not (out / "src").exists()
    assert snapshots[0] == snapshots[1]


def test_added_lines_tracks_new_line_numbers_and_paths():
    diff = ("+++ b/calc.py\n@@ -0,0 +1,3 @@\n+def f(x):\n+    # note\n+    return x < 1\n"
            "+++ b/tests/t.py\n@@ -0,0 +1 @@\n+assert f(0)\n")
    lines = evidence.added_lines(diff, ("calc.py",))
    # Python comments need full-source span analysis, not diff-prefix filtering.
    assert lines == [("calc.py", 1, "def f(x):"), ("calc.py", 2, "    # note"),
                     ("calc.py", 3, "    return x < 1")]


def test_generate_is_deterministic_and_one_mutant_per_line():
    source = "def f(x, lo, y, a, b):\n    if x < lo and y > 2:\n        return a + b\n"
    lines = [("a.py", n, text) for n, text in enumerate(source.splitlines(), 1)][1:]
    first = evidence.generate(lines, 5, seed=1, sources={"a.py": source})
    assert first == evidence.generate(lines, 5, seed=1, sources={"a.py": source})
    assert len({m.line for m in first}) == len(first) == 2


@pytest.mark.parametrize("tests,expect_pass", [(WEAK, False), (STRONG, True)])
def test_mutation_separates_weak_from_strong_tests(tmp_path, tests, expect_pass):
    src, base, head = project(tmp_path, tests)
    ev = evidence.gather(src, base, head, [], set(), M, tmp_path / "out", 1)
    assert ev.mutants and all(m.result in ("killed", "survived", "invalid") for m in ev.mutants)
    assert ev.passed(0.8) is expect_pass, ev.summary()
    if not expect_pass:
        assert "survived: calc.py:" in ev.summary()
    assert (tmp_path / "out" / "evidence.json").exists()


def test_mutation_on_first_source_line_is_executed(tmp_path):
    repo, _, head = project(tmp_path, STRONG)
    mut = evidence.Mutant("calc.py", 1, "rename", CODE.splitlines()[0],
                          "def renamed_within(x, lo, hi):")
    out = tmp_path / "out"

    assert evidence.mutate(repo, head, [mut], M, out)
    assert mut.result == "killed"
    receipts = json.loads((out / "mutant-1" / "receipts.json").read_text())
    assert [r["id"] for r in receipts] == ["test"]
    assert receipts[0]["passed"] is False
    assert "has no attribute 'within'" in receipts[0]["tail"]
    assert (repo / "calc.py").read_text() == CODE


def test_mutant_source_preserves_symlink_imports(tmp_path):
    repo, _, _ = project(tmp_path, STRONG.replace("import calc", "import calc_alias as calc"))
    (repo / "calc_alias.py").symlink_to("calc.py")
    sh(repo, "git", "add", "calc_alias.py")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "source alias")
    head = sh(repo, "git", "rev-parse", "HEAD")
    mut = evidence.Mutant("calc.py", 2, "<=", CODE.splitlines()[1],
                          "    return lo < x and x <= hi")
    out = tmp_path / "out"

    assert evidence.mutate(repo, head, [mut], M, out)
    # The alias must resolve to the private mutated file. Dereferencing it
    # during copying gives tests an unchanged module and a false survival.
    assert mut.result == "killed"
    receipts = json.loads((out / "mutant-1" / "receipts.json").read_text())
    assert [r["id"] for r in receipts] == ["test"]
    assert receipts[0]["passed"] is False
    assert "AssertionError: False is not true" in receipts[0]["tail"]
    assert (repo / "calc_alias.py").is_symlink()
    assert (repo / "calc_alias.py").read_text() == CODE
    assert (repo / "calc.py").read_text() == CODE


def test_uncovered_scenarios_fail_and_exempt_ones_do_not(tmp_path):
    src, base, head = project(tmp_path, STRONG)
    m = replace(M, mutation_steps=(), coverage_exempt={"c/r/human": "human"})
    ev = evidence.gather(src, base, head, ["c/r/a", "c/r/human"], {"c/r/a"}, m, tmp_path / "o", 1)
    assert ev.passed(0.8) and ev.skipped
    ev = evidence.gather(src, base, head, ["c/r/a", "c/r/b"], {"c/r/a"}, m, tmp_path / "o2", 1)
    assert not ev.passed(0.8) and ev.uncovered == ["c/r/b"]


def test_extend_section_numbers_after_its_last_task():
    text = "## 1. A\n\n- [x] 1.1 one\n- [x] 1.2 two\n\n## 2. B\n\n- [ ] 2.1 three\n"
    out = tasks.extend_section(text, 1, ["more"])
    assert [t.label for t in tasks.parse(out)] == ["1.1", "1.2", "1.3", "2.1"]
    assert tasks.parse(out)[2].text == "more" and not tasks.parse(out)[2].done


def test_a_failing_baseline_is_not_evidence(tmp_path):
    src, base, head = project(tmp_path, WEAK.replace("7)", "8)"))  # the unmutated tests fail
    ev = evidence.gather(src, base, head, [], set(), M, tmp_path / "out", 1)
    assert ev.baseline_failed and not ev.passed(0.8) and "nothing could be measured" in ev.summary()


@pytest.mark.parametrize("prepare_exit", [0, 3])
def test_mutation_baseline_prepares_dependencies_and_records_failure(tmp_path, prepare_exit):
    src, base, head = project(tmp_path, STRONG)
    dependency = manifest.Step("dependency-ready", ("/bin/test", "-f", "dependency"))
    m = replace(M, prepare=(manifest.Step("dependencies", (
        "/bin/sh", "-c", f"touch dependency; exit {prepare_exit}")),),
        verify=(dependency,) + M.verify, mutation_steps=("dependency-ready", "test"))
    out = tmp_path / "out"
    ev = evidence.gather(src, base, head, [], set(), m, out, 1)
    assert ev.baseline_failed is (prepare_exit != 0)
    assert ev.passed(0.8) is (prepare_exit == 0), ev.summary()
    receipts = json.loads((out / "receipts.json").read_text())
    assert receipts[0]["id"] == "dependencies"
    assert receipts[0]["exit"] == prepare_exit
    assert [r["id"] for r in receipts] == (
        ["dependencies", "dependency-ready", "test"] if prepare_exit == 0 else ["dependencies"])


@pytest.mark.scenario("verify-preparation/prepare-steps/dependencies-ready-for-sandboxed-tests")
@pytest.mark.parametrize("tests,expect_pass", [(WEAK, False), (STRONG, True)])
def test_mutation_reprepares_copied_source_for_each_mutant(tmp_path, tests, expect_pass):
    src, base, head = project(tmp_path, tests)
    m = replace(M, prepare=(manifest.Step("copy-project", (
        "/bin/sh", "-c",
        'test ! -w "{src}/calc.py" && test ! -e project && '
        'cp -R "{src}/." project && chmod -R u+w project && cat project/calc.py',
    )),), verify=(replace(M.verify[0], run=tuple(
        arg.replace("{src}", "{build}/project") for arg in M.verify[0].run)),))
    out = tmp_path / "out"
    ev = evidence.gather(src, base, head, [], set(), m, out, 1)
    assert not ev.baseline_failed
    assert ev.passed(0.8) is expect_pass, ev.summary()
    assert ev.mutants and all(mut.result in ("killed", "survived") for mut in ev.mutants)
    if expect_pass:
        assert all(mut.result == "killed" for mut in ev.mutants)
    else:
        assert any(mut.result == "survived" for mut in ev.mutants)
    for number, mut in enumerate(ev.mutants, 1):
        receipts = json.loads((out / f"mutant-{number}" / "receipts.json").read_text())
        assert [r["id"] for r in receipts] == ["copy-project", "test"]
        assert receipts[0]["passed"]
        # The prepared copy contains this mutant alone, not the baseline or
        # accumulated changes from earlier mutants.
        assert receipts[0]["tail"] == CODE.replace(mut.before, mut.after)
        assert receipts[1]["passed"] is (mut.result == "survived")
        assert not (out / f"mutant-{number}" / "build").exists()
    baseline = json.loads((out / "receipts.json").read_text())
    assert baseline[0]["tail"] == CODE and all(r["passed"] for r in baseline)
    assert (src / "calc.py").read_text() == CODE


@pytest.mark.scenario("verify-preparation/prepare-steps/failing-prepare-fails-verification")
def test_mutant_prepare_failure_is_invalid_and_does_not_run_tests(tmp_path):
    src, _, head = project(tmp_path, STRONG)
    mut = evidence.Mutant("calc.py", 2, "<=", "    return lo <= x and x <= hi",
                          "    return lo < x and x <= hi")
    m = replace(M, prepare=(manifest.Step("prepare", (
        "/bin/sh", "-c", 'grep -Fq "$1" "$2"', "sh", mut.before, "{src}/calc.py",
    )),))
    out = tmp_path / "out"
    assert evidence.mutate(src, head, [mut], m, out)  # the baseline passed
    assert mut.result == "invalid"
    mutant_out = out / "mutant-1"
    receipts = json.loads((mutant_out / "receipts.json").read_text())
    assert [(r["id"], r["passed"], r["exit"]) for r in receipts] == [("prepare", False, 1)]
    assert not (mutant_out / "test.log").exists()


def test_lines_marked_equivalent_are_not_scored(tmp_path):
    marked = CODE.replace("def scale(x, k):\n", "def scale(x, k):\n    # mutation-equivalent: demo only\n")
    src, base, head = project(tmp_path, WEAK)
    (src / "calc.py").write_text(marked)
    sh(src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "mark")
    head = sh(src, "git", "rev-parse", "HEAD")
    ev = evidence.gather(src, base, head, [], set(), M, tmp_path / "out", 1)
    assert any(x.result == "equivalent" for x in ev.mutants) and "marked equivalent" in ev.summary()


def test_python_boolean_operators_are_mutated():
    def ops(text):
        return {name for name, rx, _ in evidence.OPERATORS if rx.search(text)}
    assert {"and", "or"} <= ops("    return x > 0 and y or z")
    assert "True" in ops("    flag = True") and "False" in ops("    if done is False:")
    assert "and" not in ops("    candidate = band")  # whole words only


HANG = '''def count(n=9):
    i = 0
    while i != n:
        i += 1
    return i
'''
HANG_TESTS = '''import unittest
import calc
class T(unittest.TestCase):
    def test_count(self):
        self.assertEqual(calc.count(), 9)
'''


def test_a_hanging_mutant_is_caught_by_a_scaled_timeout(tmp_path, monkeypatch):
    import time as _time
    monkeypatch.setattr(evidence, "MIN_MUTANT_SECONDS", 2.0)
    src, base, _ = project(tmp_path, HANG_TESTS)
    (src / "calc.py").write_text(HANG)
    sh(src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "hang")
    head = sh(src, "git", "rev-parse", "HEAD")
    started = _time.monotonic()
    ev = evidence.gather(src, base, head, [], set(), replace(M, mutation_count=20), tmp_path / "out", 1)
    hung = [x for x in ev.mutants if x.after.strip() == "i += 2"]
    assert hung and hung[0].result == "timeout"
    assert _time.monotonic() - started < 50  # not the 60 s step ceiling per hanging mutant


@pytest.mark.scenario("parallel-evidence-mutants/timeout-reporting/persist-timeout-results")
@pytest.mark.scenario("parallel-evidence-mutants/test-timeout-classification/other-outcomes-retain-classification")
@pytest.mark.scenario("parallel-evidence-mutants/stable-evidence-results/classification-rules-retained")
@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/preparation-timeout-is-invalid")
@pytest.mark.parametrize("prepared", [
    pytest.param(False, marks=pytest.mark.scenario(
        "parallel-evidence-mutants/test-timeout-classification/unprepared-test-times-out")),
    pytest.param(True, marks=pytest.mark.scenario(
        "parallel-evidence-mutants/test-timeout-classification/prepared-test-times-out")),
])
@pytest.mark.parametrize("parallel", [1, 2])
def test_mutant_timeout_is_recorded_not_killed(tmp_path, monkeypatch, prepared, parallel):
    repo, base, _ = project(tmp_path, STRONG)
    code = CODE + "unused = True  # mutation-equivalent: unused value\n"
    (repo / "calc.py").write_text(code)
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "equivalent")
    head = sh(repo, "git", "rev-parse", "HEAD")
    before = CODE.splitlines()[1]
    outcomes = ["timeout-check-1", "success", "failure", "signal", "timeout-check-2",
                "timeout-configure", "timeout-build"]
    if prepared:
        outcomes.append("timeout-dependencies")
    selected = [evidence.Mutant("calc.py", 2, "comment", before, before + f" # {outcome}")
                for outcome in outcomes]
    selected += [evidence.Mutant("calc.py", 2, "mismatch", "wrong original", "unused"),
                 evidence.Mutant("calc.py", 7, "True", code.splitlines()[6], "unused = False")]

    def generate(lines, count, seed, *, sources, mutation_skip):
        assert sources == {"calc.py": code}
        assert mutation_skip == ()
        assert ("calc.py", 2, before) in lines and seed == 1
        return [replace(mut) for mut in selected]

    monkeypatch.setattr(evidence, "generate", generate)

    def step(stage):
        # Prepared checks read the actual prepared copy. All preparation uses
        # system tools, which remain readable under the prepare sandbox.
        source = "{build}/project/calc.py" if prepared and stage != "dependencies" else "{src}/calc.py"
        script = f'''set -eu
cat "{source}"
if grep -Fq '# timeout-{stage}' "{source}"; then sleep 60; fi
'''
        if stage == "dependencies":
            script += 'cp -R "{src}" project\n'
        if stage == "check":
            script += f'''if grep -Fq '# failure' "{source}"; then exit 9; fi
if grep -Fq '# signal' "{source}"; then kill -TERM $$; fi
'''
        return manifest.Step(stage, ("/bin/sh", "-c", script + "exit 7\n"), expect=7, timeout=1)

    stages = (["dependencies"] if prepared else []) + ["configure", "build", "check", "later"]
    m = replace(M, prepare=(step("dependencies"),) if prepared else (),
                verify=tuple(step(s) for s in stages if s != "dependencies"),
                mutation_steps=("configure", "build", "check", "later"), mutation_parallel=parallel)
    out = tmp_path / "out"
    real_run = verify.run_extracted
    overlap = Barrier(2)
    lock = Lock()
    active = peak = 0

    def run(src, sha, steps, run_m, run_out):
        nonlocal active, peak
        if run_out == out:
            return real_run(src, sha, steps, run_m, run_out)
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            if parallel == 2 and run_out.name in ("mutant-1", "mutant-2"):
                overlap.wait(timeout=10)
            return real_run(src, sha, steps, run_m, run_out)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(verify, "run_extracted", run)
    ev = evidence.gather(repo, base, head, ["covered"], {"covered"}, m, out, 1)
    expected = ["timeout", "survived", "killed", "killed", "timeout", "invalid", "invalid"]
    expected += (["invalid"] if prepared else []) + ["invalid", "equivalent"]
    assert [mut.result for mut in ev.mutants] == expected
    assert [replace(mut, result="") for mut in ev.mutants] == selected
    assert peak == parallel and active == 0
    assert not ev.baseline_failed and not ev.unmeasured and ev.covered == ["covered"]
    assert ev.score == 2 / 3 and ev.passed(0.5) and not ev.passed(0.8)
    saved = json.loads((out / "evidence.json").read_text())
    assert saved == {**asdict(ev), "score": 2 / 3}
    baseline = json.loads((out / "receipts.json").read_text())
    assert [r["id"] for r in baseline] == stages and all(r["passed"] for r in baseline)
    for number, outcome in enumerate(outcomes, 1):
        worker = out / f"mutant-{number}"
        receipts = json.loads((worker / "receipts.json").read_text())
        failed_stage = outcome.split("-")[1] if outcome.startswith("timeout-") else "check"
        executed = stages if outcome == "success" else stages[:stages.index(failed_stage) + 1]
        assert [r["id"] for r in receipts] == executed
        assert all(r["passed"] and r["exit"] == 7 for r in receipts[:-1])
        assert receipts[-1]["passed"] is (outcome == "success")
        if outcome.startswith("timeout-"):
            assert receipts[-1]["exit"] is None
        elif outcome == "signal":
            assert receipts[-1]["exit"] == -15
        else:
            assert receipts[-1]["exit"] == (7 if outcome == "success" else 9)
        assert all((worker / f"{s}.log").exists() is (s in executed) for s in stages)
        assert not (worker / "src").exists() and not (worker / "build").exists()
    for number in (len(outcomes) + 1, len(outcomes) + 2):
        assert not (out / f"mutant-{number}").exists()
    assert not (out / "src").exists() and not (out / "build").exists()
    assert (repo / "calc.py").read_text() == code


@pytest.mark.scenario("parallel-evidence-mutants/test-timeout-classification/other-outcomes-retain-classification")
@pytest.mark.parametrize("stage", ["dependencies", "configure", "build", "check"])
def test_baseline_timeout_is_invalid_without_dispatch(tmp_path, monkeypatch, stage):
    step = manifest.Step(stage, ("/bin/sh", "-c", "true"))
    m = replace(M, prepare=(step,) if stage == "dependencies" else (),
                verify=() if stage == "dependencies" else (step,), mutation_steps=(stage,))
    mutants = [evidence.Mutant("calc.py", 1, "int", "x = 1", "x = 2")]

    def baseline(*args):
        return [verify.Receipt(stage, list(step.run), 0, None, False, 1, "")]

    def unexpected_pool(*args, **kwargs):
        pytest.fail("baseline timeout must not dispatch mutants")

    monkeypatch.setattr(verify, "run_steps", baseline)
    monkeypatch.setattr(evidence, "ThreadPoolExecutor", unexpected_pool)
    assert not evidence.mutate(tmp_path, "head", mutants, m, tmp_path / "out")
    assert [mut.result for mut in mutants] == ["invalid"]


@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/missing-duration-uses-manifest-ceiling")
@pytest.mark.scenario("parallel-evidence-mutants/test-timeout-classification/other-outcomes-retain-classification")
@pytest.mark.parametrize("seconds", [None, 0, 20])
@pytest.mark.parametrize("stage", ["dependencies", "configure", "build", "check"])
def test_timeout_at_manifest_ceiling_keeps_invalid_step_precedence(tmp_path, monkeypatch, seconds, stage):
    step = manifest.Step(stage, ("/bin/sh", "-c", "true"), timeout=5)
    m = replace(M, prepare=(step,) if stage == "dependencies" else (),
                verify=() if stage == "dependencies" else (step,), mutation_steps=(stage,))
    original = asdict(m)
    out = tmp_path / "out"
    baseline_receipt = verify.Receipt(stage, list(step.run), 0, 0, True, seconds, "")
    before = CODE.splitlines()[1]
    mutants = [evidence.Mutant("calc.py", 2, "comment", before, before + " # mutant")]

    def baseline(repo, head, steps, run_m, run_out):
        assert run_m is m and run_m.prepare + steps == (step,)
        (run_out / "src").mkdir(parents=True)
        (run_out / "src/calc.py").write_text(CODE)
        return [baseline_receipt]

    def run(src, head, steps, run_m, run_out):
        assert run_m.prepare + steps == (step,)  # fallback or cap, never the 30/60-second budget
        # Only the first failure determines classification, even if a caller
        # supplies later failures with a different step kind or exit status.
        return [verify.Receipt("earlier", [], 0, 0, True, 0.1, ""),
                verify.Receipt(stage, [], 0, None, False, 5, ""),
                verify.Receipt("build" if stage == "check" else "check", [], 0, 9, False, 0.1, "")]

    monkeypatch.setattr(verify, "run_steps", baseline)
    monkeypatch.setattr(verify, "run_extracted", run)
    assert evidence.mutate(tmp_path, "head", mutants, m, out)
    assert mutants[0].result == ("timeout" if stage == "check" else "invalid")
    assert asdict(m) == original and baseline_receipt.seconds == seconds
    assert not (out / "src").exists() and not (out / "mutant-1/src").exists()


@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/missing-duration-uses-manifest-ceiling")
@pytest.mark.parametrize("prepared", [False, True])
def test_mutant_timeout_overrides_are_exact_and_immutable(tmp_path, monkeypatch, prepared):
    # Exercise the floor, fractional scaling, zero/missing durations, and caps
    # both below the scaled duration and below the floor, for each kind of step.
    durations = (2, 12.125, 0, None, 25, 1)
    ceilings = (100, 100, 75, 80, 40, 5)
    expected = (30.0, 36.375, 75, 80, 40, 5)

    def make_steps(prefix):
        return tuple(manifest.Step(f"{prefix}-{i}", ("/bin/sh", "-c", "true"), timeout=ceiling)
                     for i, ceiling in enumerate(ceilings))

    selected = make_steps("verify")
    preparation = make_steps("prepare") if prepared else ()
    unselected = manifest.Step("unselected", ("/usr/bin/false",), timeout=999)
    m = replace(M, prepare=preparation, verify=selected + (unselected,),
                mutation_steps=tuple(s.id for s in reversed(selected)))
    original = replace(m)
    receipts = [verify.Receipt(s.id, list(s.run), s.expect, 0, True, seconds, "")
                for group in (preparation, selected) for s, seconds in zip(group, durations)]
    out = tmp_path / "out"
    baseline_calls = []

    def baseline(repo, head, steps, baseline_m, run_out):
        baseline_calls.append(run_out)
        assert baseline_m is m and steps == selected
        assert tuple(s.timeout for s in steps) == ceilings
        assert baseline_m.prepare == preparation
        (run_out / "src").mkdir(parents=True)
        (run_out / "src" / "calc.py").write_text(CODE)
        return receipts

    calls = []

    def run_mutant(src, head, steps, bounded, run_out):
        assert m == original
        assert bounded is not m
        assert steps == tuple(replace(s, timeout=t) for s, t in zip(selected, expected))
        assert bounded == replace(m, prepare=tuple(
            replace(s, timeout=t) for s, t in zip(preparation, expected)))
        assert all(a is not b for a, b in zip(steps, selected))
        assert all(a is not b for a, b in zip(bounded.prepare, preparation))
        calls.append((steps, bounded))
        # Subsequent workers must use the snapshot, not recompute from receipts.
        for receipt in receipts:
            receipt.seconds = 999
        return [verify.Receipt(s.id, list(s.run), s.expect, 0, True, 999, "")
                for s in bounded.prepare + steps]

    monkeypatch.setattr(verify, "run_steps", baseline)
    monkeypatch.setattr(verify, "run_extracted", run_mutant)
    before = CODE.splitlines()[1]
    mutants = [evidence.Mutant("calc.py", 2, "comment", before, before + f" # {i}")
               for i in range(3)]
    assert evidence.mutate(tmp_path, "head", mutants, m, out)
    assert baseline_calls == [out] and len(calls) == 3
    assert all(steps is calls[0][0] and bounded is calls[0][1] for steps, bounded in calls)
    assert [mut.result for mut in mutants] == ["survived"] * 3
    assert m == original


PROCESS_TREE_STEP = '''from pathlib import Path
import json, os, subprocess, sys, time
source = Path("{src}/calc.py").read_text()
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
artifacts = Path("artifacts")
artifacts.mkdir(exist_ok=True)
(artifacts / "pids.json").write_text(json.dumps([os.getpid(), child.pid]))
(artifacts / "source.txt").write_text(source)
print("diagnostic", flush=True)
if "# timeout" in source:
    time.sleep(60)
sys.exit(3 if "# failure" in source else 0)
'''


# Prepare cannot read a Python interpreter installed under the host's HOME.
# Keep the same process-tree, artifact, and exit behavior using system tools.
PREPARE_PROCESS_TREE_STEP = '''set -eu
sleep 60 &
child=$!
mkdir -p artifacts
printf '[%s, %s]\\n' "$$" "$child" > artifacts/pids.json
cp "{src}/calc.py" artifacts/source.txt
printf 'diagnostic\\n'
if grep -Fq '# timeout' "{src}/calc.py"; then
    sleep 60
fi
if grep -Fq '# failure' "{src}/calc.py"; then
    exit 3
fi
'''


def assert_worker_resources_released(run_out, expected_exit, expected_source):
    assert not (run_out / "src").exists()
    assert not (run_out / "build").exists()
    assert (run_out / "artifacts" / "source.txt").read_text() == expected_source
    receipts = json.loads((run_out / "receipts.json").read_text())
    diagnostic = next(r for r in receipts if r["tail"] == "diagnostic\n")
    assert diagnostic["exit"] == expected_exit
    assert diagnostic["passed"] is (expected_exit == 0)
    assert (run_out / f"{diagnostic['id']}.log").read_text() == "diagnostic\n"
    for pid in json.loads((run_out / "artifacts" / "pids.json").read_text()):
        deadline = time.monotonic() + 5
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline, f"step process {pid} still running"
            time.sleep(0.05)
    return receipts


@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/preparation-timeout-is-invalid")
@pytest.mark.scenario("parallel-evidence-mutants/worker-cleanup/completed-workers-release-resources")
@pytest.mark.parametrize("stage", ["dependencies", "configure", "build", "test"])
def test_real_mutant_step_timeout_stops_its_process_group(tmp_path, monkeypatch, stage):
    repo, _, head = project(tmp_path, STRONG)
    out = tmp_path / "out"
    monkeypatch.setattr(evidence, "MIN_MUTANT_SECONDS", 1.0)
    command = (("/bin/sh", "-c", PREPARE_PROCESS_TREE_STEP) if stage == "dependencies"
               else (sys.executable, "-c", PROCESS_TREE_STEP))
    target = manifest.Step(stage, command, timeout=10)
    later = manifest.Step("later", ("/usr/bin/true",), timeout=10)
    m = replace(M, prepare=(target,) if stage == "dependencies" else (),
                verify=(later,) if stage == "dependencies" else (target, later),
                mutation_steps=(stage, "later"))
    real_run = verify.run_extracted
    observed = []

    def run(src, sha, steps, run_m, run_out):
        scheduled = run_m.prepare + steps
        observed.append(run_out)
        assert all(s.timeout == (10 if run_out == out else 1.0) for s in scheduled)
        receipts = real_run(src, sha, steps, run_m, run_out)
        if run_out == out:
            # Control baseline timing only; all commands and timeout handling are real.
            for receipt in receipts:
                receipt.seconds = 0.01
        return receipts

    monkeypatch.setattr(verify, "run_extracted", run)
    before = CODE.splitlines()[1]
    mutants = [evidence.Mutant("calc.py", 2, "comment", before, before + f" # {outcome}")
               for outcome in ("timeout", "failure", "success")]
    assert evidence.mutate(repo, head, mutants, m, out)
    failure = "killed" if stage == "test" else "invalid"
    timeout = "timeout" if stage == "test" else "invalid"
    assert [mut.result for mut in mutants] == [timeout, failure, "survived"]
    assert len(observed) == 4 and observed[0] == out
    for number, (mut, code) in enumerate(zip(mutants, (None, 3, 0)), 1):
        worker = out / f"mutant-{number}"
        receipts = assert_worker_resources_released(worker, code, CODE.replace(before, mut.after))
        assert [r["id"] for r in receipts] == ([stage, "later"] if code == 0 else [stage])
        assert (worker / "later.log").exists() is (code == 0)
    assert_worker_resources_released(out, 0, CODE)
    assert target.timeout == 10 and later.timeout == 10


@pytest.mark.scenario("parallel-evidence-mutants/worker-cleanup/completed-workers-release-resources")
@pytest.mark.parametrize("prepared", [False, True])
def test_concurrent_private_workers_release_processes_and_trees(tmp_path, monkeypatch, prepared):
    repo, _, head = project(tmp_path, STRONG)
    out = tmp_path / "out"
    command = (("/bin/sh", "-c", PREPARE_PROCESS_TREE_STEP) if prepared
               else (sys.executable, "-c", PROCESS_TREE_STEP))
    target = manifest.Step("exercise", command, timeout=1)
    m = replace(M, prepare=(target,) if prepared else (), verify=() if prepared else (target,))
    verify.extract(repo, head, out / "src")
    before = CODE.splitlines()[1]
    mutants = [evidence.Mutant("calc.py", 2, "comment", before, before + f" # {outcome}")
               for outcome in ("timeout", "failure", "success")]
    # Exercise worker concurrency independently of the coordinator's scheduling.
    barrier = Barrier(3)
    real_run = verify.run_extracted

    def overlapping_run(src, sha, steps, run_m, run_out):
        barrier.wait(timeout=10)
        return real_run(src, sha, steps, run_m, run_out)

    monkeypatch.setattr(verify, "run_extracted", overlapping_run)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(evidence._mutate_one, i, mut, out / "src", head, m.verify, m, out)
                       for i, mut in enumerate(mutants, 1)]
            results = [future.result() for future in futures]
        failure = "invalid" if prepared else "killed"
        timeout = "invalid" if prepared else "timeout"
        assert results == [timeout, failure, "survived"]
        assert (out / "src" / "calc.py").read_text() == CODE
        for i, (mut, code) in enumerate(zip(mutants, (None, 3, 0)), 1):
            assert_worker_resources_released(out / f"mutant-{i}", code, CODE.replace(before, mut.after))
    finally:
        verify.cleanup(out)


def _evidence_with_results(*results):
    return evidence.Evidence(1, covered=["c/r/s"], mutants=[
        evidence.Mutant("calc.py", i, "<", "a < b", "a <= b", result)
        for i, result in enumerate(results, 1)
    ])


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/mixed-results-exclude-timeouts")
@pytest.mark.parametrize("results,score,fraction,timeout_line", [
    (("killed", "survived", "timeout", "timeout", "invalid", "equivalent"),
     0.5, "1/2 caught (1 marked equivalent)", "2 of 6 mutants timed out and were not counted"),
    (("survived", "timeout", "timeout"),
     0.0, "0/1 caught", "2 of 3 mutants timed out and were not counted"),
])
def test_timeouts_do_not_raise_the_score(results, score, fraction, timeout_line):
    ev = _evidence_with_results(*results)
    assert ev.score == score and not ev.unmeasured
    assert ev.passed(score) and not ev.passed(score + 0.1)
    survivor = results.index("survived") + 1
    assert ev.summary().splitlines() == [
        "section 1: 1 scenario(s) covered",
        f"mutation: {fraction}",
        f"  survived: calc.py:{survivor} <  `a <= b`",
        timeout_line,
    ]


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/all-timeouts-are-unmeasured")
@pytest.mark.scenario("parallel-evidence-mutants/timeout-reporting/unmeasured-summary-explains-timeouts")
def test_all_timeouts_is_unmeasured():
    ev = _evidence_with_results("timeout", "timeout", "timeout")
    assert not ev.baseline_failed
    assert ev.score is None and ev.unmeasured
    assert not any(ev.passed(minimum) for minimum in (0, 0.5, 0.8, 1))
    assert ev.summary().splitlines() == [
        "section 1: 1 scenario(s) covered",
        "mutation: none of the 3 mutants could be judged (every one was invalid or timed out), "
        "so nothing was measured; check mutation_steps and any prepare steps",
        "3 of 3 mutants timed out and were not counted",
    ]


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/timeouts-mixed-with-invalid-are-unmeasured")
@pytest.mark.scenario("parallel-evidence-mutants/timeout-reporting/unmeasured-summary-explains-timeouts")
def test_timeouts_and_invalid_mutants_are_unmeasured():
    ev = _evidence_with_results("timeout", "invalid", "timeout")
    assert ev.score is None and ev.unmeasured
    assert not any(ev.passed(minimum) for minimum in (0, 0.5, 0.8, 1))
    assert ev.summary().splitlines() == [
        "section 1: 1 scenario(s) covered",
        "mutation: none of the 3 mutants could be judged (every one was invalid or timed out), "
        "so nothing was measured; check mutation_steps and any prepare steps",
        "2 of 3 mutants timed out and were not counted",
    ]


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/equivalent-and-skipped-behavior-retained")
def test_equivalent_and_timeout_mutants_retain_coverage_and_baseline_rules():
    ev = _evidence_with_results("equivalent", "timeout")
    assert ev.score is None and ev.unmeasured
    assert not any(ev.passed(minimum) for minimum in (0, 0.5, 0.8, 1))
    assert ev.summary().splitlines() == [
        "section 1: 1 scenario(s) covered",
        "mutation: none of the 2 mutants were killed or survived (1 marked equivalent), "
        "so nothing was measured; review equivalence annotations and check mutation_steps and any prepare steps",
        "1 of 2 mutants timed out and were not counted",
    ]
    for failed in (replace(ev, uncovered=["c/r/missing"]), replace(ev, baseline_failed=True)):
        assert failed.score is None and failed.unmeasured
        assert not failed.passed(0)
        assert failed.summary().splitlines().count("1 of 2 mutants timed out and were not counted") == 1
    assert "UNCOVERED: c/r/missing" in replace(ev, uncovered=["c/r/missing"]).summary()
    assert "the unmutated head fails mutation_steps" in replace(ev, baseline_failed=True).summary()


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/equivalent-and-skipped-behavior-retained")
@pytest.mark.parametrize("steps,base,reason", [
    ((), "base", "no mutation_steps in the manifest"),
    (("test",), "", "section started before evidence was recorded"),
    (("test",), "base", "no mutable lines added under mutation_paths"),
])
def test_skipped_mutation_retains_reason_and_coverage_failure(tmp_path, monkeypatch, steps, base, reason):
    monkeypatch.setattr(evidence, "git", lambda *args: b"")
    for seen in ({"c/r/s"}, set()):
        ev = evidence.gather(tmp_path, base, "head", ["c/r/s"], seen,
                             replace(M, mutation_steps=steps), tmp_path / "out", 1)
        assert ev.mutants == [] and ev.skipped == reason
        assert ev.score is None and not ev.unmeasured
        assert ev.passed(0) is bool(seen) and ev.passed(1) is bool(seen)
        coverage = ("section 1: 1 scenario(s) covered" if seen else
                    "section 1: 0 scenario(s) covered, UNCOVERED: c/r/s")
        assert ev.summary() == f"{coverage}\nmutation: skipped ({reason})"


@pytest.mark.scenario("parallel-evidence-mutants/stable-evidence-results/all-invalid-remains-unmeasured")
@pytest.mark.parametrize("results,expected", [
    (("killed", "survived", "invalid", "equivalent"),
     "mutation: 1/2 caught (1 marked equivalent)\n  survived: calc.py:2 <  `a <= b`"),
    (("invalid", "invalid", "invalid"),
     "mutation: none of the 3 mutants could be judged (every one was invalid), "
     "so nothing was measured; check mutation_steps and any prepare steps"),
    (("equivalent",),
     "mutation: none of the 1 mutants were killed or survived (1 marked equivalent), "
     "so nothing was measured; review equivalence annotations and check mutation_steps and any prepare steps"),
])
def test_zero_timeout_summary_explains_results(results, expected):
    ev = _evidence_with_results(*results)
    assert ev.summary() == "section 1: 1 scenario(s) covered" + (f"\n{expected}" if expected else "")
    if all(result == "invalid" for result in results):
        assert ev.score is None and ev.unmeasured
        assert not any(ev.passed(minimum) for minimum in (0, 0.5, 0.8, 1))


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/all-timeouts-are-unmeasured")
@pytest.mark.parametrize("parallel", [1, 2])
def test_all_timeout_section_pauses_without_strengthening(tmp_path, monkeypatch, parallel):
    from specloop import loop
    from specloop.state import Run

    ev = _evidence_with_results("timeout", "timeout", "timeout")
    run = Run(tmp_path / "run")
    run.state.section_base["1"] = "base"
    m = replace(M, mutation_min=0, mutation_parallel=parallel, coverage_step="")
    ctx = loop.Ctx(run, m, {})
    all_tasks = tasks.parse("## 1. Core\n\n- [x] 1.1 Cover `c/r/s`\n")
    original_tasks = [asdict(task) for task in all_tasks]
    calls = []

    def gather(repo, base, head, cited, seen, manifest, out, section):
        calls.append(section)
        assert (repo, base, head) == (ctx.repo, "base", "head")
        assert cited == ["c/r/s"] and seen == {"c/r/s"}
        assert manifest == m and out == run.dir / "gates" / "section-1-0"
        return ev

    def unexpected_strengthening(*args, **kwargs):
        pytest.fail("unmeasured evidence must pause without strengthening tasks")

    monkeypatch.setattr(evidence, "gather", gather)
    monkeypatch.setattr(tasks, "extend_section", unexpected_strengthening)
    monkeypatch.setattr(loop, "_rewrite_tasks", unexpected_strengthening)
    assert not loop.section_evidence(ctx, 1, "head", all_tasks)
    assert calls == [1] and [asdict(task) for task in all_tasks] == original_tasks
    saved = Run(run.dir).state
    assert saved.status == "paused"
    assert saved.pause.reason == "gate_evidence" and saved.pause.task == "section:1"
    assert saved.pause.detail == ev.summary()
    assert "3 of 3 mutants timed out and were not counted" in saved.pause.detail
    assert saved.strengthen_rounds == {} and saved.evidence_scores == {}
    assert saved.tasks == {} and saved.evidenced == [] and saved.evidence_summary == {}


def test_all_invalid_mutants_are_unmeasured_not_a_pass():
    from specloop.evidence import Evidence, Mutant

    ev = Evidence(1, covered=["c/r/s"])
    ev.mutants = [Mutant("a.py", 1, "<", "a < b", "a <= b", "invalid") for _ in range(3)]
    assert ev.unmeasured and ev.score is None and not ev.passed(0.8)
    assert "none of the 3 mutants could be judged" in ev.summary()
    ev.mutants[0].result = "killed"
    assert not ev.unmeasured and ev.passed(0.3)
    assert Evidence(1).passed(0.8)  # no mutants generated (e.g. no mutable lines) is still not a failure


@pytest.mark.scenario("parallel-evidence-mutants/stable-evidence-results/parallel-results-match-serial")
@pytest.mark.scenario("parallel-evidence-mutants/stable-evidence-results/classification-rules-retained")
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("case", ["weak", "strong", pytest.param("all-invalid", marks=pytest.mark.scenario(
    "parallel-evidence-mutants/stable-evidence-results/all-invalid-remains-unmeasured"))])
def test_parallel_mutants_match_serial_results(tmp_path, monkeypatch, prepared, case):
    repo, base, _ = project(tmp_path, WEAK if case == "weak" else STRONG)
    code = CODE + "unused = True  # mutation-equivalent: unused value\n"
    (repo / "calc.py").write_text(code)
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "equivalent")
    head = sh(repo, "git", "rev-parse", "HEAD")
    before = CODE.splitlines()[1]
    selected = [
        evidence.Mutant("calc.py", 2, "<=", before, "    return lo < x and x <= hi"),
        evidence.Mutant("calc.py", 6, "+", CODE.splitlines()[5], "    return x * k - 1"),
    ]
    stages = (["prepare"] if prepared else []) + ["configure", "build"]
    invalid = [evidence.Mutant("calc.py", 2, "comment", before, before + f" # fail-{stage}")
               for stage in stages]
    mismatch = evidence.Mutant("calc.py", 2, "mismatch", "wrong original", "unused")
    if case == "all-invalid":
        selected = invalid + [mismatch]
    else:
        selected += invalid + [mismatch, evidence.Mutant(
            "calc.py", 7, "True", code.splitlines()[6], "unused = False")]

    # Fix selection to include failures and nonexecuting mutants, but exercise
    # gather, the real dispatcher, private workers, and real command receipts.
    def generate(lines, count, seed, *, sources, mutation_skip):
        assert sources == {"calc.py": code}
        assert mutation_skip == ()
        assert ("calc.py", 2, before) in lines and seed == 1
        return [replace(mut) for mut in selected]

    monkeypatch.setattr(evidence, "generate", generate)

    def stage_step(stage):
        if stage == "prepare":
            return manifest.Step(stage, ("/bin/sh", "-c", '''set -eu
mkdir -p artifacts
cp "{src}/calc.py" artifacts/source.txt
chmod u+w artifacts/source.txt
cat "{src}/calc.py"
cp -R "{src}" project
if grep -Fq 'fail-prepare' "{src}/calc.py"; then
    exit 3
fi
'''), timeout=10)
        script = '''from pathlib import Path
import sys
source = Path("{src}/calc.py").read_text()
Path("artifacts").mkdir(exist_ok=True)
Path("artifacts/source.txt").write_text(source)
print(source, end="")
'''
        script += f'sys.exit(3 if "fail-{stage}" in source else 0)\n'
        return manifest.Step(stage, (sys.executable, "-c", script), timeout=10)

    test = M.verify[0]
    if prepared:
        test = replace(test, run=tuple(arg.replace("{src}", "{build}/project") for arg in test.run))
    m = replace(M, prepare=(stage_step("prepare"),) if prepared else (),
                verify=(stage_step("configure"), stage_step("build"), test),
                mutation_steps=("configure", "build", "test"), coverage_exempt={"human": "manual"})
    real_run, real_cleanup = verify.run_extracted, verify.cleanup
    snapshots = []
    for parallel in (1, 2, len(selected) + 2):
        out = tmp_path / f"parallel-{parallel}"
        second_cleaned = Event()
        first_pair = Barrier(2)
        lock = Lock()
        executed, completed, cleaned = [], [], []
        active = peak = 0

        def run(src, sha, steps, run_m, run_out):
            nonlocal active, peak
            if run_out == out:
                return real_run(src, sha, steps, run_m, run_out)
            number = int(run_out.name.split("-")[1])
            with lock:
                executed.append(number)
                active += 1
                peak = max(peak, active)
            try:
                # The second worker must finish before the first, even when
                # queued work and nonexecuting mutants finish in between.
                if parallel > 1 and number <= 2:
                    first_pair.wait(timeout=20)
                if parallel > 1 and number == 1:
                    assert second_cleaned.wait(20)
                expected = code.replace(selected[number - 1].before, selected[number - 1].after)
                assert (src / "calc.py").read_text() == expected
                receipts = real_run(src, sha, steps, run_m, run_out)
                if prepared:
                    assert (run_out / "build/project/calc.py").read_text() == expected
                return receipts
            finally:
                with lock:
                    active -= 1
                    completed.append(number)

        def cleanup(run_out):
            if run_out == out:
                assert active == 0 and len(cleaned) == len(selected)
            else:
                assert (out / "src/calc.py").read_text() == code
            real_cleanup(run_out)
            with lock:
                cleaned.append(run_out)
            if run_out == out / "mutant-2":
                second_cleaned.set()

        monkeypatch.setattr(verify, "run_extracted", run)
        monkeypatch.setattr(verify, "cleanup", cleanup)
        ev = evidence.gather(repo, base, head, ["covered", "human"], {"covered"},
                             replace(m, mutation_parallel=parallel), out, 1)
        executable_count = len(selected) - (1 if case == "all-invalid" else 2)
        assert sorted(executed) == list(range(1, executable_count + 1))
        assert 1 <= peak <= min(parallel, executable_count)
        if parallel == 1:
            assert completed == executed and peak == 1
        else:
            assert peak >= 2 and completed.index(2) < completed.index(1)
        assert cleaned[-1] == out
        assert ev.covered == ["covered"] and not ev.uncovered and not ev.baseline_failed
        if case == "all-invalid":
            expected_results = ["invalid"] * len(selected)
            assert ev.score is None and ev.unmeasured
            assert not any(ev.passed(limit) for limit in (0, 0.5, 0.8, 1))
            assert f"none of the {len(selected)} mutants could be judged" in ev.summary()
        else:
            expected_results = (["survived" if case == "weak" else "killed", "killed"]
                                + ["invalid"] * (len(invalid) + 1) + ["equivalent"])
            assert ev.score == (0.5 if case == "weak" else 1.0) and not ev.unmeasured
            assert ev.passed(0.8) is (case == "strong")
            assert "1 marked equivalent" in ev.summary()
        assert [mut.result for mut in ev.mutants] == expected_results
        assert [replace(mut, result="") for mut in ev.mutants] == selected
        saved = json.loads((out / "evidence.json").read_text())
        assert saved == {**asdict(ev), "score": ev.score}
        snapshots.append((saved, ev.score, ev.summary(), [ev.passed(n) for n in (0, 0.5, 0.8, 1)]))
        for number, mut in enumerate(ev.mutants, 1):
            worker = out / f"mutant-{number}"
            assert not (worker / "src").exists() and not (worker / "build").exists()
            if number > executable_count:
                assert not worker.exists()  # mismatch/equivalent executed no commands
                continue
            assert (worker / "artifacts/source.txt").read_text() == code.replace(mut.before, mut.after)
            receipts = json.loads((worker / "receipts.json").read_text())
            failed_stage = next((stage for stage in stages if f"fail-{stage}" in mut.after), None)
            expected_steps = stages[:stages.index(failed_stage) + 1] if failed_stage else stages + ["test"]
            assert [r["id"] for r in receipts] == expected_steps
            assert receipts[-1]["passed"] is (mut.result == "survived")
            assert all((worker / f"{step}.log").exists() is (step in expected_steps)
                       for step in stages + ["test"])
        assert not (out / "src").exists() and not (out / "build").exists()
    assert snapshots[0] == snapshots[1] == snapshots[2]
    assert (repo / "calc.py").read_text() == code


@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/parallel-mutants-respect-time-box")
@pytest.mark.scenario("parallel-evidence-mutants/worker-cleanup/completed-workers-release-resources")
@pytest.mark.parametrize("prepared", [False, True])
def test_parallel_mutants_respect_time_box(tmp_path, monkeypatch, prepared):
    repo, _, head = project(tmp_path, STRONG)
    out = tmp_path / "out"
    # Keep real subprocess timeouts short; test every branch of the budget
    # formula in both preparation and verification with controlled receipts.
    monkeypatch.setattr(evidence, "MIN_MUTANT_SECONDS", 1.0)
    durations = (0.01, 0.375, 0, None, 2, 0.01)
    ceilings = (10, 10, 4, 5, 2, 0.5)
    expected = (1.0, 1.125, 4, 5, 2, 0.5)

    def steps(prefix):
        return tuple(manifest.Step(f"{prefix}-{i}", ("/bin/sh", "-c", "true"), timeout=ceiling)
                     for i, ceiling in enumerate(ceilings))

    preparation = steps("prepare") if prepared else ()
    checks = steps("verify") + (manifest.Step("test", (sys.executable, "-c", PROCESS_TREE_STEP), timeout=10),)
    m = replace(M, mutation_parallel=2, prepare=preparation, verify=checks,
                mutation_steps=tuple(s.id for s in checks))
    original = replace(m)
    baseline_seconds = {s.id: seconds for group in (preparation, checks[:-1])
                        for s, seconds in zip(group, durations)}
    baseline_seconds["test"] = 0.01
    before = CODE.splitlines()[1]
    outcomes = ("timeout", "timeout", "timeout", "failure", "success")
    mutants = [evidence.Mutant("calc.py", 2, "comment", before, before + f" # {outcome}-{i}")
               for i, outcome in enumerate(outcomes)]
    real_run, real_cleanup = verify.run_extracted, verify.cleanup
    overlap = Barrier(2)
    lock = Lock()
    started, finished, cleaned = {}, [], []
    active = peak = 0
    baseline_calls = []

    def run(src, sha, selected_steps, run_m, run_out):
        nonlocal active, peak
        assert m == original
        if run_out == out:
            baseline_calls.append(run_out)
            assert selected_steps == checks and run_m.prepare == preparation
            receipts = real_run(src, sha, selected_steps, run_m, run_out)
            for receipt in receipts:
                receipt.seconds = baseline_seconds[receipt.id]
            return receipts
        assert tuple(s.timeout for s in selected_steps) == expected + (1.0,)
        assert tuple(s.timeout for s in run_m.prepare) == (expected if prepared else ())
        number = int(run_out.name.split("-")[1])
        with lock:
            if number > 2:
                assert finished  # work was queued behind a completed worker
            started[number] = time.monotonic()
            active += 1
            peak = max(peak, active)
        try:
            if number <= 2:
                overlap.wait(timeout=10)
            return real_run(src, sha, selected_steps, run_m, run_out)
        finally:
            with lock:
                active -= 1
                finished.append(number)

    def cleanup(run_out):
        if run_out == out:
            assert active == 0 and sorted(finished) == list(range(1, 6))
            assert set(cleaned) == {out / f"mutant-{i}" for i in range(1, 6)}
        else:
            assert (out / "src/calc.py").read_text() == CODE
            assert (out / "build").exists()
        real_cleanup(run_out)
        with lock:
            cleaned.append(run_out)

    monkeypatch.setattr(verify, "run_extracted", run)
    monkeypatch.setattr(verify, "cleanup", cleanup)
    assert evidence.mutate(repo, head, mutants, m, out)
    assert baseline_calls == [out] and peak == 2 and active == 0
    # The third mutant waited longer than its test budget but still receives a
    # full second to execute. Queue dwell must never be deducted from a step.
    assert started[3] - min(started[1], started[2]) >= 1.0
    assert [mut.result for mut in mutants] == ["timeout"] * 3 + ["killed", "survived"]
    assert cleaned[-1] == out and m == original
    for number, (mut, exit_code) in enumerate(zip(mutants, (None, None, None, 3, 0)), 1):
        receipts = assert_worker_resources_released(
            out / f"mutant-{number}", exit_code, CODE.replace(before, mut.after))
        assert [r["id"] for r in receipts] == [s.id for s in preparation + checks]
        assert all(r["passed"] for r in receipts[:-1])
        if exit_code is None:
            assert receipts[-1]["seconds"] >= 1.0
    assert_worker_resources_released(out, 0, CODE)
    assert (repo / "calc.py").read_text() == CODE


@pytest.mark.parametrize("parallel", [
    pytest.param(2, marks=pytest.mark.scenario(
        "parallel-evidence-mutants/baseline-before-bounded-work/baseline-precedes-overlap")),
    pytest.param(1, marks=pytest.mark.scenario(
        "parallel-evidence-mutants/baseline-before-bounded-work/serial-and-oversized-limits")),
    pytest.param(8, marks=pytest.mark.scenario(
        "parallel-evidence-mutants/baseline-before-bounded-work/serial-and-oversized-limits")),
])
def test_mutant_dispatch_waits_for_baseline_and_obeys_limit(tmp_path, monkeypatch, parallel):
    repo, _, head = project(tmp_path, STRONG)
    out = tmp_path / "out"
    before = CODE.splitlines()[1]
    mutants = [evidence.Mutant("calc.py", 2, "comment", before, before + f" # {i}")
               for i in range(3)]
    baseline_started, baseline_release, baseline_finished = Event(), Event(), Event()
    started = [Event() for _ in mutants]
    release = [Event() for _ in mutants]
    finished = [Event() for _ in mutants]
    lock = Lock()
    calls, completed = [], []
    active = peak = 0

    def run(src, sha, steps, m, run_out):
        nonlocal active, peak
        with lock:
            calls.append(run_out)
        if run_out == out:
            baseline_started.set()
            assert baseline_release.wait(10)
            baseline_finished.set()
            return [verify.Receipt("test", [], 0, 0, True, 0.1, "")]
        assert baseline_finished.is_set()
        index = int(run_out.name.split("-")[1]) - 1
        assert (src / "calc.py").read_text() == CODE.replace(before, mutants[index].after)
        with lock:
            active += 1
            peak = max(peak, active)
        started[index].set()
        try:
            assert release[index].wait(10)
            return [verify.Receipt("test", [], 0, 0 if index == 1 else 1, index == 1, 0.1, "")]
        finally:
            with lock:
                active -= 1
                completed.append(index)
            finished[index].set()

    monkeypatch.setattr(verify, "run_extracted", run)
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        future = coordinator.submit(evidence.mutate, repo, head, mutants,
                                    replace(M, mutation_parallel=parallel), out)
        try:
            assert baseline_started.wait(10)
            assert calls == [out] and not any(event.is_set() for event in started)
            baseline_release.set()
            capacity = min(parallel, len(mutants))
            for event in started[:capacity]:
                assert event.wait(10)
            with lock:
                assert active == peak == capacity
            # Release later indices first to prove collection preserves selection
            # order even when a queued job completes before the first worker.
            order = [0, 1, 2] if parallel == 1 else [1, 2, 0]
            for index in order:
                assert started[index].wait(10)
                release[index].set()
                assert finished[index].wait(10)
            assert future.result(timeout=10)
        finally:
            baseline_release.set()
            for event in release:
                event.set()
    assert completed == order
    assert peak == min(parallel, 3) and active == 0
    assert len(calls) == 4 and set(calls) == {out, *(out / f"mutant-{i}" for i in range(1, 4))}
    assert [mut.result for mut in mutants] == ["killed", "survived", "killed"]
    for run_out in calls:
        assert not (run_out / "src").exists()


@pytest.mark.scenario("parallel-evidence-mutants/baseline-before-bounded-work/failed-baseline-stops-mutants")
@pytest.mark.parametrize("stage", ["prepare", "test"])
@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_failed_baseline_never_dispatches_mutants(tmp_path, monkeypatch, stage, failure):
    repo, base, head = project(tmp_path, STRONG)
    script = "exit 3" if failure == "exit" else "sleep 60"
    bad = manifest.Step(stage, ("/bin/sh", "-c", script), timeout=0.2)
    m = replace(M, prepare=(bad,) if stage == "prepare" else (),
                verify=M.verify if stage == "prepare" else (bad,))
    out = tmp_path / "out"
    calls = []
    real_run = verify.run_extracted

    def run(src, sha, steps, run_m, run_out):
        calls.append(run_out)
        assert run_out == out
        return real_run(src, sha, steps, run_m, run_out)

    def unexpected_pool(*args, **kwargs):
        pytest.fail("failed baseline must not create a worker pool")

    monkeypatch.setattr(verify, "run_extracted", run)
    monkeypatch.setattr(evidence, "ThreadPoolExecutor", unexpected_pool)
    ev = evidence.gather(repo, base, head, ["covered"], {"covered"}, m, out, 1)
    assert calls == [out]
    assert ev.baseline_failed and ev.mutants
    assert all(mut.result == "invalid" for mut in ev.mutants)
    assert not ev.passed(0) and ev.score is None
    assert not (out / "src").exists() and not (out / "build").exists()
    receipts = json.loads((out / "receipts.json").read_text())
    assert [(r["id"], r["exit"], r["passed"]) for r in receipts] == [
        (stage, 3 if failure == "exit" else None, False)]
    saved = json.loads((out / "evidence.json").read_text())
    assert saved["baseline_failed"] and saved["covered"] == ["covered"]
    assert all(mut["result"] == "invalid" for mut in saved["mutants"])


@pytest.mark.scenario("parallel-evidence-mutants/baseline-before-bounded-work/skipped-mutation-stays-skipped")
@pytest.mark.parametrize("skip,reason", [
    ("steps", "no mutation_steps in the manifest"),
    ("base", "section started before evidence was recorded"),
    ("mutants", "no mutable lines added under mutation_paths"),
])
@pytest.mark.parametrize("uncovered", [False, True])
def test_skipped_mutation_preserves_coverage_without_starting_work(tmp_path, monkeypatch, skip, reason, uncovered):
    repo, base, head = project(tmp_path, STRONG)
    m = replace(M, mutation_steps=() if skip == "steps" else M.mutation_steps,
                coverage_exempt={"human": "manual check"})
    if skip == "base":
        base = ""
    elif skip == "mutants":
        base = head

    def unexpected_work(*args, **kwargs):
        pytest.fail("skipped mutation must not start baseline or workers")

    monkeypatch.setattr(verify, "run_steps", unexpected_work)
    monkeypatch.setattr(evidence, "ThreadPoolExecutor", unexpected_work)
    out = tmp_path / "out"
    wanted = ["covered", "human"] + (["missing"] if uncovered else [])
    ev = evidence.gather(repo, base, head, wanted, {"covered"}, m, out, 1)
    assert ev.skipped == reason and not ev.baseline_failed and ev.mutants == []
    assert ev.covered == ["covered"] and ev.uncovered == (["missing"] if uncovered else [])
    assert ev.passed(0.8) is (not uncovered) and ev.score is None
    saved = json.loads((out / "evidence.json").read_text())
    assert saved["skipped"] == reason and saved["covered"] == ev.covered
    assert saved["uncovered"] == ev.uncovered and saved["mutants"] == []


def test_empty_mutants_do_not_create_a_pool(tmp_path, monkeypatch):
    repo, _, head = project(tmp_path, STRONG)

    def unexpected_pool(*args, **kwargs):
        pytest.fail("empty selection must not create a worker pool")

    monkeypatch.setattr(evidence, "ThreadPoolExecutor", unexpected_pool)
    out = tmp_path / "out"
    assert evidence.mutate(repo, head, [], M, out)
    assert not (out / "src").exists() and not (out / "build").exists()


@pytest.mark.scenario("parallel-evidence-mutants/worker-cleanup/worker-exception-cannot-pass")
def test_worker_exception_drains_active_worker_before_baseline_cleanup(tmp_path, monkeypatch):
    repo, base, head = project(tmp_path, STRONG)
    out = tmp_path / "out"
    active, failed_cleaned, release, finished = Event(), Event(), Event(), Event()
    error = OSError("unexpected worker I/O failure")
    cleaned = []
    real_run, real_cleanup = verify.run_extracted, verify.cleanup

    def run(src, sha, steps, m, run_out):
        if run_out == out:
            return real_run(src, sha, steps, m, run_out)
        artifacts = run_out / "build" / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "source.txt").write_text((src / "calc.py").read_text())
        (run_out / "test.log").write_text("worker diagnostic")
        if run_out.name == "mutant-1":
            assert active.wait(10)
            raise error
        assert run_out.name == "mutant-2"
        active.set()
        assert release.wait(10)
        assert (out / "src" / "calc.py").read_text() == CODE
        assert (out / "build").exists()
        finished.set()
        return [verify.Receipt("test", [], 0, 0, True, 0.1, "")]

    def cleanup(run_out):
        if run_out == out:
            assert finished.is_set()
            assert cleaned == [out / "mutant-1", out / "mutant-2"]
        else:
            assert (out / "src" / "calc.py").read_text() == CODE
            assert (out / "build").exists()
        real_cleanup(run_out)
        cleaned.append(run_out)
        if run_out.name == "mutant-1":
            failed_cleaned.set()

    monkeypatch.setattr(verify, "run_extracted", run)
    monkeypatch.setattr(verify, "cleanup", cleanup)
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        future = coordinator.submit(evidence.gather, repo, base, head, [], set(),
                                    replace(M, mutation_parallel=2), out, 1)
        try:
            assert failed_cleaned.wait(10)
            assert not future.done() and not finished.is_set()
            assert (out / "src").exists() and (out / "mutant-2" / "src").exists()
            release.set()
            with pytest.raises(OSError, match="unexpected worker I/O failure") as caught:
                future.result(timeout=10)
            assert caught.value is error
        finally:
            release.set()
    assert cleaned == [out / "mutant-1", out / "mutant-2", out]
    assert not (out / "evidence.json").exists()
    for run_out in cleaned:
        assert not (run_out / "src").exists() and not (run_out / "build").exists()
    for number in (1, 2):
        assert (out / f"mutant-{number}" / "artifacts" / "source.txt").exists()
        assert (out / f"mutant-{number}" / "test.log").read_text() == "worker diagnostic"
    assert (repo / "calc.py").read_text() == CODE


@pytest.mark.parametrize("prepared", [
    pytest.param(False, marks=pytest.mark.scenario(
        "parallel-evidence-mutants/independent-mutant-workspaces/same-file-mutations-remain-separate")),
    pytest.param(True, marks=pytest.mark.scenario(
        "parallel-evidence-mutants/independent-mutant-workspaces/prepared-copies-remain-separate")),
])
@pytest.mark.parametrize("parallel", [1, 2])
def test_mutants_have_private_source_and_fresh_build(tmp_path, monkeypatch, prepared, parallel):
    repo, _, head = project(tmp_path, STRONG)
    out = tmp_path / "out"
    mutants = [
        evidence.Mutant("calc.py", 2, "<=", CODE.splitlines()[1], "    return lo < x and x <= hi"),
        evidence.Mutant("calc.py", 6, "+", CODE.splitlines()[5], "    return x * k - 1"),
        evidence.Mutant("calc.py", 2, "and", CODE.splitlines()[1], "    return lo <= x or x <= hi"),
    ]
    inspect = '''set -eu
test ! -w "{src}/calc.py"
test "$(pwd -P)" = "{build}"
test "$HOME" = "{build}/home"
test "$TMPDIR" = "{build}/tmp"
test ! -e visited
test -z "$(ls -A home)"
test -z "$(ls -A tmp)"
printf used > visited
touch home/used tmp/used
mkdir artifacts
cp "{src}/calc.py" artifacts/source.txt
cat "{src}/calc.py"
'''
    inspect_step = manifest.Step("inspect", ("/bin/sh", "-c", inspect))
    if prepared:
        copy_step = manifest.Step("copy", ("/bin/sh", "-c",
            'test ! -e project && cp -R "{src}/." project && chmod -R u+w project'))
        test_step = replace(M.verify[0], run=tuple(
            arg.replace("{src}", "{build}/project") for arg in M.verify[0].run))
        m = replace(M, prepare=(inspect_step, copy_step), verify=(test_step,))
    else:
        m = replace(M, verify=(inspect_step,) + M.verify, mutation_steps=("inspect", "test"))
    m = replace(m, mutation_parallel=parallel)

    real_run = verify.run_extracted
    runs = []
    overlap = Barrier(2)

    def observe(src, sha, steps, m, run_out):
        assert not (src / "calc.py").stat().st_mode & 0o222
        if run_out != out:
            assert src == run_out / "src"
            assert (out / "src" / "calc.py").read_text() == CODE
            assert not (out / "src" / "calc.py").stat().st_mode & 0o222
            for name in ("calc.py", "tests/test_calc.py"):
                assert not (src / name).samefile(out / "src" / name)
            assert (out / "build" / "visited").exists()
            if parallel > 1 and run_out.name in ("mutant-1", "mutant-2"):
                overlap.wait(timeout=10)
        result = real_run(src, sha, steps, m, run_out)
        assert (out / "src" / "calc.py").read_text() == CODE
        if run_out != out:
            number = int(run_out.name.split("-")[1])
            mut = mutants[number - 1]
            expected = CODE.replace(mut.before, mut.after)
            assert (src / "calc.py").read_text() == expected
            if prepared:
                assert (run_out / "build" / "project" / "calc.py").read_text() == expected
            if parallel > 1 and number <= 2:
                # Keep both prepared builds alive until each has been inspected.
                overlap.wait(timeout=10)
        runs.append(run_out)
        return result

    monkeypatch.setattr(verify, "run_extracted", observe)
    assert evidence.mutate(repo, head, mutants, m, out)
    assert [mut.result for mut in mutants] == ["killed"] * 3
    expected_runs = [out] + [out / f"mutant-{i}" for i in range(1, 4)]
    assert runs[0] == out and len(runs) == len(expected_runs)
    assert set(runs) == set(expected_runs)
    if parallel == 1:
        assert runs == expected_runs
    for number, mut in enumerate(mutants, 1):
        worker = out / f"mutant-{number}"
        expected = CODE.replace(mut.before, mut.after)
        assert (worker / "artifacts" / "source.txt").read_text() == expected
        assert (worker / "inspect.log").read_text() == expected
        receipts = json.loads((worker / "receipts.json").read_text())
        assert [r["id"] for r in receipts] == (["inspect", "copy", "test"] if prepared else ["inspect", "test"])
        assert receipts[0]["tail"] == expected
        assert receipts[-1]["passed"] is False
        assert str(worker / "build") in (worker / "verify.sb").read_text()
        if prepared:
            assert str(worker / "src") in (worker / "prepare.sb").read_text()
    for run_out in runs:
        assert not (run_out / "src").exists()
        assert not (run_out / "build").exists()
    assert (out / "artifacts" / "source.txt").read_text() == CODE
    assert (repo / "calc.py").read_text() == CODE
    assert sh(repo, "git", "status", "--porcelain") == ""


@pytest.mark.scenario("parallel-evidence-mutants/stable-evidence-results/classification-rules-retained")
@pytest.mark.parametrize("prepared", [False, True])
def test_mutant_classifications_and_nonexecuting_annotations(tmp_path, prepared):
    repo, _, _ = project(tmp_path, STRONG)
    code = CODE + ("inline = True  # mutation-equivalent: unused\n"
                   "# mutation-equivalent: unused\npreceding = True\n")
    (repo / "calc.py").write_text(code)
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "annotations")
    head = sh(repo, "git", "rev-parse", "HEAD")
    stages = (["prepare"] if prepared else []) + ["configure", "build", "test"]
    steps = tuple(manifest.Step(stage, ("/bin/sh", "-c",
        f'echo "{stage}"; if grep -Fq "fail-{stage}" "{{src}}/calc.py"; '
        'then exit 9; else exit 7; fi'), expect=7) for stage in stages)
    m = replace(M, prepare=steps[:1] if prepared else (),
                verify=steps[1:] if prepared else steps, mutation_steps=("configure", "build", "test"))
    before = CODE.splitlines()[1]
    mutants = [evidence.Mutant("calc.py", 2, "comment", before, before + " # survive")]
    mutants += [evidence.Mutant("calc.py", 2, "comment", before, before + f" # fail-{s}") for s in stages]
    mutants += [
        evidence.Mutant("calc.py", 2, "mismatch", "wrong original", "unused"),
        evidence.Mutant("calc.py", 99, "mismatch", "past EOF", "unused"),
        evidence.Mutant("calc.py", 7, "True", code.splitlines()[6], "inline = False"),
        evidence.Mutant("calc.py", 9, "True", code.splitlines()[8], "preceding = False"),
    ]
    out = tmp_path / "out"
    assert evidence.mutate(repo, head, mutants, m, out)
    expected = ["survived"] + ["killed" if s == "test" else "invalid" for s in stages]
    assert [mut.result for mut in mutants] == expected + ["invalid", "invalid", "equivalent", "equivalent"]
    for number, failed_stage in enumerate(stages, 2):
        worker = out / f"mutant-{number}"
        receipts = json.loads((worker / "receipts.json").read_text())
        assert [r["id"] for r in receipts] == stages[:stages.index(failed_stage) + 1]
        assert receipts[-1]["exit"] == 9 and not receipts[-1]["passed"]
        for skipped in stages[stages.index(failed_stage) + 1:]:
            assert not (worker / f"{skipped}.log").exists()
    for number in range(len(expected) + 1, len(mutants) + 1):
        assert not (out / f"mutant-{number}").exists()  # no commands or copies
    ev = evidence.Evidence(1, mutants=mutants)
    assert ev.score == 0.5 and not ev.unmeasured
    assert ev.passed(0.5) and not ev.passed(0.8)
    assert "1/2 caught (2 marked equivalent)" in ev.summary()


@pytest.mark.scenario("parallel-evidence-mutants/stable-evidence-results/all-invalid-remains-unmeasured")
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("parallel", [1, 2, 8])
def test_generated_all_invalid_mutants_fail_the_gate(tmp_path, prepared, parallel):
    repo, base, head = project(tmp_path, STRONG)
    baseline_only = manifest.Step("prepare" if prepared else "build", ("/bin/sh", "-c",
        f'cmp -s "{{src}}/calc.py" - <<\'BASELINE\'\n{CODE}BASELINE\n'))
    m = replace(M, mutation_parallel=parallel,
                prepare=(baseline_only,) if prepared else (),
                verify=M.verify if prepared else (baseline_only,) + M.verify,
                mutation_steps=("test",) if prepared else ("build", "test"))
    out = tmp_path / "out"
    ev = evidence.gather(repo, base, head, ["covered"], {"covered"}, m, out, 1)
    assert ev.mutants and all(mut.result == "invalid" for mut in ev.mutants)
    assert not ev.baseline_failed and ev.covered == ["covered"]
    assert ev.unmeasured and ev.score is None
    assert not ev.passed(0) and not ev.passed(0.8)
    assert "nothing was measured" in ev.summary()
    saved = json.loads((out / "evidence.json").read_text())
    assert saved["score"] is None and not saved["baseline_failed"]
    assert all(mut["result"] == "invalid" for mut in saved["mutants"])


@pytest.mark.scenario("parallel-evidence-mutants/worker-cleanup/completed-workers-release-resources")
@pytest.mark.parametrize("prepared", [False, True])
def test_serial_workers_clean_up_and_retain_diagnostics(tmp_path, monkeypatch, prepared):
    repo, _, head = project(tmp_path, STRONG)
    before = CODE.splitlines()[1]
    mutants = [evidence.Mutant("calc.py", 2, "comment", before, before + f" # {outcome}")
               for outcome in ("success", "failure", "timeout")]
    script = '''from pathlib import Path
import subprocess, sys, time
source = Path("{src}/calc.py").read_text()
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
artifacts = Path("artifacts")
artifacts.mkdir()
(artifacts / "child.pid").write_text(str(child.pid))
(artifacts / "source.txt").write_text(source)
print("diagnostic", flush=True)
if "# timeout" in source:
    time.sleep(60)
sys.exit(3 if "# failure" in source else 0)
'''
    step = manifest.Step("test", (sys.executable, "-c", script), timeout=2)
    m = replace(M, mutation_parallel=1, verify=(step,),
                prepare=(manifest.Step("prepare", ("/bin/sh", "-c", "true")),) if prepared else ())
    out = tmp_path / "out"
    cleaned = []
    real_cleanup = verify.cleanup

    def observe_cleanup(run_out):
        if run_out != out:
            assert (out / "src" / "calc.py").read_text() == CODE
            assert (out / "build").exists()
        else:
            assert cleaned == [out / f"mutant-{n}" for n in range(1, 4)]
        real_cleanup(run_out)
        cleaned.append(run_out)

    monkeypatch.setattr(verify, "cleanup", observe_cleanup)
    assert evidence.mutate(repo, head, mutants, m, out)
    assert [mut.result for mut in mutants] == ["survived", "killed", "timeout"]
    assert cleaned == [out / f"mutant-{n}" for n in range(1, 4)] + [out]
    for run_out, code in zip(cleaned, [0, 3, None, 0]):
        assert not (run_out / "src").exists()
        assert not (run_out / "build").exists()
        assert (run_out / "test.log").read_text() == "diagnostic\n"
        receipts = json.loads((run_out / "receipts.json").read_text())
        assert receipts[-1]["exit"] == code
        assert receipts[-1]["tail"] == "diagnostic\n"
        expected = CODE if run_out == out else CODE.replace(
            before, mutants[int(run_out.name.split("-")[1]) - 1].after)
        assert (run_out / "artifacts" / "source.txt").read_text() == expected
        pid = int((run_out / "artifacts" / "child.pid").read_text())
        # Allow the OS to reap the terminated orphan without invoking ps,
        # which is unavailable inside the verification sandbox.
        deadline = time.monotonic() + 5
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline, f"step child {pid} still running"
            time.sleep(0.05)


@pytest.fixture(autouse=True)
def _all_scenarios_known(monkeypatch):
    # Tests here drive section_evidence and _gate_context with fake runs that have
    # no commit to read scenarios from; #14 added that lookup (loop._scenarios_at).
    from specloop import loop

    class Known(dict):
        def __contains__(self, key):
            return True

        def __missing__(self, key):
            return ""

    monkeypatch.setattr(loop, "_scenarios_at", lambda ctx, rev: Known(), raising=False)

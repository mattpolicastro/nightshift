import json
import subprocess
from dataclasses import replace

import pytest

from specloop import evidence, manifest, tasks

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


def test_added_lines_tracks_new_line_numbers_and_paths():
    diff = ("+++ b/calc.py\n@@ -0,0 +1,3 @@\n+def f(x):\n+    # note\n+    return x < 1\n"
            "+++ b/tests/t.py\n@@ -0,0 +1 @@\n+assert f(0)\n")
    lines = evidence.added_lines(diff, ("calc.py",))
    assert lines == [("calc.py", 1, "def f(x):"), ("calc.py", 3, "    return x < 1")]


def test_generate_is_deterministic_and_one_mutant_per_line():
    lines = [("a.py", 1, "if x < lo and y > 2:"), ("a.py", 2, "return a + b")]
    first = evidence.generate(lines, 5, seed=1)
    assert [(m.line, m.op) for m in first] == [(m.line, m.op) for m in evidence.generate(lines, 5, seed=1)]
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
        "/usr/bin/grep", "-Fq", mut.before, "{src}/calc.py",
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
    assert hung and hung[0].result == "killed"
    assert _time.monotonic() - started < 50  # not the 60 s step ceiling per hanging mutant


def test_all_invalid_mutants_are_unmeasured_not_a_pass():
    from specloop.evidence import Evidence, Mutant

    ev = Evidence(1, covered=["c/r/s"])
    ev.mutants = [Mutant("a.py", 1, "<", "a < b", "a <= b", "invalid") for _ in range(3)]
    assert ev.unmeasured and ev.score is None and not ev.passed(0.8)
    assert "none of the 3 mutants could be judged" in ev.summary()
    ev.mutants[0].result = "killed"
    assert not ev.unmeasured and ev.passed(0.3)
    assert Evidence(1).passed(0.8)  # no mutants generated (e.g. no mutable lines) is still not a failure

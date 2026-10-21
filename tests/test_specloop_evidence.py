import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

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


@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/missing-duration-uses-manifest-ceiling")
@pytest.mark.parametrize("prepared", [False, True])
def test_mutant_timeout_overrides_are_exact_and_immutable(tmp_path, monkeypatch, prepared):
    # Exercise the floor, fractional scaling, zero/missing durations, and caps
    # both below the scaled duration and below the floor, for each kind of step.
    durations = (2, 12.125, 0, None, 25, 1)
    ceilings = (100, 100, 75, 80, 40, 5)
    expected = (30.0, 36.375, 75, 80, 40, 5)

    def make_steps(prefix):
        return tuple(manifest.Step(f"{prefix}-{i}", ("/usr/bin/true",), timeout=ceiling)
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
    target = manifest.Step(stage, (sys.executable, "-c", PROCESS_TREE_STEP), timeout=10)
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
    assert [mut.result for mut in mutants] == [failure, failure, "survived"]
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
    target = manifest.Step("exercise", (sys.executable, "-c", PROCESS_TREE_STEP), timeout=1)
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
        assert results == [failure, failure, "survived"]
        assert (out / "src" / "calc.py").read_text() == CODE
        for i, (mut, code) in enumerate(zip(mutants, (None, 3, 0)), 1):
            assert_worker_resources_released(out / f"mutant-{i}", code, CODE.replace(before, mut.after))
    finally:
        verify.cleanup(out)


def test_all_invalid_mutants_are_unmeasured_not_a_pass():
    from specloop.evidence import Evidence, Mutant

    ev = Evidence(1, covered=["c/r/s"])
    ev.mutants = [Mutant("a.py", 1, "<", "a < b", "a <= b", "invalid") for _ in range(3)]
    assert ev.unmeasured and ev.score is None and not ev.passed(0.8)
    assert "none of the 3 mutants could be judged" in ev.summary()
    ev.mutants[0].result = "killed"
    assert not ev.unmeasured and ev.passed(0.3)
    assert Evidence(1).passed(0.8)  # no mutants generated (e.g. no mutable lines) is still not a failure


@pytest.mark.parametrize("prepared", [
    pytest.param(False, marks=pytest.mark.scenario(
        "parallel-evidence-mutants/independent-mutant-workspaces/same-file-mutations-remain-separate")),
    pytest.param(True, marks=pytest.mark.scenario(
        "parallel-evidence-mutants/independent-mutant-workspaces/prepared-copies-remain-separate")),
])
def test_serial_mutants_have_private_source_and_fresh_build(tmp_path, monkeypatch, prepared):
    repo, _, head = project(tmp_path, STRONG)
    out = tmp_path / "out"
    mutants = [
        evidence.Mutant("calc.py", 2, "<=", CODE.splitlines()[1], "    return lo < x and x <= hi"),
        evidence.Mutant("calc.py", 6, "+", CODE.splitlines()[5], "    return x * k - 1"),
        evidence.Mutant("calc.py", 2, "and", CODE.splitlines()[1], "    return lo <= x or x <= hi"),
    ]
    inspect = '''import json, os
from pathlib import Path
src, build = Path("{src}"), Path("{build}")
assert not (src / "calc.py").stat().st_mode & 0o222
assert Path.cwd() == build
assert Path(os.environ["HOME"]) == build / "home"
assert Path(os.environ["TMPDIR"]) == build / "tmp"
assert not (build / "visited").exists()
assert not list((build / "home").iterdir())
assert not list((build / "tmp").iterdir())
(build / "visited").write_text("used")
(build / "home" / "used").touch()
(build / "tmp" / "used").touch()
(build / "artifacts").mkdir()
(build / "artifacts" / "source.txt").write_text((src / "calc.py").read_text())
print((src / "calc.py").read_text(), end="")
'''
    inspect_step = manifest.Step("inspect", (sys.executable, "-c", inspect))
    if prepared:
        copy_step = manifest.Step("copy", ("/bin/sh", "-c",
            'test ! -e project && cp -R "{src}/." project && chmod -R u+w project'))
        test_step = replace(M.verify[0], run=tuple(
            arg.replace("{src}", "{build}/project") for arg in M.verify[0].run))
        m = replace(M, prepare=(inspect_step, copy_step), verify=(test_step,))
    else:
        m = replace(M, verify=(inspect_step,) + M.verify, mutation_steps=("inspect", "test"))

    real_run = verify.run_extracted
    runs = []

    def observe(src, sha, steps, m, run_out):
        if run_out != out:
            assert src == run_out / "src"
            assert (out / "src" / "calc.py").read_text() == CODE
            assert not (out / "src" / "calc.py").stat().st_mode & 0o222
            for name in ("calc.py", "tests/test_calc.py"):
                assert not (src / name).samefile(out / "src" / name)
            assert (out / "build" / "visited").exists()
        result = real_run(src, sha, steps, m, run_out)
        assert (out / "src" / "calc.py").read_text() == CODE
        runs.append(run_out)
        return result

    monkeypatch.setattr(verify, "run_extracted", observe)
    assert evidence.mutate(repo, head, mutants, m, out)
    assert [mut.result for mut in mutants] == ["killed"] * 3
    assert runs == [out] + [out / f"mutant-{i}" for i in range(1, 4)]
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
    steps = tuple(manifest.Step(stage, (sys.executable, "-c",
        'from pathlib import Path; import sys; '
        f'print("{stage}"); sys.exit(9 if "fail-{stage}" in '
        'Path("{src}/calc.py").read_text() else 7)'), expect=7) for stage in stages)
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
    baseline_only = manifest.Step("prepare" if prepared else "build", (sys.executable, "-c",
        'from pathlib import Path; import sys; '
        f'sys.exit(0 if Path("{{src}}/calc.py").read_text() == {CODE!r} else 1)'))
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
    m = replace(M, verify=(step,), prepare=(manifest.Step("prepare", ("/usr/bin/true",)),) if prepared else ())
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
    assert [mut.result for mut in mutants] == ["survived", "killed", "killed"]
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

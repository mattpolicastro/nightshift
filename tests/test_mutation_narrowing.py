import json
import shutil
import sys
from copy import deepcopy
from dataclasses import replace
from threading import Event, get_ident

import pytest

from specloop import evidence, manifest, verify


@pytest.fixture
def narrowing(tmp_path, monkeypatch):
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "calc.py").write_text("def within(x):\n    return x >= 0\n")
    (repo / "checks.py").write_text('''import unittest
from calc import within
class Tests(unittest.TestCase):
    def test_inside(self):
        self.assertTrue(within(1))
    def test_boundary(self):
        self.assertTrue(within(0))
''')

    def extract(repo, head, src):
        shutil.copytree(repo, src)
        for path in src.iterdir():
            path.chmod(0o444)

    monkeypatch.setattr(verify, "extract", extract)
    prepare = manifest.Step("prepare", (sys.executable, "-B", "-c", '''import shutil, sys
from pathlib import Path
assert not Path("project").exists()
assert not Path("artifacts").exists()
shutil.copytree(sys.argv[1], "project")
Path("artifacts").mkdir()
Path("artifacts/source.txt").write_text(Path("project/calc.py").read_text())
''', "{src}"), timeout=120)
    test = manifest.Step("test", (sys.executable, "-B", "-c", '''import sys, unittest
sys.path.insert(0, "project")
name = "checks.Tests.test_inside" if sys.argv[1] else "checks"
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName(name))
sys.exit(0 if result.wasSuccessful() else 1)
''', "{mutant_path}"), timeout=120)
    inspect = manifest.Step("inspect", (sys.executable, "-B", "-c", '''from pathlib import Path
assert not Path("visited").exists()
Path("visited").touch()
'''), timeout=120)
    later = manifest.Step("later", (sys.executable, "-c", "print('later')"), timeout=120)
    m = manifest.Manifest(repo, "change", "main", "main", "", {"kept": "value"},
                          ("**",), (), (inspect, test, later), prepare=(prepare,),
                          mutation_steps=("inspect", "test", "later"), mutation_parallel=1)
    calls = []
    real_run = verify.run_extracted

    def run(src, head, steps, run_m, run_out):
        calls.append((src, run_m, run_out))
        return real_run(src, head, steps, run_m, run_out)

    monkeypatch.setattr(verify, "run_extracted", run)
    return repo, m, tmp_path / "out", calls


def mutant(after="    return x > 0"):
    return evidence.Mutant("calc.py", 2, ">=", "    return x >= 0", after)


def receipts(out):
    return json.loads((out / "receipts.json").read_text())


def decision(out, mut, phases):
    saved = json.loads((out / "mutation-runs.json").read_text())
    assert saved["baselines"] == [{"number": 1, "steps": ["inspect", "test", "later"],
                                   "receipts": "receipts.json", "failed": False}]
    assert saved["mutants"] == [{
        "number": 1, "path": mut.path, "line": mut.line, "op": mut.op,
        "route": None, "steps": ["inspect", "test", "later"], "baseline": 1,
        "result": mut.result, "decided_by": phases[-1] if phases else "precheck",
        "runs": [{"phase": phase, "receipts": "mutant-1/"
                  + ("confirmation/" if phase == "confirmation" else "") + "receipts.json"}
                 for phase in phases],
    }]
    assert [r["id"] for r in receipts(out)] == ["prepare", "inspect", "test", "later"]
    for run in saved["mutants"][0]["runs"]:
        stored = json.loads((out / run["receipts"]).read_text())
        assert isinstance(stored, list) and stored
        assert set(stored[-1]) == {"id", "argv", "expect", "exit", "passed", "seconds", "tail"}


@pytest.mark.scenario("parallel-evidence-mutants/mutation-decision-receipts/deciding-runs-are-recorded")
@pytest.mark.scenario("parallel-evidence-mutants/narrow-then-confirm/narrowed-failure-kills")
def test_narrowed_kill_counts_as_killed(narrowing):
    repo, m, out, calls = narrowing
    mut = mutant("    return x < 0")
    assert evidence.mutate(repo, "head", [mut], m, out)
    worker = out / "mutant-1"
    assert [p for _, _, p in calls] == [out, worker]
    assert mut.result == "killed"
    assert [r["id"] for r in receipts(worker)] == ["prepare", "inspect", "test"]
    assert receipts(worker)[-1]["exit"] == 1
    assert not (worker / "later.log").exists()
    assert not (worker / "confirmation").exists()
    ev = evidence.Evidence(1, mutants=[mut])
    assert ev.score == 1 and "1/1 caught" in ev.summary()
    decision(out, mut, ["narrowed"])


@pytest.mark.scenario("parallel-evidence-mutants/mutation-decision-receipts/deciding-runs-are-recorded")
@pytest.mark.scenario("parallel-evidence-mutants/narrow-then-confirm/narrowed-survivor-receives-full-confirmation")
@pytest.mark.parametrize("survives", [False, True])
def test_narrowed_survivor_confirmed_on_full_steps(narrowing, survives, monkeypatch):
    repo, m, out, calls = narrowing
    prepare = m.prepare[0]
    argv = list(prepare.run)
    argv[3] += '\nPath("artifacts/phase.txt").write_text(sys.argv[2])\n'
    m = replace(m, prepare=(replace(prepare, run=(*argv, "{mutant_path}")),))
    initial = {}
    real_run = verify.run_extracted

    def snapshot(src, head, steps, run_m, run_out):
        saved = real_run(src, head, steps, run_m, run_out)
        if run_out == out / "mutant-1":
            for name in ("receipts.json", "test.log", "build/artifacts/source.txt", "build/artifacts/phase.txt"):
                initial[name] = (run_out / name).read_bytes()
        return saved

    monkeypatch.setattr(verify, "run_extracted", snapshot)
    original = deepcopy(m)
    mut = mutant("    return x >= 0 # survives" if survives else "    return x > 0")
    assert evidence.mutate(repo, "head", [mut], m, out)
    worker, confirmation = out / "mutant-1", out / "mutant-1/confirmation"
    assert [p for _, _, p in calls] == [out, worker, confirmation]
    assert [run_m.vars["mutant_path"] for _, run_m, _ in calls] == ["", "calc.py", ""]
    assert calls[1][0] == calls[2][0] == worker / "src"
    assert calls[1][1] is not calls[2][1] and m == original
    assert all(r["passed"] for r in receipts(worker))
    assert [r["id"] for r in receipts(worker)] == ["prepare", "inspect", "test", "later"]
    assert [r["id"] for r in receipts(confirmation)] == ["prepare", "inspect", "test"] + (["later"] if survives else [])
    assert "Ran 1 test" in (worker / "test.log").read_text()
    assert "Ran 2 tests" in (confirmation / "test.log").read_text()
    assert mut.result == ("survived" if survives else "killed")
    ev = evidence.Evidence(1, mutants=[mut])
    assert ev.score == (0 if survives else 1)
    assert f"{0 if survives else 1}/1 caught" in ev.summary()
    for run_out in (worker, confirmation):
        assert (run_out / "artifacts/source.txt").read_text() == "def within(x):\n" + mut.after + "\n"
        assert not (run_out / "build").exists() and not (run_out / "src").exists()
    assert (repo / "calc.py").read_text() == "def within(x):\n    return x >= 0\n"
    for name, content in initial.items():
        assert (worker / name.removeprefix("build/")).read_bytes() == content
    assert (worker / "artifacts/phase.txt").read_text() == "calc.py"
    assert (confirmation / "artifacts/phase.txt").read_text() == ""
    assert (out / "artifacts/source.txt").read_text() == (repo / "calc.py").read_text()
    decision(out, mut, ["narrowed", "confirmation"])


@pytest.mark.scenario("parallel-evidence-mutants/mutation-decision-receipts/deciding-runs-are-recorded")
@pytest.mark.scenario("parallel-evidence-mutants/narrow-then-confirm/timeout-and-invalid-results-remain-distinct")
@pytest.mark.parametrize("phase", ["narrowed", "confirmation"])
@pytest.mark.parametrize("stage", ["prepare", "configure", "build", "test"])
@pytest.mark.parametrize("code", [None, 9, -15, 7], ids=["timeout", "unexpected", "signal", "expected"])
def test_phase_classifications(narrowing, phase, stage, code):
    repo, m, out, calls = narrowing
    script = '''import os, signal, sys, time
from pathlib import Path
is_mutant = "# changed" in Path(sys.argv[1], "calc.py").read_text()
is_target = bool(sys.argv[2]) == (sys.argv[3] == "narrowed")
if is_mutant and is_target:
    code = sys.argv[4]
    if code == "None": time.sleep(60)
    if code == "-15": os.kill(os.getpid(), signal.SIGTERM)
    sys.exit(int(code))
sys.exit(7)
'''
    target = manifest.Step(stage, (sys.executable, "-B", "-c", script,
                                  "{src}", "{mutant_path}", phase, str(code)), expect=7, timeout=0.5)
    # A selected placeholder is present even when preparation is the target.
    test = replace(m.verify[1], run=(sys.executable, "-c", "import sys; sys.exit(7)", "{mutant_path}"), expect=7)
    steps = (test, m.verify[-1]) if stage == "prepare" else (target, m.verify[-1])
    m = replace(m, prepare=(target,) if stage == "prepare" else m.prepare,
                verify=steps, mutation_steps=tuple(s.id for s in steps))
    mut = mutant("    return x >= 0 # changed")
    assert evidence.mutate(repo, "head", [mut], m, out)
    expected = "survived" if code == 7 else ("invalid" if stage != "test" else "timeout" if code is None else "killed")
    assert mut.result == expected
    worker = out / "mutant-1"
    confirmed = phase == "confirmation" or code == 7
    assert [p for _, _, p in calls] == [out, worker] + ([worker / "confirmation"] if confirmed else [])
    decisive = worker / "confirmation" if confirmed else worker
    saved = receipts(decisive)
    assert saved[-1]["id"] == ("later" if code == 7 else stage)
    if code != 7:
        assert saved[-1]["exit"] == code and not saved[-1]["passed"]
        assert not (decisive / "later.log").exists()
    ev = evidence.Evidence(1, mutants=[mut])
    assert ev.score == ({"killed": 1, "survived": 0}.get(expected))
    entry = json.loads((out / "mutation-runs.json").read_text())["mutants"][0]
    assert entry["result"] == expected
    assert entry["decided_by"] == ("confirmation" if confirmed else "narrowed")
    assert len(entry["runs"]) == (2 if confirmed else 1)
    assert json.loads((out / entry["runs"][-1]["receipts"]).read_text()) == saved


@pytest.mark.scenario("parallel-evidence-mutants/mutation-decision-receipts/deciding-runs-are-recorded")
@pytest.mark.scenario("parallel-evidence-mutants/narrow-then-confirm/commands-without-narrowing-run-once")
@pytest.mark.parametrize("after,expected", [("    return x > 0", "killed"), ("    return x >= 0 # same", "survived")])
def test_commands_without_placeholder_run_once(narrowing, after, expected):
    repo, m, out, calls = narrowing
    steps = tuple(replace(s, run=tuple(arg.replace("{mutant_path}", "") for arg in s.run)) for s in m.verify)
    m = replace(m, verify=steps)
    mut = mutant(after)
    assert evidence.mutate(repo, "head", [mut], m, out)
    assert mut.result == expected
    assert [p for _, _, p in calls] == [out, out / "mutant-1"]
    assert all(run_m.vars == m.vars for _, run_m, _ in calls)
    decision(out, mut, ["full"])


@pytest.mark.scenario("parallel-evidence-mutants/mutation-decision-receipts/nonexecuted-results-and-ordering-are-explicit")
@pytest.mark.scenario("parallel-evidence-mutants/narrow-then-confirm/timeout-and-invalid-results-remain-distinct")
@pytest.mark.parametrize("precheck", ["mismatch", "past-eof", "inline", "preceding"])
def test_prechecks_execute_no_mutant_commands(narrowing, precheck):
    repo, m, out, calls = narrowing
    mut = mutant()
    if precheck == "mismatch":
        mut.before = "wrong source"
    elif precheck == "past-eof":
        mut.line = 99
    elif precheck == "inline":
        mut.before += " # mutation-equivalent: unused"
        (repo / "calc.py").write_text("def within(x):\n" + mut.before + "\n")
    else:
        (repo / "calc.py").write_text("def within(x): # mutation-equivalent: unused\n" + mut.before + "\n")
    assert evidence.mutate(repo, "head", [mut], m, out)
    assert mut.result == ("invalid" if precheck in ("mismatch", "past-eof") else "equivalent")
    assert [p for _, _, p in calls] == [out]
    assert not (out / "mutant-1").exists()
    assert evidence.Evidence(1, mutants=[mut]).score is None
    decision(out, mut, [])


@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/both-phases-use-their-full-baseline")
def test_both_phases_use_assigned_full_baseline(narrowing, monkeypatch):
    repo, m, out, _ = narrowing
    (repo / "other.py").write_text((repo / "calc.py").read_text())
    m = replace(m, mutation_routes=(manifest.MutationRoute(("other.py",), ("later", "test", "inspect")),))
    original = deepcopy(m)
    mutants = [mutant("    return x >= 0 # same") for _ in range(3)]
    mutants[1].path = "other.py"
    calls, workers = [], {}
    baselines = [out, out / "baseline-2"]
    durations = [(11.25, 0.1, 21, 0), (17, 13, 12, 50)]
    limits = [(33.75, 30, 63, 120), (51, 39, 36, 120)]

    def run(src, head, steps, run_m, run_out):
        calls.append(run_out)
        ids = ("prepare", "inspect", "test", "later")
        if run_out in baselines:
            sequence = baselines.index(run_out)
            assert all(s.timeout == 120 for s in run_m.prepare + steps)
            seconds = dict(zip(ids, durations[sequence]))
            assert run_m.vars["mutant_path"] == ""
        else:
            assert calls[:2] == baselines
            worker = run_out.parent if run_out.name == "confirmation" else run_out
            number = int(worker.name.split("-")[1])
            sequence = int(number == 2)
            expected = dict(zip(ids, limits[sequence]))
            assert all(s.timeout == expected[s.id] for s in run_m.prepare + steps)
            assert run_m.vars["mutant_path"] == ("" if run_out.name == "confirmation" else mutants[number - 1].path)
            identity = (src, get_ident(), run_m.prepare, steps)
            assert workers.setdefault(number, identity) == identity
            assert src == worker / "src"
            assert (src / mutants[number - 1].path).read_text().endswith(mutants[number - 1].after + "\n")
            seconds = dict.fromkeys(ids, 0.001)  # Never becomes confirmation's budget.
        return [verify.Receipt(s.id, [], s.expect, s.expect, True, seconds[s.id], "")
                for s in run_m.prepare + steps]

    monkeypatch.setattr(verify, "run_extracted", run)
    assert evidence.mutate(repo, "head", mutants, m, out)
    assert calls == baselines + [p for i in range(1, 4) for p in
                                (out / f"mutant-{i}", out / f"mutant-{i}/confirmation")]
    assert all(mut.result == "survived" for mut in mutants) and m == original


def gather_selected(repo, m, out, mutants, monkeypatch):
    monkeypatch.setattr(evidence, "git", lambda *args: b"")
    monkeypatch.setattr(evidence, "generate", lambda *args, **kwargs: deepcopy(mutants))
    evidence.gather(repo, "base", "head", ["fixture/scenario"], {"fixture/scenario"}, m, out, 1)
    return (json.loads((out / "mutation-runs.json").read_text()),
            json.loads((out / "evidence.json").read_text()))


@pytest.mark.scenario("parallel-evidence-mutants/mutation-decision-receipts/deciding-runs-are-recorded")
def test_unexpected_error_does_not_publish_partial_index(narrowing, monkeypatch):
    repo, m, out, _ = narrowing
    real_run = verify.run_extracted

    def fail_confirmation(src, head, steps, run_m, run_out):
        if run_out.name == "confirmation":
            raise RuntimeError("confirmation crashed")
        return real_run(src, head, steps, run_m, run_out)

    monkeypatch.setattr(verify, "run_extracted", fail_confirmation)
    with pytest.raises(RuntimeError, match="confirmation crashed"):
        gather_selected(repo, m, out, [mutant()], monkeypatch)
    assert not (out / "mutation-runs.json").exists()
    assert not (out / "evidence.json").exists()
    assert receipts(out / "mutant-1")
    assert not (out / "src").exists() and not (out / "mutant-1/src").exists()


@pytest.mark.scenario("parallel-evidence-mutants/mutation-decision-receipts/nonexecuted-results-and-ordering-are-explicit")
@pytest.mark.parametrize("failed_number", [1, 2])
def test_baseline_abort_receipts(narrowing, monkeypatch, failed_number):
    repo, m, out, calls = narrowing
    steps = tuple(manifest.Step(name, (sys.executable, "-c",
                        f"raise SystemExit({13 if i == failed_number else 0})"))
                  for i, name in enumerate(("inspect", "later", "test"), 1))
    m = replace(m, verify=steps, mutation_steps=("test",), mutation_routes=(
        manifest.MutationRoute(("calc.py",), ("inspect",)),
        manifest.MutationRoute(("other.py",), ("later",))))
    mutants = [mutant(), replace(mutant(), path="other.py"), replace(mutant(), path="fallback.py")]
    saved, ev = gather_selected(repo, m, out, mutants, monkeypatch)
    assert saved["failed_baseline"] == failed_number
    assert saved["baselines"] == [
        {"number": i, "steps": [name], "failed": i == failed_number,
         "receipts": ("receipts.json" if i == 1 else f"baseline-{i}/receipts.json")
         if i <= failed_number else None}
        for i, name in enumerate(("inspect", "later", "test"), 1)]
    assert [p for _, _, p in calls] == [out] + ([out / "baseline-2"] if failed_number == 2 else [])
    for i, entry in enumerate(saved["mutants"], 1):
        assert entry["number"] == entry["baseline"] == i
        assert entry["route"] == (i if i < 3 else None)
        assert entry["result"] == "invalid" and entry["decided_by"] == "baseline"
        assert entry["runs"] == [] and not (out / f"mutant-{i}").exists()
    for baseline in saved["baselines"][:failed_number]:
        path = out / baseline["receipts"]
        stored = json.loads(path.read_text())
        assert [r["id"] for r in stored] == ["prepare"] + baseline["steps"]
        assert stored[-1]["exit"] == (13 if baseline["failed"] else 0)
        assert not (path.parent / "src").exists() and not (path.parent / "build").exists()
    assert ev["baseline_failed"] and ev["score"] is None
    assert [mut["result"] for mut in ev["mutants"]] == ["invalid"] * 3


@pytest.mark.scenario("parallel-evidence-mutants/mutation-decision-receipts/deciding-runs-are-recorded")
@pytest.mark.scenario("parallel-evidence-mutants/mutation-decision-receipts/nonexecuted-results-and-ordering-are-explicit")
def test_persisted_indexes_ignore_completion_order(narrowing, monkeypatch):
    repo, m, out, _ = narrowing
    for name in ("other.py", "full.py", "equivalent.py"):
        (repo / name).write_text((repo / "calc.py").read_text())
    (repo / "equivalent.py").write_text(
        "def within(x): # mutation-equivalent: unused\n    return x >= 0\n")
    m = replace(m, mutation_steps=("later", "inspect"), mutation_routes=(
        manifest.MutationRoute(("calc.py",), ("test", "later", "inspect")),
        manifest.MutationRoute(("other.py",), ("test", "later", "inspect")),
        manifest.MutationRoute(("equivalent.py",), ("inspect", "later"))))
    mutants = [mutant("    return x < 0"), replace(mutant(), path="other.py"),
               replace(mutant(), path="full.py"), replace(mutant(), path="equivalent.py"),
               replace(mutant(), before="mismatch")]
    real_worker, real_completed = evidence._mutate_one, evidence.as_completed
    parallel, completion_order = False, []
    release_first = Event()
    observed = [Event() for _ in mutants]

    def worker(number, *args, **kwargs):
        # Even a serial pool can finish several prechecks before as_completed
        # observes them; synchronize observation instead of assuming its order.
        if not parallel and number > 1:
            assert observed[number - 2].wait(20)
        result = real_worker(number, *args, **kwargs)
        if parallel and number == 1:
            assert release_first.wait(20)
        return result

    def completed(futures):
        for future in real_completed(futures):
            number = futures[future] + 1
            completion_order.append(number)
            observed[number - 1].set()
            # Release only after the coordinator observes mutant 2 completing.
            if number == 2:
                release_first.set()
            yield future

    monkeypatch.setattr(evidence, "_mutate_one", worker)
    monkeypatch.setattr(evidence, "as_completed", completed)
    serial = gather_selected(repo, m, out / "serial", mutants, monkeypatch)
    assert completion_order == [1, 2, 3, 4, 5]
    parallel = True
    completion_order.clear()
    release_first.clear()
    concurrent = gather_selected(repo, replace(m, mutation_parallel=3), out / "parallel", mutants, monkeypatch)
    assert completion_order.index(2) < completion_order.index(1)
    assert serial == concurrent
    for filename in ("mutation-runs.json", "evidence.json"):
        assert (out / "serial" / filename).read_bytes() == (out / "parallel" / filename).read_bytes()
    saved, ev = serial
    assert saved["baselines"] == [
        {"number": 1, "steps": ["test", "later", "inspect"], "receipts": "receipts.json", "failed": False},
        {"number": 2, "steps": ["inspect", "later"], "receipts": "baseline-2/receipts.json", "failed": False}]
    assert [e["number"] for e in saved["mutants"]] == [1, 2, 3, 4, 5]
    assert [e["route"] for e in saved["mutants"]] == [1, 2, None, 3, 1]
    assert [e["baseline"] for e in saved["mutants"]] == [1, 1, 2, 2, 1]
    assert [e["decided_by"] for e in saved["mutants"]] == ["narrowed", "confirmation", "full", "precheck", "precheck"]
    assert [e["result"] for e in saved["mutants"]] == ["killed", "survived", "survived", "equivalent", "invalid"]
    assert set(ev) == {"section", "covered", "uncovered", "mutants", "skipped", "baseline_failed", "score"}
    assert ev["score"] == 1 / 3
    for root in (out / "serial", out / "parallel"):
        for baseline in saved["baselines"]:
            stored = json.loads((root / baseline["receipts"]).read_text())
            assert [r["id"] for r in stored] == ["prepare"] + baseline["steps"]
        for entry in saved["mutants"]:
            if entry["decided_by"] == "precheck":
                assert entry["runs"] == [] and not (root / f"mutant-{entry['number']}").exists()
            for run in entry["runs"]:
                assert (root / run["receipts"]).is_file()
    text = json.dumps(saved)
    assert str(out) not in text and "seconds" not in text and "duration" not in text

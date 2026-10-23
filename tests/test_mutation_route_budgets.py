"""Budget coverage kept separate so the existing baseline/regression tests stay intact."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from threading import Barrier, Event, Lock

import pytest

from specloop import evidence, manifest, verify
from test_specloop_evidence import PREPARE_PROCESS_TREE_STEP, assert_worker_resources_released


@pytest.fixture
def source(tmp_path, monkeypatch):
    def extract(repo, head, src):
        src.mkdir(parents=True)
        for name in ("a.py", "b.py", "calc.py"):
            (src / name).write_text("x = 1\n")

    monkeypatch.setattr(verify, "extract", extract)
    return manifest.Manifest(tmp_path, "change", "main", "main", "", {}, ("**",), (), ())


@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/parallel-mutants-respect-time-box")
@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/missing-duration-uses-manifest-ceiling")
@pytest.mark.scenario("parallel-evidence-mutants/baseline-before-bounded-work/baseline-precedes-overlap")
@pytest.mark.scenario("parallel-evidence-mutants/baseline-before-bounded-work/serial-and-oversized-limits")
@pytest.mark.parametrize("parallel", [1, 2, 8])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("routed", [False, True])
def test_sequence_budgets_survive_bounded_queue(tmp_path, monkeypatch, source, parallel, prepared, routed):
    ceilings = (100, 100, 40, 50, 20, 5)
    durations = ((0.1, 11.25, 0, None, 20, 0.1), (12, 15.5, None, 0, 100, 100))
    expected = ((30.0, 33.75, 40, 50, 20, 5), (36, 46.5, 40, 50, 20, 5))

    def group(prefix):
        return tuple(manifest.Step(f"{prefix}-{i}", ("/usr/bin/true",), timeout=ceiling)
                     for i, ceiling in enumerate(ceilings))

    checks = group("check") + (manifest.Step("test", ("/usr/bin/true",), timeout=100),)
    keys = tuple(s.id for s in checks)
    routes = (manifest.MutationRoute(("b.py",), tuple(reversed(keys))),) if routed else ()
    m = replace(source, prepare=group("prepare") if prepared else (), verify=checks,
                mutation_steps=keys, mutation_routes=routes, mutation_parallel=parallel)
    original = deepcopy(m)
    mutants = [evidence.Mutant(path, 1, "int", "x = 1", f"x = {i + 2}")
               for i, path in enumerate(("a.py", "b.py", "a.py"))]
    out = tmp_path / "out"
    baseline_dirs = [out, out / "baseline-2"] if routed else [out]
    baseline_started = [Event() for _ in baseline_dirs]
    baseline_release = [Event() for _ in baseline_dirs]
    started, release, finished = ([Event() for _ in mutants] for _ in range(3))
    calls, completed, baseline_receipts = [], [], []
    lock = Lock()
    active = peak = 0

    def run(src, head, steps, run_m, run_out):
        nonlocal active, peak
        assert m == original
        with lock:
            calls.append(run_out)
        if run_out in baseline_dirs:
            sequence = baseline_dirs.index(run_out)
            assert steps == (tuple(reversed(checks)) if sequence else checks)
            assert run_m.prepare == m.prepare
            seconds = {s.id: duration for group in (m.prepare, checks[:-1])
                       for s, duration in zip(group, durations[sequence])}
            receipts = [verify.Receipt(s.id, [], 0, 0, True, seconds.get(s.id, 0.1), "")
                        for s in run_m.prepare + steps]
            baseline_receipts.append((receipts, deepcopy(receipts)))
            baseline_started[sequence].set()
            assert baseline_release[sequence].wait(10)
            return receipts
        assert all(event.is_set() for event in baseline_release)
        assert len(baseline_receipts) == len(baseline_dirs)
        index = int(run_out.name.split("-")[1]) - 1
        sequence = int(routed and index == 1)
        limits = {s.id: limit for group in (m.prepare, checks[:-1])
                  for s, limit in zip(group, expected[sequence])}
        limits["test"] = 30.0
        scheduled = run_m.prepare + steps
        assert [(s.id, s.timeout) for s in scheduled] == [(s.id, limits[s.id]) for s in scheduled]
        assert (src / mutants[index].path).read_text() == mutants[index].after + "\n"
        with lock:
            active += 1
            peak = max(peak, active)
        started[index].set()
        try:
            assert release[index].wait(10)
            # Sibling durations and queue dwell cannot change the frozen steps.
            assert all(s.timeout == limits[s.id] for s in scheduled)
            receipts = [verify.Receipt(s.id, [], 0, 0, True, 10000, "") for s in scheduled]
            if index != 1:
                receipts[-1] = replace(receipts[-1], exit=None, passed=False)
            return receipts
        finally:
            with lock:
                active -= 1
                completed.append(index)
            finished[index].set()

    monkeypatch.setattr(verify, "run_extracted", run)
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        future = coordinator.submit(evidence.mutate, tmp_path, "head", mutants, m, out)
        try:
            for i in range(len(baseline_dirs)):
                assert baseline_started[i].wait(10)
                assert calls == baseline_dirs[:i + 1]
                assert not any(event.is_set() for event in started)
                baseline_release[i].set()
            capacity = min(parallel, len(mutants))
            for event in started[:capacity]:
                assert event.wait(10)
            with lock:
                assert active == peak == capacity
            assert not any(event.is_set() for event in started[capacity:])
            order = [0, 1, 2] if parallel == 1 else [1, 2, 0]
            for index in order:
                assert started[index].wait(10)
                release[index].set()
                assert finished[index].wait(10)
            assert future.result(timeout=10)
        finally:
            for event in baseline_release + release:
                event.set()
    assert completed == order and active == 0 and peak == capacity
    assert calls[:len(baseline_dirs)] == baseline_dirs
    assert sorted(calls[len(baseline_dirs):]) == [out / f"mutant-{i}" for i in range(1, 4)]
    assert [mut.result for mut in mutants] == ["timeout", "survived", "timeout"]
    assert m == original and all(receipts == saved for receipts, saved in baseline_receipts)


@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/missing-duration-uses-manifest-ceiling")
def test_budget_snapshot_is_immutable_and_missing_receipt_uses_ceiling():
    steps = (manifest.Step("missing", ("true",), timeout=17),
             manifest.Step("measured", ("true",), timeout=100))
    receipt = verify.Receipt("measured", [], 0, 0, True, 11.25, "")
    budget = evidence._mutation_budgets(steps, steps, [receipt])
    assert tuple(s.timeout for s in budget.steps) == (17, 33.75)
    assert budget.prepare == budget.steps and steps[1].timeout == 100
    receipt.seconds = 1000
    assert budget.steps[1].timeout == 33.75
    with pytest.raises(FrozenInstanceError):
        budget.steps = ()
    with pytest.raises(FrozenInstanceError):
        budget.prepare[1].timeout = 1


@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/preparation-timeout-is-invalid")
@pytest.mark.scenario("parallel-evidence-mutants/baseline-step-time-boxes/parallel-mutants-respect-time-box")
@pytest.mark.parametrize("stage,prepared", [
    ("dependencies", True), ("configure", True), ("build", True), ("test", False), ("test", True),
])
def test_routed_timeout_stops_process_group_and_later_steps(tmp_path, monkeypatch, source, stage, prepared):
    monkeypatch.setattr(evidence, "MIN_MUTANT_SECONDS", 1.0)
    target = manifest.Step(stage, ("/bin/sh", "-c", PREPARE_PROCESS_TREE_STEP), timeout=100)
    later = manifest.Step("later", ("/usr/bin/true",), timeout=100)
    preparation = (target,) if stage == "dependencies" else (
        (manifest.Step("dependencies", ("/usr/bin/true",), timeout=100),) if prepared else ())
    checks = (later,) if stage == "dependencies" else (target, later)
    m = replace(source, prepare=preparation, mutation_step=checks,
                mutation_steps=tuple(s.id for s in checks), mutation_parallel=2,
                mutation_routes=(manifest.MutationRoute(("calc.py",), tuple(s.id for s in checks)),))
    original = deepcopy(m)
    mutants = [evidence.Mutant("calc.py", 1, "comment", "x = 1", "x = 1 # " + outcome)
               for outcome in ("timeout", "success", "timeout")]
    out = tmp_path / "out"
    overlap = Barrier(2)
    real_run = verify.run_extracted
    finished = Event()

    def run(src, head, steps, run_m, run_out):
        baseline = run_out == out
        assert all(s.timeout == (100 if baseline else 1.0) for s in run_m.prepare + steps)
        if not baseline:
            if run_out.name in ("mutant-1", "mutant-2"):
                overlap.wait(timeout=10)
            else:
                assert finished.is_set()
        receipts = real_run(src, head, steps, run_m, run_out)
        if baseline:
            for receipt in receipts:
                receipt.seconds = 0.01
        else:
            finished.set()
        return receipts

    monkeypatch.setattr(verify, "run_extracted", run)
    assert evidence.mutate(tmp_path, "head", mutants, m, out)
    timeout = "timeout" if stage == "test" else "invalid"
    assert [mut.result for mut in mutants] == [timeout, "survived", timeout]
    for i, mut in enumerate(mutants, 1):
        worker = out / f"mutant-{i}"
        receipts = assert_worker_resources_released(worker, 0 if i == 2 else None, mut.after + "\n")
        expected_ids = [s.id for s in preparation + checks]
        assert [r["id"] for r in receipts] == (expected_ids if i == 2 else expected_ids[:-1])
        assert (worker / "later.log").exists() is (i == 2)
        if i != 2:
            assert receipts[-1]["seconds"] >= 1.0
    assert_worker_resources_released(out, 0, "x = 1\n")
    assert all(r["passed"] for r in json.loads((out / "receipts.json").read_text()))
    assert m == original

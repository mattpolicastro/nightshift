import json
import sys
from dataclasses import replace

import pytest

from specloop import evidence, manifest, verify


@pytest.fixture
def routed(tmp_path, monkeypatch):
    def step(name):
        return manifest.Step(name, (sys.executable, "-c", f"print({name!r})"), timeout=120)

    m = manifest.Manifest(
        tmp_path, "change", "main", "main", "", {}, ("src/**",), (),
        (step("first"), step("second")), prepare=(step("prepare"),),
        mutation_step=(step("extra"), step("unused")),
        mutation_steps=("extra", "second", "first"), mutation_paths=("src/**",),
        mutation_routes=(
            manifest.MutationRoute(("src/a*",), ("extra", "second")),
            manifest.MutationRoute(("src/a*", "src/b*"), ("extra", "second")),
            manifest.MutationRoute(("src/c*",), ("second", "extra")),
            manifest.MutationRoute(("src/unused*",), ("unused",)),
        ),
    )
    mutants = [evidence.Mutant(f"src/{name}.py", 1, "int", "x = 1", "x = 2")
               for name in ("a", "b", "c", "fallback", "again")]
    out = tmp_path / "out"
    calls = []
    real_run = verify.run_extracted

    def extract(repo, head, src):
        assert repo == tmp_path and head == "sealed-head"
        for mut in mutants:
            target = src / mut.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(mut.before + "\n")
        (src / "baseline-origin").write_text(str(src.parent.relative_to(out)))

    def run(src, head, steps, run_m, run_out):
        baseline = not run_out.name.startswith("mutant-")
        calls.append((run_out, tuple(s.id for s in steps)))
        if baseline:
            assert run_m.vars.get("mutant_path", "") == ""
            assert all(s.timeout == 120 for s in run_m.prepare + steps)
        receipts = real_run(src, head, steps, run_m, run_out)
        if baseline:
            # Different sequences deliberately measure shared IDs differently.
            for receipt in receipts:
                receipt.seconds = 11 * len(calls)
        else:
            origin = (src / "baseline-origin").read_text()
            number = next(i for i, (p, _) in enumerate(calls, 1)
                          if p == out / origin)
            assert all(s.timeout == min(120, 33 * number) for s in run_m.prepare + steps)
            mut = mutants[int(run_out.name.split("-")[1]) - 1]
            assert (src / mut.path).read_text() == mut.after + "\n"
            (run_out / "origin").write_text(origin)
        artifacts = run_out / "build/artifacts"
        artifacts.mkdir()
        (artifacts / "kept").write_text("diagnostic")
        return receipts

    monkeypatch.setattr(verify, "extract", extract)
    monkeypatch.setattr(verify, "run_extracted", run)
    return m, mutants, out, calls


def assert_cleaned(run_out):
    assert not (run_out / "src").exists()
    assert not (run_out / "build").exists()
    assert (run_out / "receipts.json").is_file()
    assert (run_out / "prepare.log").is_file()
    assert (run_out / "artifacts/kept").read_text() == "diagnostic"


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/first-matching-route-wins")
def test_mutant_uses_first_matching_route(tmp_path, routed):
    m, mutants, out, calls = routed
    m = replace(m, mutation_routes=(m.mutation_routes[0],
                                   manifest.MutationRoute(("src/*",), ("unused",))))
    assert evidence.mutate(tmp_path, "sealed-head", mutants[:1], m, out)
    assert calls == [(out, ("extra", "second")), (out / "mutant-1", ("extra", "second"))]
    for run_out, _ in calls:
        receipts = json.loads((run_out / "receipts.json").read_text())
        assert [r["id"] for r in receipts] == ["prepare", "extra", "second"]
        assert [r["tail"].strip() for r in receipts] == ["prepare", "extra", "second"]
        assert_cleaned(run_out)
    assert mutants[0].result == "survived"


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/unmatched-file-uses-fallback")
def test_unrouted_mutant_uses_mutation_steps(tmp_path, routed):
    m, mutants, out, calls = routed
    # Keep the fixture's worker numbering aligned with the selected list.
    mutants[:] = [mutants[3]]
    assert evidence.mutate(tmp_path, "sealed-head", mutants, m, out)
    expected = ("first", "second", "extra")
    assert calls == [(out, expected), (out / "mutant-1", expected)]
    receipts = json.loads((out / "mutant-1/receipts.json").read_text())
    assert [r["id"] for r in receipts] == ["prepare", *expected]
    assert mutants[0].result == "survived"


@pytest.mark.scenario("parallel-evidence-mutants/baseline-before-bounded-work/used-step-sequences-are-baselined-once")
@pytest.mark.parametrize("variables", [{}, {"mutant_path": "src/a.py"}])
def test_full_baseline_clears_placeholder_before_precheck(tmp_path, routed, variables):
    m, mutants, out, calls = routed
    original_vars = dict(variables)
    extra = replace(m.mutation_step[0], run=m.mutation_step[0].run + ("{mutant_path}",))
    m = replace(m, vars=variables, mutation_step=(extra, m.mutation_step[1]))
    # A mismatched mutant still selects its route and requires its full baseline.
    mutants[:] = [replace(mutants[0], line=99)]
    assert evidence.mutate(tmp_path, "sealed-head", mutants, m, out)
    assert calls == [(out, ("extra", "second"))]
    receipts = json.loads((out / "receipts.json").read_text())
    assert receipts[1]["argv"][-1] == ""
    assert mutants[0].result == "invalid" and m.vars == original_vars
    assert_cleaned(out)


@pytest.mark.scenario("parallel-evidence-mutants/baseline-before-bounded-work/used-step-sequences-are-baselined-once")
@pytest.mark.parametrize("parallel", [1, 2])
def test_baseline_runs_each_route_step_set_once(tmp_path, monkeypatch, routed, parallel):
    m, mutants, out, calls = routed
    m = replace(m, mutation_parallel=parallel)
    original = replace(m)
    # First use, rather than route declaration order, determines baseline order.
    mutants[:] = [mutants[2], mutants[0], mutants[1], *mutants[3:]]
    identities = [id(mut) for mut in mutants]
    baselines = [(out, ("second", "extra")),
                 (out / "baseline-2", ("extra", "second")),
                 (out / "baseline-3", ("first", "second", "extra"))]
    real_pool = evidence.ThreadPoolExecutor

    def pool(*args, **kwargs):
        assert calls == baselines
        for run_out, _ in baselines:
            assert (run_out / "src").is_dir() and (run_out / "build").is_dir()
        return real_pool(*args, **kwargs)

    monkeypatch.setattr(evidence, "ThreadPoolExecutor", pool)
    assert evidence.mutate(tmp_path, "sealed-head", mutants, m, out)
    assert calls[:3] == baselines and len(calls) == 8
    assert [id(mut) for mut in mutants] == identities
    assert [mut.result for mut in mutants] == ["survived"] * 5
    assert [(out / f"mutant-{n}/origin").read_text() for n in range(1, 6)] == [
        ".", "baseline-2", "baseline-2", "baseline-3", "baseline-2"]
    assert m == original and m.vars == {}
    for run_out, _ in calls:
        assert_cleaned(run_out)


@pytest.mark.scenario("parallel-evidence-mutants/baseline-before-bounded-work/failed-baseline-stops-mutants")
@pytest.mark.parametrize("stage", ["prepare", "second"])
@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_later_failed_baseline_stops_all_mutants(tmp_path, monkeypatch, routed, stage, failure):
    m, mutants, out, calls = routed
    real_run = verify.run_extracted

    def run(src, head, steps, run_m, run_out):
        if run_out == out / "baseline-2":
            script = "raise SystemExit(3)" if failure == "exit" else "import time; time.sleep(60)"
            bad = manifest.Step(stage, (sys.executable, "-c", script), timeout=120)
            # Preserve the baseline ceiling assertion, enforce only the actual
            # failing subprocess's timeout through the verifier's wait call.
            if stage == "prepare":
                run_m = replace(run_m, prepare=(bad,))
            else:
                steps = tuple(bad if s.id == stage else s for s in steps)
        return real_run(src, head, steps, run_m, run_out)

    if failure == "timeout":
        real_wait = verify.subprocess.Popen.wait

        def wait(process, timeout=None):
            if "import time; time.sleep(60)" in process.args and timeout is not None:
                timeout = 0.05
            return real_wait(process, timeout=timeout)

        monkeypatch.setattr(verify.subprocess.Popen, "wait", wait)
    monkeypatch.setattr(verify, "run_extracted", run)
    monkeypatch.setattr(evidence, "ThreadPoolExecutor", lambda **kw: pytest.fail("no workers"))
    monkeypatch.setattr(evidence, "git", lambda *args: b"")
    monkeypatch.setattr(evidence, "generate", lambda *args, **kw: mutants)
    ev = evidence.gather(tmp_path, "base", "sealed-head", ["covered"], {"covered"}, m, out, 1)
    assert calls == [(out, ("extra", "second")), (out / "baseline-2", ("second", "extra"))]
    assert ev.baseline_failed and not ev.passed(0) and ev.score is None
    assert [mut.result for mut in ev.mutants] == ["invalid"] * 5
    receipts = json.loads((out / "baseline-2/receipts.json").read_text())
    assert receipts[-1]["id"] == stage and not receipts[-1]["passed"]
    assert receipts[-1]["exit"] == (3 if failure == "exit" else None)
    assert not (out / "baseline-3").exists()
    for run_out, _ in calls:
        assert_cleaned(run_out)


@pytest.mark.scenario("parallel-evidence-mutants/baseline-before-bounded-work/skipped-mutation-stays-skipped")
@pytest.mark.parametrize("skip,reason", [
    ("steps", "no mutation_steps in the manifest"),
    ("base", "section started before evidence was recorded"),
    ("mutants", "no mutable lines added under mutation_paths"),
])
@pytest.mark.parametrize("uncovered", [False, True])
def test_routes_do_not_start_work_when_skipped(tmp_path, monkeypatch, routed, skip, reason, uncovered):
    m, _, out, calls = routed
    if skip == "steps":
        m = replace(m, mutation_steps=(), mutation_routes=())
    monkeypatch.setattr(evidence, "git", lambda *args: b"")
    monkeypatch.setattr(verify, "run_steps", lambda *a: pytest.fail("no baseline"))
    monkeypatch.setattr(evidence, "ThreadPoolExecutor", lambda **kw: pytest.fail("no workers"))
    wanted = ["covered", "missing"] if uncovered else ["covered"]
    ev = evidence.gather(tmp_path, "" if skip == "base" else "base", "sealed-head",
                         wanted, {"covered"}, m, out, 1)
    assert ev.skipped == reason and not ev.baseline_failed and ev.mutants == []
    assert ev.covered == ["covered"] and ev.uncovered == (["missing"] if uncovered else [])
    assert ev.passed(0.8) is (not uncovered) and ev.score is None
    assert calls == []
    saved = json.loads((out / "evidence.json").read_text())
    assert saved["skipped"] == reason and saved["covered"] == ev.covered
    assert saved["uncovered"] == ev.uncovered and saved["mutants"] == []

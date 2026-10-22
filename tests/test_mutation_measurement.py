import json
from dataclasses import asdict, replace

import pytest

from specloop import evidence, loop, manifest, tasks, verify
from specloop.state import Run


def with_results(*results):
    return evidence.Evidence(1, covered=["c/r/s"], mutants=[
        evidence.Mutant("calc.py", i, "True", f"value_{i} = True", f"value_{i} = False", result)
        for i, result in enumerate(results, 1)
    ])


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/a-behavioral-outcome-establishes-measurement")
@pytest.mark.parametrize("results", [
    ("equivalent",), ("timeout",), ("invalid",), ("",),
    ("equivalent", "timeout"), ("equivalent", "timeout", "invalid"),
    ("equivalent", "timeout", "invalid", ""), ("timeout", "invalid", ""),
])
@pytest.mark.parametrize("behavioral,score", [(None, None), ("killed", 1.0), ("survived", 0.0)])
@pytest.mark.parametrize("minimum", [0, 0.5, 0.8, 1])
def test_measurement_result_and_threshold_matrix(results, behavioral, score, minimum):
    ev = with_results(*results)
    if behavioral is not None:
        ev.mutants.append(evidence.Mutant("calc.py", 99, "True", "flag = True", "flag = False", behavioral))
    assert ev.unmeasured is (behavioral is None)
    assert ev.score == score
    assert ev.passed(minimum) is (score is not None and score >= minimum)
    for failed in (replace(ev, uncovered=["c/r/missing"]), replace(ev, baseline_failed=True)):
        assert failed.unmeasured == ev.unmeasured
        assert failed.score == score
        assert not failed.passed(minimum)


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/mixed-results-exclude-timeouts")
@pytest.mark.parametrize("results,score,fraction", [
    (("killed", "survived", "timeout", "timeout", "invalid", "equivalent"), 0.5, "1/2"),
    (("survived", "timeout", "timeout"), 0.0, "0/1"),
])
def test_mixed_results_count_only_behavioral_outcomes(results, score, fraction):
    ev = with_results(*results)
    assert not ev.unmeasured and ev.score == score
    assert ev.passed(score) and not ev.passed(score + 0.1)
    assert f"mutation: {fraction} caught" in ev.summary()
    assert ev.summary().splitlines().count(
        f"2 of {len(results)} mutants timed out and were not counted") == 1


@pytest.mark.scenario("parallel-evidence-mutants/unmeasured-equivalence-reporting/equivalent-outcomes-explained-accurately")
@pytest.mark.parametrize("results", [
    ("equivalent",), ("equivalent", "equivalent"),
    ("equivalent", "timeout"), ("invalid", "equivalent", "timeout", "equivalent"),
    ("equivalent", "timeout", "invalid", ""), ("",), ("invalid", ""),
])
def test_unmeasured_summaries_explain_annotations_and_pending_results(results):
    ev = with_results(*results)
    summary = ev.summary()
    assert ev.score is None and ev.unmeasured
    assert "nothing was measured" in summary
    assert "none of the" in summary and "were killed or survived" in summary
    assert "every one was invalid" not in summary
    assert "caught" not in summary and "skipped" not in summary
    equivalents = results.count("equivalent")
    assert ("review equivalence annotations" in summary) is bool(equivalents)
    if equivalents:
        assert f"{equivalents} marked equivalent" in summary
    if "" in results:
        assert f"{results.count('')} pending" in summary
    timeout_lines = [line for line in summary.splitlines() if "timed out and were not counted" in line]
    assert timeout_lines == ([f"{results.count('timeout')} of {len(results)} mutants timed out and were not counted"]
                             if "timeout" in results else [])


@pytest.fixture
def measurement_manifest(tmp_path):
    return manifest.parse({
        "run": {"repo": str(tmp_path), "change": "measurement", "branch": "main"},
        "roles": {"implement": "p", "review": "p"},
        "profiles": {"p": {"adapter": "codex", "model": "m"}},
        "paths": {"allowed": ["calc.py"]},
        "verify": [{"id": stage, "run": ["unused"]} for stage in ("build", "test")],
        "gates": {"evidence": True, "mutation_steps": ["build", "test"],
                  "mutation_paths": ["calc.py"], "mutation_count": 10, "mutation_min": 0},
    })


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/equivalent-and-timeout-only-evidence-is-unmeasured")
@pytest.mark.scenario("parallel-evidence-mutants/unmeasured-equivalence-reporting/equivalent-outcomes-explained-accurately")
@pytest.mark.parametrize("results", [
    ("equivalent", "equivalent"),
    ("equivalent", "timeout"),
    ("invalid", "equivalent", "timeout", "equivalent"),
    pytest.param(("timeout", "timeout"), marks=pytest.mark.scenario(
        "parallel-evidence-mutants/timeout-scoring/all-timeouts-are-unmeasured")),
    pytest.param(("timeout", "invalid"), marks=pytest.mark.scenario(
        "parallel-evidence-mutants/timeout-scoring/timeouts-mixed-with-invalid-are-unmeasured")),
])
def test_equivalent_and_timeout_only_evidence_is_unmeasured(tmp_path, monkeypatch, measurement_manifest, results):
    # Run real selection, dispatch and classification, with host-independent
    # verification receipts instead of sandboxed commands or a Git repository.
    statements = [f"value_{i} = True" + (
        "  # mutation-equivalent: unused value" if result == "equivalent" else "")
        for i, result in enumerate(results, 1)]
    # An annotation also applies to the immediately following line.
    source_lines = [line for statement in statements for line in (statement, "")]
    source = "\n".join(source_lines) + "\n"
    diff = f"+++ b/calc.py\n@@ -0,0 +1,{len(source_lines)} @@\n" + "\n".join("+" + line for line in source_lines)

    def git(repo, *args):
        if args == ("diff", "--unified=0", "base", "head"):
            return diff.encode()
        assert args == ("show", "head:calc.py")
        return source.encode()

    baseline_calls = []

    def baseline(repo, head, steps, m, out):
        baseline_calls.append(m.mutation_parallel)
        (out / "src").mkdir(parents=True)
        (out / "src/calc.py").write_text(source)
        return [verify.Receipt(step.id, [], 0, 0, True, 0.1, "") for step in steps]

    def run_mutant(src, head, steps, m, out):
        changed = (src / "calc.py").read_text().splitlines()
        changed_lines = [i for i, (before, after) in enumerate(zip(source_lines, changed)) if before != after]
        assert len(changed_lines) == 1
        result = results[changed_lines[0] // 2]
        assert result != "equivalent", "annotations must not execute verification"
        stage, code = ("build", 1) if result == "invalid" else ("test", None)
        return [verify.Receipt(stage, [], 0, code, False, 0.1, "")]

    monkeypatch.setattr(evidence, "git", git)
    monkeypatch.setattr(verify, "run_steps", baseline)
    monkeypatch.setattr(verify, "run_extracted", run_mutant)
    snapshots = []
    selected = evidence.generate(evidence.added_lines(diff, ("calc.py",)), 10, 1, sources={"calc.py": source})
    for parallel in (1, 3):
        out = tmp_path / f"evidence-{parallel}"
        m = replace(measurement_manifest, mutation_parallel=parallel)
        ev = evidence.gather(tmp_path, "base", "head", ["c/r/s"], {"c/r/s"}, m, out, 1)
        assert ev.covered == ["c/r/s"] and not ev.baseline_failed and not ev.skipped
        assert ev.unmeasured and ev.score is None
        assert not any(ev.passed(minimum) for minimum in (0, 0.5, 0.8, 1))
        assert [replace(mut, result="") for mut in ev.mutants] == selected
        assert len(selected) == len(results)
        assert [mut.result for mut in ev.mutants] == [results[(mut.line - 1) // 2] for mut in selected]
        saved = json.loads((out / "evidence.json").read_text())
        assert saved == {**asdict(ev), "score": None}
        snapshots.append((saved, ev.summary()))
    assert baseline_calls == [1, 3]
    assert snapshots[0] == snapshots[1]

    # With no previous strengthening round, unmeasured evidence must pause.
    run = Run(tmp_path / "run")
    run.state.section_base["1"] = "base"
    ctx = loop.Ctx(run, m, {})
    all_tasks = tasks.parse("## 1. Core\n\n- [x] 1.1 Cover `c/r/s`\n")
    original_tasks = [asdict(task) for task in all_tasks]
    monkeypatch.setattr(loop, "_scenarios_at", lambda *args: {"c/r/s": "covered"})
    monkeypatch.setattr(evidence, "gather", lambda *args: ev)

    def unexpected_strengthening(*args, **kwargs):
        pytest.fail("unmeasured evidence must pause without strengthening")

    monkeypatch.setattr(tasks, "extend_section", unexpected_strengthening)
    monkeypatch.setattr(loop, "_rewrite_tasks", unexpected_strengthening)
    assert not loop.section_evidence(ctx, 1, "head", all_tasks)
    saved_state = Run(run.dir).state
    assert saved_state.status == "paused" and saved_state.pause.reason == "gate_evidence"
    assert saved_state.pause.detail == ev.summary()
    assert saved_state.strengthen_rounds == {} and saved_state.evidenced == []
    assert saved_state.evidence_summary == {} and saved_state.tasks == {}
    assert [asdict(task) for task in all_tasks] == original_tasks


@pytest.mark.scenario("parallel-evidence-mutants/timeout-scoring/equivalent-and-skipped-behavior-retained")
@pytest.mark.parametrize("reason", [
    "no mutation_steps in the manifest", "section started before evidence was recorded",
    "no mutable lines added under mutation_paths",
])
def test_skipped_evidence_keeps_coverage_and_baseline_checks(reason):
    ev = evidence.Evidence(1, covered=["c/r/s"], skipped=reason)
    assert not ev.unmeasured and ev.score is None
    assert ev.summary().splitlines()[-1] == f"mutation: skipped ({reason})"
    for minimum in (0, 0.5, 0.8, 1):
        assert ev.passed(minimum)
        assert not replace(ev, uncovered=["c/r/missing"]).passed(minimum)
        assert not replace(ev, baseline_failed=True).passed(minimum)

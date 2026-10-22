import json
from dataclasses import asdict, replace
from threading import Barrier, Event, Lock

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


@pytest.fixture
def filtered_measurement(monkeypatch, measurement_manifest):
    """Use the existing receipt-stub pattern with real selection and workers."""
    real_cleanup = verify.cleanup

    def install(out, outcomes, prepared, parallel):
        # Every eligible line has one code operator; prose and configured
        # exclusions would otherwise compete for the exact-sized sample.
        lines = ['"""True and 123 are documentation."""',
                 '# False or 456', 'padding = 789  # cosmetic']
        expected_by_line = {}
        for index, outcome in enumerate(outcomes):
            statement = f"candidate_{index} = True"
            statement += ("  # mutation-equivalent: unused value" if outcome == "equivalent"
                          else "  # True and 123 are commentary")
            lines.append(statement)
            expected_by_line[len(lines)] = outcome
            lines.append("")  # Equivalence annotations also cover the next line.
        source = "\n".join(lines) + "\n"
        diff = f"+++ b/calc.py\n@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + line for line in lines)
        checks = tuple(manifest.Step(stage, ("unused",)) for stage in ("configure", "build", "test"))
        m = replace(measurement_manifest, mutation_parallel=parallel, mutation_count=len(outcomes),
                    mutation_skip=("cosmetic",), prepare=(manifest.Step("dependencies", ("unused",)),)
                    if prepared else (), verify=checks, mutation_steps=tuple(s.id for s in checks))
        selected = evidence.generate(evidence.added_lines(diff, m.mutation_paths), m.mutation_count,
                                     seed=1, sources={"calc.py": source}, mutation_skip=m.mutation_skip)
        assert len(selected) == len(outcomes)
        assert {mut.line for mut in selected} == set(expected_by_line)
        for mut in selected:
            assert mut.path == "calc.py" and mut.op == "True"
            assert mut.before == lines[mut.line - 1]
            assert mut.after == mut.before.replace("= True", "= False", 1)
        classifications = {"configure": "invalid", "build": "invalid", "dependencies": "invalid",
                           "mismatch": "invalid"}
        expected = evidence.Evidence(1, covered=["c/r/s"], mutants=[
            replace(mut, result=classifications.get(expected_by_line[mut.line], expected_by_line[mut.line]))
            for mut in selected])
        executable = [i for i, mut in enumerate(selected, 1)
                      if expected_by_line[mut.line] not in ("mismatch", "equivalent")]
        baseline_source = source
        for mut in selected:
            if expected_by_line[mut.line] == "mismatch":
                # Simulate an extracted line that no longer matches the diff;
                # leave generation untouched so filtering is still exercised.
                baseline_source = baseline_source.replace(mut.before, mut.before + " # stale extract")
        git_calls, baseline_calls, completed = [], [], []
        first_pair, second_cleaned, lock = Barrier(2), Event(), Lock()

        def git(repo, *args):
            git_calls.append(args)
            if args == ("diff", "--unified=0", "base", "head"):
                return diff.encode()
            assert args == ("show", "head:calc.py")
            return source.encode()

        def receipts_for(src, steps, run_m, run_out, outcome):
            stages = run_m.prepare + steps
            assert bool(run_m.prepare) is prepared
            text = (src / "calc.py").read_text()
            if prepared:
                project = run_out / "build/project"
                project.mkdir(parents=True)
                (project / "calc.py").write_text(text)
                assert (project / "calc.py").read_text() == text
            receipts = []
            for step in stages:
                code = 0
                if step.id == outcome or (step.id == "test" and outcome == "killed"):
                    code = 9
                elif step.id == "test" and outcome == "timeout":
                    code = None
                receipts.append(verify.Receipt(step.id, list(step.run), 0, code, code == 0, 0.1, text))
                (run_out / f"{step.id}.log").write_text(text)
                if code != 0:
                    break
            (run_out / "receipts.json").write_text(json.dumps([asdict(r) for r in receipts]))
            return receipts

        def baseline(repo, head, steps, run_m, run_out):
            assert head == "head" and run_out == out
            baseline_calls.append(run_out)
            (out / "src").mkdir(parents=True)
            (out / "src/calc.py").write_text(baseline_source)
            return receipts_for(out / "src", steps, run_m, out, "survived")

        def run_mutant(src, head, steps, run_m, run_out):
            assert baseline_calls == [out] and head == "head"
            number = int(run_out.name.removeprefix("mutant-"))
            assert number in executable, "mismatches and equivalents must execute no commands"
            mut = selected[number - 1]
            assert (src / "calc.py").read_text() == baseline_source.replace(mut.before, mut.after)
            assert (out / "src/calc.py").read_text() == baseline_source
            if parallel == 2 and len(executable) >= 2:
                if number in executable[:2]:
                    first_pair.wait(timeout=10)
                if number == executable[0]:
                    assert second_cleaned.wait(10)
            receipts = receipts_for(src, steps, run_m, run_out, expected_by_line[mut.line])
            with lock:
                completed.append(number)
            return receipts

        def cleanup(run_out):
            real_cleanup(run_out)
            if len(executable) >= 2 and run_out == out / f"mutant-{executable[1]}":
                second_cleaned.set()

        monkeypatch.setattr(evidence, "git", git)
        monkeypatch.setattr(verify, "run_steps", baseline)
        monkeypatch.setattr(verify, "run_extracted", run_mutant)
        monkeypatch.setattr(verify, "cleanup", cleanup)

        def check_persisted():
            assert git_calls == [("diff", "--unified=0", "base", "head"), ("show", "head:calc.py")]
            assert baseline_calls == [out]
            assert sorted(completed) == executable
            if parallel == 1:
                assert completed == executable
            elif len(executable) >= 2:
                assert completed.index(executable[1]) < completed.index(executable[0])
            saved = (out / "evidence.json").read_text()
            assert json.loads(saved) == {**asdict(expected), "score": expected.score}
            stages = [s.id for s in m.prepare + m.verify]
            baseline_receipts = json.loads((out / "receipts.json").read_text())
            assert [r["id"] for r in baseline_receipts] == stages
            assert all(r["passed"] for r in baseline_receipts)
            for number, mut in enumerate(selected, 1):
                worker = out / f"mutant-{number}"
                assert not (worker / "src").exists() and not (worker / "build").exists()
                if number not in executable:
                    assert not worker.exists()
                    continue
                outcome = expected_by_line[mut.line]
                receipts = json.loads((worker / "receipts.json").read_text())
                executed = stages[:stages.index(outcome) + 1] if outcome in stages else stages
                assert [r["id"] for r in receipts] == executed
                assert all(r["passed"] for r in receipts[:-1])
                assert receipts[-1]["passed"] is (outcome == "survived")
                assert receipts[-1]["exit"] == (0 if outcome == "survived" else None if outcome == "timeout" else 9)
                for stage in stages:
                    log = worker / f"{stage}.log"
                    assert log.exists() is (stage in executed)
                    if log.exists():
                        assert log.read_text() == baseline_source.replace(mut.before, mut.after)
                assert all(r["tail"] == baseline_source.replace(mut.before, mut.after) for r in receipts)
            assert not (out / "src").exists() and not (out / "build").exists()
            return saved

        return m, expected, check_persisted

    return install


@pytest.mark.scenario("parallel-evidence-mutants/stable-evidence-results/parallel-results-match-serial")
@pytest.mark.scenario("parallel-evidence-mutants/stable-evidence-results/classification-rules-retained")
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("case", ["mixed", pytest.param("all-invalid", marks=pytest.mark.scenario(
    "parallel-evidence-mutants/stable-evidence-results/all-invalid-remains-unmeasured"))])
def test_filtered_gather_results_match_serial(tmp_path, filtered_measurement, prepared, case):
    outcomes = ["configure", "build", "mismatch"] + (["dependencies"] if prepared else [])
    if case == "mixed":
        outcomes += ["survived", "killed", "timeout", "equivalent"]
    snapshots = []
    for parallel in (1, 2):
        out = tmp_path / f"evidence-{parallel}"
        m, expected, check_persisted = filtered_measurement(out, outcomes, prepared, parallel)
        ev = evidence.gather(tmp_path, "base", "head", ["c/r/s"], {"c/r/s"}, m, out, 1)
        assert ev == expected
        assert ev.covered == ["c/r/s"] and not ev.uncovered and not ev.baseline_failed and not ev.skipped
        if case == "mixed":
            assert not ev.unmeasured and ev.score == 0.5
            assert ev.passed(0.5) and not ev.passed(0.8)
            assert "mutation: 1/2 caught (1 marked equivalent)" in ev.summary()
            assert ev.summary().splitlines().count(f"1 of {len(outcomes)} mutants timed out and were not counted") == 1
        else:
            assert ev.unmeasured and ev.score is None
            assert all(mut.result == "invalid" for mut in ev.mutants)
            assert not any(ev.passed(minimum) for minimum in (0, 0.5, 0.8, 1))
            assert "every one was invalid" in ev.summary() and "nothing was measured" in ev.summary()
        snapshots.append((check_persisted(), ev.summary(), [ev.passed(n) for n in (0, 0.5, 0.8, 1)]))
    assert snapshots[0] == snapshots[1]


@pytest.mark.scenario("parallel-evidence-mutants/unmeasured-equivalence-reporting/unmeasured-equivalents-pause-the-section")
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("parallel", [1, 2])
@pytest.mark.parametrize("outcomes", [
    ("equivalent", "equivalent"), ("equivalent", "timeout"),
    ("equivalent", "build"), ("equivalent", "timeout", "timeout", "configure"),
])
def test_filtered_unmeasured_section_pauses_before_strengthening(
        tmp_path, monkeypatch, filtered_measurement, prepared, parallel, outcomes):
    run = Run(tmp_path / "run")
    run.state.section_base["1"] = "base"
    assert run.state.strengthen_rounds == {}
    out = run.dir / "gates/section-1-0"
    m, expected, check_persisted = filtered_measurement(out, outcomes, prepared, parallel)
    ctx = loop.Ctx(run, m, {})
    all_tasks = tasks.parse("## 1. Core\n\n- [x] 1.1 Cover `c/r/s`\n")
    original_tasks = [asdict(task) for task in all_tasks]
    monkeypatch.setattr(loop, "_scenarios_at", lambda *args: {"c/r/s": "covered"})

    def unexpected_strengthening(*args, **kwargs):
        pytest.fail("unmeasured evidence must pause without strengthening")

    monkeypatch.setattr(tasks, "extend_section", unexpected_strengthening)
    monkeypatch.setattr(loop, "_rewrite_tasks", unexpected_strengthening)
    assert not loop.section_evidence(ctx, 1, "head", all_tasks)
    check_persisted()
    state = Run(run.dir).state
    assert state.status == "paused" and state.pause.reason == "gate_evidence"
    assert state.pause.detail == expected.summary()
    assert "nothing was measured" in state.pause.detail
    assert "review equivalence annotations" in state.pause.detail
    assert "every one was invalid" not in state.pause.detail
    assert state.strengthen_rounds == {} and state.evidenced == []
    assert state.evidence_summary == {} and state.evidence_scores == {} and state.tasks == {}
    assert [asdict(task) for task in all_tasks] == original_tasks
    events = [json.loads(line) for line in (run.dir / "events.jsonl").read_text().splitlines()]
    recorded = [event for event in events if event["event"] == "evidence"]
    assert len(recorded) == 1
    event = recorded[0]
    assert isinstance(event.pop("at"), (int, float))
    assert type(event["timeouts"]) is int
    assert event == {"event": "evidence", "section": 1, "passed": False, "score": None,
                     "timeouts": outcomes.count("timeout"), "uncovered": [], "survived": []}
    timeout_lines = [line for line in state.pause.detail.splitlines() if "timed out and were not counted" in line]
    assert timeout_lines == ([f"{outcomes.count('timeout')} of {len(outcomes)} mutants timed out and were not counted"]
                             if "timeout" in outcomes else [])

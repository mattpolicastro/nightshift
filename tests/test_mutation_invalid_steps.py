import json
import shutil
import sys
import tomllib
from dataclasses import asdict, replace

import pytest

from specloop import evidence, manifest, verify
from test_mutation_skip import TEMPLATE


@pytest.mark.scenario("parallel-evidence-mutants/configurable-invalid-steps/defaults-match-build-steps")
def test_mutation_invalid_steps_default_matches_build_steps():
    parsed = manifest.parse(tomllib.loads(TEMPLATE))
    direct = manifest.Manifest(parsed.repo, parsed.change, parsed.branch, parsed.pr_base,
                               parsed.github, parsed.roles, parsed.allowed, parsed.frozen, parsed.verify)
    assert parsed.mutation_invalid_steps == direct.mutation_invalid_steps == evidence.BUILD_STEPS == (
        "configure", "build")


@pytest.mark.scenario("parallel-evidence-mutants/configurable-invalid-steps/explicit-settings-round-trip")
@pytest.mark.parametrize("ids", [[], ["dart-analyze"], ["unused", " padded ", " ", "unused", "test"]])
@pytest.mark.parametrize("overrides", [{}, {"change": "another", "branch": "spec/another"}])
def test_invalid_steps_round_trip(tmp_path, ids, overrides):
    text = TEMPLATE + '\n[gates]\nmutation_steps = ["test"]\nmutation_invalid_steps = ' + json.dumps(ids)
    parsed = manifest.parse(tomllib.loads(text))
    path = tmp_path / "manifest.toml"
    path.write_text(text)
    effective, stored = manifest.template(path, **overrides)
    path.write_text(stored)
    assert manifest.load(path) == effective
    assert parsed.mutation_invalid_steps == effective.mutation_invalid_steps == tuple(ids)
    assert effective.change == overrides.get("change", parsed.change)
    assert effective.branch == overrides.get("branch", parsed.branch)
    assert effective.verify == parsed.verify and effective.mutation_steps == ("test",)
    assert evidence.resolve_mutation_steps("src/a.dart", effective) == (None, parsed.verify)


@pytest.mark.scenario("parallel-evidence-mutants/configurable-invalid-steps/invalid-settings-are-rejected")
@pytest.mark.parametrize("value", ['"build"', '""', "1", "1.5", "true", "{}",
                                       '["build", 1]', '[false]', '[[]]', '[{}]', '[""]'])
def test_invalid_steps_reject_malformed_settings(tmp_path, value):
    text = TEMPLATE + "\n[gates]\nmutation_invalid_steps = " + value
    with pytest.raises(ValueError, match=r"gates\.mutation_invalid_steps"):
        manifest.parse(tomllib.loads(text))
    path = tmp_path / "manifest.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match=r"gates\.mutation_invalid_steps"):
        manifest.load(path)


@pytest.fixture
def source(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("x = 1\n")
    monkeypatch.setattr(verify, "extract", lambda repo, head, src: shutil.copytree(repo, src))
    monkeypatch.setattr(verify, "already_sandboxed", lambda: True)
    return repo, manifest.parse(tomllib.loads(TEMPLATE)), tmp_path / "out"


@pytest.mark.scenario("parallel-evidence-mutants/configured-failure-classification/non-compiling-mutant-is-invalid")
@pytest.mark.parametrize("parallel", [1, 2])
def test_non_compiling_mutant_is_invalid_not_killed(source, monkeypatch, parallel):
    repo, m, out = source
    compiler = manifest.Step("dart-analyze", (sys.executable, "-B", "-c",
        'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(), "calc.py", "exec")',
        "{src}/calc.py"))
    test = manifest.Step("test", (sys.executable, "-B", "-c", "print('tests ran')"))
    m = replace(m, verify=(compiler, test), mutation_steps=(compiler.id, test.id),
                mutation_invalid_steps=(compiler.id,), mutation_parallel=parallel)
    selected = [evidence.Mutant("calc.py", 1, "fixture", "x = 1", after) for after in ("x =", "x = (")]
    monkeypatch.setattr(evidence, "git", lambda *args: b"")
    monkeypatch.setattr(evidence, "generate", lambda *args, **kwargs: selected)
    ev = evidence.gather(repo, "base", "head", [], set(), m, out, 1)
    assert not ev.baseline_failed and [mut.result for mut in ev.mutants] == ["invalid", "invalid"]
    assert ev.unmeasured and ev.score is None and not ev.passed(0)
    assert json.loads((out / "evidence.json").read_text()) == {**asdict(ev), "score": None}
    assert (out / "test.log").read_text().strip() == "tests ran"
    for number in (1, 2):
        worker = out / f"mutant-{number}"
        saved = json.loads((worker / "receipts.json").read_text())
        assert len(saved) == 1 and saved[0]["id"] == "dart-analyze"
        assert saved[0]["exit"] == 1 and not saved[0]["passed"]
        assert "SyntaxError" in saved[0]["tail"]
        assert not (worker / "test.log").exists()
        assert not (worker / "src").exists() and not (worker / "build").exists()


@pytest.mark.scenario("parallel-evidence-mutants/configured-failure-classification/replacement-and-preparation-precedence")
@pytest.mark.scenario("parallel-evidence-mutants/configured-failure-classification/routes-and-phases-agree")
@pytest.mark.parametrize("ids,stage,invalid", [
    (("configure", "build"), "build", True),
    (("dart-analyze",), "dart-analyze", True),
    (("dart-analyze",), "configure", False), (("dart-analyze",), "build", False),
    ((), "configure", False), ((), "build", False),
    (("dart-analyze",), "test", False), (("test",), "test", True),
    ((), "prepare", True), (("dart-analyze",), "prepare", True),
])
@pytest.mark.parametrize("code", [None, 9, -15, 7])
@pytest.mark.parametrize("phase", ["full", "narrowed", "confirmation"])
@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("parallel", [1, 2])
def test_configured_receipt_classification(source, monkeypatch, ids, stage, invalid, code, phase, routed, parallel):
    repo, m, out = source
    target = manifest.Step(stage, ("fixture",), expect=7, timeout=100)
    later = manifest.Step("later", ("fixture",) + (("{mutant_path}",) if phase != "full" else ()), timeout=100)
    steps = (later,) if stage == "prepare" else (target, later)
    m = replace(m, prepare=(target,) if stage == "prepare" else (), verify=steps,
                mutation_steps=tuple(s.id for s in steps), mutation_invalid_steps=ids, mutation_parallel=parallel,
                mutation_routes=(manifest.MutationRoute(("*.py",), tuple(s.id for s in steps)),) if routed else ())
    original = asdict(m)

    def run(src, head, steps, run_m, run_out):
        baseline = run_out == out
        current_phase = "confirmation" if run_out.name == "confirmation" else (
            "narrowed" if phase != "full" else "full")
        saved = []
        for step in run_m.prepare + steps:
            assert step.timeout == (100 if baseline else 30)
            exit_code = code if not baseline and current_phase == phase and step.id == stage else step.expect
            saved.append(verify.Receipt(step.id, [], step.expect, exit_code, exit_code == step.expect, 0.1, ""))
            if not saved[-1].passed:
                break
        run_out.mkdir(parents=True, exist_ok=True)
        (run_out / "receipts.json").write_text(json.dumps([asdict(r) for r in saved]))
        return saved

    monkeypatch.setattr(verify, "run_extracted", run)
    mutants = [evidence.Mutant("calc.py", 1, "int", "x = 1", f"x = {n}") for n in (2, 3)]
    assert evidence.mutate(repo, "head", mutants, m, out)
    expected = "survived" if code == 7 else "invalid" if invalid else "timeout" if code is None else "killed"
    assert [mut.result for mut in mutants] == [expected, expected]
    assert evidence.Evidence(1, mutants=mutants).score == {"killed": 1, "survived": 0}.get(expected)
    index = json.loads((out / "mutation-runs.json").read_text())
    phases = ["full"] if phase == "full" else ["narrowed", "confirmation"] if phase == "confirmation" or code == 7 else ["narrowed"]
    for number, entry in enumerate(index["mutants"], 1):
        assert entry["number"] == number and entry["route"] == (1 if routed else None)
        assert entry["result"] == expected and entry["decided_by"] == phases[-1]
        assert [r["phase"] for r in entry["runs"]] == phases
        saved = json.loads((out / entry["runs"][-1]["receipts"]).read_text())
        assert saved[-1]["id"] == ("later" if code == 7 else stage)
        if code != 7:
            assert saved[-1]["exit"] == code and not saved[-1]["passed"]
        assert not (out / f"mutant-{number}/src").exists()
    assert asdict(m) == original and (repo / "calc.py").read_text() == "x = 1\n"
    assert not (out / "src").exists()

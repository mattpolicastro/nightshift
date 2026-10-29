import json
import subprocess
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest

from specloop import evidence, verify
from test_loopback_launch import STEP, launches
from test_specloop_evidence import CODE, M, STRONG, project


@pytest.mark.scenario("parallel-evidence-mutants/per-execution-loopback-policy/evidence-phases-use-fresh-policies")
@pytest.mark.scenario("parallel-evidence-mutants/per-execution-loopback-policy/sandbox-failure-after-baseline-cannot-kill-a-mutant")
@pytest.mark.scenario("parallel-evidence-mutants/per-execution-loopback-policy/baseline-sandbox-failure-dispatches-no-mutants")
@pytest.mark.parametrize("parallel", [1, 2])
@pytest.mark.parametrize("phase", ["baseline", "mutant", "confirmation", "command"])
@pytest.mark.parametrize("expect", [0, 65])
def test_loopback_sandbox_startup_failure_is_not_mutant_kill(tmp_path, monkeypatch, launches, parallel, phase, expect):
    repo, base, head = project(tmp_path, STRONG)
    out = tmp_path / "out"
    mutants = [evidence.Mutant("calc.py", 2, "<=", CODE.splitlines()[1], after) for after in
               ("    return lo < x and x <= hi", "    return lo <= x and x < hi")]
    monkeypatch.setattr(evidence, "generate", lambda *a, **kw: mutants)
    script = f"import os,sys; sys.exit(65 if {phase == 'command'} and 'mutant-' in os.getcwd() else {expect})"
    step = replace(STEP, run=(*STEP.run[:2], script, "{mutant_path}"), expect=expect)
    before = replace(STEP, id="before", loopback=False)
    m = replace(M, verify=(before, step), mutation_steps=("before", "check"), mutation_parallel=parallel)
    budgets = evidence._mutation_budgets((step,), (step,), [verify.Receipt("check", [], expect, expect, True, 0.01, "")])
    assert budgets.steps[0].loopback and budgets.prepare[0].loopback
    assert budgets.steps[0].timeout < step.timeout
    profile, run, cleanup = verify.profile, subprocess.run, verify.cleanup
    rejected, cleaned = [], []
    overlap = Barrier(parallel)

    def render(build, m, **kw):
        current = "baseline" if build.parent == out else "confirmation" if build.parent.name == "confirmation" else "mutant"
        return "(malformed" if kw.get("loopback") and current == phase else profile(build, m, **kw)

    def probe(argv, **kw):
        result = run(argv, **kw)
        if argv[0] == "/usr/bin/sandbox-exec" and Path(argv[2]).read_text() == "(malformed":
            rejected.append(Path(argv[2]).parent)
            kw["stdout"].write(b"probe rejected malformed profile\n")
            if phase != "baseline":
                overlap.wait(timeout=10)
            result.returncode = 65
        return result

    def release(path):
        cleanup(path)
        cleaned.append(path)

    monkeypatch.setattr(verify, "profile", render)
    monkeypatch.setattr(subprocess, "run", probe)
    monkeypatch.setattr(verify, "cleanup", release)
    if phase == "command":
        ev = evidence.gather(repo, base, head, [], set(), m, out, 1)
        assert [mut.result for mut in ev.mutants] == ["killed" if expect == 0 else "survived"] * 2
    else:
        with pytest.raises(RuntimeError, match="check.*probe.*65"):
            evidence.gather(repo, base, head, [], set(), m, out, 1)
        assert [mut.result for mut in mutants] == [""] * 2
        assert len(rejected) == (1 if phase == "baseline" else 2)
        assert not (out / "evidence.json").exists()
        for path in rejected:
            assert [r["id"] for r in json.loads((path / "receipts.json").read_text())] == ["before"]
            assert "probe rejected" in (path / "check.log").read_text()
            assert not any(kind == "launch" and sb.parent == path and "loopback" in sb.name for kind, sb in launches)
    profiles = [value for kind, value in launches if kind == "probe"]
    assert len(set(profiles)) == len(profiles) == sum(kind == "snapshot" for kind, _ in launches)
    assert cleaned[-1] == out and set(cleaned) == {p.parent for p in profiles}
    assert all(not (p / d).exists() for p in cleaned for d in ("src", "build"))
    if phase == "baseline":
        assert cleaned == [out] and not list(out.glob("mutant-*"))

import json
import os
import shutil
import sys
from dataclasses import replace

import pytest

from specloop import evidence, manifest, verify
from test_specloop_units import MANIFEST

KINDS = ("prepare", "verify", "acceptance", "mutation_step")
REPORT = (sys.executable, "-c", "import json, os, sys; print(json.dumps([dict(os.environ), sys.argv[1:]]))")
SNAPSHOT = {"id": "deps", "lockfiles": ["lock"], "warm": list(REPORT)}


def configured(kind, env):
    return {**MANIFEST, kind: [{"id": kind, "run": list(REPORT), "env": env}]}


@pytest.fixture
def local_child(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "wrap", lambda argv, sb: argv)  # Plumbing, not sandbox enforcement.
    monkeypatch.setattr(verify, "tool_path", lambda: "/usr/bin:/bin")
    src = tmp_path / "src"
    src.mkdir()
    (src / "lock").write_text("dependencies")
    return src


def reported(out, step_id):
    return json.loads((out / f"{step_id}.log").read_text())


def fixed(build):
    return {"PATH": "/usr/bin:/bin", "HOME": str(build / "home"), "TMPDIR": str(build / "tmp"),
            "LANG": "en_US.UTF-8", "COPYFILE_DISABLE": "1"}


@pytest.mark.scenario("verify-preparation/step-environment-tables/tables-round-trip-for-all-step-kinds")
@pytest.mark.parametrize("overrides", [{}, {"change": "new", "branch": "spec/new"}])
def test_step_env_round_trip(tmp_path, overrides):
    env = {"CACHE": "{build}/a b", "EMPTY": "", "path": "~/$PATH;{}"}
    data = dict(MANIFEST)
    for kind in KINDS:
        data[kind] = [{"id": kind + str(i), "run": ["tool", "{CACHE}"], **fields}
                      for i, fields in enumerate(({}, {"env": {}}, {"env": env, "expect": 7, "timeout": 19}))]
    parsed = manifest.parse(data)
    path = tmp_path / "manifest.toml"
    path.write_text("\n".join(f"{manifest._toml_value(k)} = {manifest._toml_value(v)}" for k, v in data.items()))
    effective, stored = manifest.template(path, **overrides)
    path.write_text(stored)
    assert manifest.load(path) == effective == replace(parsed, **overrides)
    for kind in KINDS:
        steps = getattr(parsed, kind)
        assert steps == (manifest.Step(kind + "0", ("tool", "{CACHE}")),
                         manifest.Step(kind + "1", ("tool", "{CACHE}")),
                         manifest.Step(kind + "2", ("tool", "{CACHE}"), 7, 19, env))
        assert steps[0].env is not steps[1].env and steps[2].env is not env
    first, second = manifest.Step("one", ("true",), 0, 600), manifest.Step("two", ("true",))
    first.env["ONLY"] = "one"
    assert second.env == {}


@pytest.mark.scenario("verify-preparation/step-environment-tables/invalid-tables-fail-before-execution")
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("env", [None, "private-value", [], 1, {"X": 123456789}, {"X": True},
    {"X": ["private-value"]}, {"X": {"secret": "private-value"}}, {"": "private-value"},
    {1: "private-value"}, {"a=b": "private-value"}, {"a\0b": "private-value"}, {"X": "private-value\0"}])
def test_invalid_step_env(kind, env, tmp_path, monkeypatch):
    monkeypatch.setattr(verify.subprocess, "Popen", lambda *a, **kw: pytest.fail("invalid env launched"))
    with pytest.raises(ValueError, match=rf"{kind} '{kind}'.*env") as error:
        manifest.parse(configured(kind, env))
    assert "private-value" not in str(error.value) and "123456789" not in str(error.value)
    with pytest.raises(ValueError, match=rf"'{kind}'.*env"):
        verify._run_commands([(manifest.Step(kind, REPORT, env=env), tmp_path / "sb")],
                             {}, tmp_path, tmp_path, "/usr/bin:/bin")


@pytest.mark.scenario("verify-preparation/harness-environment-ownership/protected-variables-cannot-be-replaced")
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("key", ["PATH", "HOME", "TMPDIR"])
@pytest.mark.parametrize("mode", ["empty", "identical", "different"])
def test_step_env_cannot_override_harness_vars(kind, key, mode, tmp_path, monkeypatch):
    value = {"empty": "", "identical": fixed(tmp_path)[key], "different": "private-value"}[mode]
    monkeypatch.setattr(verify.subprocess, "Popen", lambda *a, **kw: pytest.fail("protected env launched"))
    with pytest.raises(ValueError, match=rf"{kind} '{kind}'.*env.*{key}"):
        manifest.parse(configured(kind, {key: value}))
    with pytest.raises(ValueError, match=rf"'{kind}'.*env.*{key}"):
        verify._run_commands([(manifest.Step(kind, REPORT, env={key: value}), tmp_path / "sb")],
                             {}, tmp_path, tmp_path, "/usr/bin:/bin")


@pytest.mark.scenario("verify-preparation/harness-environment-ownership/protected-variables-cannot-be-replaced")
@pytest.mark.parametrize("kind", KINDS)
def test_step_env_rejects_expanded_nul(kind, tmp_path, monkeypatch):
    m = manifest.parse({**configured(kind, {"X": "{bad}"}), "vars": {"bad": "private-value\0"}})
    monkeypatch.setattr(verify.subprocess, "Popen", lambda *a, **kw: pytest.fail("NUL env launched"))
    with pytest.raises(ValueError, match=rf"'{kind}'.*env.*X") as error:
        verify._run_commands([(getattr(m, kind)[0], tmp_path / "sb")], m.vars, tmp_path, tmp_path, "path")
    assert "private-value" not in str(error.value)


@pytest.mark.scenario("verify-preparation/step-environment-substitution/values-use-the-command-context")
@pytest.mark.scenario("verify-preparation/step-environment-substitution/snapshot-references-use-private-paths")
@pytest.mark.scenario("verify-preparation/step-environment-isolation/overrides-are-confined-to-one-step")
@pytest.mark.parametrize("kind", KINDS)
def test_step_env_applied_with_substitution(kind, local_child, tmp_path, monkeypatch):
    env = {"CONTEXT": "{word}|{src}|{build}|{sha}", "DIRECT": "{snapshot:deps}", "ALIAS": "{cache}",
           "EMPTY": "", "LITERAL": "~ $HOST_ONLY $(echo nope); ' spaced ' {unknown} {EMPTY}",
           "{word}": "unchanged key", "LANG": "C", "COPYFILE_DISABLE": "0", "MUTANT": "{mutant_path}"}
    data = {**configured(kind, env), "snapshot": [SNAPSHOT],
            "vars": {"word": "a word", "cache": "{next}", "next": "{snapshot:deps}"}}
    data[kind][0]["run"] += list(env.values())
    data[kind].append({"id": "next", "run": list(REPORT), "env": {"OWN": "{build}"}})
    m = manifest.parse(data)
    monkeypatch.setenv("HOST_ONLY", "must not escape")
    parent, variables, original_env = dict(os.environ), dict(m.vars), dict(env)
    for index in range(2):
        out = tmp_path / str(index)
        steps = () if kind == "prepare" else getattr(m, kind)
        receipts = verify.run_extracted(local_child, "revision", steps, m, out)
        assert [r.id for r in receipts] == [kind, "next"] and all(r.passed for r in receipts)
        child, argv = reported(out, kind)
        build, cache = out / "build", out / "build/snapshots/deps"
        expected = {**env, "CONTEXT": f"a word|{local_child}|{build}|revision",
                    "DIRECT": str(cache), "ALIAS": str(cache), "MUTANT": ""}
        assert {k: child[k] for k in expected} == expected
        assert argv == list(expected.values()) == receipts[0].argv[len(REPORT):]
        assert {k: child[k] for k in ("PATH", "HOME", "TMPDIR")} == {
            k: fixed(build)[k] for k in ("PATH", "HOME", "TMPDIR")}
        following, _ = reported(out, "next")
        assert {k: following[k] for k in fixed(build)} == fixed(build)
        assert following["OWN"] == str(build) and "CONTEXT" not in following
        assert "HOST_ONLY" not in child and "HOST_ONLY" not in following
        assert cache.is_dir() and list(cache.iterdir()) == []
        assert json.loads((out / "snapshot-events.jsonl").read_text())["reason"] == "context_unavailable"
        assert verify.profile(build, m) == verify.profile(build, replace(m, **{kind: ()}))
    assert os.environ == parent and m.vars == variables and getattr(m, kind)[0].env == env == original_env


@pytest.mark.scenario("verify-preparation/step-environment-substitution/snapshot-references-use-private-paths")
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("alias", [False, True])
def test_step_env_unknown_snapshot(kind, alias):
    data = {**configured(kind, {"CACHE": "{cache}" if alias else "{snapshot:missing}"}),
            "snapshot": [SNAPSHOT], "vars": {"cache": "{next}", "next": "{snapshot:missing}"}}
    with pytest.raises(ValueError, match=rf"{kind} '{kind}'.*env.*snapshot:missing"):
        manifest.parse(data)


@pytest.mark.scenario("verify-preparation/step-environment-substitution/mutation-context-does-not-select-phases")
@pytest.mark.parametrize("kind", KINDS)
def test_step_env_reserved_mutant_override(kind):
    with pytest.raises(ValueError, match="vars.mutant_path is reserved"):
        manifest.parse({**configured(kind, {"MUTANT": "{mutant_path}"}), "vars": {"mutant_path": "x.py"}})


@pytest.mark.scenario("verify-preparation/step-environment-substitution/mutation-context-does-not-select-phases")
@pytest.mark.parametrize("narrowed", [False, True])
def test_step_env_mutation_phases(narrowed, local_child, tmp_path):
    data = configured("mutation_step", {"MUTANT": "{mutant_path}"})
    if narrowed:
        data["mutation_step"][0]["run"].append("{mutant_path}")
    m = manifest.parse(data)
    steps = evidence._mutation_budgets((), m.mutation_step,
        [verify.Receipt("mutation_step", [], 0, 0, True, 1, "")]).steps
    assert steps[0].timeout == 30 and steps[0].env == {"MUTANT": "{mutant_path}"}
    assert m.mutation_step[0].timeout == 300 and m.vars["mutant_path"] == ""
    (local_child / "x.py").write_text("x = 1\n")
    out, phases = tmp_path / "evidence", []
    result = evidence._mutate_one(1, evidence.Mutant("x.py", 1, "int", "x = 1", "x = 2"),
                                  local_child, "revision", steps, m, out, phases)
    assert result == "survived" and phases == (["narrowed", "confirmation"] if narrowed else ["full"])
    assert reported(out / "mutant-1", "mutation_step")[0]["MUTANT"] == ("x.py" if narrowed else "")
    if narrowed:
        assert reported(out / "mutant-1/confirmation", "mutation_step")[0]["MUTANT"] == ""


@pytest.mark.scenario("verify-preparation/step-environment-isolation/empty-environments-preserve-existing-execution")
@pytest.mark.parametrize("mode", ["omitted", "empty", "direct"])
def test_empty_step_env_execution(mode, local_child, tmp_path):
    data = configured("verify", {})
    if mode == "omitted":
        del data["verify"][0]["env"]
    m = manifest.parse(data)
    first = manifest.Step("verify", REPORT) if mode == "direct" else m.verify[0]
    steps = (first, manifest.Step("expected", (sys.executable, "-c", "raise SystemExit(7)"), 7, 5),
             manifest.Step("fail", (sys.executable, "-c", "raise SystemExit(1)")), first)
    out = tmp_path / "execution"
    receipts = verify.run_extracted(local_child, "revision", steps, m, out)
    assert [r.id for r in receipts] == ["verify", "expected", "fail"]
    assert [r.passed for r in receipts] == [True, True, False]
    assert [r.argv for r in receipts] == [list(s.run) for s in steps[:3]]
    child, _ = reported(out, "verify")
    assert {k: child[k] for k in fixed(out / "build")} == fixed(out / "build")
    assert all(set(r) == {"id", "argv", "expect", "exit", "passed", "seconds", "tail"}
               for r in json.loads((out / "receipts.json").read_text()))


@pytest.mark.scenario("verify-preparation/step-environment-isolation/empty-environments-preserve-existing-execution")
def test_snapshot_warm_empty_step_env(local_child, tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "extract", lambda repo, sha, src: shutil.copytree(local_child, src))
    context = verify.SnapshotContext(tmp_path / "cache")
    verify.warm_snapshots(local_child, "a" * 40, manifest.parse({**MANIFEST, "snapshot": [SNAPSHOT]}), context)
    out = next((context.root / "warms").iterdir())
    assert json.loads((out / "receipts.json").read_text())[0]["passed"]
    child, argv = reported(out, "warm")
    build = next(context.root.glob(".staging-*")) / "build"
    assert {k: child[k] for k in fixed(build)} == fixed(build) and argv == []

"""Exercise the shipped manifest with the real selector and a fixture pytest."""

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import replace
from pathlib import Path
from venv import EnvBuilder

import pytest

from specloop import evidence, manifest, verify


@pytest.fixture(autouse=True)
def _unwrapped_steps(monkeypatch):
    """Run fixture steps directly, as inside specloop's own verify sandbox.

    These tests cover mutation routing and decisions, not sandbox enforcement.
    Outside an enclosing sandbox (CI, a plain pytest run), sandbox-exec would
    apply each fixture manifest's profile, which cannot read the test venv.
    """
    monkeypatch.setattr(verify, "already_sandboxed", lambda: True)


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/specloop/self-hosting.toml"


@pytest.mark.scenario(
    "specloop-documentation/routed-evidence-guidance/reader-configures-routed-evidence"
)
def test_guide_routes_reference_separately_defined_suites():
    guide = EXAMPLE.parents[2] / "docs/specloop/README.md"
    section = guide.read_text().split("### Section evidence", 1)[1]
    blocks = re.findall(r"```toml\n(.*?)```", section, re.DOTALL)
    routes = next(tomllib.loads(block) for block in blocks if "[[gates.mutation_route]]" in block)
    data = tomllib.loads(EXAMPLE.read_text())
    data["gates"]["mutation_route"] = routes["gates"]["mutation_route"]
    # Stand in for the reader's existing suite; parsing never executes it.
    data["mutation_step"].append({"id": "plugin-tests", "run": ["fixture-plugin-suite"]})
    configured = manifest.parse(data)
    for path, index, step_id in (
        ("nightshift/core.py", 1, "python-mutation"),
        ("plugins/nightshift/src/index.ts", 2, "plugin-tests"),
        ("other/module.py", None, "unit-tests"),
    ):
        route, steps = evidence.resolve_mutation_steps(path, configured)
        assert route == index
        assert [step.id for step in steps] == [step_id]
    for target in re.findall(r"\]\(([^)]+)\)", section):
        if target.startswith("../../examples/"):
            assert (guide.parent / target).is_file()


@pytest.mark.scenario(
    "specloop-documentation/routed-evidence-guidance/reader-configures-routed-evidence"
)
@pytest.mark.parametrize("overrides", [{}, {"change": "routed", "branch": "spec/routed"}])
def test_example_round_trip_preserves_verification(tmp_path, overrides):
    data = tomllib.loads(EXAMPLE.read_text())
    original = manifest.parse(data)
    assert manifest.load(EXAMPLE) == original
    effective, stored = manifest.template(EXAMPLE, **overrides)
    assert effective == replace(original, **overrides)
    saved = tmp_path / "saved.toml"
    saved.write_text(stored)
    assert manifest.load(saved) == effective

    assert original.verify == (
        manifest.Step("unit-tests", (
            "/usr/bin/env", "PYTHONPATH={src}:{checks}", "PYTHONDONTWRITEBYTECODE=1",
            "{venv}/bin/python", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
            "-p", "scenario_plugin", "--rootdir={src}", "{src}/tests",
        ), timeout=900),
        manifest.Step("negative-control", (
            "/usr/bin/env", "PYTHONDONTWRITEBYTECODE=1", "{venv}/bin/python",
            "-m", "pytest", "-q", "-p", "no:cacheprovider", "{checks}/negative_control.py",
        ), expect=1, timeout=120),
    )
    assert original.coverage_step == "unit-tests"
    assert data["coverage"] == {"step": "unit-tests"}
    assert original.mutation_steps == ("unit-tests",)
    assert original.mutation_routes == (
        manifest.MutationRoute(("specloop/**", "nightshift/**"), ("python-mutation",)),
    )
    assert len(original.mutation_step) == 1
    step = original.mutation_step[0]
    assert step.id == "python-mutation"
    assert step.expect == 0
    assert step.timeout == 900
    assert "{mutant_path}" in step.run
    assert "{checks}" in step.run
    assert original.vars["mutant_path"] == ""
    for path in ("specloop/evidence.py", "nightshift/nested/module.py"):
        assert evidence.resolve_mutation_steps(path, original) == (1, (step,))
    assert evidence.resolve_mutation_steps("other/module.py", original) == (
        None, (original.verify[0],),
    )


@pytest.fixture
def example_command(tmp_path):
    src = tmp_path / "source with spaces"
    checks = tmp_path / "trusted checks with spaces"
    build = tmp_path / "build with spaces"
    venv = tmp_path / "venv with spaces"
    for directory in (src, checks, build, venv / "bin"):
        directory.mkdir(parents=True)
    EnvBuilder(with_pip=False).create(venv)
    shutil.copyfile(EXAMPLE.with_name("select_tests.py"), checks / "select_tests.py")

    def write(path, content):
        target = src / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    # Only the trusted copy may supply the selector, even with src on PYTHONPATH.
    poison = "raise AssertionError('untrusted selector or project code executed')\n"
    write("select_tests.py", poison)
    (build / "select_tests.py").write_text(poison)
    write("nightshift/__init__.py", poison)
    write("nightshift/core.py", poison)
    write("nightshift/unimported.py", poison)
    write("nightshift/file with spaces.py", poison)
    write("tests/test_core with spaces.py", "import nightshift.core\n" + poison)
    write("tests/nested folder/other_test.py", "from nightshift import core\n" + poison)
    write("tests/test_unrelated.py", "import unrelated\n" + poison)
    # Run a real child interpreter, but record pytest's invocation instead of
    # importing tests or launching the host application.
    write("pytest.py", '''import json
import os
import sys
print(json.dumps({
    "args": sys.argv[1:],
    "executable": sys.executable,
    "pythonpath": os.environ.get("PYTHONPATH"),
    "bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE"),
    "cwd": os.getcwd(),
}))
print("fixture pytest stderr", file=sys.stderr)
raise SystemExit(int(os.environ["FIXTURE_PYTEST_EXIT"]))
''')
    m = manifest.load(EXAMPLE)
    values = {**m.vars, "src": str(src), "checks": str(checks), "venv": str(venv)}
    return m, values, build


@pytest.mark.scenario(
    "specloop-documentation/routed-evidence-guidance/example-fallback-runs-the-whole-suite"
)
@pytest.mark.parametrize("exit_code", [0, 1, 5])
@pytest.mark.parametrize("mutant_path, targets", [
    ("nightshift/core.py", ["tests/nested folder/other_test.py", "tests/test_core with spaces.py"]),
    ("", ["tests"]),  # The same full invocation serves baseline and confirmation.
    ("missing.py", ["tests"]),
    ("nightshift/unimported.py", ["tests"]),
    ("nightshift/file with spaces.py", ["tests"]),
])
def test_example_adapter_argv_and_exit(example_command, mutant_path, targets, exit_code):
    m, values, build = example_command
    values = {**values, "mutant_path": mutant_path}
    argv = [verify.expand(arg, values) for arg in m.mutation_step[0].run]
    result = subprocess.run(
        argv, cwd=build, capture_output=True, text=True, timeout=15,
        env={**os.environ, "FIXTURE_PYTEST_EXIT": str(exit_code)},
    )
    assert result.returncode == exit_code, result.stderr
    assert result.stderr == "fixture pytest stderr\n"
    recorded = json.loads(result.stdout)
    # Match the existing verification options exactly; only test targets vary.
    full_argv = [verify.expand(arg, values) for arg in m.verify[0].run]
    options = full_argv[full_argv.index("pytest") + 1:-1]
    assert recorded == {
        "args": options + [str(Path(values["src"]) / path) for path in targets],
        "executable": str(Path(values["venv"]) / "bin/python"),
        "pythonpath": values["src"] + ":" + values["checks"],
        "bytecode": "1",
        "cwd": str(build.resolve()),
    }
    assert m.vars["mutant_path"] == ""


def guide_evidence():
    guide = EXAMPLE.parents[2] / "docs/specloop/README.md"
    section = guide.read_text().split("### Section evidence\n", 1)[1]
    return guide, section.split("**Usage limits**", 1)[0]


@pytest.mark.scenario(
    "specloop-documentation/routed-evidence-guidance/reader-configures-routed-evidence"
)
def test_guide_explains_routed_execution_and_decision_receipts():
    _, section = guide_evidence()
    prose = " ".join(section.split())
    for instruction in (
        "trusted `{checks}` directory beside `scenario_plugin.py` and `negative_control.py`",
        "first matching table wins",
        "required nonempty `mutation_steps` fallback",
        "Each distinct ordered step sequence",
        "Identical sequences share a baseline",
        "Unused routes get none",
        "prepare or selected step's timeout",
        "max(30 seconds, 3 * baseline step seconds)",
        "separate argv element, including the empty string",
        "Confirmation reruns even steps without the placeholder",
        "Narrowed timeout or invalid results trigger no confirmation",
        "mutation-runs.json",
        "mutant-N/receipts.json",
        "mutant-N/confirmation/receipts.json",
        "baseline-2/receipts.json",
        "expanded argv, expected/actual exits, durations and diagnostics",
    ):
        assert instruction in prose
    for phase in ("full", "narrowed", "confirmation", "precheck", "baseline"):
        assert f"`{phase}`" in prose


@pytest.mark.scenario(
    "specloop-documentation/routed-evidence-guidance/example-fallback-runs-the-whole-suite"
)
@pytest.mark.parametrize("uncertainty", ["unsupported", "escape", "parse"])
def test_documented_uncertainty_runs_full_example_command(example_command, uncertainty):
    _, section = guide_evidence()
    prose = " ".join(section.split())
    for instruction in (
        "unsupported or escaping path",
        "any scan read/parse failure",
        "does not chase transitive or dynamic imports",
        "Only this full confirmation can establish survival",
        "Scoring and classifications stay unchanged",
    ):
        assert instruction in prose
    m, values, build = example_command
    src = Path(values["src"])
    path = "nightshift/core.py"
    if uncertainty == "parse":
        (src / "tests/test_broken.py").write_text("def broken(\n")
    else:
        path = "nightshift/core.ts" if uncertainty == "unsupported" else "../outside.py"
        (src / path).write_text("import nightshift.core\n")
    values = {**values, "mutant_path": path}
    result = subprocess.run(
        [verify.expand(arg, values) for arg in m.mutation_step[0].run],
        cwd=build, capture_output=True, text=True, timeout=15,
        env={**os.environ, "FIXTURE_PYTEST_EXIT": "5"},
    )
    assert result.returncode == 5, result.stderr
    recorded = json.loads(result.stdout)
    full_argv = [verify.expand(arg, values) for arg in m.verify[0].run]
    assert recorded["args"] == full_argv[full_argv.index("pytest") + 1:]
    assert recorded["pythonpath"] == values["src"] + ":" + values["checks"]
    assert recorded["bytecode"] == "1"


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/scope-and-regression-checks-remain-intact"
)
def test_evidence_links_resolve_and_commands_remain_separate():
    # Diff scope and unchanged existing assertions are also checked in review;
    # do not pin unrelated guide prose that another change may legitimately edit.
    guide, section = guide_evidence()
    local = [target for target in re.findall(r"\]\(([^)]+)\)", section)
             if not target.startswith("https://")]
    assert local
    for target in local:
        assert (guide.parent / target).is_file(), target
    m = manifest.load(EXAMPLE)
    assert {s.id for s in m.mutation_step}.isdisjoint(s.id for s in m.verify)
    assert m.coverage_step == "unit-tests"
    assert [s.id for s in m.verify] == ["unit-tests", "negative-control"]


@pytest.mark.scenario(
    "specloop-documentation/documentation-integrity/references-and-reader-checks-are-reviewable"
)
def test_evidence_citations_and_pending_human_rollout_are_reviewable():
    _, section = guide_evidence()
    references = dict(re.findall(r"\[((?:issue|PR) #\d+)\]\(([^)]+)\)", section))
    expected = {"issue": (4, 21, 31, 33, 73), "PR": (27, 44, 48, 63, 64)}
    assert set(references) == {f"{kind} #{n}" for kind, nums in expected.items() for n in nums}
    for label, url in references.items():
        kind, number = label.split(" #")
        endpoint = "issues" if kind == "issue" else "pull"
        assert url == f"https://github.com/mattpolicastro/nightshift/{endpoint}/{number}"
    prose = " ".join(section.split())
    for instruction in (
        "After merge, Matt copies the reviewed selector",
        "combined verify step and a full fallback",
        "human rollout remains pending",
        "approximately 14-minute baseline using the existing timing report",
        "mutant counts, route mix, narrowed kills and confirmations",
        "Extra baselines, survivors and machine load",
        "CI cannot prove the real-gate speed-up",
    ):
        assert instruction in prose

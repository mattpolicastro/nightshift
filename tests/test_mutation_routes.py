import sys
import tomllib
from copy import deepcopy
from dataclasses import replace

import pytest

from specloop import manifest, verify


TEMPLATE = '''[run]
repo = "/unused"
change = "example"
branch = "spec/example"
[roles]
implement = "p"
review = "p"
[profiles.p]
adapter = "codex"
model = "m"
[paths]
allowed = ["src/**"]
[vars]
checks = "~/checks"
[coverage]
step = "tests"
[gates]
mutation_steps = ["narrow", "tests", "negative"]
[[prepare]]
id = "dependencies"
run = ["true"]
[[verify]]
id = "tests"
run = ["true"]
[[acceptance]]
id = "accept"
run = ["true"]
[[mutation_step]]
id = "narrow"
run = ["python", "{checks}/select tests.py", "{mutant_path}", ""]
[[mutation_step]]
id = "negative"
run = ["python", "negative.py"]
expect = 1
timeout = 42
'''


@pytest.mark.scenario("mutation-selection/mutation-command-definitions/definitions-round-trip")
@pytest.mark.parametrize("overrides", [{}, {"change": "another", "branch": "spec/another"}])
def test_mutation_definitions_round_trip(tmp_path, overrides):
    data = tomllib.loads(TEMPLATE)
    original = deepcopy(data)
    m = manifest.parse(data)
    assert data == original
    assert m.mutation_step == (
        manifest.Step("narrow", ("python", "{checks}/select tests.py", "{mutant_path}", "")),
        manifest.Step("negative", ("python", "negative.py"), expect=1, timeout=42),
    )
    assert m.mutation_step[0].expect == 0
    assert m.mutation_step[0].timeout == 300
    assert m.mutation_steps == ("narrow", "tests", "negative")
    assert m.verify == (manifest.Step("tests", ("true",)),)
    assert m.prepare == (manifest.Step("dependencies", ("true",)),)
    assert m.acceptance == (manifest.Step("accept", ("true",)),)
    assert m.coverage_step == "tests"
    assert m.vars["mutant_path"] == ""
    path = tmp_path / "template.toml"
    path.write_text(TEMPLATE)
    assert manifest.load(path) == m
    effective, stored = manifest.template(path, **overrides)
    assert effective == replace(m, **overrides)
    saved = tmp_path / "saved.toml"
    saved.write_text(stored)
    assert manifest.load(saved) == effective


@pytest.mark.scenario("mutation-selection/mutation-command-definitions/invalid-definitions-rejected")
@pytest.mark.parametrize("definition", [
    "command", {}, ["command"], [{}], [{"run": ["true"]}],
    [{"id": [], "run": ["true"]}], [{"id": "", "run": ["true"]}],
    [{"id": "bad"}], [{"id": "bad", "run": "true"}],
    [{"id": "bad", "run": []}], [{"id": "bad", "run": ["true", 1]}],
    [{"id": "bad", "run": ["true", ["nested"]]}],
    [{"id": "bad", "run": ["true"], "expect": "invalid"}],
    [{"id": "bad", "run": ["true"], "timeout": {}}],
    [{"id": "duplicate", "run": ["true"]}, {"id": "duplicate", "run": ["false"]}],
])
def test_invalid_mutation_definitions(tmp_path, definition):
    data = tomllib.loads(TEMPLATE)
    data["mutation_step"] = definition
    with pytest.raises(ValueError, match="mutation_step"):
        manifest.parse(data)
    path = tmp_path / "invalid.toml"
    path.write_text("\n".join(f"{manifest._toml_value(k)} = {manifest._toml_value(v)}"
                              for k, v in data.items()))
    with pytest.raises(ValueError, match="mutation_step"):
        manifest.load(path)


@pytest.mark.scenario("mutation-selection/mutation-command-definitions/invalid-definitions-rejected")
@pytest.mark.parametrize("name", ["tests", "dependencies", "accept"])
def test_mutation_ids_cannot_collide(name):
    data = tomllib.loads(TEMPLATE)
    data["mutation_step"][0]["id"] = name
    with pytest.raises(ValueError, match=f"mutation_step.*{name}"):
        manifest.parse(data)


@pytest.mark.scenario("mutation-selection/mutation-command-definitions/invalid-definitions-rejected")
@pytest.mark.parametrize("references", [["missing"], ["dependencies"], ["accept"],
                                       "tests", [1], [[]], [""]])
def test_mutation_references_must_name_defined_checks(references):
    data = tomllib.loads(TEMPLATE)
    data["gates"]["mutation_steps"] = references
    with pytest.raises(ValueError, match=r"gates\.mutation_steps"):
        manifest.parse(data)


@pytest.mark.scenario("mutation-selection/mutation-command-definitions/invalid-definitions-rejected")
def test_mutation_only_step_cannot_supply_coverage():
    data = tomllib.loads(TEMPLATE)
    data["coverage"]["step"] = "narrow"
    with pytest.raises(ValueError, match="coverage step 'narrow'"):
        manifest.parse(data)


@pytest.mark.scenario("mutation-selection/mutation-command-definitions/placeholder-defaults-to-full-outside-mutation")
def test_ordinary_verification_expands_empty_placeholder(tmp_path):
    data = tomllib.loads(TEMPLATE)
    data["verify"][0]["run"] = [sys.executable, "-c",
                                 "import sys; assert sys.argv[1:] == ['']; print('full suite')",
                                 "{mutant_path}"]
    m = manifest.parse(data)
    src = tmp_path / "src"
    src.mkdir()
    receipts = verify.run_extracted(src, "head", m.verify, m, tmp_path / "ordinary")
    assert [r.id for r in receipts] == ["dependencies", "tests"]
    assert all(r.passed for r in receipts)
    assert receipts[-1].argv[-1] == ""
    assert receipts[-1].tail.strip() == "full suite"
    assert m.coverage_step == "tests"
    # Evidence can supply a path in its own copy without changing ordinary runs.
    narrowed = replace(m, vars={**m.vars, "mutant_path": "src/file with spaces.py"})
    assert verify.expand(m.verify[0].run[-1], narrowed.vars) == "src/file with spaces.py"
    assert m.vars["mutant_path"] == ""
    assert "mutant_path" not in data["vars"]


@pytest.mark.scenario("mutation-selection/mutation-command-definitions/placeholder-defaults-to-full-outside-mutation")
@pytest.mark.parametrize("namespace", ["verify", "mutation_step"])
@pytest.mark.parametrize("value", ["", "src/one.py", " "])
def test_reserved_placeholder_override(namespace, value):
    data = tomllib.loads(TEMPLATE)
    data["mutation_step"][0]["run"] = ["true"]
    data[namespace][0]["run"] = ["command", "--path={mutant_path}"]
    data["vars"]["mutant_path"] = value
    if value:
        with pytest.raises(ValueError, match=r"vars\.mutant_path"):
            manifest.parse(data)
    else:
        assert manifest.parse(data).vars["mutant_path"] == ""


@pytest.mark.scenario("mutation-selection/mutation-command-definitions/definitions-round-trip")
@pytest.mark.scenario("mutation-selection/mutation-command-definitions/placeholder-defaults-to-full-outside-mutation")
@pytest.mark.parametrize("variables", [{}, {"custom": "value"}, {"mutant_path": "legacy"}])
def test_legacy_defaults_and_vars_are_preserved(variables):
    data = tomllib.loads(TEMPLATE)
    del data["mutation_step"]
    data["gates"]["mutation_steps"] = ["tests"]
    data["vars"] = variables
    m = manifest.parse(data)
    assert m.mutation_step == ()
    assert m.mutation_steps == ("tests",)
    assert m.vars == variables
    direct = manifest.Manifest(m.repo, m.change, m.branch, m.pr_base, m.github,
                               m.roles, m.allowed, m.frozen, m.verify)
    assert direct.mutation_step == ()
    # Existing verify/acceptance ID sharing remains permitted.
    data["acceptance"][0]["id"] = "tests"
    assert manifest.parse(data).acceptance[0].id == "tests"

import sys
import tomllib
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

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


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/routes-validate-and-round-trip")
@pytest.mark.parametrize("overrides", [{}, {"change": "routed", "branch": "spec/routed"}])
def test_mutation_routes_round_trip(tmp_path, overrides):
    text = TEMPLATE.replace("[[prepare]]", '''[[gates.mutation_route]]
paths = ["src/special?.py", "src/[ab]*.py", "src/file with spaces.py"]
steps = ["negative", "tests", "narrow"]
[[gates.mutation_route]]
paths = ["src/**", "src/*", "src/**"]
steps = ["narrow", "tests"]
[[gates.mutation_route]]
paths = ["*"]
steps = ["tests", "narrow"]
[[prepare]]''')
    data = tomllib.loads(text)
    original = deepcopy(data)
    m = manifest.parse(data)
    assert data == original
    assert m.mutation_routes == (
        manifest.MutationRoute(("src/special?.py", "src/[ab]*.py", "src/file with spaces.py"),
                               ("negative", "tests", "narrow")),
        manifest.MutationRoute(("src/**", "src/*", "src/**"), ("narrow", "tests")),
        manifest.MutationRoute(("*",), ("tests", "narrow")),
    )
    assert replace(m, mutation_routes=()) == manifest.parse(tomllib.loads(TEMPLATE))
    for field in ("paths", "steps"):
        with pytest.raises(FrozenInstanceError):
            setattr(m.mutation_routes[0], field, ())
    # Parsed routes own immutable copies of the input arrays.
    data["gates"]["mutation_route"][0]["paths"].append("changed")
    data["gates"]["mutation_route"][0]["steps"].reverse()
    path = tmp_path / "template.toml"
    path.write_text(text)
    assert manifest.load(path) == m
    effective, stored = manifest.template(path, **overrides)
    assert effective == replace(m, **overrides)
    saved = tmp_path / "saved.toml"
    saved.write_text(stored)
    assert manifest.load(saved) == effective


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/routes-validate-and-round-trip")
@pytest.mark.parametrize("routes, offending", [
    (None, "None"), ({}, "{}"), ("route", "'route'"), (1, "1"),
    (["route"], "'route'"), ([[]], "[]"), ([{}], "None"),
    ([{"paths": ["src/**"]}], "None"),
    ([{"steps": ["tests"]}], "None"),
])
def test_invalid_route_tables(routes, offending):
    data = tomllib.loads(TEMPLATE)
    data["gates"]["mutation_route"] = routes
    with pytest.raises(ValueError, match=r"gates\.mutation_route") as exc:
        manifest.parse(data)
    assert offending in str(exc.value)


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/routes-validate-and-round-trip")
@pytest.mark.parametrize("field", ["paths", "steps"])
@pytest.mark.parametrize("value", [None, [], "tests", {}, [1], [False], [[]], [""], ["tests", 1]])
def test_route_fields_require_nonempty_string_arrays(field, value):
    data = tomllib.loads(TEMPLATE)
    route = {"paths": ["src/**"], "steps": ["tests"], field: value}
    data["gates"]["mutation_route"] = [{"paths": ["*"], "steps": ["narrow"]}, route]
    with pytest.raises(ValueError, match=rf"gates\.mutation_route\[2\]\.{field}") as exc:
        manifest.parse(data)
    assert repr(value) in str(exc.value)


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/routes-validate-and-round-trip")
@pytest.mark.parametrize("pattern", [
    "/src/*.py", "//server/share/*", "C:/src/*.py", "../*.py", "src/../*.py",
    "src/..", "..", r"src\*.py", r"C:\src\*.py",
])
def test_invalid_route_paths(pattern):
    data = tomllib.loads(TEMPLATE)
    data["gates"]["mutation_route"] = [{"paths": ["src/**", pattern], "steps": ["tests"]}]
    with pytest.raises(ValueError, match=r"gates\.mutation_route\[1\]\.paths") as exc:
        manifest.parse(data)
    assert repr(pattern) in str(exc.value)


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/routes-validate-and-round-trip")
@pytest.mark.parametrize("steps, offending", [
    (["tests", "missing"], "missing"), (["dependencies"], "dependencies"),
    (["accept"], "accept"), (["tests", "tests"], "tests"),
    (["narrow", "tests", "narrow"], "narrow"),
])
def test_invalid_route_step_references(steps, offending):
    data = tomllib.loads(TEMPLATE)
    data["gates"]["mutation_route"] = [{"paths": ["src/**"], "steps": steps}]
    with pytest.raises(ValueError, match=r"gates\.mutation_route\[1\]\.steps") as exc:
        manifest.parse(data)
    assert repr(offending) in str(exc.value)


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/routes-validate-and-round-trip")
@pytest.mark.parametrize("explicit_empty", [False, True])
def test_routes_require_fallback(explicit_empty):
    data = tomllib.loads(TEMPLATE)
    data["gates"] = {"mutation_route": [{"paths": ["*"], "steps": ["tests"]}]}
    if explicit_empty:
        data["gates"]["mutation_steps"] = []
    with pytest.raises(ValueError, match=r"gates\.mutation_route.*gates\.mutation_steps.*\[\]"):
        manifest.parse(data)


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/legacy-manifest-is-unchanged")
@pytest.mark.parametrize("evidence", [False, True])
@pytest.mark.parametrize("fallback", [None, [], ["tests", "build"]])
def test_manifest_without_routes_unchanged(evidence, fallback):
    data = tomllib.loads(TEMPLATE)
    del data["mutation_step"]
    data["vars"] = {"custom": "unchanged"}
    data["verify"].insert(0, {"id": "build", "run": ["true"]})
    data["gates"] = {"evidence": evidence}
    if fallback is not None:
        data["gates"]["mutation_steps"] = fallback
    m = manifest.parse(data)
    data["gates"]["mutation_route"] = []
    assert manifest.parse(data) == m
    assert m.mutation_routes == ()
    assert m.mutation_step == ()
    assert m.evidence is evidence
    assert m.mutation_steps == tuple(fallback or [])
    assert [s.id for s in m.verify] == ["build", "tests"]
    assert all(s.expect == 0 and s.timeout == 300 for s in m.verify)
    assert m.vars == {"custom": "unchanged"}
    assert m.mutation_paths == m.mutation_skip == ()
    assert (m.mutation_count, m.mutation_min, m.mutation_parallel) == (12, 0.8, 2)
    assert (m.gate_review, m.stuck) == ("human", "human")
    direct = manifest.Manifest(m.repo, m.change, m.branch, m.pr_base, m.github,
                               m.roles, m.allowed, m.frozen, m.verify)
    assert direct.mutation_routes == ()
    assert direct.mutation_steps == ()
    assert direct.evidence is False


@pytest.mark.scenario("mutation-selection/ordered-mutation-routes/routes-validate-and-round-trip")
@pytest.mark.parametrize("namespace, key", [("paths", "allowed"), ("paths", "frozen"),
                                          ("gates", "mutation_paths")])
def test_route_globs_do_not_relax_path_policy(namespace, key):
    data = tomllib.loads(TEMPLATE)
    data["gates"]["mutation_route"] = [{"paths": ["src/*.py"], "steps": ["tests"]}]
    assert manifest.parse(data).mutation_routes[0].paths == ("src/*.py",)
    data[namespace][key] = ["src/*.py"]
    with pytest.raises(ValueError, match="bad path pattern"):
        manifest.parse(data)
    assert manifest.matches("src/nested/module.py", ("src/**",))
    assert manifest.matches("src/module.py", ("src/module.py",))
    assert not manifest.matches("src/module.py", ("src/*.py",))
    assert not manifest.matches("SRC/module.py", ("src/**",))
    assert not manifest.matches("src-other/module.py", ("src/**",))

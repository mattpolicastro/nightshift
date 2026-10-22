import json
import tomllib
from dataclasses import replace

import pytest

from specloop import evidence, manifest


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
[[verify]]
id = "test"
run = ["true"]
'''


@pytest.mark.scenario("mutation-selection/configurable-line-exclusions/defaults-and-explicit-patterns")
@pytest.mark.parametrize("setting, expected", [
    ("", ()),
    ("[gates]\n", ()),
    ("[gates]\nmutation_skip = []\n", ()),
    ('[gates]\nmutation_skip = ["fontWeight", "padding"]\n', ("fontWeight", "padding")),
    ('''[gates]\nmutation_skip = [' padded ', ' ', 'a[0].*', 'quote"', 'back\\slash']\n''',
     (" padded ", " ", "a[0].*", 'quote"', "back\\slash")),
])
@pytest.mark.parametrize("overrides", [{}, {"change": "another", "branch": "spec/another"}])
def test_mutation_skip_parsing_and_template_round_trip(tmp_path, setting, expected, overrides):
    text = TEMPLATE + setting
    assert manifest.parse(tomllib.loads(text)).mutation_skip == expected
    path = tmp_path / "template.toml"
    path.write_text(text)
    assert manifest.load(path).mutation_skip == expected

    effective, stored = manifest.template(path, **overrides)
    assert effective.mutation_skip == expected
    assert effective.change == overrides.get("change", "example")
    assert effective.branch == overrides.get("branch", "spec/example")
    saved = tmp_path / "saved.toml"
    saved.write_text(stored)
    assert manifest.load(saved) == effective


@pytest.mark.scenario("mutation-selection/configurable-line-exclusions/defaults-and-explicit-patterns")
def test_mutation_skip_constructor_default():
    m = manifest.parse(tomllib.loads(TEMPLATE))
    direct = manifest.Manifest(m.repo, m.change, m.branch, m.pr_base, m.github,
                               m.roles, m.allowed, m.frozen, m.verify)
    assert direct.mutation_skip == ()


@pytest.mark.scenario("mutation-selection/configurable-line-exclusions/invalid-patterns-rejected")
@pytest.mark.parametrize("value", [
    '"fontWeight"', '""', "1", "1.5", "true", "{}",
    '["padding", 1]', '["padding", false]', '["padding", []]',
    '["padding", {}]', '[""]', '["padding", ""]',
])
def test_mutation_skip_rejects_invalid_patterns(tmp_path, value):
    text = TEMPLATE + f"[gates]\nmutation_skip = {value}\n"
    with pytest.raises(ValueError, match=r"gates\.mutation_skip"):
        manifest.parse(tomllib.loads(text))
    path = tmp_path / "invalid.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match=r"gates\.mutation_skip"):
        manifest.load(path)


@pytest.mark.scenario("mutation-selection/configurable-line-exclusions/matching-lines-excluded-before-sampling")
@pytest.mark.parametrize("seed", [0, 1, 7, 42])
@pytest.mark.parametrize("count", [1, 3, 20])
def test_mutation_skip_patterns_exclude_matching_lines(seed, count):
    texts = [
        "fontWeight = 600",
        "padding = 12 if x <= 4 and enabled != False else 0",
        "enabled = True  # cosmetic[0].*",
        "FontWeight = 600",
        "enabled = False  # cosmetic0anything",
        "limit = 5 if x >= 2 else 3",
    ]
    lines = [("src/example.py", n, text) for n, text in enumerate(texts, 1)]
    patterns = ("fontWeight", "padding", "cosmetic[0].*")
    # With no configuration every line is mutable, including the cosmetic ones.
    assert {m.line for m in evidence.generate(lines, 20, seed)} == set(range(1, 7))
    selected = evidence.generate(lines, count, seed, mutation_skip=patterns)
    # The same seeded sample as removing the lines up front proves exclusions
    # neither consume the budget nor perturb the remaining candidates' order.
    assert selected == evidence.generate(lines[3:], count, seed)
    assert len(selected) == min(count, 3)
    assert {m.line for m in selected} <= {4, 5, 6}
    assert all(m.before == texts[m.line - 1] for m in selected)


@pytest.mark.scenario("mutation-selection/configurable-line-exclusions/matching-lines-excluded-before-sampling")
@pytest.mark.parametrize("skip_all", [False, True])
def test_gather_applies_manifest_mutation_skip(tmp_path, monkeypatch, skip_all):
    patterns = ["fontWeight", "padding"] + (["limit"] if skip_all else [])
    data = tomllib.loads(TEMPLATE)
    data["gates"] = {"mutation_skip": patterns, "mutation_steps": ["test"],
                     "mutation_paths": ["src/**"], "mutation_count": 1}
    m = manifest.parse(data)
    diff = b'''+++ b/src/example.py
@@ -0,0 +1,3 @@
+fontWeight = 600
+enabled = True  # padding
+limit = 5
'''

    def git(repo, *args):
        assert repo == tmp_path
        assert args == ("diff", "--unified=0", "base", "head")
        return diff

    executed = []

    def mutate(repo, head, mutants, effective, out):
        executed.extend(replace(mut) for mut in mutants)
        assert effective == m and head == "head"
        for mut in mutants:
            mut.result = "killed"
        return True

    monkeypatch.setattr(evidence, "git", git)
    monkeypatch.setattr(evidence, "mutate", mutate)
    out = tmp_path / "evidence"
    ev = evidence.gather(tmp_path, "base", "head", ["covered"], {"covered"}, m, out, 1)
    if skip_all:
        assert executed == ev.mutants == []
        assert ev.skipped == "no mutable lines added under mutation_paths"
        assert ev.score is None
    else:
        assert len(executed) == 1
        assert executed[0].line == 3
        assert executed[0].before == "limit = 5"
        assert executed[0].after == "limit = 6"
        assert ev.score == 1.0
    assert ev.passed(m.mutation_min)
    stored = json.loads((out / "evidence.json").read_text())
    assert stored["score"] == ev.score
    assert stored["skipped"] == ev.skipped

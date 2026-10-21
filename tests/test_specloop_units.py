import pytest

from specloop import agents, manifest, review, tasks

TASKS = """\
# Tasks

## 1. Core

- [ ] 1.1 Add the thing covering `cap/req/first-case`
- [x] 1.2 Already done
- [ ] 1.3 Another thing

## 2. Docs

- [ ] 2.1 Write it up
"""


def test_parse_sections_labels_and_scenarios():
    ts = tasks.parse(TASKS)
    assert [(t.section, t.label, t.done) for t in ts] == [(1, "1.1", False), (1, "1.2", True), (1, "1.3", False), (2, "2.1", False)]
    assert ts[0].scenarios == ["cap/req/first-case"]
    assert ts[0].section_title == "Core"


def test_key_survives_renumbering_but_not_rewording():
    before = tasks.parse(TASKS)[0].key
    assert tasks.parse(TASKS.replace("1.1 Add", "7.4 Add"))[0].key == before
    assert tasks.parse(TASKS.replace("Add the thing", "Add a thing"))[0].key != before


def test_tick_finds_a_task_that_moved():
    t = tasks.parse(TASKS)[2]
    moved = TASKS.replace("## 1. Core\n", "## 1. Core\n\nSome new prose.\n")
    out = tasks.tick(moved, t)
    assert "- [x] 1.3 Another thing" in out and "- [ ] 1.1 Add" in out


def test_tick_refuses_a_task_that_was_reworded():
    t = tasks.parse(TASKS)[2]
    with pytest.raises(KeyError):
        tasks.tick(TASKS.replace("Another thing", "A different thing"), t)


def test_split_and_append():
    t = tasks.parse(TASKS)[0]
    out = tasks.split(TASKS, t, ["first half `cap/req/first-case`", "second half"])
    labels = [x.label for x in tasks.parse(out)]
    assert labels == ["1.1.1", "1.1.2", "1.2", "1.3", "2.1"]
    out2 = tasks.append_section(out, "Coverage", ["cover `cap/req/x`"])
    assert tasks.parse(out2)[-1].label == "3.1" and tasks.parse(out2)[-1].section_title == "Coverage"


MANIFEST = {
    "run": {"repo": "~/r", "change": "c", "branch": "spec/c"},
    "roles": {"implement": "a", "review": "b"},
    "profiles": {"a": {"adapter": "codex", "model": "m1"}, "b": {"adapter": "claude", "model": "m2", "base_url": "http://x:1"}},
    "paths": {"allowed": ["src/**", "README.md"], "frozen": ["openspec/**"]},
    "verify": [{"id": "t", "run": ["true"]}],
}


def test_manifest_parses_and_overrides_roles():
    m = manifest.parse(MANIFEST)
    assert m.roles["implement"].adapter == "codex" and m.roles["review"].base_url == "http://x:1"
    m2 = manifest.parse(MANIFEST, {"implement": "b", "review": None})
    assert m2.roles["implement"].name == "b" and m2.roles["review"].name == "b"


def test_manifest_loads_prepare_tables(tmp_path):
    path = tmp_path / "manifest.toml"
    path.write_text("\n".join(f"{manifest._toml_value(k)} = {manifest._toml_value(v)}"
                              for k, v in MANIFEST.items()) + """
[[prepare]]
id = "dependencies"
run = ["installer", "--frozen-lockfile"]
expect = 2
timeout = 60
""")
    m = manifest.load(path)
    assert m.prepare == (manifest.Step("dependencies", ("installer", "--frozen-lockfile"), 2, 60),)
    assert manifest.parse(MANIFEST).prepare == ()
    effective, stored = manifest.template(path, change="another", branch="spec/another")
    path.write_text(stored)
    assert manifest.load(path).prepare == effective.prepare == m.prepare


@pytest.mark.parametrize("prepare", [
    [{"id": "p", "run": "installer"}],
    [{"id": "p", "run": []}],
    [{"id": "p", "run": ["true"]}, {"id": "p", "run": ["false"]}],
    [{"id": "t", "run": ["true"]}],  # would overwrite the verify log
])
def test_manifest_rejects_invalid_prepare_steps(prepare):
    with pytest.raises(ValueError):
        manifest.parse({**MANIFEST, "prepare": prepare})


def test_manifest_rejects_prepare_acceptance_log_collision():
    with pytest.raises(ValueError, match="prepare step ids"):
        manifest.parse({**MANIFEST, "prepare": [{"id": "p", "run": ["true"]}],
                        "acceptance": [{"id": "p", "run": ["true"]}]})


@pytest.mark.parametrize("bad", ["/abs", "src/*.py", "../up", "a/**/b"])
def test_manifest_rejects_globs_and_escapes(bad):
    with pytest.raises(ValueError):
        manifest.parse({**MANIFEST, "paths": {"allowed": [bad]}})


def test_matches_is_exact_or_prefix():
    pats = ("src/**", "README.md")
    assert manifest.matches("src/a/b.cpp", pats) and manifest.matches("README.md", pats)
    assert not manifest.matches("srcx/a", pats) and not manifest.matches("README.md.bak", pats)


def f(blocking, basis, text="t"):
    return {"blocking": blocking, "basis": basis, "text": text}


def test_blocking_needs_a_basis():
    d = review.decide({"verdict": "FAIL", "prior": [], "findings": [f(True, "style"), f(True, "scenario:a/b/c")],
                       "needs_human": []}, attempt=1, waived=[])
    assert d.verdict == "FAIL" and [x["basis"] for x in d.blocking] == ["scenario:a/b/c"]
    assert [x["basis"] for x in d.followups] == ["style"]


def test_fail_with_only_unfounded_blocks_is_a_pass():
    d = review.decide({"verdict": "FAIL", "prior": [], "findings": [f(True, "naming")], "needs_human": []}, 1, [])
    assert d.verdict == "PASS"


def test_pass_with_a_founded_block_is_a_fail():
    d = review.decide({"verdict": "PASS", "prior": [], "findings": [f(True, "input:-0.0")], "needs_human": []}, 1, [])
    assert d.verdict == "FAIL"


def test_later_attempts_narrow_new_blocks_but_keep_unresolved_ones():
    rv = {"verdict": "FAIL", "prior": [{"finding": "ramp length off by one sample", "resolved": False}],
          "findings": [f(True, "task", "ramp length still off by one sample"), f(True, "scope", "new scope worry")],
          "needs_human": []}
    d = review.decide(rv, attempt=2, waived=[])
    assert [x["text"] for x in d.blocking] == ["ramp length still off by one sample"]


def test_waived_and_needs_human():
    rv = {"verdict": "FAIL", "prior": [], "findings": [f(True, "task", "Pan law is wrong")], "needs_human": ["spec contradicts"]}
    assert review.decide(rv, 1, ["pan law"]).verdict == "PASS"
    assert review.decide(rv, 1, []).verdict == "NEEDS_HUMAN"


def test_test_edits_lists_removed_test_lines_only():
    diff = ("diff --git a/tests/t.cpp b/tests/t.cpp\n--- a/tests/t.cpp\n+++ b/tests/t.cpp\n-  check(x == 1);\n+  check(x >= 1);\n"
            "diff --git a/src/a.cpp b/src/a.cpp\n--- a/src/a.cpp\n+++ b/src/a.cpp\n-old\n+new\n")
    out = review.test_edits(diff)
    assert "tests/t.cpp" in out and "check(x == 1)" in out and "old" not in out


def test_schema_subset():
    from specloop.prompts import IMPLEMENT_SCHEMA, REVIEW_SCHEMA

    assert agents.validate({"status": "ready", "summary": "s", "subtasks": []}, IMPLEMENT_SCHEMA) is None
    assert "not in" in agents.validate({"status": "done", "summary": "s", "subtasks": []}, IMPLEMENT_SCHEMA)
    assert "missing" in agents.validate({"verdict": "PASS"}, REVIEW_SCHEMA)
    bad = {"verdict": "PASS", "prior": [], "findings": [{"blocking": "yes", "basis": "", "text": ""}], "needs_human": []}
    assert "expected boolean" in agents.validate(bad, REVIEW_SCHEMA)


def test_state_written_by_newer_code_still_loads(tmp_path, monkeypatch):
    import json

    from specloop import state

    monkeypatch.setattr(state, "ROOT", tmp_path)
    run = state.Run.for_change("c")
    run.state.task("t").attempts = 2
    run.save()
    raw = json.loads((run.dir / "state.json").read_text())
    raw["tasks"]["t"]["a_field_from_the_future"] = 1
    raw["pause"] = {"reason": "stuck", "task": "t", "later": True}
    (run.dir / "state.json").write_text(json.dumps(raw))
    again = state.Run.for_change("c")
    assert again.state.tasks["t"].attempts == 2 and again.state.pause.reason == "stuck"


@pytest.mark.parametrize("name", ["", ".", "..", "../x", "a/b", "-x"])
def test_unsafe_change_names_are_refused(tmp_path, monkeypatch, name):
    from specloop import state

    monkeypatch.setattr(state, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="unsafe change name"):
        state.Run.for_change(name)

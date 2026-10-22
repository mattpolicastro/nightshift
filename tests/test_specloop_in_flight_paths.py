"""Path extraction and source-checkout lookup for files named in unfinished tasks."""

from pathlib import Path

import pytest

from specloop import state
from specloop.propose import in_flight, paths
from specloop.seal import IDENTITY, git


@pytest.mark.scenario("in-flight-file-map/function-suffix-normalization/single-and-double-colon-references")
def test_single_colon_function_suffix_is_parsed():
    assert paths("Edit `specloop/loop.py:section_evidence`.") == {"specloop/loop.py"}
    assert paths(
        "Edit `specloop/loop.py:section_evidence`, "
        "`specloop/loop.py::section_evidence`, and `specloop/loop.py`."
    ) == {"specloop/loop.py"}


@pytest.mark.scenario("in-flight-file-map/function-suffix-normalization/single-and-double-colon-references")
@pytest.mark.parametrize("suffix", ["f", "_", "_helper2", "TestCase_9"])
@pytest.mark.parametrize("punctuation", ["", ":", ",", ".", ":,."])
def test_single_colon_identifiers_and_trailing_punctuation(suffix, punctuation):
    assert paths(f"`src/module.py:{suffix}{punctuation}`") == {"src/module.py"}


@pytest.mark.scenario("in-flight-file-map/function-suffix-normalization/single-and-double-colon-references")
@pytest.mark.parametrize("suffix", ["42", "2helper", "some-function", "func()", "é", "f二"])
def test_single_colon_suffix_requires_an_ascii_identifier(suffix):
    assert paths(f"`src/module.py:{suffix}`") == set()


@pytest.mark.scenario("in-flight-file-map/function-suffix-normalization/existing-lexical-paths-remain-supported")
def test_existing_lexical_paths_remain_supported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert paths(
        "Edit `tests/new_test.py`, `src/*.py`, and `tests/test_x.py::TestCase::test_case`. "
        "Ignore `openspec/specs/example/spec.md`, `/tmp/file.py`, `~/file.py`, "
        "`https://example.com/file.py`, and `in-flight-file-map/requirement/scenario`."
    ) == {"tests/new_test.py", "src/*.py", "tests/test_x.py"}


@pytest.mark.scenario("in-flight-file-map/function-suffix-normalization/existing-lexical-paths-remain-supported")
def test_punctuation_wildcards_and_double_colon_suffixes_remain_supported():
    assert paths(
        "`tests/a.py:` `tests/b.py,` `tests/c.py.` `tests/d.py:,.` "
        "`src/*` `src/*.py` `src/*:helper` `tests/a.py::test_case[param-value]` "
        "`tests/b.py::` `tests/c.py::42` `tests/d.py::TestCase::test_case`"
    ) == {"tests/a.py", "tests/b.py", "tests/c.py", "tests/d.py", "src/*", "src/*.py"}


@pytest.mark.scenario("in-flight-file-map/function-suffix-normalization/existing-lexical-paths-remain-supported")
@pytest.mark.parametrize("suffix", ["", ":helper", "::TestCase::test_case"])
def test_excluded_prefixes_remain_excluded_with_suffixes(suffix):
    tokens = ["openspec/specs/example/spec.md", "/tmp/file.py", "~/file.py",
              "https://example.com/file.py", "http_files/file.py"]
    assert paths(" ".join(f"`{token}{suffix}`" for token in tokens)) == set()


@pytest.mark.scenario("in-flight-file-map/function-suffix-normalization/existing-lexical-paths-remain-supported")
def test_backticks_and_lexical_filename_rules_remain_required():
    assert paths(
        "src/unquoted.py `src/with space.py` `src/with\nnewline.py` "
        "`src/no_extension` `README.md` `helper` `cap/requirement/scenario:helper`"
    ) == set()


@pytest.mark.scenario("in-flight-file-map/source-root-file-recognition/existing-root-files-are-included")
def test_root_level_files_count_when_they_exist(tmp_path):
    names = {"README.md", "pyproject.toml", "CLAUDE.md", "LICENSE"}
    for name in names:
        (tmp_path / name).touch()
    assert paths(
        "`README.md` `README.md:overview` `README.md::overview` "
        "`pyproject.toml,` `CLAUDE.md::instructions` `CLAUDE.md:instructions.` "
        "`LICENSE` `LICENSE:terms`", root=tmp_path,
    ) == names


@pytest.mark.scenario("in-flight-file-map/source-root-file-recognition/bare-identifiers-and-directories-are-excluded")
def test_bare_identifiers_are_not_paths(tmp_path):
    (tmp_path / "folder.md").mkdir()
    (tmp_path / "README.md").touch()
    assert paths(
        "`foo.bar` `missing.py` `missing.py:helper` `folder.md` `folder.md::contents` "
        "`*.md` `*` `helper`", root=tmp_path,
    ) == set()


@pytest.mark.scenario("in-flight-file-map/source-root-file-recognition/no-implicit-working-directory-lookup")
def test_no_root_does_not_look_up_files_in_the_working_directory(tmp_path, monkeypatch):
    (tmp_path / "README.md").touch()
    (tmp_path / "LICENSE").touch()
    monkeypatch.chdir(tmp_path)
    text = "`README.md` `README.md:overview` `LICENSE::terms` `src/new.py:helper` `src/*`"
    expected = {"src/new.py", "src/*"}
    assert paths(text) == expected
    assert paths(text, root=None) == expected


@pytest.mark.scenario("in-flight-file-map/source-root-file-recognition/existing-root-files-are-included")
def test_root_files_use_is_file_semantics_and_keep_prefix_exclusions(tmp_path):
    (tmp_path / "LICENSE").touch()
    (tmp_path / "COPYING").symlink_to(tmp_path / "LICENSE")
    (tmp_path / "broken.md").symlink_to(tmp_path / "absent.md")
    for name in ("http_file.md", "~notes.md"):
        (tmp_path / name).touch()
    assert paths(
        "`COPYING::terms` `broken.md` `http_file.md` `~notes.md` "
        "`openspec/specs/new/spec.md` `/tmp/file.py` `~/file.py` `https://example.com/file.py` "
        "`src/new.py:helper` `src/*` `src/no_extension`", root=tmp_path,
    ) == {"COPYING", "src/new.py", "src/*"}


@pytest.fixture
def local_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path / "run-home")
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "commit", "--allow-empty", "-qm", "base", env=IDENTITY)

    def add_run(name, text, *, status="running", repo=None, files=()):
        work = tmp_path / f"work-{name}"
        git(tmp_path, "clone", "-q", str(source), str(work))
        branch = f"spec/{name}"
        git(work, "checkout", "-q", "-b", branch)
        task_file = work / "openspec" / "changes" / name / "tasks.md"
        task_file.parent.mkdir(parents=True)
        task_file.write_text(f"## 1. Work\n\n- [ ] 1.1 {text}\n")
        for filename in files:
            (work / filename).touch()
        git(work, "add", "-A")
        git(work, "commit", "-qm", "tasks", env=IDENTITY)
        run = state.Run.for_change(name)
        run.state.status = status
        run.save()
        git(tmp_path, "clone", "-q", "--bare", str(work), str(run.dir / "repo.git"))
        # Tasks must be read from the manifest branch, even when HEAD differs.
        git(run.dir / "repo.git", "symbolic-ref", "HEAD", "refs/heads/main")
        (run.dir / "manifest.toml").write_text(f"""
[run]
repo = "{repo or source}"
change = "{name}"
branch = "{branch}"
[roles]
implement = "p"
review = "p"
[profiles.p]
adapter = "codex"
model = "m"
[paths]
allowed = ["README.md", "tests/**", "specloop/**"]
[[verify]]
id = "tests"
run = ["true"]
""")
        return run

    return source, add_run


@pytest.mark.scenario("in-flight-file-map/in-flight-map-integration/source-checkout-determines-root-existence")
def test_in_flight_uses_the_resolved_source_checkout(local_runs, tmp_path, monkeypatch):
    source, add_run = local_runs
    # Untracked source files count, even though the run's repository lacks them.
    for name in ("README.md", "pyproject.toml", "CLAUDE.md"):
        (source / name).touch()
    add_run(
        "other-change",
        "Edit `README.md` `README.md:overview` `README.md::overview` "
        "`pyproject.toml` `CLAUDE.md::instructions` `specloop/loop.py:section_evidence` "
        "`specloop/loop.py::section_evidence` `specloop/loop.py` `foo.bar` `branch-only.md`.",
        files=("branch-only.md",),
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "foo.bar").touch()
    (tmp_path / "source-alias").symlink_to(source, target_is_directory=True)
    monkeypatch.chdir(elsewhere)
    assert in_flight(Path("../source-alias"), "current-change") == {
        "README.md": ["other-change"], "pyproject.toml": ["other-change"],
        "CLAUDE.md": ["other-change"], "specloop/loop.py": ["other-change"],
    }


@pytest.mark.scenario("in-flight-file-map/in-flight-map-integration/run-selection-remains-unchanged")
@pytest.mark.parametrize("unreadable", ["state", "manifest", "tasks"])
def test_in_flight_keeps_run_selection_and_sorted_attribution(local_runs, tmp_path, unreadable):
    source, add_run = local_runs
    (source / "README.md").touch()
    text = "Edit `README.md` `README.md:overview` `tests/new.py` `tests/new.py::test_case`."
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    # Deliberately create eligible runs in reverse name order.
    add_run("z-running", text, repo=alias)
    add_run("a-paused", text, status="paused")
    add_run("current-change", text)
    add_run("done-change", text, status="done")
    add_run("aborted-change", text, status="aborted")
    other_source = tmp_path / "other-source"
    git(tmp_path, "clone", "-q", str(source), str(other_source))
    add_run("different-repository", text, repo=other_source)
    bad = add_run("0-unreadable", text)
    if unreadable == "state":
        (bad.dir / "state.json").write_text("not json")
    elif unreadable == "manifest":
        (bad.dir / "manifest.toml").write_text("not toml [")
    else:
        git(bad.dir / "repo.git", "update-ref", "-d", "refs/heads/spec/0-unreadable")
    missing_state = add_run("missing-state", text)
    (missing_state.dir / "state.json").unlink()
    assert in_flight(source, "current-change") == {
        "README.md": ["a-paused", "z-running"],
        "tests/new.py": ["a-paused", "z-running"],
    }

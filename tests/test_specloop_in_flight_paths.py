"""Lexical path extraction for files named in unfinished tasks."""

import pytest

from specloop.propose import paths


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

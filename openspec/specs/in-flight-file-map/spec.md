# in-flight-file-map Specification

## Purpose
Expose files named by other unfinished runs' tasks to proposal consumers, including function-qualified paths and existing source-root files, without mistaking absent bare identifiers for paths.

## Requirements

### Requirement: Function suffix normalization

Path extraction SHALL remove a trailing single-colon identifier and retain double-colon suffix support before filename classification, returning the underlying path once. A newly supported single-colon identifier SHALL match `[A-Za-z_][A-Za-z0-9_]*`. Extraction SHALL preserve existing backtick tokenization, trailing punctuation handling, nested extension-or-wildcard recognition, and excluded prefixes.

#### Scenario: Single and double colon references
- **WHEN** tasks contain backticked `specloop/loop.py:section_evidence`, `specloop/loop.py::section_evidence`, and `specloop/loop.py`
- **THEN** extraction returns `specloop/loop.py` once, including when no repository root is supplied

#### Scenario: Existing lexical paths remain supported
- **WHEN** text names backticked `tests/new_test.py`, `src/*.py`, and `tests/test_x.py::TestCase::test_case`, alongside `openspec/specs/example/spec.md`, `/tmp/file.py`, `~/file.py`, `https://example.com/file.py`, and a scenario ID without a file extension
- **THEN** extraction returns the three normalized eligible paths even if the nested files do not exist, and excludes the other tokens

### Requirement: Source root file recognition

When a repository root is supplied, path extraction SHALL additionally accept otherwise eligible slash-free tokens exactly when the normalized name is a file directly under that root. This check SHALL use file existence rather than a filename-extension heuristic or directory existence. With no root supplied, extraction SHALL continue excluding slash-free tokens.

#### Scenario: Existing root files are included
- **WHEN** a supplied root contains files `README.md`, `pyproject.toml`, `CLAUDE.md`, and `LICENSE`, and task text names those files including `README.md:overview` and `CLAUDE.md::instructions`
- **THEN** extraction includes all four normalized root filenames once each

#### Scenario: Bare identifiers and directories are excluded
- **WHEN** task text names `foo.bar`, `missing.py`, and `folder.md`, the first two do not exist under the supplied root, and `folder.md` is a directory there
- **THEN** none of those names is returned as a path

#### Scenario: No implicit working directory lookup
- **WHEN** task text names a root file that exists in the current working directory but extraction is called without a root
- **THEN** extraction excludes that slash-free token

### Requirement: In flight map integration

`in_flight(source, change)` SHALL supply the resolved source repository root to path extraction when reading other runs' tasks. It SHALL retain running and paused run selection, same-repository matching, current-change exclusion, unreadable-run isolation, and the existing path-to-change-list result with each run attributed once per path.

#### Scenario: Source checkout determines root existence
- **WHEN** an eligible run's committed tasks name `README.md`, `pyproject.toml`, `CLAUDE.md`, and `specloop/loop.py:section_evidence`, the three bare files exist in the source checkout, and collection runs from another directory containing only an additional task-named `foo.bar` file
- **THEN** the map includes the three root files and `specloop/loop.py` attributed to that run, and excludes `foo.bar`
- **THEN** repeated or differently suffixed mentions of one file contribute only one attribution for that run

#### Scenario: Run selection remains unchanged
- **WHEN** running and paused runs on the source repository name the same eligible file alongside the current change, done or aborted runs, a run on a different repository, and an unreadable run
- **THEN** the file's attribution contains only the other running and paused runs in run-name order, and the unreadable run does not prevent collection

### Requirement: Hands-off mentions

`in_flight` SHALL exclude a path that another run's tasks name only in a clause telling the implementer to leave it alone (the same rule as `edited_paths`), so a "Do not edit X" list does not appear as a planned edit. A path also named in an editing clause SHALL still count.

#### Scenario: Hands-off paths are not planned edits
- **WHEN** an eligible run's task says "Edit `specloop/loop.py`. Do not edit `nightshift/cli.py`, `README.md` or `specloop/cli.py`." and `README.md` exists in the source checkout
- **THEN** the map contains only `specloop/loop.py` for that run

#### Scenario: An edited path still counts
- **WHEN** a task names `specloop/cli.py` in an editing clause and again in a hands-off clause
- **THEN** the map includes `specloop/cli.py` for that run

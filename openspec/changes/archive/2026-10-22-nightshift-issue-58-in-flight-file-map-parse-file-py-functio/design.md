## Context

`specloop/propose.py:paths` extracts whitespace-free backticked tokens, splits off `::` suffixes, trims trailing punctuation, and requires a slash plus a filename extension or trailing wildcard. A single-colon function suffix therefore fails the filename check, and every bare filename fails the slash check.

`in_flight(source, change)` already resolves the source repository root, filters other runs to running or paused runs on that repository, and reads their tasks from each run's bare repository at its manifest branch. It currently calls `paths(text)` without the root. The resulting map already reaches the drafter and the critic added by #51. Their interfaces need no changes.

No applicable `AGENTS.md` exists in this checkout or its ancestors. Repository guidance in `README.md`, `CLAUDE.md`, and `docs/specloop/README.md` and the current spec-critic and CLI specifications were consulted.

## Goals / Non-Goals

Recognize both requested forms of planned file edits, avoid treating nonexistent bare identifiers as paths, and preserve existing assertions and run filtering. All new behavior must be testable with temporary files and local Git repositories.

Do not change the critic, `lint`, `overlaps`, `edited_paths`, `PROMPT`, `_in_flight_section`, PR lookup, or run storage. Do not introduce filesystem checks for slash-containing paths, since planned new files must still count. Do not expand this into general Markdown parsing, line-number parsing, or an inference system for nonexistent root files.

## Decisions

1. **Keep the existing caller contract.** Extend the interface to `paths(text: str, root: Path | None = None) -> set[str]`. Omitting `root` retains lexical extraction of slash-containing paths only, with the new suffix support. This leaves `edited_paths()` and overlap policy unchanged while sharing the suffix fix.

2. **Normalize before classifying.** Keep the existing backtick tokenizer and punctuation handling. Remove a trailing `:identifier` or `::identifier` before checking the filename; use ASCII identifiers matching `[A-Za-z_][A-Za-z0-9_]*` for the newly supported single-colon form. Preserve existing double-colon splitting behavior, including pytest-style references already accepted. Return the underlying path, deduplicated as today. Keep existing exclusions for tokens beginning with `openspec/`, `/`, `~`, or `http`, and retain the current extension-or-wildcard rule for slash-containing paths. This is not a new path canonicalizer.

3. **Check bare names against the source checkout.** For otherwise eligible slash-free tokens, require a supplied root and `(root / path).is_file()`. Do not require an extension on this branch: an existing `LICENSE` is also a file. Directories, absent names, and root wildcard patterns without a matching literal file are excluded. Do not expand globs or scan the repository. Use normal `Path.is_file()` semantics, including existing file symlinks; no Git-tracked-file requirement is introduced.

4. **Wire the existing resolved root.** In `in_flight()`, call `paths(text, root=root)` using the root already computed from `source`. Do not use the process working directory, the proposal clone, or the run's bare repository for existence checks. Keep task loading, eligible statuses, current-change exclusion, repository matching, unreadable-run isolation, and `dict[str, list[str]]` attribution unchanged. Duplicate references within one run contribute that run once per normalized path.

5. **Keep tests local and assertions intact.** Add all new tests and local fixture helpers to `tests/test_specloop_in_flight_paths.py`; mark them with the scenario IDs. Include `test_single_colon_function_suffix_is_parsed`, `test_root_level_files_count_when_they_exist`, and `test_bare_identifiers_are_not_paths`. Use direct parser tests plus actual `in_flight()` calls over temporary run state and local Git task commits. Exercise source-root lookup from a different working directory, suffixed root filenames, missing and directory names, absent nested planned files, duplicate attribution, and retained run selection.

   One existing assertion needs different setup, not replacement: `test_drafter_is_told_about_work_in_flight_and_moves_shared_tests` in `tests/test_specloop_propose.py` asserts that `- ` followed by backticked `calc.py` is absent from the drafting prompt, while its source fixture creates `calc.py` and its other-run tasks name that file. After creating the other run, make `calc.py` absent only in this test's temporary source checkout before invoking `draft()` (for example, unlink that temporary file). Keep the task text and every assertion unchanged, and update the obsolete directory-only comment to explain the absent-file case. This is the sole existing-test setup exception; do not change the shared source fixture or mock away path extraction. The new positive root-file test must fail if all root files are simply excluded. Explain this setup change and any further necessary fixture adjustment in the implementation PR; do not weaken assertions.

6. **Bound the implementation files.** Production edits are confined to `paths()` and `in_flight()` in `specloop/propose.py`, plus relevant docstrings. Tests belong in the new file, with only the setup/comment exception above in `tests/test_specloop_propose.py`. In particular, do not edit `specloop/loop.py` despite using its path as a test example; do not edit `specloop/critic.py`, `specloop/evidence.py`, `specloop/manifest.py`, `specloop/state.py`, any `nightshift/` or plugin file, or any other existing test file.

7. **Verification.** Run the new tests and existing proposal and critic tests, then `uv run pytest`. Preserve every existing assertion. No host application, live service, credentials, or manual check is needed; there is nothing beyond these automated tests for the PR's human checklist.

## Risks / Trade-offs

- Bare-file detection reflects the source checkout at collection time. A root file created only on another run's branch remains omitted until it exists in the source checkout; this is the requested boundary against false positives.
- A bare dotted token that actually names an existing root file counts as a path even if its author intended a symbol. Filesystem existence is the explicit discriminator.
- Suffix normalization also improves existing callers of `paths()` through the shared helper. Tests retain current double-colon, nested-path, wildcard, exclusion, and deduplication behavior; lint policy and prompt text stay untouched.
- The fixture adjustment is necessary because the old test's setup implied directory-only extraction. Its original assertions continue to protect prompt feedback, successful drafting, and absent-root-file exclusion, while new tests cover the positive case.

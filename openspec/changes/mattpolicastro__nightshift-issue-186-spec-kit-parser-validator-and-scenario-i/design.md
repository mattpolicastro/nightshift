## Context

Issue #186 is the parser layer of native spec-kit support (#184); #185 owns its
house-rules guide. No `specloop/speckit.py` or house-rules guide exists in this
checkout. No applicable `AGENTS.md` was found. The existing `Task` dataclass has
zero-based source lines, content-derived keys and backticked scenario references.
Its mutators reparse OpenSpec headings, so sharing the type does not yet enable
tick/split/extend/append on spec-kit documents. That integration is separate work.

#193 supplies `tests/fixtures/speckit-upstream/v1.3.2/templates/` and its sibling
README and MIT LICENSE. The authoritative pin is release tag `v1.3.2`, commit
`55264c2f2f2d1146f02abc543cb913dcdf1f9342` (2026-11-06), not research commit
`c43849408ac0de2ca6a52f14eaae71449776057b`. No network or vault access is needed.
Existing `spec-drafting`, `agent-test-rules`, `specloop-documentation` and
`issue-intake` requirements remain unchanged: retain assertions, use physical-line
rules, keep task sessions bounded, and leave human verification in this design.

## Goals / Non-Goals

Provide a small pure-Python module usable directly from tests and future format
adapters. Keep path lookup and text transformation separate from validation's
read-only filesystem access. Preserve all current OpenSpec guarantees.

Do not add a Markdown parser, dependencies, commands, adapter wiring or new task
types. Do not implement task mutation or claim spec-kit admission/build support.
The roughly 250-line module estimate is guidance, not a correctness cap.

Implementation edits are limited to `specloop/speckit.py`, new files under
`tests/fixtures/speckit/`, `tests/test_speckit_format.py`, and the conditional
paragraph in `docs/specloop/speckit.md`. Do not edit the upstream fixture source,
`mypy-baseline.txt`, existing tests, `specloop/tasks.py`, `specloop/openspec.py`,
`specloop/cli.py`, `specloop/loop.py`, `specloop/propose.py`, `specloop/revise.py`,
`specloop/manifest.py`, `specloop/spec_format.py`, or anything under `nightshift/`.
Do not edit `README.md`, `KNOWN-LIMITATIONS.md`, `docs/specloop/README.md`,
`docs/specloop/command-json.md`, `examples/specloop/self-hosting.toml`,
`tests/merge_readiness_fixtures.py`, `tests/test_merge_readiness_evidence.py`,
`tests/test_mixed_root_readers.py`, `tests/test_spec_format.py`,
`tests/test_specloop_base_publication.py`, `tests/test_specloop_design_checks.py`
or `tests/test_specloop_messages.py`. This standalone API changes no claims in
those guides and requires no concurrent-file edits.

## Decisions

### Public interfaces and records

Expose `feature_dir(repo: Path, change: str) -> Path`, `parse_spec(text: str) -> Spec`,
`scenario_ids(change: str, spec: Spec) -> dict[str, str]`,
`parse_tasks(text: str) -> list[Task]`, `validate(repo: Path, change: str) -> list[str]`,
`questions(spec: Spec) -> list[str]`, `human_checks(plan_text: str) -> list[str]`,
and `mark_complete(spec_text: str) -> str` from `specloop/speckit.py`.
`feature_dir` returns exactly `repo / "specs" / change`, without filesystem access.
Reuse `SpecError` from `specloop.openspec`; introduce only small data dataclasses.

`Spec` exposes title, optional status, stories, functional requirements, success
criteria and clarification markers. Story records expose numeric number, title,
optional numeric priority, source line and ordered scenarios. Scenario records
expose numeric number, text and source line; requirement/criterion and marker
records retain original text and source line. All spec-record lines are one-based.
Keep missing priorities and duplicate labels in the parsed records so validation
can report them; never overwrite a duplicate in a dictionary during parsing.
Stories are sorted by ascending numeric priority, absent priorities last, with
source order breaking ties. Scenario and marker order remains source order.

### Physical-line spec grammar

Use `splitlines()` and strip outer ASCII spaces/tabs for spec recognition. Do not
interpret fences, HTML or indentation as Markdown containers: matching physical
lines inside examples count. Over-detection is intentional; examples cannot hide
clarifications, duplicate labels or unchecked checks. Unsupported spellings do
not satisfy required structure, and ambiguous identities block validation.

- The first `^#\s+(.+?)\s*$` line supplies the title, including any `Feature
  Specification:` prefix. `^\*\*Status\*\*:\s*(.*?)\s*$` supplies status.
- Required level-two headings are exactly `## User Scenarios & Testing`,
  `## Requirements` and `## Success Criteria`, allowing the upstream suffix
  ` *(mandatory)*`. Other trailing text does not satisfy these headings.
- Recognize `### User Story <digits> - <title> (Priority: P<digits>)` with numeric
  story/priority captures. Also retain the same numbered heading with a missing
  or malformed priority suffix as a story with `priority=None`; it must not
  disappear and produce misleading unknown-scenario errors. Parse stories
  independently of mandatory-heading presence so a removed section heading
  reports just that omission.
- A story ends at the next physical heading matching `^(#{1,3})[ \t]+`.
  Within it, exact `**Acceptance Scenarios**:` opens the acceptance list, which
  ends at the next heading of any level or the story end. Match list items with
  `^\d+\.\s+\*\*AS(\d+)\*\*\s+(.*)$`. Capture only that physical line's text;
  do not infer an ID from the list ordinal or join continuation lines. Require
  nonblank scenario text. Unlabelled prose does not provide a labelled scenario.
- Collect each line containing a word-boundary `FR-\d+` or `SC-\d+` token into
  its respective list, retaining line numbers and original line text. This
  intentionally detects references/examples too; it adds no FR/SC count rule.
- On every line collect each `\[NEEDS CLARIFICATION(?:\s*:[^\]]*)?\]` occurrence,
  including the bare marker. Preserve its complete bracketed text and location.
  `questions(spec)` returns those complete marker strings in source order,
  including duplicates; it does not read other files or invent questions.

### Scenario identity

For each story/scenario, produce exactly
`f"{change}/us{k}/as{n}" -> f"US{k} — AS{n}\n{scenario text}"`, using numeric
story and label captures, never priority or list position. Reordering or editing
story titles therefore does not rename scenarios. Reuse `openspec.slug` to check
that `change` is already canonical (`slug(change) == change`); do not silently
rename a directory. Require a whole-string `tasks.SCENARIO_ID.fullmatch` for
every resulting ID, and raise `SpecError` for an invalid ID or duplicate ID.
Numeric captures canonicalize leading zeroes, so `AS01` and `AS1` collide.

### Task parsing and phase ownership

Apply the supplied phase regex `^##\s+Phase\s+(\d+):\s+(.+?)\s*$` to physical
task-file lines, retaining the captured phase number and title. A new physical
level-one or level-two heading ends the previous phase; deeper headings retain
it. Reuse `tasks.TASK` for checkboxes and the label regex
`^(T\d{3,}(?:\.\d+)*)\s+(?:\[P\]\s+)?(?:\[US(\d+)\]\s+)?(.*)$` for bodies.
Only labelled checkbox lines inside a recognized phase become tasks; other
lines cannot satisfy task-count or phase-ownership requirements. Fences/HTML
provide no exemption. Phase headings are column-zero, as specified by the regex;
checkbox indentation follows the existing `TASK` rule.

Return the existing `Task` with the captured `T...` label, section/section_title,
checkbox done flag and zero-based physical line. Preserve the body after the
numeric task label in `Task.text`, including `[P]` and `[USk]` flags and backticks;
this retains ownership and title fidelity without changing the shared dataclass.
`Task.scenarios` continues to use `tasks.SCENARIO_REF` unchanged. A phase is a
user-story phase when its captured title matches `^User Story (\d+)\b`.
Ownership comes only from the label-rule position of `[USk]`, not a prose mention.
Setup/foundational/polish phases need no story marker. Checked tasks count for
ownership and coverage too. Parsing does not call the existing task mutators.

### Validation and diagnostics

Read UTF-8 `spec.md`, `tasks.md`, `plan.md` under the feature directory and sorted
direct `checklists/*.md`. Missing/unreadable required files and unreadable listed
checklists are errors, never successful empty input. Emit deterministic
repository-relative `path:line: message` strings. Use one-based source lines;
use line 1 for file absence, missing sections or document-wide minimum failures.
Return all independent problems without writing files or running subprocesses.
Skip checks whose source file could not be read, avoiding fabricated cascades.

Validate these rules:

1. All three required spec sections are present.
2. Every parsed story has a priority and at least one labelled nonblank scenario.
3. Scenario labels are unique within each story, and generated IDs are unique
   across the feature. Report the later conflicting source line; do not silently
   replace it. Validation reports duplicate-ID errors rather than letting
   `scenario_ids` escape as an exception. For coverage, retain the unique ID set
   so a duplicate does not manufacture extra uncovered scenarios.
4. Each user-story phase contains at least one task with a matching positional
   `[USk]`; report failures at that phase's heading. A flag in another phase or
   a prose mention does not satisfy this rule.
5. Every generated scenario has at least one backticked reference in a parsed
   task, and no parsed task cites an unknown ID. Use `tasks.SCENARIO_REF`, not
   substring searches or prose inference. Locate uncovered errors at the
   scenario, and unknown-reference errors at the task.
6. No clarification markers remain in the spec, tasks, plan or checklists.
   Never scan `.specify/templates/` or the upstream sources for this rule.
7. Every checkbox in `checklists/*.md` is checked. After outer ASCII trim,
   `^[-*+]\s+\[([^\]]*)\]` identifies a checkbox; only `x` and `X` are checked.
   Empty/unknown states block at that line even inside examples. No checklist
   directory, or a readable file with no checkbox lines, adds no checkbox error.
   Unchecked implementation tasks and plan human checks are allowed.
8. `plan.md` exists and is readable; its human checks need not be completed.
9. At least one story has a labelled nonblank scenario; otherwise report
   `specs/<change>/spec.md:1: no user story with a labelled acceptance scenario`.
10. At least one parsed task occurs in a user-story phase and carries that
    phase's matching `[USk]`; otherwise report
    `specs/<change>/tasks.md:1: no user-story task`.

Use the aggregate no-story diagnostic instead of per-story missing-scenario
diagnostics when no story has any labelled scenario. Similarly, use the aggregate
no-user-story-task diagnostic instead of per-phase ownership errors when none
qualifies. Independent coverage/priority/unknown-reference failures still report;
this is not a first-error-only validator. A headings-only spec with empty readable
tasks and plan must produce both minimum-content errors. Isolated tests retain
other valid content to test one rule at a time; removing a priority must preserve
the story's scenarios, and adding an unknown reference must retain valid coverage.

### Pure text helpers

`human_checks` selects only exact `## Human checks`. As in
`loop.design_human_checks`, use `splitlines()`, outer ASCII space/tab trimming,
and `^(#{1,6})[ \t]+`: same-or-shallower headings close a section, deeper headings
retain it, repeated selected sections reopen it. Collect literal `- [ ]` followed
by space/tab and nonblank text, retaining the full trimmed checkbox line in
source order, including duplicates. Fences/HTML/indentation do not hide lines.
Do not add the legacy design heading as a second plan entry point or modify loop.py.

`mark_complete` replaces each physical status line matching the spec status
rule with `**Status**: Complete`; if absent, insert it immediately after the first
title. A missing title raises `SpecError` rather than guessing an insertion point.
Preserve unrelated text and existing line endings (use the title's ending for
insertion, or LF if it has none). Repeated calls return identical text. Completing
multiple status lines prevents a contradictory earlier status from surviving.

### Fixtures, tests and documentation

Copy the three local template files byte-for-byte to
`tests/fixtures/speckit/.specify/templates/{spec,plan,tasks}-template.md`. Copy
the upstream MIT LICENSE to `tests/fixtures/speckit/LICENSE` and add a README
naming the local source, release tag and commit. Add a short synthetic
`.specify/memory/constitution.md`; it need not be an upstream template copy.
Do not modify pinned placeholders to add labels. Add authored feature files
under `specs/042-add-a-gate/` with three priority-ordered stories, at least two
scenarios for US1, FR/SC lists, matching phases/tasks, a checked checklist and a
plan containing `## Human checks`. Keep example human checks pending.

All tests go in new `tests/test_speckit_format.py`. Use temporary copies of the
synthetic project for broken cases, never mutate its pinned inputs. Require the
named tests from the issue: `test_fixture_parses_stories_scenarios_and_tasks`,
`test_validate_passes_on_fixture`, `test_validate_reports_each_rule`,
`test_scenario_ids_match_harness_regex`, `test_questions_from_clarification_markers`
and `test_mark_complete_is_idempotent`. Parametrize single-rule failures with
exact diagnostic count, rule and `path:line`, including missing story labels;
add failing empty-story and no-user-story-task cases and a combined empty-feature
case. For the isolated no-story case, remove all acceptance lists and references
but retain matching story-phase tasks; for the isolated no-task case, retain
coverage in setup tasks and remove the user-story phases. Also test duplicate-ID
exceptions, invalid change names, source lines, phase boundaries, split labels,
checked tasks, multiple clarification markers, literal examples, helper section
boundaries, status insertion and read failures. Compare copied template bytes
against the pinned source rather than relying on a version string alone.

Run `uv run pytest` and `uv run python scripts/check_types.py`, preserving all
pre-existing assertions and without growing `mypy-baseline.txt`. No host
application, service or network is needed for automated validation.

If #185's guide exists when implementing, add only a short paragraph explaining
the API, literal-line limitations and lack of engine wiring, citing #186. If it
is absent, skip the paragraph and record that in the PR description; #185 will
provide it. Do not create a substitute guide or edit shared documentation. Any
documentation checks verify structure, links or runnable examples, not phrases.

## Risks / Trade-offs

- Literal-line recognition intentionally reports examples as real markers,
  identities and checks; authors must remove ambiguous examples from feature files.
- Synthetic fixtures prove the specified grammar, not all real generator output.
  The live feature check below remains pending even when CI is green.
- Five tasks in one section combine the local fixture copies and provenance
  checks, then separate spec parsing, task parsing, validation and text helpers.
  Verbatim copied lines are not agent-authored lines and do not count toward
  the roughly 300-line session ceiling. Session and module line budgets are
  estimates; preserve correctness and named coverage first.
- Sharing `Task` preserves keys and scenario references; future integration must
  still adapt mutation and format selection without broadening this issue.
- #185 may land during implementation. Its absence is an accepted documentation
  deferral, not a reason to fetch sources or block this parser.

## Human checks

- [ ] Run `uv run python -c 'from pathlib import Path; from specloop import speckit; print(speckit.validate(Path("/absolute/path/to/matts/repo"), "042-feature-name"))'` with the actual repository and feature name for Matt's first hand-authored feature produced with `/speckit.specify` and the `nightshift-speckit` preset; inspect any diagnostics and record the command, feature provenance and result on tracking issue #184. CI cannot prove this generator compatibility.

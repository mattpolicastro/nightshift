# Tasks: [FEATURE NAME]

**Input**: Design documents from `specs/<NNN>-<slug>/`
**Prerequisites**: plan.md and spec.md; research.md, data-model.md and contracts/ as applicable.

## Format

Use `- [ ] T001 [P] [US1] Description with file path`; `[P]` marks independent work.
Every story task must cite covered IDs in backticks, e.g. `042-add-a-gate/us1/as1`;
every scenario needs a covering task. Keep story tags and concrete file paths.
Setup tasks need no invented scenario. Replace example paths and feature IDs.

## Path Conventions

Examples use `src/` and `tests/`; follow the project's layout in plan.md.

## Phase 1: Setup

- [ ] T001 Create the project structure in src/ and tests/ per plan.md.

## Phase 2: Foundation

- [ ] T002 Establish shared test setup in tests/conftest.py.

## Phase 3: User Story 1 (Priority: P1)

- [ ] T003 [P] [US1] Test the primary and alternate outcomes in tests/test_primary.py; `042-add-a-gate/us1/as1`, `042-add-a-gate/us1/as2`.
- [ ] T004 [US1] Implement both outcomes in src/primary.py; `042-add-a-gate/us1/as1`, `042-add-a-gate/us1/as2`.

## Phase 4: User Story 2 (Priority: P2)

- [ ] T005 [US2] Implement and verify the next outcome in src/next.py and tests/test_next.py; `042-add-a-gate/us2/as1`.

## Phase 5: User Story 3 (Priority: P3)

- [ ] T006 [US3] Implement and verify the additional outcome in src/additional.py and tests/test_additional.py; `042-add-a-gate/us3/as1`.

## Phase 6: Polish and Dependencies

- [ ] T007 Update usage examples in docs/usage.md.

Finish setup/foundation before dependent work. Deliver P1 independently and verify
each story's scenarios. Mark dependencies; parallelize only without file/prerequisite conflicts.

## Harness-owned Review and Completion

- Review feedback alone appends `## Phase N: Review round K`; K is the largest
  existing review-round number plus one.
- Section evidence and reviewer fixes extend the existing `## Phase N: Title`;
  they keep its phase number and title.
- Final fixes append `## Phase N: Coverage` or `## Phase N: Acceptance fixes`,
  using the supplied title.

Every appended phase uses the largest existing phase number plus one.
All new base task IDs follow the largest existing numeric T root M (including
dotted children), using
`T{M+1:03d}` without truncation. Keep committed task IDs and scenario citations
stable; never renumber or reuse removed IDs. Only the harness ticks `[X]` or
updates frozen authoring files. As with `/speckit.converge`, preserve identities.
Implementers cannot edit `specs/**` or `.specify/**`.
Human/live checks belong in plan.md under `## Human checks`, each
with exactly one scenario ID, and are not implementing-agent tasks or CI claims.

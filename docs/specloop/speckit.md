# Nightshift spec-kit house rules

These authoring rules and the `nightshift-speckit` preset belong to
[issue #185](https://github.com/mattpolicastro/nightshift/issues/185) in
[issue #184](https://github.com/mattpolicastro/nightshift/issues/184).
Native engine parsing, admission, freezing and finish support remain separate work;
these rules do not enable them. Existing [OpenSpec naming and lifecycle guidance](README.md) stays in effect.

## Feature directory and branch

Use `specs/<NNN>-<slug>/` and `spec/<NNN>-<slug>`. Canonicalize the positive issue
number as decimal, pad to at least three digits, and never truncate. Lowercase the
supplied issue title, replace runs outside ASCII `a-z0-9` with `-`, strip boundary
hyphens, take the first 40 characters, then strip trailing hyphens; use `issue`
if empty. Do not transliterate Unicode or invent a semantic short name.

| Issue title and number | Feature directory | Branch |
| --- | --- | --- |
| 42: Add a gate | `specs/042-add-a-gate/` | `spec/042-add-a-gate` |
| 1234: Big feature | `specs/1234-big-feature/` | `spec/1234-big-feature` |

Issue numbers are repository-local on GitHub and Gitea; gaps are fine. This meets
upstream's `^[0-9]{3,}-` directory rule and avoids parallel drafts picking the same
next number from a sequential scan. It does not prevent concurrent edits to the
same issue: isolate drafts in worktrees. Never rename a committed feature after
a title edit; stable identities keep references valid. OpenSpec names are unchanged.

## Open the issue first

Open the issue, then specify with its number: `/speckit.specify 42 Add a gate`
or `/speckit.specify #42 Add a gate`. The remaining text is the issue title used
for both the slug and initial feature description; expand requirements during drafting.
Both forms select `specs/042-add-a-gate` and branch `spec/042-add-a-gate`.
The preset's `speckit.specify` wrapper supplies `SPECIFY_FEATURE_DIRECTORY=specs/042-add-a-gate`
using the upstream [directory override](https://github.com/github/spec-kit/blob/v1.3.2/docs/overview/core.md).
Sequential or timestamp configuration cannot derive a repository issue number;
passing it explicitly keeps identity tied to the issue without a forge API lookup
or a new numbering script.

## Labelled acceptance scenarios

Number labels locally within each user story:

```markdown
1. **AS1** **Given** a draft **When** approved **Then** the gate opens.
2. **AS2** **Given** an unresolved question **When** reviewed **Then** approval waits.
```

Use `<NNN>-<slug>/us<k>/as<n>`, for example `042-add-a-gate/us1/as2`.
Tests cite `@pytest.mark.scenario("042-add-a-gate/us1/as2")`; tasks cite the same
backticked ID below. Append labels without renumbering committed labels or reusing
removed IDs, like `/speckit.converge` preserves task IDs. Stable three-part IDs
retain the existing regex, pytest marker and per-story grouping, so tests keep
their references when stories grow.

## Tasks cite scenarios

Retain `- [ ] T001 [P] [US1] Description with file path`: `[P]` is optional,
and story work keeps its story tag. Every user-story task cites its scenario IDs
in backticks; every scenario has at least one covering task for traceability.
Setup tasks need no invented story reference:

```markdown
- [ ] T001 Create tests/gates/conftest.py
- [ ] T002 [P] [US1] Test approval in tests/gates/test_gate.py; `042-add-a-gate/us1/as1`
- [ ] T003 [US1] Handle questions in src/gate.py; `042-add-a-gate/us1/as2`
```

Harness-owned review and coverage additions append `## Phase N: Review round K`,
with N after existing phases and IDs after the largest T number M, using
`T{M+1:03d}` without truncation. The harness ticks completion `[X]`. Appending
preserves identity without collisions; this contract adds no harness behavior.

## Human-only checks

In `plan.md`, add a checklist with exactly one scenario ID per item:

```markdown
## Human checks
- [ ] Confirm the deployed gate waits for an answer; `042-add-a-gate/us1/as2`
```

For a post-deploy check, put `<!-- nightshift:post-deploy -->` inside that scenario's
body in `spec.md`, matching today's OpenSpec `design.md` convention. Human/live
checks require observation outside automated verification: keep them out of agent
tasks and do not claim CI performed them. No plan template override is needed.

## Flow-forward persistence

Drafting edits before approval are allowed. Delivered feature directories are
immutable records; lifecycle writes belong to the harness: task ticks in `tasks.md`
and final `**Status**: Complete` in `spec.md`. Nothing is archived or merged into a
consolidated spec; this does not restrict merging the feature's Git PR.
The overarching spec is `.specify/memory/constitution.md`. For later behavior
changes, create a new feature and name superseded features under `## Assumptions`,
for example `Supersedes 042-add-a-gate`. Keeping delivered records preserves history
and stable references; it does not change OpenSpec's archival workflow.

## Frozen paths

During implementation, agents cannot edit `specs/**` or `.specify/**`; only the
harness updates `tasks.md` and the Status line. Authoring happens before this
boundary. Freezing prevents an implementer from changing acceptance criteria to
grade its own work. Runtime enforcement is separate work.

## No [NEEDS CLARIFICATION] at approval

Resolve every `[NEEDS CLARIFICATION]` marker, including `[NEEDS CLARIFICATION: ...]`,
before approval. Return remaining questions to the issue for the drafter to resolve;
an unresolved draft is not an approved build. Autonomous implementation needs
settled intent. This is authoring guidance, not a new validator.

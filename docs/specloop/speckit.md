# Nightshift spec-kit house rules

These authoring rules and the `nightshift-speckit` preset belong to
[issue #185](https://github.com/mattpolicastro/nightshift/issues/185) in
[issue #184](https://github.com/mattpolicastro/nightshift/issues/184).
Native features support explicit drafting, admission, build and publication with a
trusted native manifest; see the [drafting guide](README.md#native-spec-kit-drafting),
[build guide](README.md#native-spec-kit-builds)
and [manifest example](../../examples/specloop/speckit.toml). Installing authoring
assets does not enroll a repository or configure a build. Native admission runs
feasibility normally; daemon intake remains unsupported. Existing
[OpenSpec naming and lifecycle guidance](README.md) stays in effect.

## What the drafter produces

Explicit `nightshift propose` reads the local `.specify/templates/spec-template.md`,
`plan-template.md` and `tasks-template.md`, the constitution and relevant code/tests.
House rules override incompatible scaffold examples; missing templates must be
supplied locally. The drafter does not download templates, run `specify`, or
rewrite templates or the constitution. It writes only the selected feature's
`spec.md`, `plan.md` and initial `tasks.md` under `specs/<change>/`.

The spec has numbered, prioritized user stories and stable `ASn` acceptance
labels. Tasks begin with Setup/Foundational work, followed by exactly one positive
`## Phase N: User Story K - ...` per story, ordered by numeric priority and source
order for ties. Use `T001` labels, optional `[P]`, positional `[USk]` story tags
and backticked citations such as `042-add-a-gate/us1/as2`. Cover every scenario;
setup tasks need no invented citations. Human-only work goes in `plan.md` under
`## Human checks`, with one scenario citation per checklist item.

Choose one concern per task within the unchanged ceiling of about 300 changed
lines at most. Combine related same-file work, order dependencies and keep tests
with code. Target six tasks or fewer per user story: every task incurs a review
cycle and every phase an evidence gate. Documentation-only features normally use
one or two tasks per story, preserving story phases rather than splitting by
document heading. Six is advisory, not a rejection threshold.

### Story sizing examples

| Tasks per story phase | Feature total | Six-task target met | Count-only critic risk |
| --- | ---: | --- | --- |
| 5, 5, 5 | 15 | Yes | No |
| 8 | 8 | No | No |
| 9 | 9 | No | Yes |

More than eight parsed task entries in any one story phase warrants advisory
merge advice, never a count-only contradiction or correction round. Checked and
unchecked entries, including dotted children, count. Setup, Foundational and
Polish do not contribute to this story threshold; ordinary feasibility and
design review still apply. Whole-feature totals, including documentation-only
totals, never cause a count-only finding. The per-task ceiling still applies.

### Questions and revisions

Ask at most three genuine missing-intent questions as
`[NEEDS CLARIFICATION: ...]` in `spec.md`; decide routine details. Unresolved
questions return `needs_input` before lint correction, commit or review, and
must be answered before approval. To revise an undelivered feature, name it in
the idea and use the same change, branch and directory; retain committed task
and scenario IDs and unrelated content. Conflicting target names require
resolution. Complete features stay immutable; later changes need a new feature
identifying supersession in `## Assumptions`.

Follow the [ordinary propose/admit examples](README.md#native-spec-kit-drafting)
and [revision example](README.md#revise-an-undelivered-feature). Feasibility and
critic reviews read the constitution and explicitly named Complete features
within the [bounded review context](README.md#bounded-review-context).
The [real-profile quality observation](README.md#draft-quality-observation)
remains pending human work on
[issue #184](https://github.com/mattpolicastro/nightshift/issues/184); offline
tests do not prove draft quality or complete the spec-PR inspection.

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
updates frozen authoring files.

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
grade its own work. Configure both globs in the trusted manifest's `[paths] frozen`,
alongside workflow and steering-file protections, as in the
[native example](../../examples/specloop/speckit.toml).

## No [NEEDS CLARIFICATION] at approval

Resolve every `[NEEDS CLARIFICATION]` marker, including `[NEEDS CLARIFICATION: ...]`,
before approval. Return remaining questions to the issue for the drafter to resolve;
an unresolved draft is not an approved build. Autonomous implementation needs
settled intent. This is authoring guidance, not a new validator.

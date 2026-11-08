## Context

#188 has landed in this checkout. `spec_format.SPECKIT` supplies native paths,
validation, scenarios and task operations. `propose.lint` already uses adapter
validation/root/scenarios, but it and `overlaps` still call `tasks.parse`.
`propose.draft` always renders its OpenSpec `PROMPT` and does not collect
`speckit.questions`. Feasibility and critic also have OpenSpec-only prompts.
Native `context_files` deliberately creates `proposal.md`, `design.md`,
`specs/spec.md` and `specs/constitution.md` aliases for unchanged build prompts.

Read sources: repository README, the affected engine code and tests, canonical
capability specs, `docs/specloop/speckit.md`, local pinned spec-kit templates and
#188's archived design. No `AGENTS.md` exists in the checkout or its ancestors.
The vault research note is not supplied in the checkout; the issue and landed
interfaces provide the decisions needed here.

## Goals / Non-Goals

Enable explicit native `propose` and ordinary native admission with feasibility,
without changing daemon intake. Keep the existing question/correction/review
flow, verdict fields, profile selection, read-only reviews and task evidence.
Make native feature sizing practical while preserving bounded sessions.

Do not add schema/state fields, agents, tools, template downloads, format
auto-detection in the build loop, dependency graphs, native archival, or task
mutation/revision mechanics. Do not change any OpenSpec prompt text, existing
OpenSpec guidance paragraphs or OpenSpec critic behavior.

## Decisions

### 1. Native initial draft and adapter lint

Add a separate `SPECKIT_PROMPT` in `specloop/propose.py` and select it only for
`m.format == "speckit"`. Keep `PROMPT` and its OpenSpec rendering byte-identical.
Use the adapter's `drafter_root(change)` everywhere confinement is described.
The native prompt contains no generated `openspec/` references and requires:

- Read `.specify/templates/spec-template.md`, `plan-template.md` and
  `tasks-template.md` locally, the constitution, repository instructions and
  relevant code/tests. Do not invoke `specify` or use the network. Missing
  templates are an actionable prerequisite problem, not permission to download.
- Write `specs/<change>/spec.md`, `plan.md` and `tasks.md` using those scaffolds.
  House rules override incompatible template examples, including unlabelled
  scenarios and optional tests. Do not rewrite templates or the constitution.
- Label acceptance scenarios `ASn` within numbered, prioritized stories and
  preserve committed IDs. Use `<change>/us<k>/as<n>` citations in backticks.
  Every scenario has covering work; every story task carries positional `[USk]`.
- Start with Setup/Foundational, then exactly one positive `## Phase N: User
  Story K - ...` per user story, ordered by numeric priority, source order for
  ties. Use native `T001` labels and optional `[P]`; setup needs no invented
  story citations. Do not reproduce template sample tasks.
- Use one concern and about 300 changed lines per task, tests with code,
  dependency ordering and deterministic physical-line rules for rich text.
  Target six tasks or fewer per user story, minimizing unnecessary phases
  within the required story structure.
- Put human-only work in `plan.md` under `## Human checks` with one scenario
  citation per checklist item. For a genuine missing intent, use at most three
  `[NEEDS CLARIFICATION: …]` questions in `spec.md`; decide routine details.

The caller's effective change remains authoritative: explicit CLI callers pass
the existing feature's name as `change` (and its branch for revision). When the
idea names that existing undelivered feature, revise its files and retain IDs,
unrelated content and directory identity instead of generating a second feature.
The prompt must stop and explain conflicting target names rather than write
outside the adapter root or silently rename `change`. Complete features remain
immutable: behavior changes require a new feature identifying supersession in
Assumptions. No issue-number inference or front-door naming is added here.

Route lint and overlap task reads through `fmt.parse_tasks`. Preserve adapter
`validate`, `scenarios`, `tasks_path` and `drafter_root` use and existing
`ValidatorUnavailable` propagation. Convert ordinary native parse failures to
lint problems; do not let malformed phase/task identities pass as an empty plan.
Native diagnostics name native phases and `plan.md`. Strip only the existing
positional `[P]`/`[USk]` task flags before applying the common human-task matcher,
so `[US1] Manually …` is not missed; retain OpenSpec diagnostics unchanged.

After an OK drafting result, inspect the drafted native `spec.md` using
`speckit.parse_spec` and `speckit.questions` before strict lint. Markers feed
`Draft.questions` and the existing needs-input return path, even if the agent
reported `drafted`. If agent questions are also present, retain them first and
append parser questions in source order, preserving parser duplicates. No
truncation of existing markers: the three-question bound is authoring guidance.
Question results perform no lint correction, commit, feasibility, PR lookup or
critic call. Missing/unreadable specs with no explicit agent questions remain
ordinary draft problems; do not treat unreadability as an empty successful draft.
Direct lint and admission still reject markers everywhere the existing validator
checks; only drafted spec markers enter this question path.

### 2. Shared path extraction

Add `specs/` to the existing lexical excluded-prefix tuple in `propose.paths`.
Apply it with or without `root`, after existing suffix normalization. The helper
is used by `nightshift.arm.scope_paths` through a rootless `edited_paths` call,
so adding only a manifest argument or checking for `.specify` would miss that
caller. Treat both format-owned spec roots as specification references for this
advisory map; this intentionally also excludes a `specs/` token in a mixed repo.
All other prefixes, function suffixes, negation clauses, bare-file existence
checks and run selection remain unchanged. No `nightshift/` edit is needed.

### 3. Native feasibility and critic context

Add native variants beside `feasibility.PROMPT` and `critic.PROMPT`, selected by
manifest format. Share a small native review-instructions fragment if useful;
do not build a feature index or dependency resolver. Both agents already see
a fresh extraction of the entire candidate repository.

The prompts explicitly read `specs/<change>/spec.md`, `plan.md`, `tasks.md`,
`.specify/memory/constitution.md` and `specs/<named-feature>/spec.md` for each
Complete feature named under Depends on or Assumptions. Feasibility during
native drafting must receive the effective draft change, not a stale manifest
template name; the critic already receives an explicit change argument.

Give the review prompt a bounded physical-line reading convention: plan sections
start at exact column-zero `## Depends on` or `## Assumptions` and end at the next
column-zero level-one/two heading; also accept feature names on an ASCII-trimmed
`Depends on:` or `**Depends on**:` field line. References are explicit canonical
`NNN-slug` names or `specs/NNN-slug/` paths, optionally ending in `spec.md`.
Read the spec's `## Assumptions` too, because the pinned spec template and
flow-forward house rule place supersession there. Match the same section
boundary rule. Literal matching lines inside examples receive no exemption.

Check named files' physical `**Status**: Complete` before treating them as
delivered guarantees. Do not infer a feature from a title, recurse through its
references, scan unrelated historical features or treat Draft features as
Complete. Missing/ambiguous referenced identity, unreadable required context,
or conflicting Status lines must be reported as a concrete inability to complete
review, not silently omitted to return a clean verdict. This is agent reading
guidance, not a new harness Markdown parser or validator. Tests exercise prompt
instructions and the exact extracted files, not a claim to prove model reasoning.

For native input, replace only the OpenSpec requirements/delta contradiction
rule: breaking a constitution principle or changing behavior specified by a named
Complete feature without explicitly identifying and justifying it in
`## Assumptions` is a contradiction. The statement may be in the feature spec
or plan. A generic dependency mention is not acknowledgement of the changed
behavior. Acknowledgement removes that particular missing-disclosure finding;
it does not waive allowed/frozen paths, trusted checks or other impossibilities.
Human checks belong in `plan.md`. Preserve all eight critic checklist items,
evidence obligations, risk/contradiction distinction, time limits and
`feasibility.SCHEMA`. Existing correction counts and delivery are unchanged.

### 4. Native build prompts and real context names

Keep `IMPLEMENT`, `REVIEW`, `GATE_REVIEW` and `ARBITER` as the original OpenSpec
strings. Add a small selector in `specloop/prompts.py`, for example
`for_format(template, format="openspec") -> str`. It returns the exact original
for OpenSpec and a native variant for spec-kit. Select before `.format` in
`loop._implement_prompt` and the task-review, section-review and arbitration
call sites. Do not put format branches or content detection in `loop.py`.

Native variants name `{ctx}/spec.md`, `{ctx}/plan.md` and
`{ctx}/.specify/memory/constitution.md`; retain `task.md`'s full scenario text
and the gate's `section.md` scenario context. Preserve verbatim the
implementation rule beginning “For every scenario ID the task names” and all
review bases, schemas and acceptance rules.

Change only `_SpecKit.context_files` destinations, in this order:
`spec.md`, `plan.md`, `.specify/memory/constitution.md`. Retain filtering to
existing entries and byte-preserving copying. Remove all four transitional
aliases. Do not change source files, copy historical features automatically or
alter `_OpenSpec.context_files`. Ship selector wiring and alias removal together,
including arbitration, so no prompt points at a removed alias.

### 5. Sizing and verification

The native critic's size rule counts parsed task entries in each story phase:
a phase recognized by existing `speckit.PHASE` whose title matches
`speckit.STORY_PHASE`. Use existing literal boundaries: only valid native T-task
checkboxes within a phase count; a same-or-higher heading closes it, deeper
headings retain it, examples get no semantic exemption. Count checked and
unchecked entries and dotted children as entries. Ambiguous phase/task identity
remains a validator/parser refusal, never a size pass.

This is prompt guidance, not a second deterministic size gate. More than eight
entries in one story phase warrants merge advice as a non-blocking risk; exactly
eight does not. Fifteen across three five-task story phases is not a count risk.
Ignore the total, including in documentation-only native input; Setup,
Foundational and Polish are outside the story-count test but remain subject to
ordinary feasibility/over-engineering review. The six-task target is advisory;
the per-task 300-line ceiling remains. Native documentation-only features retain
compact one/two-task guidance within their required story structure. OpenSpec
keeps its existing whole-plan eight-task/two-documentation-task critic thresholds.

Use fixture copies, real adapters and fake agents for offline tests:
`tests/test_speckit_drafting.py` contains the five named acceptance tests plus
revision, clarification precedence, native lint/overlap and path-exclusion cases.
Verify the feasibility and critic calls retain the constitution and named
Complete feature files at the exact candidate commit, with both Depends on and
Assumptions examples. Test native admission without `--skip-feasibility` using
the existing CLI and an injected feasibility agent; no CLI behavior change.

Add native prompt/context cases to the new test file for all four consumers.
Migrate only `context_aliases` and the alias-destination assertions in
`tests/test_speckit_build.py::test_native_context_bytes` to real native paths,
retaining CRLF/non-ASCII byte checks, source immutability and absence of unrelated
history/tasks. Every OpenSpec prompt assertion remains unchanged.

Add `tests/test_specloop_critic_task_risk.py::test_speckit_counts_per_story`:
assert the native rendered instructions and drive fake verdicts for 15/3,
8/1 and 9/1 tasks through proposal handling, including no correction for a risk.
Keep all existing OpenSpec assertions. Add
`tests/test_specloop_drafting_guidance.py::test_speckit_sizing_guidance` beside,
not instead of, the existing tests. These are requested guidance contracts;
other new documentation checks use real CLI/TOML parsers and local links.
Select documentation passages by a unique exact column-zero heading through
the next same-or-higher heading; missing or duplicate boundaries fail. Do not
interpret fences, HTML or indentation semantically.

### 6. Documentation and preserved boundaries

Add “What the drafter produces” to `docs/specloop/speckit.md` and a separate
native drafting section in `docs/specloop/README.md`. Leave its existing
“OpenSpec drafting” prose unchanged. Include explicit `propose` and ordinary
`admit` examples, feature revision identity, local templates, question return,
per-story sizing and the bounded native review context. Correct pending-support
claims in root `README.md`, `docs/specloop/command-json.md`,
`presets/nightshift-speckit/README.md` and comments in
`examples/specloop/speckit.toml`. Keep daemon intake pending.

Retain existing `--skip-feasibility` command examples and assertions, but label
those blocks as optional operator-requested bypass examples, no longer a native
prerequisite. Put the default no-bypass workflow in the new native drafting
section and test it separately. Keep the preset's option mention as optional.
This preserves `tests/test_speckit_build_docs.py` unchanged.

`KNOWN-LIMITATIONS.md` needs one narrowly bounded exception to the concurrent-file
avoidance rule: update only the “Native spec-kit context” bullet to distinguish
selected-feature build context from named-feature feasibility/critic reads and
to remove the obsolete unsupported-drafting claim. Do not edit its readiness
material. Do not edit `nightshift/specloop_intake.py`,
`tests/merge_readiness_fixtures.py`, `tests/test_merge_readiness_evidence.py` or
`tests/test_specloop_base_publication.py`. No other `nightshift/` files change.

Canonical requirements are preserved through explicit MODIFIED deltas for
sizing, aliases, drafting dispatch and superseded support claims. No direct
canonical-spec edit is an implementation task; normal OpenSpec archival applies
these deltas. Adapter revision and coverage append contracts remain untouched.

## Risks / Trade-offs

Prompt duplication can drift; exact OpenSpec-rendering tests and native parallel
cases constrain it without refactoring the legacy text. Native alias removal
requires the four consumer updates to land together. Prompt tests establish
instructions, routing and available context, not whether a real reviewer follows
them or whether a feature draft is good.

The shared lexical `specs/` exclusion reserves that prefix for specifications
even in mixed repositories, preventing shared front-door callers from treating
feature references as production edits. No automatic historical traversal means
unnamed old guarantees can still be missed; documentation must state that limit.
The small KNOWN-LIMITATIONS correction may conflict with #182 and must preserve
its separately edited readiness bullet.

## Human checks

- [ ] Draft one real feature-sized issue in a spec-kit scratch repository with
  the real profiles, inspect the spec PR for house-rule shape, useful task
  boundaries and review findings, and record drafting rounds, questions and
  critic findings with the issue/PR identity on tracking issue #184. Confirm
  ordinary native feasibility works without the bypass. This is live quality
  evidence; offline tests and this draft do not claim it was performed.


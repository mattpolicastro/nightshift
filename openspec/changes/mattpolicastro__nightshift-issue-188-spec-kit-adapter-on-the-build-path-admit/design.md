## Context

This draft targets main at `5284294d`, which includes #186, #187 and the merged
#194/#185 house rules. `speckit.py` already parses literal physical lines into the
shared `Task` type, validates four-scenario fixture features, collects plan
checks and implements idempotent `mark_complete`. `spec_format.get` still rejects
native format. `tasks.parse` and its mutators understand only OpenSpec headings;
`revise._append` likewise counts only those headings. `_gate_context` selects
notes with an OpenSpec numeric prefix. `_finish` clones and archives regardless
of format. These are the build-path gaps, not reasons to extend drafting.

The checkout has no `AGENTS.md`; `CLAUDE.md`, repository docs, house rules and the
affected canonical requirements supply local context. The named vault research
note is not present in this checkout; this design relies on the issue's settled
decisions and the landed interfaces, not unverified content from that note.

## Goals / Non-Goals

Support explicit `nightshift admit`, `run`, `revise`, `publish` and `finish` for
approved native features, with the same task sealing, evidence, human holds,
publication safety and restart behavior. A previous supervisor must still load
new run state: do not add `Run`/`RunState` fields or serialize adapters.

Do not implement native proposal generation, daemon intake/enrollment dispatch,
critic or feasibility format support, new prompts, new evidence scoring, automatic
merging or feature-history search. Native admission uses the existing explicit
`--skip-feasibility` option in examples and build tests until drafting/feasibility
support lands; it still runs native structural validation. Do not silently bypass
feasibility when the operator did not request that option. The real build check
uses real implementer/reviewer profiles after this structural admission.

## Decisions

### 1. Manifest-selected task operations

Keep `specloop/tasks.py` unchanged. Its `parse`, `tick`, `split`,
`extend_section` and `append_section` retain byte-for-byte OpenSpec behavior,
including ignoring native headings as section declarations. Never auto-detect
heading styles or reject a file for containing both styles, even in examples.
The already-selected manifest format alone determines task recognition.
Engine routing compatibility remains in force without a MODIFIED delta.

Extend `SpecFormat` in `specloop/spec_format.py` with these typed operations:

| Member | OpenSpec delegation | Native delegation |
| --- | --- | --- |
| `parse_tasks(text: str) -> list[tasks.Task]` | `tasks.parse(text)` | `speckit.parse_tasks(text)` |
| `tick(text: str, task: tasks.Task) -> str` | `tasks.tick(text, task)` | `speckit.tick(text, task)` |
| `split(text: str, task: tasks.Task, subtasks: list[str]) -> str` | `tasks.split(text, task, subtasks)` | `speckit.split(text, task, subtasks)` |
| `extend_section(text: str, section: int, items: list[str]) -> str` | `tasks.extend_section(text, section, items)` | `speckit.extend_section(text, section, items)` |
| `append_section(text: str, title: str, items: list[str]) -> str` | `tasks.append_section(text, title, items)` | `speckit.append_section(text, title, items)` |
| `append_round(text: str, blocks: list[str]) -> tuple[int, str]` | Existing revision numbering/quoting moved into the OpenSpec adapter | `speckit.append_round(text, blocks)`; decision 3 |

Resolve delegated functions at call time. `cli.py` and `loop.py` route every
task read/mutation through `spec_format.get(m)` or its already-selected adapter;
`revise.py` delegates fresh append and replay reconstruction through it. Shared
Task records and scenario regexes remain importable from `tasks`; no new state
fields or serialized adapter are needed. Preserve existing adapter selection
call counts, including selecting once per revision invocation.

Native recognition scans `text.splitlines()` with the existing column-zero
`speckit.PHASE` rule only. An OpenSpec-style `## N. Title` is not a native
phase; like any other non-phase level-one/two heading it closes the current
phase through the existing `HEADING` rule, and subsequent checkboxes remain
outside a phase until the next native heading. This is ordinary native parsing,
not mixed-style detection. OpenSpec examples containing `## Phase 1: Example`
retain exactly the task records and mutation bytes produced today by `tasks`.

Reuse native `PHASE`, `TASK_LABEL`, `HEADING` and `tasks.TASK`, including their
existing whitespace, dotted-ID, flags and phase-boundary rules. Do not parse
Markdown fences, HTML or indentation semantically. Repeated native phase numbers,
reused native labels (compare tuples of integer root/dotted components, so
numerically equivalent spellings collide), or multiple
matches for a task key must raise `SpecError` from native parsing/mutation
rather than choosing the first. Apply these guards in `speckit.parse_tasks`
and native mutators; retain `_parse_phases` for validator source diagnostics
and ensure native admission converts structural failures to ordinary refusal.
Missing mutation targets raise rather than editing a nearby line. Native
phase-zero recognition remains legal in the standalone parser; admission alone
rejects it, including empty `## Phase 0:` sections.

Native ticking changes only the located unchecked marker to `[X]`; OpenSpec
keeps `[x]`. Splitting `T003` yields `T003.1`, `T003.2`, retaining positional
`[P]`/`[USk]` flags from the parent, scenario text supplied in each subtask and
unrelated lines. Avoid duplicate inherited flags; a conflicting supplied story
flag refuses. Extending a phase inserts after its last parsed task; appending a
phase uses `max(existing phase number) + 1`, including empty phases. New base
IDs start after the largest existing numeric T root across all parsed tasks,
including dotted children, and use `T{M+1:03d}` without truncation above 999.
Reject missing/duplicate target phases, pre-existing ambiguous IDs/keys and
collisions in the proposed result before returning a mutation.
The original `Task.key`, scenario regex and record shape remain unchanged.

### 2. Thin adapter and admission

`spec_format.py` exposes `SPECKIT` alongside `OPENSPEC`. Its native methods
delegate at call time to `speckit.py` or compose its existing pure helpers:

| Member | Native result |
| --- | --- |
| `change_dir(repo, change)` | `speckit.feature_dir(repo, change)` |
| `tasks_path(change)` | `specs/<change>/tasks.md` |
| `tasks_paths_at(repo, head, change)` | Only that live relative path, even if missing; never search archives |
| `validate(repo, change)` | Existing native validator plus native build-admission structural diagnostics |
| `scenarios(repo, change)` | `scenario_ids(change, parse_spec(spec.md text))` |
| `human_checks(repo, head, change)` | Read committed `plan.md` with Git, pass text to `speckit.human_checks` |
| `context_files(repo, change)` | Existing feature `spec.md` mapped to `proposal.md` and `specs/spec.md`, feature `plan.md` to `design.md`, and `.specify/memory/constitution.md` to `specs/constitution.md`, in that order |
| `drafter_root(change)` | `specs/<change>/`; path metadata only, not support for native drafting |
| `pr_title(change)` | `"{change}: spec-kit feature via specloop"` |
| `pr_kind` | `"spec-kit feature"` |

Missing/unreadable/undecodable committed plan checks block readiness/publication;
they are not an empty success. Readable plans with no pending checks yield an
empty list. Preserve the existing literal `## Human checks` collector, duplicate
handling in `append_design_checks`, and PR checklist rendering. Uncommitted
source-plan edits must not affect a run. Do not introduce a new requirement that
legacy fixture human-check items contain scenario IDs.

Wrap native scenario input read/decode failures in the existing `SpecError`
with the input path, so `problems_with` retains validator diagnostics and its
ordinary domain-refusal contract even for a missing native feature. Do not
convert these failures into an empty scenario dictionary.

`cli.problems_with` uses adapter validation/scenarios and `fmt.parse_tasks`
records for no-task, duplicate-key, unknown-reference and gate checks. Native
diagnostics name `## Phase N: Title`, not only `## N. Title`. Preserve validator
`path:line: message` strings. For native format, a physical phase-zero heading
or configured `phase_gates` value zero refuses with a clear phase-zero admission
message; a nonzero gate must name a phase containing parsed tasks. Do not change
`loop.run`'s gate truthiness test. Build admission diagnostics may be exposed by
a native helper rather than broadening the standalone parser's grammar.

Successful native `admit --json` adds integer `sections` and `scenarios` counts
to its version-1 admitted document: distinct parsed task phase numbers and unique
scenario IDs at the admitted head. OpenSpec's JSON stays unchanged. Refusal saves
no admitted state and retains existing partial-admission cleanup/locking.

Keep all three canonical Manifest format selection scenario names in its
MODIFIED block, including `Spec kit is refused at admission`. That scenario
now covers an invalid or missing native feature with a valid native manifest;
its unconditional unavailable-format premise is deliberately replaced by
native validation. Add successful selection as a fourth scenario. Preserve
refusal JSON, cleanup, lock inode, source refs and no-agent/no-state assertions,
and retain the saved-run scenario's post-deploy marker and pending live check.

### 3. Revision numbering belongs to the adapter

Add `append_round(text: str, blocks: list[str]) -> tuple[int, str]` to the
format protocol and both adapters. The OpenSpec implementation preserves the
current round scan, JSON quoting, ordinal text and commit bytes. The native
implementation recognizes only `speckit.PHASE` headings whose captured title
fully matches `Review round (\d+)`, chooses `K = max(K) + 1`, then delegates
to native append for phase `max phase + 1` and globally fresh T IDs. Apply the
same native physical-line/identity rules as task operations; an OpenSpec
heading is not a native round and never causes mixed-style refusal. Different rounds have
different section titles and therefore different keys even for equal feedback.

`revise._append` becomes a format-independent delegate. Pass the same selected
adapter into both fresh append and `_validate_candidate` replay reconstruction;
do not leave a native fresh append with OpenSpec candidate validation. Select it
once per revision invocation, retaining existing selection/path call counts.
Preserve `RevisionStore`, quoting of embedded newlines/checklists, original
round commit message, prepared-before-ref ordering, locking and control-state
preservation. The regression submits two different keyed feedback blocks and
observes Review round 1 then 2, distinct keys, increasing phase/T numbers, and
replay of the first key returning the first commit after the second lands.

### 4. Existing gates and seal, native context

Audit task reads used by run, accept, status, PR counts and finish so all use the
selected adapter's `parse_tasks`. Route candidate replay ticking, task splitting,
landing ticks, section strengthening/review fixes and final coverage/acceptance
appends through its matching mutators; no direct `tasks.parse`/mutation calls
remain in `cli.py`, `loop.py` or `revise.py`. Drafting stays unchanged and keeps
using OpenSpec `tasks` functions. `_copy_change` already calls `context_files`; create each returned
destination's parent directory and generalize its directory/file copy decision
by source type rather than the destination name `specs`. Keep OpenSpec
destinations unchanged. The unchanged IMPLEMENT, REVIEW and GATE_REVIEW prompts
explicitly require `proposal.md`, `design.md` and `specs/`; their reviewer rubric
can pause on an unreadable named file. The native adapter therefore supplies
literal content aliases in this disposable context directory: spec as proposal
and `specs/spec.md`, plan as design, constitution as `specs/constitution.md`.
These are copies, not generated OpenSpec documents or repository changes.
Test that every existing prompt-required context path is readable and its bytes
match the appropriate native input. Do not synthesize capability directories,
interpret native content as OpenSpec, or copy historical native features.
Prompts stay unchanged, including their legacy format wording; native task and
scenario text and these actual files supply the build context.

`section_evidence` uses the numeric phase as section, native scenario lookup and
unchanged `rsplit('/', 1)` grouping. Test phase 1 citing both `us1/as1` and
`us1/as2`, with a coverage receipt naming only `us1/as1`: the only uncovered
ID is `us1/as2`, never `us2/as1` or `us3/as1`. Preserve runtime filtering of
unknown candidate IDs and the closed-on-lookup-failure behavior. Strengthening
and gate-fix tasks use native extension; final coverage/acceptance additions use
native append with the existing supplied titles (`Coverage` or `Acceptance
fixes`). Only review-feedback appends use `Review round K`. Clarify the house
rules' formerly broad review/coverage sentence to distinguish these existing
engine mechanisms; next-phase/global-T numbering applies to every appended
phase. No edits to `evidence.py` are necessary.

Select follow-up notes by exact parsed task labels in the section. A physical
line must start `- (` followed by a label and `) `; parse that first closing
delimiter and compare the entire label to the section's parsed labels, not a
numeric prefix. Thus `T003` cannot select `T003.1` or `T0030`. Preserve order,
duplicate lines and the existing `(none)` output; malformed/unmatched lines
are not selected. Reject ambiguous native labels earlier so a note cannot
silently be assigned to the wrong phase. Preserve full followups for the PR.

`[paths] frozen` in the trusted manifest includes `specs/**` and `.specify/**`
alongside workflow/steering protections. Do not add a seal bypass or permit
agent-authored Status changes. Existing harness `seal.commit_files` operations
tick/split/append tasks and complete Status after sealed candidate checks. Tests
attempt both frozen edits beside ordinary code and verify only ordinary code
lands, then verify the harness can tick and complete without changing criteria.

### 5. Complete without a checkout

Widen `SpecFormat.finish(work, change, all_tasks_done, *, head: str | None = None)`
without changing existing OpenSpec calls or delegation. Native finish requires
an explicit head and treats `work` as the run's Git repository. Implement
`speckit.finish(repo, change, all_tasks_done, *, head)` using `git show`, existing
`mark_complete` and `seal.commit_files`; return a dictionary with `commit`.
It refuses `all_tasks_done=False` and missing/unreadable tasks/spec or an empty
or unfinished parsed task list. It modifies only `specs/<change>/spec.md`, using
the existing literal Status replacement/insertion rule, preserving other files
and line endings. Its message is exactly
`specloop: complete spec-kit feature {change}` with harness identity.

If `mark_complete` returns identical bytes, return the supplied head without a
new commit. A Complete status does not bypass unfinished-task checks. No clone,
worktree, OpenSpec command, `npx`, new dependency, archive or source-tree write
is involved. Git object creation does not itself advance the branch.

In locked `cli._finish`, retain this order:

1. Check saved `human_checks` eligibility, read head, enforce `--expect-head`.
2. Read the adapter's committed tasks path and reject unfinished tasks (native
   empty/ambiguous tasks also refuse; avoid vacuous success).
3. Validate forge/credentials and source origin using the current boundaries.
4. For native format call the adapter on `ctx.repo` and that head; advance
   `ctx.ref` with `git update-ref <ref> <candidate> <old-head>`. A race refuses
   without pushing, readiness writes or saving done; preserve the competing ref.
   For OpenSpec keep the clone/archive/commit/internal-push path unchanged.
5. Use existing `_push(..., source=ctx.m.repo)` for the external push.
6. Retain GitHub `gh pr ready`/read-back or Gitea exact WIP-prefix
   PATCH/read-back, then save done and emit the existing finished event.

Native success wording says completed and marked ready, not archived. Never
merge. Publish continues to fetch/check the base and reject workflow paths
before push/PR operations; use the native title/body only when creating a PR,
and retain adoption/reuse behavior.

The branch's committed Status records completion across push or readiness
failure. Retry at `human_checks` reads live tasks, returns the same completion
head, retries external effects and only then marks done. Status remains a
read-only live-path count after partial finish. `--expect-head` always compares
the current head: an old pre-completion head does not bypass the guard on retry.
Git credential helper forwarding and sanitization are unchanged and apply on
each external push, never to internal object construction.

### 6. Offline fixtures and acceptance

New `tests/test_speckit_build.py` copies `tests/fixtures/speckit` to a temporary
Git source; never modify the fixture or pinned upstream templates. The upstream
synthetic authored fixture has six phases, seven tasks and four scenarios. For
the required three-section build case, replace Phase 1's heading with Phase 3's
User Story 1 title numbered 1; remove the exact Phase 2 and Phase 3 headings;
renumber Phase 4/5 to 2/3; remove the exact Phase 6 heading. Require each source
heading once before transforming. This groups setup/foundation with US1 and
polish with US3 while preserving all seven task lines and four scenario IDs.
Also retain a regression admitting the unmodified six-phase copy.

Use real temporary Git histories and fake agents, coverage receipts and forge
responses, with no live services or host application. The required named tests
are `test_admit_speckit_fixture`, `test_admit_refuses_broken_fixture`,
`test_tick_split_extend_append_on_speckit_tasks`,
`test_section_evidence_groups_by_story`, `test_human_checks_from_plan`, and
`test_finish_marks_complete_without_npx`. Capture subprocess argv as the existing
command JSON tests do; native finish asserts neither `npx` nor `git clone`, not
merely a mocked archive call. Add distinct round, phase-zero, reviewer-note,
seal, fake-driver, ordering, race and recovery cases. Add adapter regressions
that compare OpenSpec parse/tick/split/extend/append results byte-for-byte with
the unchanged `tasks` functions on text containing literal native headings,
and prove a native file containing `## N. Title` still follows only native
phase boundaries. Native duplicate-identity cases refuse independently of
other heading styles. Exercise every consumer operation through the selected
adapter, including accept/candidate replay, status and PR counts. Larger test concerns may
live in new `tests/test_speckit_revisions.py` and
`tests/test_speckit_finish_recovery.py`; docs use new
`tests/test_speckit_build_docs.py`. Selection/legacy contract migrations use new
`tests/test_speckit_adapter.py`; none of these additions go into a shared test
file being changed by another issue.

Implementation verification requires `uv run pytest` and
`uv run python scripts/check_types.py`, without growing `mypy-baseline.txt`.
Preserve every existing OpenSpec finish/publish assertion and plugin assertion.
Do not treat fake-agent tests as proof of a real build.

### 7. Documentation and work-in-flight boundaries

Extend the current formats paragraph and retain the house-rules link sentence
in `docs/specloop/README.md`. Describe direct native admission, no archive,
Complete Status and frozen paths under How the engine works; qualify Publication,
explicit-forge and command-summary Finish wording. Root `README.md` enrollment
explains the trusted manifest's `[run] format`, with OpenSpec default, and clearly
separates explicit native builds from future native front-door support. Correct
its explicit-forge and Factory Finish claims too.

Add `examples/specloop/speckit.toml` with editable source/branch/profile/check
paths, native format, `specs/**`/`.specify/**` frozen, workflow exclusions, a
positive `[gates] phases` example, real verify step ID and `[coverage] step`
pointing to it. Follow `scenario_plugin.py`'s existing coverage-log convention.
Parse the complete example with the real manifest loader without executing its
commands. Say to copy trusted manifests outside the target repo.

`plugins/nightshift/README.md` must qualify both the Finish action table (current
line 27) and paragraph (current line 68): OpenSpec archives into
`openspec/specs/`; native features set `**Status**: Complete` and archive nothing.
Both push and mark ready without merging; keep confirmation/forge/credential
wording. `KNOWN-LIMITATIONS.md` explains that only the selected feature and
constitution are supplied as native context: no consolidated current spec,
automatic supersession traversal or cross-feature conflict detection. Qualify
blanket Finish claims; preserve OpenSpec-specific archive recovery tables.
Update stale support disclaimers and clarify harness addition headings in
`docs/specloop/speckit.md`, retaining its authoring rules, and the obsolete reserved-format comments in
`examples/specloop/self-hosting.toml`. Correct the same refusal description in
`docs/specloop/command-json.md` without changing unrelated JSON contracts.

Necessary shared-file exceptions: README.md, KNOWN-LIMITATIONS.md,
docs/specloop/README.md, docs/specloop/command-json.md and
plugins/nightshift/README.md cannot be avoided because this change makes their
format/finish claims false. Merge their scoped wording with #182/#191 work;
do not revert new readiness or OpenSpec binary guidance. Do not edit
`specloop/tasks.py`, `specloop/openspec.py`, `bin/**`, `nightshift/**`, plugin server code, or the
in-flight test files `tests/test_openspec_binary.py`,
`tests/test_openspec_binary_docs.py`, `tests/test_preflight.py`,
`tests/test_merge_readiness_evidence.py`, `tests/merge_readiness_fixtures.py`,
`tests/test_specloop_base_publication.py`, `tests/test_specloop_command_json.py`,
`tests/test_specloop_documentation_integrity.py`, or
`plugins/nightshift/tests/factory.test.ts`. New tests go in new files.

All added or updated test bodies belong in new files. A deletion-only relocation
from `tests/test_spec_format.py` is the one necessary shared-test exception:
three current cases explicitly demand that `get(speckit)` raise, contradicting
the requested native selection, and a fourth mocks the protocol without wiring
the new revision operation. Leaving these collected unchanged would make the
mandatory full suite fail. Do not skip, xfail, deselect, monkeypatch collection
or add production compatibility branches just to satisfy obsolete assertions.

In task 1.2 move the complete decorated definitions of
`test_complete_self_hosting_example_selects_supported_format`,
`test_manifest_construction_and_adapter_selection`, and
`test_manifest_format_speckit_refused_at_admission` into new
`tests/test_speckit_adapter.py`. Only in that new file replace unavailable-format
selection assertions with `SPECKIT` selection and adapt the refusal case to a
missing committed feature. Keep its original scenario marker/name, template
parameterization, refusal cleanup, lock inode, source refs, single JSON document,
no agent/state-save assertions and unchanged OpenSpec expectations. Permit the
now-required checkout/native validation and assert real feature diagnostics.

In task 1.3 move the complete decorated
`test_task_path_routes_through_revision_and_replay` definition into new
`tests/test_speckit_revisions.py` and wire its adapter mock's `append_round` to
`OPENSPEC.append_round` there. Retain every assertion, including call counts and
commit bytes, alongside the native two-round regression. Import needed existing
fixtures/helpers explicitly without importing collected test functions; reuse
their original behavior. Keep shared fixtures, imports and all other definitions
in the source file unchanged. Review the source deletion and destination copy
together; only the three obsolete expectation migrations and single mock wiring
are permitted differences, with no lost test parameterization or markers.

Reconcile any #191 changes to those four definitions from the current landed
source before moving them, retaining its binary-specific assertions and helpers.
Do not copy a stale version over incoming changes. This is relocation into new
test files as requested, not new test development in the shared file; if #191
already relocated a case, update that new location instead of deleting twice.

Documentation tests use unique exact column-zero physical headings through the
next same-or-higher heading; a missing or duplicate start refuses selection.
Use exact physical fence delimiters to select runnable examples and fail on
missing/ambiguous pairs. Do not interpret containers, HTML or indentation.
Parse TOML and CLI examples without dispatch, check local links and necessary
tokens/structure, and avoid exact explanatory-prose assertions.

## Risks / Trade-offs

- Explicit format dispatch preserves permissive OpenSpec behavior. Native
  identity checks alone refuse repeated phase numbers, reused T labels and
  ambiguous keys; matching native example lines get no exemption. A numeric
  OpenSpec heading in a native file still closes a phase under the existing
  heading-boundary rule, so task placement matters without mixed-style detection.
- Native context is flow-forward history, not a maintained global specification.
  Authors name superseded features; this issue does not resolve contradictions
  across delivered feature records.
- Keep completion/ref guards small. A committed native completion can survive a
  failed push or ready request, so branch state, not a new state field, drives
  retries. Test this separately from the initial completion session.
- Seven tasks in two sections isolate mutation, admission, revision, gates,
  completion, recovery and docs. Target roughly 300 changed lines per session;
  reuse test helpers and parametrize failures, without weakening tests to fit.
- #191 may change the OpenSpec binary contract during implementation. Preserve
  that incoming implementation and assertions; native finish remains independent
  of whichever OpenSpec executable is installed. No binary installer work here.
- The old refusal assertions cannot coexist with successful native selection.
  The deletion-only source relocation is unavoidable; all changed test bodies
  live in new files and retain unrelated assertions. Reconcile that small source
  hunk with #191 rather than weakening selection or leaving a failing suite.

## Human checks

- [ ] Matt or the orchestrator admits the fixture feature in a scratch repository
  with a native trusted manifest and real implementer/reviewer profiles, lets a
  story phase pass its agent gate, publishes, completes the PR's human checks and
  finishes. Confirm Status Complete, retained feature files, unchanged acceptance
  criteria and no archive; record run/PR identity and touchpoint count on #184;
  `specloop-interface/native-build-gates/real-story-build`.

CI cannot establish real agent quality, provider permissions or remote forge
behavior. This is an unchecked rollout item, not an implementing-agent task.
Retain #187's separate deployed legacy-run resume checklist and tracking links;
this draft does not claim that check has been performed.

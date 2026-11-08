# spec-format Specification

## Purpose
Separate engine orchestration from change-format operations while retaining
OpenSpec behavior and compatibility with existing admitted runs.

## Requirements

### Requirement: Format seam

`specloop/spec_format.py` SHALL expose `OPENSPEC`, `SPECKIT`, `get(m: Manifest)`
and a typed protocol containing `change_dir`, `tasks_path`, `tasks_paths_at`,
`validate`, `scenarios`, `human_checks`, `context_files`, `drafter_root`,
`finish`, `pr_title`, `pr_kind`, `parse_tasks`, `tick`, `split`, `extend_section`,
`append_section` and `append_round`. `finish` SHALL retain its
existing positional arguments and add optional keyword-only `head`; native
completion SHALL require that committed head. `append_round(text, blocks)`
SHALL return `(round_number, rewritten_text)`. The OpenSpec adapter SHALL
resolve delegated OpenSpec functions at call time and retain return values
and exception identities.

The exact-member restriction changes to permit manifest-selected task operations,
adapter-owned revision numbering and checkout-free native completion; existing
OpenSpec calls keep their original signatures at the backend boundary. This
protocol extension does not modify Engine routing compatibility or OpenSpec
task parsing/mutation behavior.

#### Scenario: Legacy paths and publication labels
- **WHEN** the OpenSpec adapter is used for sample change `sample-change`
- **THEN** `change_dir(repo, change)` is `repo / "openspec/changes/sample-change"`, `tasks_path(change)` is `openspec/changes/sample-change/tasks.md`, and `drafter_root(change)` is `openspec/changes/sample-change/`
- **THEN** `pr_kind` is `OpenSpec change` and `pr_title(change)` is `sample-change: OpenSpec change via specloop`

#### Scenario: Delegation stays late bound
- **WHEN** `specloop.openspec.validate`, `.scenarios` or `.archive` is replaced after the singleton is created
- **THEN** the corresponding adapter method calls that replacement with the original arguments, including `all_tasks_done`, and preserves results and exceptions
- **THEN** the OpenSpec executable contract, environment, timeout and transient-versus-domain error classification remain unchanged by this change

### Requirement: Committed task discovery

The OpenSpec adapter SHALL supply task paths at a committed head with the
existing live-first, exact-change archive and missing-file behavior.

#### Scenario: Live and archived task precedence
- **WHEN** a committed tree contains a live tasks file and archives, or contains only dated archives for the requested change alongside similarly named changes
- **THEN** the first returned path is the live file when present, otherwise the lexically latest full-match dated archive for the exact escaped change name
- **THEN** archive candidates are ordered newest-name first and do not include another change's tasks

#### Scenario: Missing tasks retain read failure
- **WHEN** neither live tasks nor a matching dated archive exists at the requested head
- **THEN** the adapter returns the live task path as its sole candidate so the existing subsequent Git read fails
- **THEN** status retains its per-run error isolation and does not mutate state or retry finish

### Requirement: Context and human checks

The OpenSpec adapter SHALL preserve existing context selection and committed
design-check collection, without introducing semantic Markdown parsing or
changing publication's deduplication and rendering.

#### Scenario: Existing context files are copied
- **WHEN** context is assembled for a change with any subset of proposal.md, design.md and specs/
- **THEN** the adapter returns only existing entries in that order with the same context-relative destinations, retaining specs/ as a directory
- **THEN** implementation, gate and review context retains the same copied content and missing-entry behavior

#### Scenario: Literal committed checks remain pending
- **WHEN** the committed design contains supported selected headings and unchecked lines, including nested selected headings or examples inside fences, HTML or indentation
- **THEN** the adapter applies the existing splitlines, ASCII-edge-trimming, heading-depth and literal-checkbox rules described in the design, in document order
- **THEN** examples remain pending, uncommitted source text is ignored, and existing readiness/PR-body ordering, deduplication and rendering remain unchanged

#### Scenario: Unreadable design blocks readiness
- **WHEN** Git lookup, blob reading or decoding fails while collecting design checks
- **THEN** the same diagnostic prevents readiness rather than substituting an empty checklist
- **THEN** an established absent design or a readable design with no matching checks still returns an empty list

### Requirement: Engine routing compatibility

The four consumer modules `cli.py`, `loop.py`, `revise.py` and `propose.py`
SHALL route the extracted operations through the selected adapter, containing
no `openspec/changes/` literal and no direct `openspec.` attribute call.
Except for the pinned OpenSpec backend invocation, version gate, strict-validator upgrade and error classification explicitly specified by this change, and the shared planned-file exclusion below, OpenSpec rendered prompts, messages, argv, commits, state transitions and recovery behavior SHALL remain unchanged.

Shared planned-file extraction (`propose.paths` and `edited_paths`) SHALL
exclude tokens beginning `specs/` as well as `openspec/`, for every format
and with or without a root argument. This deliberately changes OpenSpec
planned-file behavior because the shared rootless caller has no manifest;
both prefixes are reserved specification roots. All other OpenSpec routing,
parsing, exclusions, argv, messages and state behavior SHALL remain unchanged.

#### Scenario: Drafting and revision retain behavior
- **WHEN** an OpenSpec draft is linted, checked for overlaps, staged or revised, or another run contributes to the in-flight file map
- **THEN** the relevant manifest's adapter supplies paths and operations while existing confinement diagnostics, prompt text, git add argv, review ordering, run filtering and revision idempotency remain unchanged
- **THEN** harness task/scenario parsing and the existing exclusion and hands-off rules do not change except for the shared `specs/` exclusion; external strict parsing follows the pinned OpenSpec release
- **THEN** mixed canonical/legacy roots retain the same in-flight map and read order except for the shared `specs/` exclusion, without state mutation, and successful drafting retains its notification and manifest/change/branch command arguments; the two named `SimpleNamespace` fixture inputs may gain `format="openspec"` as specified in #187 task 1.3, with every existing assertion preserved apart from this change's explicit validator-contract migrations

#### Scenario: Publication and interrupted finish retain behavior
- **WHEN** an OpenSpec run publishes or finishes, including an expected-head mismatch or retry after an archive commit survives a failed push or ready operation
- **THEN** adapter routing retains exact title/body and commit text, checks the expected head before inspecting tasks, and refuses unfinished tasks before archiving
- **THEN** archive reuse, status reads, GitHub argv, Gitea readiness verification, pushes and durable state transitions retain their existing ordering and outcomes

#### Scenario: Consumers contain no direct format operations
- **WHEN** the four consumer source files are checked after extraction
- **THEN** none contains an `openspec/changes/` literal or direct `openspec.` attribute call; format path construction and delegated calls reside in the adapter, with openspec.py retained as its backend and changed only for the specified standalone invocation, version gate and error classification
- **THEN** a drafting-template path placeholder renders the exact original prompt text, without splitting literals to evade the check

#### Scenario: OpenSpec planned edits exclude reserved specs root
- **WHEN** an OpenSpec change's task text names backticked `specs/helper.py` and `src/helper.py` as edits
- **THEN** shared extraction through `propose.paths` and `edited_paths` no longer lists `specs/helper.py` as a planned edit, while `src/helper.py` is still listed, with or without a root argument
- **THEN** the same lexical exclusion applies to spec-kit input and preserves the existing `openspec/` exclusion

### Requirement: Manifest format selection

The trusted manifest SHALL expose `format` with default `"openspec"` and
preserve explicit `"speckit"` through loading and template overrides.
`get(m)` SHALL return `OPENSPEC` for OpenSpec and `SPECKIT` for spec-kit.
Selection SHALL NOT add run-state fields, serialize an adapter or migrate
existing runs. This replaces the temporary native-format unavailability
refusal because the native build adapter is now implemented.
The existing refusal scenario remains under its original name for invalid or
missing native features: only the unconditional unavailable-format diagnostic
and pre-validation refusal change. Its JSON, cleanup and no-agent guarantees
remain in force.

#### Scenario: Omitted and explicit OpenSpec agree
- **WHEN** a manifest omits `[run] format` or explicitly sets `format = "openspec"`
- **THEN** the effective value is `openspec` and selection returns the same adapter with the same admission and runtime behavior
- **THEN** existing direct Manifest construction remains valid

#### Scenario: Spec kit selects the native adapter
- **WHEN** a manifest sets `format = "speckit"`, including after change/branch overrides
- **THEN** selection returns `SPECKIT` and admission invokes native validation rather than returning `spec-kit format is not available yet`
- **THEN** an invalid or missing native feature still receives ordinary refusal JSON and cleanup without invoking an agent or saving admitted state

#### Scenario: Spec kit is refused at admission
- **WHEN** a valid manifest sets `format = "speckit"`, including after change/branch template overrides, but its committed native feature is missing or invalid, and is checked by `problems_with` or `nightshift admit --json`
- **THEN** manifest parsing succeeds and `problems_with` invokes native validation and returns the feature diagnostics instead of `spec-kit format is not available yet`
- **THEN** admission exits 1 with one version-1 `outcome: refused` document whose reasons include those diagnostics, invokes no agent, cleans partial admission while preserving its lock inode and source refs, and saves no admitted state

#### Scenario: Saved OpenSpec runs remain compatible
<!-- nightshift:post-deploy -->
- **WHEN** old OpenSpec manifests/state are loaded by new code or new OpenSpec run data is read by previous code
- **THEN** omitted format still means OpenSpec, explicit format remains ordinary TOML ignored by the previous loader, and state/task/revision data retains its prior shape and meaning
- **THEN** no state migration, adapter serialization or manifest rewrite is required; offline tests do not claim the separate deployed-resume check was performed

### Requirement: Native adapter operations

`SPECKIT` SHALL delegate native feature paths, validation, scenario extraction,
human checks and completion to `specloop.speckit` at call time. It SHALL expose
the paths and publication strings specified in the design and SHALL NOT look
for native archives or require an OpenSpec executable.

#### Scenario: Native paths and labels
- **WHEN** the selected adapter describes `042-add-a-gate`
- **THEN** its change directory is `specs/042-add-a-gate`, tasks path is `specs/042-add-a-gate/tasks.md`, drafter root is `specs/042-add-a-gate/`, title is `042-add-a-gate: spec-kit feature via specloop` and kind is `spec-kit feature`
- **THEN** `tasks_paths_at` returns only the live tasks path, even when absent, so a subsequent Git read fails rather than finding an archive

#### Scenario: Native delegation remains offline
- **WHEN** native validation or scenario extraction runs, including with backend helpers replaced after adapter construction
- **THEN** the adapter calls the current native helpers, retains their results and diagnostics, and spawns no OpenSpec validator or `npx`
- **THEN** unreadable or undecodable scenario input raises the existing SpecError with its path, preserving ordinary admission refusal and never implying an empty successful lookup

### Requirement: Native context and human checks

The native adapter SHALL provide the selected feature's spec and plan and the
repository constitution as existing context files, in that order, with
context-relative destinations `spec.md`, `plan.md` and
`.specify/memory/constitution.md`. It SHALL no longer supply proposal.md,
design.md, specs/spec.md or specs/constitution.md aliases because native
consumers now name real files. It SHALL read human checks from committed
plan.md, use the existing literal collector and preserve publication's
deduplication/rendering. Missing or unreadable committed plans SHALL block
rather than imply no checks.

#### Scenario: Selected feature context
- **WHEN** implementation, task review, phase review or arbitration assembles native context
- **THEN** native spec content is copied to spec.md, plan content to plan.md and constitution content to .specify/memory/constitution.md, without transitional OpenSpec aliases
- **THEN** copies retain original native bytes, with no invented capability-directory assumption, OpenSpec document conversion or automatic historical feature traversal
- **THEN** OpenSpec retains its existing context destinations and directory copying

#### Scenario: Plan checks use the committed revision
- **WHEN** a committed plan contains unchecked lines under exact ## Human checks, nested headings, repeated sections and literal examples, while source worktree text differs
- **THEN** the collector returns the committed pending lines in order with duplicates, following existing physical-line boundaries and excluding checked/empty/unrelated items
- **THEN** readiness and the PR body retain existing deduplication, manifest exemptions and checklist rendering

#### Scenario: Plan failure blocks readiness
- **WHEN** the committed plan is missing, Git lookup/read fails or UTF-8 decoding fails
- **THEN** readiness/publication fails with a diagnostic instead of dropping human checks
- **THEN** a readable plan without pending selected lines returns an empty list

### Requirement: Manifest selected task operations

Task operations SHALL dispatch only through the manifest-selected adapter.
OPENSPEC SHALL delegate at call time to unchanged tasks functions; SPECKIT
SHALL delegate to speckit.parse_tasks and native mutation helpers. The build
consumers SHALL use adapter parsing, mutations and review-round reconstruction.
Proposal lint and overlap checks SHALL also use adapter parsing, replacing
#188's explicit temporary exclusion of drafting from that routing.
No implementation SHALL detect or refuse mixed heading styles. tasks.py SHALL
remain unchanged; Engine routing compatibility SHALL retain its guarantees
except for the shared planned-file exclusion explicitly modified above.

#### Scenario: OpenSpec ignores native heading declarations
- **WHEN** an OpenSpec task document contains literal ## Phase 1: Example lines, including inside examples, and is parsed, ticked, split, extended or appended through OPENSPEC
- **THEN** task records and returned mutation bytes exactly match the unchanged corresponding tasks functions, including their numeric-section recognition, checkbox spelling, numbering and error behavior
- **THEN** adapter parse_tasks delegates to tasks.parse, and tick, split, extend_section and append_section delegate to their same-named tasks functions
- **THEN** native headings neither select SPECKIT nor cause mixed-style refusal, and late replacement of a delegated tasks function is observed with its original arguments, return value and exception identity

#### Scenario: Native ignores OpenSpec phase declarations
- **WHEN** a native task document contains ## N. Title headings alongside literal native phase headings and is read or mutated through SPECKIT
- **THEN** only native PHASE headings create phases; numeric OpenSpec headings close the current phase under the existing same-or-higher heading rule and tasks outside native phases remain unrecognized
- **THEN** the document is not refused for mixing heading styles, native edits round-trip through speckit.parse_tasks, and late replacement of a delegated native helper is observed
- **THEN** repeated native phase numbers, reused numeric T-label identities and ambiguous native keys still refuse according to native rules

#### Scenario: Consumers use selected task operations
- **WHEN** admission, run, accept, status, PR body construction, finish, revision, proposal lint or overlap detection needs to read or mutate tasks
- **THEN** the manifest-selected adapter supplies parsing, ticking, splitting, extension, append or review-round reconstruction as applicable, including candidate replay and revision replay
- **THEN** loop.py contains no heading-style detection or format-specific task branch, drafting now uses the selected parser, and no run-state field or serialized adapter is added

### Requirement: Format aware build prompts

All four build consumers SHALL select IMPLEMENT, REVIEW, GATE_REVIEW and ARBITER
variants through `specloop/prompts.py` using manifest format, without loop format
detection. Native variants SHALL name real spec, plan and constitution paths.
Scenario text, the scenario-ID testing sentence, schemas, review bases and
arbitration rules SHALL remain unchanged. Omitted or explicit OpenSpec format
SHALL preserve the original rendered bytes.

#### Scenario: Native consumers read real files
- **WHEN** implementation, task review, phase review or arbitration renders its prompt for a native feature
- **THEN** every named native document resolves to spec.md, plan.md or .specify/memory/constitution.md in its context, with no dependency on the removed aliases
- **THEN** task.md or section.md retains full scenario text and the implementation scenario-ID sentence is unchanged

#### Scenario: OpenSpec consumers retain exact prompts
- **WHEN** the same four consumers render with omitted or explicit openspec format
- **THEN** every existing exact OpenSpec prompt assertion passes unchanged, with original proposal.md, design.md and specs/ context destinations

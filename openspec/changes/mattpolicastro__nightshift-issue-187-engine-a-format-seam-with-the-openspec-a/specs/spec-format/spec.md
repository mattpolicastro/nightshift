## Purpose

Separate engine orchestration from change-format operations while retaining
OpenSpec behavior and compatibility with existing admitted runs.

## ADDED Requirements

### Requirement: Format seam

`specloop/spec_format.py` SHALL expose `OPENSPEC`, `get(m: Manifest)` and a
typed protocol with exactly `change_dir`, `tasks_path`, `tasks_paths_at`,
`validate`, `scenarios`, `human_checks`, `context_files`, `drafter_root`,
`finish`, `pr_title` and `pr_kind`, using the signatures in the design.
The OpenSpec adapter SHALL resolve delegated OpenSpec functions at call time
and retain their return values and exception identities.

#### Scenario: Legacy paths and publication labels
- **WHEN** the OpenSpec adapter is used for sample change `sample-change`
- **THEN** `change_dir(repo, change)` is `repo / "openspec/changes/sample-change"`, `tasks_path(change)` is `openspec/changes/sample-change/tasks.md`, and `drafter_root(change)` is `openspec/changes/sample-change/`
- **THEN** `pr_kind` is `OpenSpec change` and `pr_title(change)` is `sample-change: OpenSpec change via specloop`

#### Scenario: Delegation stays late bound
- **WHEN** `specloop.openspec.validate`, `.scenarios` or `.archive` is replaced after the singleton is created
- **THEN** the corresponding adapter method calls that replacement with the original arguments, including `all_tasks_done`, and preserves results and exceptions
- **THEN** the original validator npx argv, environment, timeout and transient-versus-domain error classification remain unchanged

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
OpenSpec rendered prompts, messages, argv, commits, state transitions and
recovery behavior SHALL remain unchanged.

#### Scenario: Drafting and revision retain behavior
- **WHEN** an OpenSpec draft is linted, checked for overlaps, staged or revised, or another run contributes to the in-flight file map
- **THEN** the relevant manifest's adapter supplies paths and operations while existing confinement diagnostics, prompt text, git add argv, review ordering, run filtering and revision idempotency remain unchanged
- **THEN** task/scenario parsing and the existing exclusion and hands-off rules do not change
- **THEN** mixed canonical/legacy roots retain the same in-flight map and read order without state mutation, and successful drafting retains its notification and manifest/change/branch command arguments; the two named `SimpleNamespace` fixture inputs may gain `format="openspec"` as specified in task 1.3, with every existing assertion preserved

#### Scenario: Publication and interrupted finish retain behavior
- **WHEN** an OpenSpec run publishes or finishes, including an expected-head mismatch or retry after an archive commit survives a failed push or ready operation
- **THEN** adapter routing retains exact title/body and commit text, checks the expected head before inspecting tasks, and refuses unfinished tasks before archiving
- **THEN** archive reuse, status reads, GitHub argv, Gitea readiness verification, pushes and durable state transitions retain their existing ordering and outcomes

#### Scenario: Consumers contain no direct format operations
- **WHEN** the four consumer source files are checked after extraction
- **THEN** none contains an `openspec/changes/` literal or direct `openspec.` attribute call; format path construction and delegated calls reside in the adapter, with openspec.py retained as its unchanged backend
- **THEN** a drafting-template path placeholder renders the exact original prompt text, without splitting literals to evade the check

### Requirement: Manifest format selection

The trusted manifest SHALL expose `format` with default `"openspec"` and
preserve explicit `"speckit"` through loading and template overrides.
`get(m)` SHALL return `OPENSPEC` for OpenSpec and raise existing `SpecError`
with `spec-kit format is not available yet` for spec-kit. Admission SHALL
report that refusal through its existing domain outcome.

#### Scenario: Omitted and explicit OpenSpec agree
- **WHEN** a manifest omits `[run] format` or explicitly sets `format = "openspec"`
- **THEN** the effective value is `openspec` and selection returns the same adapter with the same admission and runtime behavior
- **THEN** existing direct Manifest construction remains valid

#### Scenario: Spec kit is refused at admission
- **WHEN** a valid manifest sets `format = "speckit"`, including after change/branch template overrides, and is checked by `problems_with` or `nightshift admit --json`
- **THEN** parsing succeeds and `problems_with` returns `spec-kit format is not available yet` without invoking the validator
- **THEN** admission exits 1 with one version-1 `outcome: refused` document whose reasons include that diagnostic, invokes no agent, cleans partial admission and saves no admitted state

#### Scenario: Saved OpenSpec runs remain compatible
<!-- nightshift:post-deploy -->
- **WHEN** old OpenSpec run manifests/state are loaded by the new code or OpenSpec run data saved by the new code is read by the previous code
- **THEN** omitted format still means OpenSpec, explicit format remains ordinary TOML ignored by the previous loader, and state/task/revision data retains its prior shape and meaning
- **THEN** the change requires no state migration, adapter serialization or manifest rewrite for existing runs

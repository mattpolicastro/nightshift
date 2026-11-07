## Purpose

Preserve format routing and late-bound delegation through the standalone OpenSpec cut-over.

## MODIFIED Requirements

### Requirement: Format seam

`specloop/spec_format.py` SHALL expose `OPENSPEC`, `get(m: Manifest)` and a
typed protocol with exactly `change_dir`, `tasks_path`, `tasks_paths_at`,
`validate`, `scenarios`, `human_checks`, `context_files`, `drafter_root`,
`finish`, `pr_title` and `pr_kind`, using the signatures established by #187 in specloop/spec_format.py.
The OpenSpec adapter SHALL resolve delegated OpenSpec functions at call time
and retain their return values and exception identities.

#### Scenario: Legacy paths and publication labels
- **WHEN** the OpenSpec adapter is used for sample change `sample-change`
- **THEN** `change_dir(repo, change)` is `repo / "openspec/changes/sample-change"`, `tasks_path(change)` is `openspec/changes/sample-change/tasks.md`, and `drafter_root(change)` is `openspec/changes/sample-change/`
- **THEN** `pr_kind` is `OpenSpec change` and `pr_title(change)` is `sample-change: OpenSpec change via specloop`

#### Scenario: Delegation stays late bound
- **WHEN** `specloop.openspec.validate`, `.scenarios` or `.archive` is replaced after the singleton is created
- **THEN** the corresponding adapter method calls that replacement with the original arguments, including `all_tasks_done`, and preserves results and exceptions
- **THEN** the adapter uses the pinned standalone backend and timeout-only transient classification specified by openspec-binary and Validator availability classification, retaining the existing environment and per-subprocess timeout

### Requirement: Engine routing compatibility

The four consumer modules `cli.py`, `loop.py`, `revise.py` and `propose.py`
SHALL route the extracted operations through the selected adapter, containing
no `openspec/changes/` literal and no direct `openspec.` attribute call.
Except for the pinned OpenSpec backend invocation, version gate, strict-validator upgrade and error classification explicitly specified by this change, OpenSpec rendered prompts, messages, argv, commits, state transitions and recovery behavior SHALL remain unchanged.

#### Scenario: Drafting and revision retain behavior
- **WHEN** an OpenSpec draft is linted, checked for overlaps, staged or revised, or another run contributes to the in-flight file map
- **THEN** the relevant manifest's adapter supplies paths and operations while existing confinement diagnostics, prompt text, git add argv, review ordering, run filtering and revision idempotency remain unchanged
- **THEN** harness task/scenario parsing and the existing exclusion and hands-off rules do not change; external strict parsing follows the pinned OpenSpec release
- **THEN** mixed canonical/legacy roots retain the same in-flight map and read order without state mutation, and successful drafting retains its notification and manifest/change/branch command arguments; the two named `SimpleNamespace` fixture inputs may gain `format="openspec"` as specified in #187 task 1.3, with every existing assertion preserved apart from this change's explicit validator-contract migrations

#### Scenario: Publication and interrupted finish retain behavior
- **WHEN** an OpenSpec run publishes or finishes, including an expected-head mismatch or retry after an archive commit survives a failed push or ready operation
- **THEN** adapter routing retains exact title/body and commit text, checks the expected head before inspecting tasks, and refuses unfinished tasks before archiving
- **THEN** archive reuse, status reads, GitHub argv, Gitea readiness verification, pushes and durable state transitions retain their existing ordering and outcomes

#### Scenario: Consumers contain no direct format operations
- **WHEN** the four consumer source files are checked after extraction
- **THEN** none contains an `openspec/changes/` literal or direct `openspec.` attribute call; format path construction and delegated calls reside in the adapter, with openspec.py retained as its backend and changed only for the specified standalone invocation, version gate and error classification
- **THEN** a drafting-template path placeholder renders the exact original prompt text, without splitting literals to evade the check

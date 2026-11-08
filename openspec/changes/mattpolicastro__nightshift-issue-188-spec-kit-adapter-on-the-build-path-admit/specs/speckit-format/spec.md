## Purpose

Extend the existing native parser with the task and completion operations
needed by the build adapter, preserving its physical-line grammar.

## ADDED Requirements

### Requirement: Native task mutation

Shared task operations SHALL recognize native phase headings using the
design's deterministic physical-line dispatch, return existing `Task` records
and round-trip native task edits through `speckit.parse_tasks`. Native ticking
SHALL use `[X]`; OpenSpec ticking SHALL retain `[x]`. Mutation SHALL preserve
unrelated lines, native flags and task identity rules.

#### Scenario: Tick split extend and append
- **WHEN** the three-phase fixture copy is ticked, T003 is split, a phase is extended and Review round 1 is appended
- **THEN** ticking changes the selected checkbox to `[X]`, split children include `T003.1` with inherited positional flags, extension uses globally fresh T roots and append creates `## Phase 4: Review round 1`
- **THEN** parsing the result retains task records, flags and scenario references; labels above T999 are not truncated and unrelated text is unchanged

#### Scenario: Ambiguous mutation refuses
- **WHEN** task text mixes recognized native and numeric section headings, repeats a native phase number or numeric label identity, has multiple matches for the selected key, lacks the target, or a split supplies conflicting story ownership
- **THEN** the operation refuses without returning a partially rewritten document or choosing the first matching line
- **THEN** collisions introduced by a proposed split/extension/append also refuse before returning the mutation
- **THEN** matching lines in fences or HTML receive no exemption, and empty phases and dotted task roots participate in next-number discovery

### Requirement: Native completion commit

`speckit.finish` SHALL require a committed head and completed nonempty task
records, apply existing `mark_complete` to the committed spec and produce a
harness-owned commit changing only that spec with message
`specloop: complete spec-kit feature {change}`. It SHALL use Git object
operations, neither clone nor worktree nor `npx`, leave ref advancement to the
caller and retain live feature files without archiving.

#### Scenario: Complete with no external validator
- **WHEN** finish is called for a native feature whose parsed tasks are all ticked
- **THEN** its only content change sets matching Status lines to `**Status**: Complete`, or inserts the line according to `mark_complete`, preserving unrelated spec text and line endings
- **THEN** it returns the completion commit with the named message and harness identity, leaves tasks/plan/constitution and source checkout untouched, and recorded argv includes no `npx`, clone or worktree command

#### Scenario: Incomplete or unreadable feature refuses
- **WHEN** completion is requested with `all_tasks_done=False`, unfinished/empty/ambiguous tasks, absent head, unreadable spec/tasks or a spec without a title
- **THEN** it refuses without advancing a ref, pushing, archiving or marking ready, even when the spec already says Complete

#### Scenario: Complete status is idempotent
- **WHEN** the same completed feature is finished again after its Status commit
- **THEN** the adapter returns the supplied head without creating another completion commit and still verifies completed task records
- **THEN** live-path task discovery remains available for status and retry

## MODIFIED Requirements

### Requirement: Local feature API

`specloop.speckit` SHALL retain its eight existing pure-Python parser, validator
and text interfaces without new dependencies or external calls from those
interfaces. `feature_dir(repo, change)` SHALL return `repo / "specs" / change`.
Additional build helpers MAY perform the explicitly specified Git completion
operations. Importing the module SHALL have no I/O or dispatch side effects.
Native integration SHALL occur through the format adapter/shared task seam,
without changing existing OpenSpec or daemon behavior. This replaces the
parser-only prohibition on engine integration to enable the requested build path.

#### Scenario: Resolve a feature without integration
- **WHEN** a caller imports the module or resolves `042-add-a-gate` under a repository path
- **THEN** the result is that repository's `specs/042-add-a-gate` path without filesystem access
- **THEN** import/lookup does not dispatch a command or select a format, and existing pure helpers retain their offline contracts

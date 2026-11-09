## Purpose

Retain format-seam compatibility while explicitly qualifying optional discovery-base pinning.

## MODIFIED Requirements

### Requirement: Engine routing compatibility

The four consumer modules `cli.py`, `loop.py`, `revise.py` and `propose.py`
SHALL route the extracted operations through the selected adapter, containing
no `openspec/changes/` literal and no direct `openspec.` attribute call.
Except for the pinned OpenSpec backend invocation, version gate, strict-validator upgrade and error classification explicitly specified by this change, and the shared planned-file exclusion below, OpenSpec rendered prompts, messages, argv, commits, state transitions and recovery behavior SHALL remain unchanged for unpinned calls. An explicitly supplied discovery-base pin under spec-drafting is a narrow exception: a pinned call SHALL use its already resolved commit. This exception SHALL NOT change OpenSpec prompts, ordinary resolution, flat draft-workspace paths, task operations or successful result shapes.

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

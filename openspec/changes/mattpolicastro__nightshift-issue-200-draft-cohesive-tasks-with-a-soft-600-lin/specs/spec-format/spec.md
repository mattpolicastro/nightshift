## Purpose

Permit the scoped sizing-policy prompt migration while preserving OpenSpec format
routing, exact unrelated prompt bytes and existing execution compatibility.

## ADDED Requirements

### Requirement: Scoped prompt compatibility migration

Prompt compatibility tests SHALL cover the authorized policy changes while
preserving exact-byte protection for unrelated content. They SHALL reconstruct
legacy bytes through exact test-local substitutions and retain original rendering
inputs, test names, scenario markers and digests. Build-prompt hashes and unrelated
assertions SHALL remain unchanged.

#### Scenario: Drafting hash preserves unrelated bytes
- **WHEN** the OpenSpec drafting prompt adopts the cohesive-task soft-600 policy
- **THEN** migration is confined to tests/test_spec_format.py::test_drafting_prompt_retains_exact_bytes and its test-local literals
- **THEN** the existing exact-byte test asserts each authorized new sizing passage occurs exactly once and its obsolete counterpart is absent, restores only those exact passages to their former bytes in a test-local copy, and checks the original complete-prompt SHA-256 digest
- **THEN** missing or duplicate replacement passages fail; no broad section removal, whitespace normalization, skipped assertion or replacement of the legacy digest hides unrelated changes
- **THEN** prompt-contract tests separately assert the new policy, while all other assertions in tests/test_spec_format.py and all build-prompt hashes remain unchanged

#### Scenario: Review additions preserve unrelated bytes
- **WHEN** the critic and feasibility prompts gain advisory-size guidance
- **THEN** migration uses test_original_review_prompts_and_native_checklist_are_preserved in tests/test_speckit_drafting.py
- **THEN** the existing review-prompt test asserts each new clause occurs exactly once, removes only that exact addition in a test-local copy and checks the retained original digests
- **THEN** checklist composition, count thresholds, native structure and all unrelated prompt bytes remain protected

## MODIFIED Requirements

### Requirement: Engine routing compatibility

The four consumer modules `cli.py`, `loop.py`, `revise.py` and `propose.py` SHALL route
the extracted operations through the selected adapter, containing no `openspec/changes/`
literal and no direct `openspec.` attribute call.

#### Scenario: Drafting and revision retain behavior
- **WHEN** an OpenSpec draft is linted, checked for overlaps, staged or revised, or another run contributes to the in-flight file map
- **THEN** the relevant manifest's adapter supplies paths and operations while existing confinement diagnostics, git add argv, review ordering, run filtering and revision idempotency remain unchanged; prompt text changes only for the explicitly scoped sizing-policy migration below
- **THEN** harness task/scenario parsing and the existing exclusion and hands-off rules do not change except for the shared `specs/` exclusion; external strict parsing follows the pinned OpenSpec release
- **THEN** mixed canonical/legacy roots retain the same in-flight map and read order except for the shared `specs/` exclusion, without state mutation, and successful drafting retains its notification and manifest/change/branch command arguments; the two named `SimpleNamespace` fixture inputs retain the permitted `format="openspec"` migration specified in #187 task 1.3, with every existing assertion preserved apart from previously specified validator-contract migrations and the scoped prompt-compatibility migration here

Except for the pinned OpenSpec backend invocation, version gate, strict-validator
upgrade and error classification previously specified, and the shared
planned-file exclusion below and the sizing-policy prompt migration specified here,
OpenSpec rendered prompts, messages, argv, commits, state transitions and recovery
behavior SHALL remain unchanged for unpinned calls. An
explicitly supplied discovery-base pin under spec-drafting is a narrow exception: a
pinned call SHALL use its already resolved commit. This exception SHALL NOT change
OpenSpec prompts, ordinary resolution, flat draft-workspace paths, task operations or
successful result shapes.

#### Scenario: Publication and interrupted finish retain behavior
- **WHEN** an OpenSpec run publishes or finishes, including an expected-head mismatch or retry after an archive commit survives a failed push or ready operation
- **THEN** adapter routing retains exact title/body and commit text, checks the expected head before inspecting tasks, and refuses unfinished tasks before archiving
- **THEN** archive reuse, status reads, GitHub argv, Gitea readiness verification, pushes and durable state transitions retain their existing ordering and outcomes

The sizing-policy migration SHALL permit only cohesive single-session task guidance,
soft 600-combined-line planning, dependent ceiling language, explicit fewer-task and
no-padding guidance, advisory-size critic guidance and the feasibility clarification
that estimated size alone is not impossibility. This exception SHALL NOT authorize
changes to root confinement, task/scenario structure or coverage, count thresholds,
review ordering, correction rules, execution budgets or any other prompt content.
It is separate from the discovery-base pin exception, which still SHALL NOT change
prompts. This replaces the original exact-prompt promise only for those policy
passages; exact build-consumer prompts SHALL remain protected.

All other OpenSpec routing, parsing, exclusions, argv, messages and state behavior SHALL
remain unchanged.

#### Scenario: Consumers contain no direct format operations
- **WHEN** the four consumer source files are checked after extraction
- **THEN** none contains an `openspec/changes/` literal or direct `openspec.` attribute call; format path construction and delegated calls reside in the adapter, with openspec.py retained as its backend and changed only for the specified standalone invocation, version gate and error classification
- **THEN** a drafting-template path placeholder renders the original root and all original prompt text except the explicitly scoped sizing-policy passages; restoring only those passages reproduces the exact original rendered bytes, without splitting literals to evade the check

#### Scenario: OpenSpec planned edits exclude reserved specs root
- **WHEN** an OpenSpec change's task text names backticked `specs/helper.py` and `src/helper.py` as edits
- **THEN** shared extraction through `propose.paths` and `edited_paths` no longer lists `specs/helper.py` as a planned edit, while `src/helper.py` is still listed, with or without a root argument
- **THEN** the same lexical exclusion applies to spec-kit input and preserves the existing `openspec/` exclusion

Shared planned-file extraction (`propose.paths` and `edited_paths`) SHALL exclude tokens
beginning `specs/` as well as `openspec/`, for every format and with or without a root
argument. This deliberately changes OpenSpec planned-file behavior because the shared
rootless caller has no manifest; both prefixes are reserved specification roots.


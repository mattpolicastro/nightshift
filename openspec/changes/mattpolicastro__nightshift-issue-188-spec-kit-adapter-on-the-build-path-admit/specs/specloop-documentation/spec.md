## Purpose

Document explicit native builds, trusted manifest configuration, completion
and remaining flow-forward context limits without claiming native front-door
or real deployment validation.

## ADDED Requirements

### Requirement: Native build operator guidance

The engine guide SHALL describe explicit native builds, Complete Status,
no archive and frozen authoring paths. Enrollment guidance SHALL explain
`[run] format` separately from future native front-door support. Both plugin
Finish passages SHALL qualify completion by format. Other false passages,
including the preset README and task scaffold, SHALL receive scoped corrections.

#### Scenario: Operator finishes the selected format
- **WHEN** an operator reads How the engine works, Publication, root enrollment and the plugin Finish action/confirmation guidance
- **THEN** the docs agree that OpenSpec Finish archives into `openspec/specs/`, native Finish sets `**Status**: Complete` and archives nothing, and both push and mark ready without merging
- **THEN** native examples use the explicit existing `--skip-feasibility` option until native feasibility support lands, retain structural admission validation, and do not promise native drafting or daemon intake support
- **THEN** the shipped preset README describes the same explicit build path without claiming installation configures it, and its task template distinguishes review-round appends, existing-phase fixes and final Coverage/Acceptance fixes

#### Scenario: Flow forward context limits are explicit
- **WHEN** an operator reads KNOWN-LIMITATIONS.md and the native engine path
- **THEN** guidance states that context contains the selected feature and constitution, with no consolidated current spec, automatic supersession traversal or cross-feature conflict detection
- **THEN** delivered feature records remain in place, later changes name superseded features, unrelated limitations remain and the real story-build/touchpoint check is pending until performed

### Requirement: Native manifest example

`examples/specloop/speckit.toml` SHALL be a complete parsable trusted-manifest
template with native format, editable repository/profile/check paths,
`specs/**` and `.specify/**` frozen, workflow protections, positive phase-gate
numbers and a `[coverage] step` naming an actual verify step. Guidance SHALL
instruct operators to keep the effective trusted manifest outside the target
repository.

#### Scenario: Example selects native build and coverage
- **WHEN** the new example is loaded through the real manifest loader without running commands
- **THEN** it selects SPECKIT, freezes both authoring globs and workflows, uses only positive example gates and resolves its coverage step to a declared verifier using the existing scenario-log convention
- **THEN** self-hosting remains OpenSpec and only its obsolete spec-kit-unavailable comments change

### Requirement: Native documentation verification

New documentation checks SHALL live in a new test file, validate real TOML/CLI
syntax and local links without dispatch, and select passages using the design's
deterministic physical-line heading/fence rules. Missing or ambiguous boundaries
SHALL fail. Unrelated examples, links, pending checks and existing plugin
assertions SHALL remain intact.

#### Scenario: Documentation remains structurally reviewable
- **WHEN** native documentation and examples are verified
- **THEN** unique exact column-zero headings bound passages through the next same-or-higher heading, exact physical delimiters select command blocks and missing/duplicate boundaries fail without semantic Markdown parsing
- **THEN** real parsers accept examples, relative links resolve, new tests avoid exact explanatory-prose assertions and all unaffected existing assertions remain

## MODIFIED Requirements

### Requirement: Spec-kit task traceability

The guide SHALL retain spec-kit's task grammar
`- [ ] T001 [P] [US1] Description with file path`, backticked scenario IDs on
every user-story task and at least one covering task for every scenario. It
SHALL document review-feedback appends as `## Phase N: Review round K`, section
evidence/reviewer fixes as extensions of their existing phase, and final
Coverage/Acceptance fixes as new phases with those supplied titles. All new
base task IDs SHALL follow the largest existing T root using `T{M+1:03d}`;
harness ticking SHALL use `[X]`. This replaces the prior documentation-only
description that conflated coverage additions with review-round appends, to
match the existing engine's distinct operations without changing scoring.

#### Scenario: Author links tasks and scenarios
- **WHEN** an author reads Tasks cite scenarios
- **THEN** examples retain optional parallel markers, story tags, file paths and backticked scenario IDs, while setup tasks need no invented story reference
- **THEN** the guide requires full scenario coverage, stable task identities and distinct harness addition forms with collision-free phase/T numbering

### Requirement: Format selection guidance

The engine guide, command/manifest guide and self-hosting example SHALL document
`[run] format` and its OpenSpec default. They SHALL replace the temporary
spec-kit admission refusal with explicit native build support and its scope.
Guidance SHALL preserve OpenSpec workflow/executable requirements and incoming
binary guidance, cite #187 and #184 alongside #188, and distinguish offline
compatibility checks from deployed resume. This removes a deliberately temporary
unavailability claim, not the compatibility guarantees.

#### Scenario: Operator selects the supported format
- **WHEN** an operator reads How the engine works, command-json.md's Manifest format and the self-hosting example
- **THEN** the formats paragraph explains omitted or explicit OpenSpec and native spec-kit build selection, links both examples and retains the existing house-rules link sentence
- **THEN** self-hosting parses as OpenSpec, native structural admission can succeed and existing manifests need no migration or change to their OpenSpec executable contract

#### Scenario: Compatibility claims retain their limits
- **WHEN** format guidance and implementation are reviewed
- **THEN** OpenSpec finish/human-check/recovery guidance remains true, and the separate unchecked legacy-run deployed-resume item and its #184/#187 links remain present
- **THEN** the three named obsolete native-refusal cases are relocated into new tests/test_speckit_adapter.py and the revision adapter case into new tests/test_speckit_revisions.py; only the new files receive changed expectations or mock wiring, preserving selection/refusal-cleanup coverage, parameterization, markers and all unaffected assertions
- **THEN** the shared source test file receives only the four necessary definition removals, retaining its helpers and incoming binary assertions; no skip, xfail or collection override hides an obsolete expectation
- **THEN** documentation checks use deterministic physical boundaries, block ambiguity and avoid semantic Markdown parsing or exact-prose assertions

### Requirement: Source local credential guidance

The explicit-publication guide SHALL describe ordered, ephemeral source-local
helper forwarding for base fetch, publish and finish, separately from forge
API credentials. It SHALL preserve command examples/boundaries, identify live
Gitea validation as a human check and qualify completion by format. The former
blanket archive wording changes only because native completion retains files.

#### Scenario: Operator publishes and finishes with local helpers
- **WHEN** an operator follows the explicit-publication passage
- **THEN** it explains forwarding including empty resets via Git config environment, no helper copies into repo.git/config, unchanged gh argv/no-helper behavior and helper path/host credential-store prerequisites
- **THEN** it retains the #174 citation and explains both publish and finish use source helpers; finish archives OpenSpec or completes native Status and marks ready without merging, with daemon/land boundaries unchanged
- **THEN** edits use the existing unique physical-heading boundary, block missing/duplicate/indented starts and preserve concurrent steering-file guidance, command examples and links

### Requirement: Factory forge action guidance

The plugin README and engine guide SHALL describe Factory Publish/Finish as
confirmed shared-engine calls using the manifest-selected forge. They SHALL
explain Gitea WIP behavior, host credentials and existing display restrictions,
preserve GitHub-only Land and distinguish daemon eligibility from confirmation.
Finish SHALL be described as OpenSpec archival or native Status completion,
followed by push/readiness without merge. This permits the two scoped plugin
README corrections previously excluded by the earlier forge-only change;
plugin behavior and tests stay unchanged.

#### Scenario: Factory operator understands the destination
- **WHEN** an operator reads Factory's action table, confirmation and act guidance
- **THEN** it describes GitHub/configured Gitea publication, WIP-title creation and prefix removal, confirmation before either push and no send on Cancel
- **THEN** it retains Paseo-host config/bot/git credential instructions, engine links and display-origin versus write-routing distinctions
- **THEN** HTTPS/root-path display limits, direct-CLI needs for HTTP loopback/prefixed URLs and GitHub-only Land remain explicit, while both Finish passages state the selected format's lifecycle without changing plugin code

### Requirement: Spec-kit discovery

The engine guide SHALL retain its one-sentence house-rules link under How the
engine works, and root README SHALL retain its `presets/` Layout entry.
Guidance SHALL distinguish authoring assets and explicit native build support
from future native drafting/front-door work. It SHALL preserve existing
assertions and other in-flight changes, correcting only passages made false.
This replaces pending engine/validator wording now superseded by #186/#188.

#### Scenario: Find authoring guidance
- **WHEN** a reader follows the engine guide link or root Layout entry
- **THEN** house rules/preset remain discoverable, relative links resolve and no unperformed installation or live validation is described as complete
- **THEN** the eight authoring rules retain distinct headings or bold leads and the guide retains `042-add-a-gate/us1/as2`, with original authoring-asset assertions unchanged

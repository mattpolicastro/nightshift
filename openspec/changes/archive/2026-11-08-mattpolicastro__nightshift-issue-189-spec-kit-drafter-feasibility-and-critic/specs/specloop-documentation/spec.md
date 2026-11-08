## Purpose

Explain native drafting and per-story sizing without changing OpenSpec guidance
or claiming daemon integration or live quality evidence.

## ADDED Requirements

### Requirement: Native drafting guidance

The engine guide and house rules SHALL explain native draft artifacts,
local templates, stable revisions, scenario/task shape, questions and human
checks. They SHALL state the six-task per-story target, unchanged roughly
300-changed-line per-task ceiling, advisory risk above eight tasks in a story
phase and absence of whole-feature count risk. Existing OpenSpec drafting prose
SHALL remain unchanged.

#### Scenario: Author finds the native workflow
- **WHEN** an author reads the native drafting guide and house rules
- **THEN** the guide explains templates, ASn labels, prioritized story phases, story tags, backticked citations, plan human checks, up to three genuine questions and revision in the existing undelivered feature directory
- **THEN** it shows explicit propose and normal admission without a feasibility bypass, states the per-story sizing rule and leaves daemon intake unsupported
- **THEN** the new drafting paragraph identifies `specs/` and `openspec/` as reserved specification roots excluded from shared planned-file extraction for every format, with or without a root, and explains the deliberate OpenSpec change: `specs/helper.py` is no longer a planned edit while `src/helper.py` remains eligible

#### Scenario: Quality evidence remains human
- **WHEN** documentation describes verification of native drafting
- **THEN** it leaves a real feature-sized scratch-repository draft with real profiles and spec-PR inspection pending until performed, recording rounds, questions and critic findings on tracking issue 184
- **THEN** offline prompt/routing tests are not described as proving draft quality or completed live checks

## MODIFIED Requirements

### Requirement: Spec-kit discovery

The engine guide SHALL retain its one-sentence house-rules link under How the
engine works, and root README SHALL retain its presets/ Layout entry.
Guidance SHALL distinguish authoring assets and explicit native drafting/build
support from future native front-door work. It SHALL preserve existing
assertions and other in-flight changes, correcting only passages made false.
This replaces the pending native-drafting wording now superseded by #189.

#### Scenario: Find authoring guidance
- **WHEN** a reader follows the engine guide link or root Layout entry
- **THEN** house rules/preset remain discoverable, relative links resolve and no unperformed installation or live validation is described as complete
- **THEN** the eight authoring rules retain distinct headings or bold leads and the guide retains 042-add-a-gate/us1/as2, with original authoring-asset assertions unchanged

### Requirement: Native build operator guidance

The engine guide SHALL describe explicit native drafting/builds, Complete Status,
no archive and frozen authoring paths. Enrollment guidance SHALL explain
[run] format separately from future native front-door support. Both plugin
Finish passages SHALL continue qualifying completion by format. False
pending-drafting/feasibility passages in root README, the house rules, command
JSON guide, preset README, native manifest comments and the native-spec-kit
KNOWN-LIMITATIONS bullet SHALL receive scoped corrections.

Existing skip-feasibility examples SHALL remain valid as explicitly optional
operator-requested bypass examples, while a separate native workflow SHALL show
normal admission with feasibility. This replaces the obsolete requirement that
native builds need that interim bypass, without changing the option or protected
example assertions. Task-scaffold revision/coverage instructions SHALL remain
unchanged.

#### Scenario: Operator finishes the selected format
- **WHEN** an operator reads How the engine works, Publication, root enrollment and the plugin Finish action/confirmation guidance
- **THEN** the docs agree that OpenSpec Finish archives into openspec/specs/, native Finish sets Status Complete and archives nothing, and both push and mark ready without merging
- **THEN** native examples distinguish the optional existing skip-feasibility bypass from the normal reviewed admission path, retain structural admission validation and promise no native daemon intake
- **THEN** the preset README describes the same support without claiming installation configures it, and its task template retains review-round appends, existing-phase fixes and final Coverage/Acceptance fixes

#### Scenario: Flow forward context limits are explicit
- **WHEN** an operator reads KNOWN-LIMITATIONS.md and the native engine path
- **THEN** guidance distinguishes selected-feature/constitution build context from feasibility/critic reads of explicitly named Complete features, with no consolidated current spec, automatic supersession traversal or exhaustive cross-feature conflict detection
- **THEN** delivered feature records remain in place, later changes name superseded features, unrelated limitations remain and real build/drafting observations remain pending until performed
- **THEN** the KNOWN-LIMITATIONS edit is confined to its native-spec-kit bullet and preserves concurrent readiness changes


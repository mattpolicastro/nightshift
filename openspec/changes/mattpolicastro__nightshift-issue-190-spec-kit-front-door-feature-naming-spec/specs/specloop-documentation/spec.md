## Purpose

Document native front-door support and its rollout without weakening existing examples or claiming live verification.

## ADDED Requirements

### Requirement: Native front door guidance

The scoped guides SHALL explain native enrollment, naming, reuse, PR text, AS markers, revisions, isolated draft workspaces and bounded recovery. The arming guide SHALL document native issue attribution and both reserved spec roots. Updates SHALL preserve examples, headings, prerequisites, assertions and pending checks. New tests SHALL use deterministic physical heading/fence boundaries, fail ambiguity and preserve #182's protected files.

#### Scenario: Operator finds native enrollment
- **WHEN** an operator reads the native front-door and enrollment guidance
- **THEN** it shows issue 7 becoming 007-add-a-gate and issue 1234 growing without truncation, the spec/ branch and specs/ directory, and format = speckit in the trusted manifest without a new repos field
- **THEN** it explains existing-feature reuse, unchanged recorded identities, same-line AS markers, adapter revision numbering, native drafts/@speckit/<owner>__<repo>/<change> isolation, unchanged flat OpenSpec workspaces, existing daemon one-job-per-item receipts/stopped-job holds, direct CLI same-repository/change coordination and pre-upgrade writer draining
- **THEN** the ledger retains globally change-keyed run storage with existing collision refusal and separate state roots for concurrent identical native names; no draft lock or run namespace is claimed
- **THEN** docs/arming.md recognizes native <NNN>-<slug> and compatibility <owner>__<repo>-<NNN>-<slug> identities using the numeric prefix as the source issue, excludes openspec/ and specs/ from scope paths, and retains existing OpenSpec examples
- **THEN** existing OpenSpec naming literals, CLI examples, heading/fence boundaries, links, plugin content and the separate legacy-resume checklist remain intact; new offline checks parse real CLI/TOML examples without dispatch

#### Scenario: Live enrollment remains pending
- **WHEN** documentation reports native front-door availability and verification
- **THEN** it leaves the first spec-kit-only enrollment through draft, review, approval, build, publish and merge as a human check on either forge, with labels, trusted manifest and installed preset
- **THEN** the operator records issue/PR identity and a touchpoint report against the baseline on #184, while existing draft-quality, build/Finish and deployed legacy-resume checks remain pending until performed
- **THEN** KNOWN-LIMITATIONS changes only its native-spec-kit bullet, retains #182 readiness material and does not present offline tests as live success

## MODIFIED Requirements

### Requirement: Spec-kit discovery

The engine guide SHALL retain its one-sentence house-rules link under How the
engine works, and root README SHALL retain its presets/ Layout entry.
Guidance SHALL distinguish authoring asset installation from explicit native
drafting/builds and manifest-enrolled native front-door support. It SHALL
preserve existing assertions and other in-flight changes, correcting only
passages made false. This replaces the future-front-door wording because this
change supplies that integration; installation alone still does not enroll it.

#### Scenario: Find authoring guidance
- **WHEN** a reader follows the engine guide link or root Layout entry
- **THEN** house rules/preset remain discoverable, relative links resolve and no unperformed installation or live validation is described as complete
- **THEN** the eight authoring rules retain distinct headings or bold leads and the guide retains 042-add-a-gate/us1/as2, with original authoring-asset assertions unchanged

### Requirement: Native build operator guidance

The engine guide SHALL describe explicit native drafting/builds, Complete Status,
no archive and frozen authoring paths. Enrollment guidance SHALL explain
[run] format as the selector for native front-door support, with no new repository configuration field. Both plugin
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
- **THEN** native examples distinguish the optional existing skip-feasibility bypass from the normal reviewed admission path, retain structural admission validation and describe native daemon intake under the existing enrollment and authorization gates
- **THEN** the preset README describes the same support without claiming installation configures it, and its task template retains review-round appends, existing-phase fixes and final Coverage/Acceptance fixes

#### Scenario: Flow forward context limits are explicit
- **WHEN** an operator reads KNOWN-LIMITATIONS.md and the native engine path
- **THEN** guidance distinguishes selected-feature/constitution build context from feasibility/critic reads of explicitly named Complete features, with no consolidated current spec, automatic supersession traversal or exhaustive cross-feature conflict detection
- **THEN** delivered feature records remain in place, later changes name superseded features, unrelated limitations remain and real build/drafting observations remain pending until performed
- **THEN** the KNOWN-LIMITATIONS edit is confined to its native-spec-kit bullet and preserves concurrent readiness changes

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
- **THEN** it shows explicit propose and normal admission without a feasibility bypass, states the per-story sizing rule and links the supported native daemon front door without adding internal base-pin options to explicit CLI examples
- **THEN** the new drafting paragraph identifies `specs/` and `openspec/` as reserved specification roots excluded from shared planned-file extraction for every format, with or without a root, and explains the deliberate OpenSpec change: `specs/helper.py` is no longer a planned edit while `src/helper.py` remains eligible

#### Scenario: Quality evidence remains human
- **WHEN** documentation describes verification of native drafting
- **THEN** it leaves a real feature-sized scratch-repository draft with real profiles and spec-PR inspection pending until performed, recording rounds, questions and critic findings on tracking issue 184
- **THEN** offline prompt/routing tests are not described as proving draft quality or completed live checks

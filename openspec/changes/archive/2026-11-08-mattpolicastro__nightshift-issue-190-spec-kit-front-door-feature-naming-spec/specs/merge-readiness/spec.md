## Purpose

Resolve native committed scenarios and task sections without changing advisory readiness or evidence policy.

## ADDED Requirements

### Requirement: Native committed human check lookup

For a copied run manifest selecting speckit, readiness SHALL resolve human-check IDs from committed specs/<change>/spec.md at the assessed run head using speckit.parse_spec and speckit.scenario_ids. Each check SHALL refer to exactly one known scenario whose parsed text contains the literal post-deploy marker. Readiness SHALL retain existing checklist source, physical-line grammar, identity gates and fail-closed behavior. The OpenSpec branch SHALL retain its current walk and slug rules.

#### Scenario: Native scenarios with marker permit readiness
- **WHEN** each check cites a unique native AS scenario whose parsed physical-line text contains <!-- nightshift:post-deploy --> at the run head
- **THEN** the human-check condition passes for either an explicit PR-body checklist or publication-receipt fallback without ticking checks or claiming completion
- **THEN** a different source working tree or enrollment manifest cannot replace the committed run's spec or format

#### Scenario: Unknown or unmarked native IDs block
- **WHEN** a check cites an unknown, duplicate, malformed or unmarked native ID, or its marker exists only on another line/scenario or in plan.md
- **THEN** readiness blocks with an outstanding/unknown-check reason; missing, undecodable or unreadable committed specs also block
- **THEN** malformed or multiply headed checklists still block, literal examples receive no fence/HTML exemption, and checked pre-merge rows do not bypass the condition

### Requirement: Adapter parsed readiness sections

For enabled evidence, readiness SHALL read the adapter-selected tasks path and use adapter-parsed task sections. It SHALL retain physical-heading integrity checks for the selected format, rejecting missing, empty, duplicate or ambiguous sections, and exclude only the selected tasks path from bounded test-only diffs. Existing evidence measurement rules and #182's disabled-evidence early return after identity validation SHALL remain unchanged.

#### Scenario: Native phases participate in evidence
- **WHEN** enabled native evidence is assessed for committed native phases with valid measured latest events and saved completions
- **THEN** their parsed phase numbers are assessed normally rather than treated as absent OpenSpec sections
- **THEN** missing events, timeouts and failed measurements retain existing blocking reasons and native tasks-file exclusion follows the adapter path

#### Scenario: Empty or ambiguous phases block
- **WHEN** the committed native tasks contain an empty phase, repeated phase number, ambiguous task identity or unreadable content
- **THEN** readiness blocks rather than omitting the affected phase or accepting a successful empty task list
- **THEN** physical backend heading rules apply without semantic Markdown parsing, and OpenSpec section assertions remain unchanged

#### Scenario: Disabled evidence keeps early exemption
- **WHEN** valid run-state/manifest identity selects evidence = false for either format
- **THEN** evidence contributes no reasons without reading tasks, events or diffs, even when those inputs are absent or malformed
- **THEN** invalid identity still blocks and all head, CI, pending-revision and human-check conditions remain required

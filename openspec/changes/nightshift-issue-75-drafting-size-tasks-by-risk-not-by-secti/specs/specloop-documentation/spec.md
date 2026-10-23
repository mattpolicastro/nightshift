## Purpose

Explain economical task sizing in the existing drafting guide while preserving
the guide's shipped workflow, scope, references and regression protections.

## ADDED Requirements

### Requirement: Task sizing guidance

The guide's drafting paragraph SHALL describe risk-based task sizing:
same-file work for one concern belongs together, documentation-only changes
use one or two tasks in one section, and most changes need six tasks or fewer
in as few sections as dependencies allow, within the existing task ceiling.
It SHALL explain that each task incurs a review cycle and each section incurs
an evidence gate, while preserving existing drafting workflow guidance.

#### Scenario: Reader sizes a plan
- **WHEN** a reader consults the drafting discussion in `docs/specloop/README.md`
- **THEN** it explains the grouping advice, documentation-only size, normal six-task target and dependency-driven section count with their review-cycle and evidence-gate rationale
- **THEN** the existing proposal stages, four-round default and references remain intact

## MODIFIED Requirements

### Requirement: Documentation integrity

Documentation-only implementation SHALL edit only its scoped documentation
files, with root README edits limited to specloop/front-door descriptions.
Changes that also alter code or tests SHALL explicitly scope those edits in
their own capability deltas. Documentation SHALL keep short, concrete,
present-tense prose; preserve runtime requirements except for justified
MODIFIED or REMOVED deltas and preserve all existing assertions; and cite
verified PR/issue identities and states without inventing references.
Implementation acceptance SHALL include a green `uv run pytest` result and
the design's human review checklist.

#### Scenario: Scope and regression checks remain intact
- **WHEN** the implementation diff and local validation are reviewed
- **THEN** documentation changes are limited to the allowed docs, code and test changes are limited to explicitly scoped capability deltas, relative documentation links resolve, and `uv run pytest` passes without weakening existing assertions
- **THEN** a documentation-only change leaves code and tests unchanged

#### Scenario: References and reader checks are reviewable
- **WHEN** the documentation is prepared for merge
- **THEN** changed behavior has adjacent PR citations that match local merge history when a merged PR exists, otherwise its verified tracking issue is cited without inventing a PR; issue and PR numbers are distinguished, and no pending work is described as shipped
- **THEN** the PR carries the design's human checklist for live reference/ledger verification and Matt's single readability pass, without assigning either check as an implementing-agent task

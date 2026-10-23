## Purpose

Keep the provider guidance consistent with retry, fallback and cooldown
behavior while retaining the existing documentation integrity guarantees.

## ADDED Requirements

### Requirement: Provider fallback guidance

The guide's existing usage-limit paragraph SHALL describe usage-limit
fallback, immediate fallback for recognized authentication/plan failures,
fallback after per-task/profile infra retries, the 15-minute unavailable
cooldown, and all-blocked earliest-deadline pauses. It SHALL distinguish
uncapped usage-limit recovery from the existing capped infra recovery and
SHALL NOT claim live-provider error wording has been validated by fixtures.

#### Scenario: Reader understands provider recovery
- **WHEN** a reader consults the provider section of `docs/specloop/README.md`
- **THEN** the usage-limit paragraph explains when the next profile is tried, when a profile becomes eligible again, when the role pauses, and how the unchanged automatic-resume limits apply
- **THEN** the edit cites issue #72 without inventing a PR reference and preserves all other guide sections, including concurrent arming/front-door changes

## MODIFIED Requirements

### Requirement: Documentation integrity

Documentation-only refreshes SHALL edit only their scoped documentation
files, with root README edits limited to specloop/front-door descriptions,
and SHALL leave runtime code and existing tests unchanged. A separately
specified runtime change MAY update its matching guide paragraph and add
tests within that change's explicit scope. All documentation changes SHALL
keep short, concrete, present-tense prose; preserve runtime requirements
unless accompanied by a justified spec delta; preserve all existing
assertions; and cite verified PR/issue identities and states without
inventing references. Implementation acceptance SHALL include a green
`uv run pytest` result and the applicable design's human review checklist.

#### Scenario: Scope and regression checks remain intact
- **WHEN** the implementation diff and local validation are reviewed
- **THEN** documentation-only refreshes change only their allowed docs and leave code and existing tests unchanged; a separately specified runtime change stays within its declared code, additive-test and documentation scope
- **THEN** relative documentation links resolve and `uv run pytest` passes without weakening existing assertions

#### Scenario: References and reader checks are reviewable
- **WHEN** the documentation is prepared for merge
- **THEN** changed behavior has adjacent verified PR citations when an implementation PR identity is known, or its verified tracking issue until then, issue and PR numbers are distinguished, and no pending work is described as shipped
- **THEN** the PR carries the applicable design's human checklist for live reference verification and a readability pass, retaining ledger verification and Matt's single readability pass for documentation refreshes, without assigning these checks as implementing-agent tasks

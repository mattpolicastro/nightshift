## Purpose

Keep the three scoped operator documents consistent with repository health, switch recovery and new identities while preserving #67's guidance and unrelated in-flight work.

## ADDED Requirements

### Requirement: Configuration resilience guidance

`README.md`, `docs/specloop/README.md` and `KNOWN-LIMITATIONS.md` SHALL describe the implemented manifest hold/repair, engine-switch recovery and owner-qualified naming behavior in their relevant passages. They SHALL preserve unrelated documentation and cite verified issue #13 or its implementing PR without inventing a PR. Only the three resolved Config limitations SHALL be removed; migration and unsupported-name limits SHALL remain explicit.

#### Scenario: Operator repairs enrollment
- **WHEN** an operator consults enrollment and recovery guidance
- **THEN** the docs explain regular-file validation, repository-local health, retained triggers, retry after file repair, config-restart requirements and continued publication/build revisions of admitted runs
- **THEN** they explain release of orphaned working issues and human inspection of surviving worker work without claiming those tasks resume automatically

#### Scenario: Operator understands names and limits
- **WHEN** an operator reads naming examples and remaining limitations
- **THEN** both guides use owner__sample-issue-7-add-a-gate and its spec branch, explain unsupported unsafe spellings and preservation of recorded legacy identities
- **THEN** limitations distinguish completed fixes from unautomated deployed-run migration, receipt-loss plus title-change discovery limits and unchanged transport retry risks

## Purpose

Keep enrollment and recovery guidance accurate about GitHub workflow support
and the explicit Gitea daemon dispatch hold.

## ADDED Requirements

### Requirement: Gitea readiness evidence limits

Guidance SHALL distinguish read-only Gitea preflight evidence from permission
to run daemon stages. It SHALL state that neither successful checks, a populated
allowlist nor clearing a foreign journal enables execution in this version.

#### Scenario: Preflight does not enable execution
- **WHEN** an operator follows Gitea configuration and preflight guidance
- **THEN** the guides and limitations explain that drafting, admission, revisions, publication and pending delivery remain disabled pending core authorization and durable transition recovery
- **THEN** real-instance checks remain human verification and are not described as CI proof of live workflow support

## MODIFIED Requirements

### Requirement: Configuration resilience guidance

README.md, docs/specloop/README.md and KNOWN-LIMITATIONS.md SHALL describe manifest
hold/repair, GitHub-only worker recovery and owner-qualified naming. They SHALL
preserve unrelated guidance and verified issue #13 references without inventing
a PR. Only the three resolved Config limitations SHALL be removed; migration
and unsupported-name limits SHALL remain explicit. Admitted-run health bypass
SHALL be qualified by the Gitea dispatch hold.

#### Scenario: Operator repairs enrollment
- **WHEN** an operator consults enrollment and recovery guidance
- **THEN** the docs explain regular-file template validation, optional run.change and run.branch supplied by intake, repository-local health, retained triggers, retry after file repair, and config-restart requirements
- **THEN** they explain continued publication/build revisions for admitted GitHub runs, GitHub-only orphan recovery and preserved worker work without claiming automatic resume
- **THEN** Gitea stages and delivery remain disabled regardless of manifest health; foreign journals require inspection and clearing them does not enable execution

#### Scenario: Operator understands names and limits
- **WHEN** an operator reads naming examples and remaining limitations
- **THEN** both guides retain owner__sample-issue-7-add-a-gate and its spec branch, unsupported unsafe spellings and preservation of recorded legacy identities
- **THEN** limitations distinguish completed fixes from unautomated deployed-run migration, receipt-loss plus title-change discovery limits and unchanged transport retry risks

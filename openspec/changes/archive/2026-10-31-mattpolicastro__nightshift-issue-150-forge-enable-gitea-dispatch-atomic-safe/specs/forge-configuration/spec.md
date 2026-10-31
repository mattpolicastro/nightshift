## Purpose

Explain eligible Gitea enrollment while retaining existing selection, credential exclusion and preflight evidence limits.

## ADDED Requirements



## MODIFIED Requirements

### Requirement: Bounded enrollment guidance

Operator guidance SHALL document forge selection, unchanged GitHub authentication,
token exclusion, preflight evidence and the staged Gitea limitations without
claiming a live enrollment. Runnable existing examples and frozen service
prerequisites SHALL remain valid.

#### Scenario: Examples preserve existing consumers
- **WHEN** the example config and guide enrollment passages are validated
- **THEN** the real TOML parser and unchanged retirement tests pass, including whole-guide raw-line extraction and replacement of every literal `[[repos]]` in the example config
- **THEN** docs explain the new fields and implicit GitHub default without adding a second raw guide enrollment or breaking existing diagnostic assertions

#### Scenario: Limits and live checks are explicit
- **WHEN** an operator reads README, KNOWN-LIMITATIONS and the engine guide
- **THEN** they can distinguish configuration and negative controls from per-item authorization; eligible Gitea dispatch uses allowlist-only actors, bot-owned canonical markers and add-first verified labels, while membership authorization remains unsupported
- **THEN** docs identify GitHub-only command boundaries, Gitea foreign-journal holds and normal eligibility gates, supervisor config/credential provisioning, protection and credential evidence limits, and the pending real-instance preflight/front-door checks, including daemon bot-token sourcing, absence of all declared landing variables and matching supervisor forge configuration

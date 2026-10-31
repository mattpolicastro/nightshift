# forge-configuration Specification

## Purpose
Configure named forges without changing implicit GitHub enrollment or existing
configuration compatibility.

## Requirements

### Requirement: Named forge definitions

Configuration SHALL parse named `[[forges]]` with name, kind, url, token_env,
land_token_env, bot_login, trusted_logins, forbidden_probe and wip_prefix, using
the design's typed defaults. Optional `repos.forge` SHALL select an exact named
definition; omission SHALL mean legacy GitHub. Unknown keys SHALL stay ignored.

#### Scenario: Legacy and explicit selection
- **WHEN** configuration contains implicit GitHub, explicitly named GitHub and Gitea repositories
- **THEN** omission resolves to GitHub independently of table order, named references resolve exactly, and omitted Gitea trusted_logins and wip_prefix become an empty list and `WIP: `
- **THEN** existing engine, manifest, path and obsolete-setting behavior remains unchanged

#### Scenario: Invalid definitions refuse
- **WHEN** a definition has a duplicate name, unknown kind, invalid known-field type, invalid URL or environment name, or a repository supplies an empty or unknown forge reference
- **THEN** loading refuses without reading credentials or dispatching work, with sanitized field diagnostics
- **THEN** retired-engine rejection still takes precedence and valid unknown keys in all tables remain ignored

### Requirement: Unique repository enrollment

Configuration SHALL reject casefolded owner/name collisions across all enrolled
rows, regardless of forge. Validation SHALL preserve the refusal diagnostics
`ambiguous enrollment` for duplicates and `invalid repository identity` for
invalid identity types or shape, without replacing existing readiness checks.

#### Scenario: Cross forge names collide
- **WHEN** two enrolled repository names differ only by case on different forges, including implicit GitHub versus Gitea
- **THEN** loading refuses with `ambiguous enrollment` before daemon dispatch, leaving configuration and state unchanged
- **THEN** distinct owner/name pairs across forges load normally

#### Scenario: Legacy refusals stay recognizable
- **WHEN** same-forge duplicate GitHub rows or a numeric repository name are loaded for landing
- **THEN** existing tests still observe `ambiguous enrollment` or `invalid repository identity` respectively, without mutation
- **THEN** safe existing names and the unsupported-new-name readiness behavior remain unchanged

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

## Purpose

Discover every configured forge credential available to the trusted sealing
process without changing callers or disclosing values.

## ADDED Requirements

### Requirement: Trusted forge discovery

`credential_values(extra_env)` SHALL read forge definitions from the absolute
process-environment `NIGHTSHIFT_FORGE_CONFIG` override or the trusted default
config path. It SHALL NOT discover configuration from candidate contents or cwd.
An absent implicit default SHALL remain compatible; an invalid explicit source
or existing unreadable/malformed default SHALL fail closed.

#### Scenario: Configuration selection is deterministic
- **WHEN** a trusted explicit config, an absent default, or a candidate-local lookalike config exists
- **THEN** the explicit config is used, absence of the implicit default contributes no forge definitions, and candidate-local files cannot select or override the source
- **THEN** existing callers and fixed credential/profile sources remain compatible, with tests isolating HOME, ENV_FILE, DEFAULT_PATH and NIGHTSHIFT_FORGE_CONFIG

#### Scenario: Invalid selected source blocks sealing
- **WHEN** the override is blank, relative, missing or unreadable, or a selected existing source is malformed
- **THEN** discovery raises a sanitized error before sealing can commit a candidate while omitting declared forge secrets
- **THEN** an existing unreadable credential env file also fails closed rather than silently dropping available-source scanning

### Requirement: Every forge token is scanned

Seal discovery SHALL collect all distinct nonempty values named by every forge's
token_env and land_token_env from extra_env, process environment and the trusted
env file, including unused forges, shadowed values and short tokens. Existing
secret sources SHALL remain intact. Detection SHALL prevent a sealed commit and
report only affected paths.

#### Scenario: All declared values are protected
- **WHEN** several forges declare bot and landing variables with different values across supported sources, including an unused forge and a short value
- **THEN** every available value is scanned, not merely the selected repo's token or the winning environment value
- **THEN** a candidate containing any such value produces secret_paths and no sealed commit, without exposing a token in diagnostics

#### Scenario: Missing values do not invent secrets
- **WHEN** declared variables are unset or empty, or a human token exists only in an inaccessible credential store
- **THEN** no empty secret or invented value is added, no keychain retrieval or daemon credential injection occurs, and existing known-token/profile scanning continues
- **THEN** guidance states that unavailable values cannot be detected and custom supervisor config selection requires operator provisioning

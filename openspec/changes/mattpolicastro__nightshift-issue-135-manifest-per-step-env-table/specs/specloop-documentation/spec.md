## Purpose

Explain per-step environment configuration with a parseable example and clear
boundaries that agree with trusted command execution.

## ADDED Requirements

### Requirement: Step environment guidance

`docs/specloop/README.md` SHALL explain the per-step env contract for all four
step kinds, cite issue #135 and preserve existing guidance. Its example SHALL
be validated using deterministic physical-line boundaries and the real TOML
and manifest parsers, without prose-exact assertions or semantic Markdown
parsing.

#### Scenario: Operator configures step environments
- **WHEN** the guide's env example is extracted using the design's exact physical-line marker and fence rules and parsed with a minimal manifest fixture
- **THEN** all four step kinds have valid distinct IDs and string-valued env entries whose placeholders expand consistently with argv
- **THEN** missing, duplicate, reversed or indented delimiters, interior fence tokens and malformed TOML block validation regardless of surrounding Markdown or HTML context
- **THEN** guidance covers empty defaults, string validation, value substitution, isolation, non-inheritance, protected PATH/HOME/TMPDIR and permitted overrides of other fixed defaults
- **THEN** the guide explains the environment contract and unchanged execution boundaries without claiming env-only mutation narrowing, additional filesystem access or inherited host credentials

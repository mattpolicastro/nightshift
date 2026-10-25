## Purpose

Keep landing guidance consistent with worker retirement while preserving interpreter, authentication and run-discovery guarantees.

## MODIFIED Requirements

### Requirement: Landing interpreter prerequisites

The prerequisites passage of `docs/landing.md` SHALL require the interpreter running landing to import `specloop.cli` without requiring its console script on PATH. It SHALL describe omitted or explicit specloop enrollment and explain that explicit worker enrollment fails configuration loading because the engine is retired. Worker retirement SHALL NOT change interpreter invocation, operator authentication, canonical PR run discovery or landing gates.

#### Scenario: Operator prepares the environment
- **WHEN** an operator follows the landing prerequisites
- **THEN** the guide explains the interpreter import and applicable run-environment requirements without requiring virtual-environment activation or a specloop executable on PATH
- **THEN** it retains `uv` and `gh` on PATH, operator authentication with permission to read/edit PRs and issue comments, update the branch and merge under repository protection rules, and exactly one case-insensitive enrollment
- **THEN** omitted and explicit specloop engines are supported, explicit worker enrollment is refused with migration guidance, and the current interpreter's `-m specloop.cli status --json` must still read the applicable runs
- **THEN** front-door discovery still uses canonical PR URL identity independently of labels or branch names, with unchanged status/finish module argv, validation and landing gates

## Purpose

Make runtime scenario references and argument errors accurately describe specloop behavior while preserving its existing version-1 contracts.

## ADDED Requirements

### Requirement: Known runtime scenario references

Runtime evidence and scenario context SHALL accept a backticked candidate ID only when it exactly matches a scenario in the change at the evaluated revision. They SHALL preserve the original task and feedback text. Drafting and admission SHALL retain strict validation of unknown authored scenario references.

#### Scenario: Paths in review feedback
- **WHEN** review-feedback task text contains backticked `a/b/c` and another unknown candidate alongside a known scenario ID
- **THEN** evidence and implementation/gate scenario lists contain only the known ID, and no strengthening task is generated for either unknown candidate
- **THEN** the feedback text and task identity remain unchanged

#### Scenario: Real coverage gaps remain actionable
- **WHEN** a review-feedback task cites an existing change scenario without passing coverage evidence
- **THEN** the evidence gate generates the normal strengthening task for that real scenario
- **THEN** a covered existing scenario does not generate a coverage strengthening task

#### Scenario: Strict validation and failed lookup
- **WHEN** authored tasks cite an unknown ID during drafting or admission, or a runtime evidence gate cannot load the change's scenarios
- **THEN** drafting and admission still report the unknown ID, and a runtime lookup failure prevents evidence from being marked passed or tasks from being appended

### Requirement: JSON argument errors

When argument parsing fails and the effective argv contains an exact `--json` token before any standalone `--`, specloop SHALL emit exactly one stdout document with `version` equal to `1` and a nonempty top-level `error`, and SHALL exit with status 2 before dispatching a command. This SHALL apply to top-level and subcommand parsing failures without changing successful invocation syntax.

#### Scenario: Invalid arguments produce a document
- **WHEN** a JSON-requesting invocation has a missing required argument, an invalid choice or numeric value, an unknown option, or an absent or unknown subcommand
- **THEN** stdout contains exactly one version-1 error document, the exit status is 2, and no command handler or command side effect runs
- **THEN** the behavior holds for explicit argv and process argv, including when the error precedes the JSON flag

#### Scenario: Human errors and help remain compatible
- **WHEN** parsing fails without an exact JSON flag before the option terminator, or argparse processes a help request
- **THEN** non-JSON failures retain normal stderr diagnostics and exit status 2 without a JSON document, and help remains ordinary help with exit status 0 even when `--json` is also present

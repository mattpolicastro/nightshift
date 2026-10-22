## Purpose

Expose the trusted preparation sequence to implementers and feasibility reviewers without changing how preparation or verification executes.

## ADDED Requirements

### Requirement: Preparation visible in agent recipes

The implementer recipe and feasibility review prompt SHALL show every configured prepare step before every verify step, in declaration order within clearly labeled groups. Each step SHALL include its ID, expanded shell-quoted command, and expected exit status. The prompts SHALL identify preparation as harness execution with network access before offline verification, preserving existing agent restrictions and execution semantics.

#### Scenario: Prepare commands precede checks
- **WHEN** a manifest contains multiple prepare and verify steps, variables, arguments containing spaces, and nonzero expected exits
- **THEN** both generated prompts show all prepare steps in order before the verify steps with their IDs, correctly quoted expanded commands, and expected exits
- **THEN** each prompt retains its existing source, build, and revision substitution context and identifies the harness preparation environment without executing any step

#### Scenario: No preparation configured
- **WHEN** a manifest has no prepare steps
- **THEN** both prompts show preparation as absent and retain all verify commands and expected exits

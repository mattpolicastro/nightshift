# Feature Specification: Add a gate

**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Check the gate (Priority: P1)

**Acceptance Scenarios**:
1. **AS1** Given a passing check, when the gate runs, then it permits the change.
7. **AS2** Given a failing check, when the gate runs, then it refuses the change.

### User Story 2 - Explain a refusal (Priority: P2)

**Acceptance Scenarios**:
1. **AS1** Given a refused change, when its result is read, then it names the failed check.

### User Story 3 - Retry a check (Priority: P3)

**Acceptance Scenarios**:
1. **AS1** Given a repaired change, when the gate reruns, then it uses the new check result.

## Requirements *(mandatory)*

- **FR-001**: The gate must evaluate the configured check.
- **FR-002**: The gate must explain each refusal.

## Success Criteria *(mandatory)*

- **SC-001**: A failing check prevents the change from proceeding.
- **SC-002**: A repaired change can pass on retry.

# Feature Specification: [FEATURE NAME]

**Feature Branch**: `spec/<NNN>-<slug>`
**Created**: [DATE]
**Status**: Draft
**Input**: User description: "$ARGUMENTS"

Scenario IDs use `<NNN>-<slug>/us<k>/as<n>`, e.g. `042-add-a-gate/us1/as2`.
Number labels locally per story; append without renumbering or reusing committed IDs.
Resolve `[NEEDS CLARIFICATION]` before approval. The harness sets `**Status**: Complete`
after delivery; retain the feature record.

## User Scenarios & Testing *(mandatory)*

Replace placeholders with independently testable, observable behavior.

### User Story 1 - [Primary outcome] (Priority: P1)

**Description / Why this priority**: [User journey and minimum useful increment.]
**Independent Test**: [How this story alone demonstrates its outcome.]

**Acceptance Scenarios**:

1. **AS1** **Given** [initial state] **When** [user acts] **Then** [observable outcome].
2. **AS2** **Given** [alternate state] **When** [user acts] **Then** [observable outcome].

### User Story 2 - [Next outcome] (Priority: P2)

**Description / Why this priority**: [User journey and incremental value.]
**Independent Test**: [How this story can be verified independently.]

**Acceptance Scenarios**:

1. **AS1** **Given** [initial state] **When** [user acts] **Then** [observable outcome].

### User Story 3 - [Additional outcome] (Priority: P3)

**Description / Why this priority**: [User journey and reason for this priority.]
**Independent Test**: [How this story can be verified independently.]

**Acceptance Scenarios**:

1. **AS1** **Given** [initial state] **When** [user acts] **Then** [observable outcome].

### Edge Cases

- [Boundary, invalid-input and dependency-failure behavior.]

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST [provide an observable capability].
- **FR-002**: System MUST [handle the relevant failure or boundary].

### Key Entities *(include if feature involves data)*

- **[Entity]**: [Meaning, key attributes and relationships; no implementation detail.]

## Success Criteria *(mandatory)*

- **SC-001**: [Measurable, technology-independent user outcome.]

## Assumptions

- [Constraints and superseded features, e.g. Supersedes 042-add-a-gate.]

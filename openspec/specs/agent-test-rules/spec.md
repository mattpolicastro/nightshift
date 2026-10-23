## Purpose

The rules implementing and reviewing agents follow about protected tests and documentation tests, so a multi-task change can evolve its own tests and docs stay freely editable.

## Requirements

### Requirement: Existing tests

The implementer and reviewer prompts SHALL define an existing test as one that predates the change. Tests added by earlier tasks of the same change MAY be updated when a later task's behaviour requires it, with the reason in the summary; tests that predate the change stay protected against weakening.

#### Scenario: Tests from earlier tasks may evolve
- **WHEN** a later task of a change alters behaviour that a test added by an earlier task of the same change encoded
- **THEN** the implementer may update that test and explain why, and the reviewer does not treat the update as a weakened existing test

### Requirement: Documentation tests

The implementer prompt SHALL require documentation to be tested by structure (links resolve, examples parse or run, tables and commands match the code) and SHALL forbid asserting that a document contains particular phrases.

#### Scenario: Docs are tested by structure
- **WHEN** a task's scenarios concern documentation
- **THEN** its tests check structure, and no test asserts that a document contains particular phrases

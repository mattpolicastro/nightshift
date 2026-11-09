## Purpose

Allow existing native parsers to support the front door without expanding or rewriting their grammar.

## MODIFIED Requirements

### Requirement: Local feature API

`specloop.speckit` SHALL retain its eight existing pure-Python parser, validator
and text interfaces without new dependencies or external calls from those
interfaces. `feature_dir(repo, change)` SHALL return `repo / "specs" / change`.
Additional build helpers MAY perform the explicitly specified Git completion
operations. Importing the module SHALL have no I/O or dispatch side effects.
Native integration SHALL occur through the manifest-selected format adapter,
preserving existing OpenSpec behavior. The daemon MAY use these helpers for the native front-door behavior explicitly specified by issue-intake and merge-readiness; all unrelated daemon policy SHALL remain unchanged. This qualifies the former daemon-behavior exclusion to enable native front-door integration; it does not change the pure parser API or physical-line grammar.

#### Scenario: Resolve a feature without integration
- **WHEN** a caller imports the module or resolves `042-add-a-gate` under a repository path
- **THEN** the result is that repository's `specs/042-add-a-gate` path without filesystem access
- **THEN** import/lookup does not dispatch a command or select a format, and existing pure helpers retain their offline contracts

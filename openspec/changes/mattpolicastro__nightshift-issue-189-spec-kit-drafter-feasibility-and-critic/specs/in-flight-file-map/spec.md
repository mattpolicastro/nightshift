## Purpose

Keep native specification references out of shared advisory planned-file maps.

## ADDED Requirements

### Requirement: Native spec reference exclusion

`propose.paths` SHALL exclude tokens starting with `specs/` alongside the
existing `openspec/` exclusion, with or without a root argument. This lexical
rule SHALL apply through `edited_paths` and existing arming callers without
changing `nightshift/`. It SHALL retain all other normalization, negation,
file-existence and run-selection rules. The exclusion intentionally reserves
both format-owned roots even in mixed repositories.

#### Scenario: Native references are not planned edits
- **WHEN** extraction sees backticked specs/042-add-a-gate/spec.md and specs/042-add-a-gate/tasks.md together with src/gate.py and tests/test_gate.py
- **THEN** only the two implementation paths are returned whether a root is supplied or omitted, including through edited_paths
- **THEN** function suffix normalization, root-file recognition and existing hands-off exclusions retain their previous behavior


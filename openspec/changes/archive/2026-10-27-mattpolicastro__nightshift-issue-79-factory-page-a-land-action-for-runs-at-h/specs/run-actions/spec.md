## Purpose

Extend finish's preconditions at the run-lock boundary while preserving existing publish, finish and act behavior.

## MODIFIED Requirements

### Requirement: Publish only to the named repository

`publish` and `finish` SHALL refuse, without pushing, when the manifest names no GitHub repository, or when the source clone's `origin` is a hosted URL for a different repository than the manifest names. A local-path origin SHALL be accepted. With optional `--expect-head SHA`, `finish` SHALL also verify the run branch head after locked reload and refuse mismatch or unreadable head before archiving or any finish side effect.

#### Scenario: Fork still pointing upstream
- **WHEN** a run is ready to publish, its manifest names `owner/fork`, and the source clone's origin is `github.com/upstream/project`
- **THEN** publish refuses with a reason naming both, and nothing is pushed

#### Scenario: Expected head checked under lock
- **WHEN** finish receives a valid expected SHA and revision republishes before finish acquires the run lock
- **THEN** finish compares the actual run branch head after locked reload, refuses with expected and observed heads, and leaves the run, archive, refs and PR unchanged by finish
- **THEN** an unreadable branch head also refuses before archiving, committing, pushing or marking the PR ready

#### Scenario: Matching and omitted head retain finish gates
- **WHEN** the expected head matches or finish is invoked without the optional flag, including through act
- **THEN** existing human_checks eligibility, completed-task, repository, archive/push recovery and ready/done behavior remain in force
- **THEN** a matching head does not bypass unfinished tasks or repository refusal, and act requires no new argument or verb

#### Scenario: Malformed expected head rejected
- **WHEN** canonical or alias finish is given an expected head other than a full 40-hex SHA
- **THEN** argument parsing exits 2 before dispatch or run mutation
- **THEN** full hex input is compared case-insensitively without treating a prefix or symbolic ref as a confirmed head

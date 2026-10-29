## Purpose

Preserve publication and guarded-finish contracts while distinguishing legacy
GitHub origin compatibility from full Gitea destination validation.

## MODIFIED Requirements

### Requirement: Publish only to the named repository

Publish and finish SHALL refuse without pushing when the manifest's github value is absent or origin identifies another repository. GitHub SHALL retain local-path origin acceptance; Gitea SHALL require Full Gitea origin match. With optional --expect-head SHA, finish SHALL verify the run branch head after locked reload and refuse mismatch or unreadable head before archiving or any finish side effect.

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

#### Scenario: Local origin compatibility is GitHub only
- **WHEN** GitHub and configured Gitea runs supply local-path origins
- **THEN** GitHub retains existing acceptance, but Gitea refuses before archive, push or API write because the path cannot establish its configured HTTP origin
- **THEN** an absent manifest repository retains the existing missing-GitHub-repository diagnostic and act refusal detail on every path

# run-actions Specification

## Purpose
What an interface needs to show a paused specloop run and act on it safely.

## Requirements

### Requirement: Actions in status
`specloop status --json` SHALL include, for every paused run, an `action` with
the same headline, why and command as the run's Slack message, plus the list of
`act` verbs that fit the pause.

#### Scenario: Gate action described
- **WHEN** a run is paused at a phase gate
- **THEN** its status entry has an action whose headline matches the Slack message and whose verbs are resume and abort

#### Scenario: Running run has no action
- **WHEN** a run is running
- **THEN** its status entry has no action

### Requirement: Act
`specloop act <change> <verb> [--note] --json` SHALL perform the verb when it
fits the run's current pause, exactly as the matching command would, and print
a version-1 outcome. It SHALL refuse a verb that does not fit, an unknown change
and a note over 2,000 characters.

#### Scenario: Resume with a note
- **WHEN** act resume is called with a note on a run paused as stuck
- **THEN** the run is running again and its paused task carries the note

#### Scenario: Wrong verb refused
- **WHEN** act publish is called on a run paused at a phase gate
- **THEN** the outcome is refused and the run is unchanged

### Requirement: Refusals keep their reason
When a verb is refused by the command it maps to, `act` SHALL report that
command's own reason as the outcome's `detail`, not a generic refusal.

#### Scenario: Publish refusal explained
- **WHEN** act publish is refused because the manifest names no GitHub repository
- **THEN** the outcome is refused and its detail says the manifest names no GitHub repository

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

### Requirement: Canonical command hints

New run actions and notifications SHALL name canonical nightshift commands while preserving pause semantics, action availability and output schemas.

#### Scenario: Messages and status agree
- **WHEN** each pause kind produces an action and human notification, including recovery and publish hints
- **THEN** command text names the same parseable nightshift build operation, with unchanged allowed verbs, notes, reasons and machine-readable structure
- **THEN** task 3.2 may update the expected command string in tests/test_specloop_status_json.py:132 and other expected action/hint command literals changed by this task to the nightshift spelling; the full-document equality assertion and every other field remain exactly as they are
- **THEN** this command-literal exception belongs only to task 3.2; task 1.2's help/usage-spelling rule remains unchanged

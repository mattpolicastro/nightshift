## Purpose

Let an operator land a reviewed PR in an enrolled repository with recorded human checks, eligible specloop completion, bounded CI and base synchronization, and a merge conditioned on the verified head.

## ADDED Requirements

### Requirement: Operator entry point

`nightshift.land` SHALL expose the standalone module command for a person or an explicitly delegated session. The daemon and supervisor SHALL never import or invoke it. All GitHub and specloop calls SHALL use an injectable runner.

#### Scenario: Explicit invocation
- **WHEN** an operator invokes `uv run python -m nightshift.land <pr-url-or-number>` with optional `--repo`, `--config`, `--checks-done`, and positive `--timeout` arguments
- **THEN** the module parses the request and all external operations use the supplied runner, or the subprocess runner by default

#### Scenario: Background paths excluded
- **WHEN** daemon and supervisor execution paths are inspected
- **THEN** neither imports `nightshift.land` nor dispatches the landing command, including through intake or another background helper

### Requirement: Enrolled PR identity

Landing SHALL resolve a unique GitHub.com PR and require its repository to be enrolled in the selected `config.toml` before any mutation. It SHALL operate only on open PRs targeting `main`.

#### Scenario: Repository resolves
- **WHEN** an enrolled PR is supplied by URL, by number with `--repo`, or by number in a repository resolved by `gh repo view`
- **THEN** all subsequent GitHub calls target that explicit repository and PR

#### Scenario: Invalid target refused
- **WHEN** identity is malformed or contradictory, enrollment is absent or ambiguous, configuration cannot be read, the PR is closed, or its base is not an existing `main`
- **THEN** landing exits nonzero with the reason before any check edit, finish, update or merge

### Requirement: Reliable run discovery

Landing SHALL identify a front-door PR only by a matching run's canonical `pr` URL from version-1 `specloop status --json`. It SHALL treat no match as direct only after complete successful discovery, and SHALL refuse ambiguous or unreadable discovery.

#### Scenario: Direct PR skips finish
- **WHEN** valid status contains no run matching the open non-draft PR URL
- **THEN** landing uses the body checklist and proceeds through CI and merge gates without invoking finish

#### Scenario: Uncertain discovery refused
- **WHEN** status fails, has an unsupported version or malformed data, contains any error row, or contains multiple matching runs
- **THEN** landing refuses before mutations instead of assuming a direct PR

### Requirement: Eligible run state

A matching run SHALL be paused at `human_checks` or already `done` before landing may mutate anything. Landing SHALL invoke finish only for the former state, after the human-check gate passes.

#### Scenario: Finish at human checks
- **WHEN** the matching run is paused at `human_checks` and human checks pass
- **THEN** landing refreshes eligibility and invokes `specloop finish <change>` once through the runner before waiting for CI
- **THEN** it accepts the resulting head only after status reports the same run done and its finished head equals the remote PR head

#### Scenario: Wrong state refused
- **WHEN** the matching run is running, aborted, or paused for any reason other than `human_checks`, including `ready_to_publish`
- **THEN** landing refuses immediately with that state and makes no checklist edit, finish, update or merge call

#### Scenario: Finish fails safely
- **WHEN** finish fails or its refreshed result does not establish the same done run and finished remote head
- **THEN** landing exits nonzero without waiting for CI or merging and leaves existing run recovery intact

### Requirement: Done run continuation

Landing SHALL skip finish for a matching `done` run whose PR remains open and SHALL repeat the human-check, CI, base and merge gates against the current remote head. It SHALL require no manual reset of a completed run.

#### Scenario: Retry after finish
- **WHEN** finish previously succeeded but landing stopped during CI, after an accepted branch update, or on a merge failure, and the operator invokes landing again
- **THEN** finish is not repeated, any difference from the local run head does not itself reject continuation, and fresh landing gates control the open PR

### Requirement: Human check sources

Landing SHALL gate unchecked task-list lines, recognized by a line rule wherever they appear, in the PR body and, for a front-door PR, its newest trusted daemon publication comment. It SHALL preserve historical and unrelated comments and SHALL report when neither selected source lists checks.

#### Scenario: Unticked checks refuse
- **WHEN** either the body or the selected publication comment contains an unticked `- [ ]` item and `--checks-done` is absent
- **THEN** landing refuses with the remaining items before finish, CI or merge

#### Scenario: Publication selection
- **WHEN** paginated comments include multiple rounds, unrelated comments, and untrusted marker lookalikes
- **THEN** only the newest trusted `**nightshift** —` publication comment for the matching change is selected alongside the body, accepting round-qualified and legacy markers
- **THEN** task-list lines count wherever they appear, including fenced code examples, and older publication rounds are not edited or used to block landing

#### Scenario: No listed checks
- **WHEN** neither selected source contains task-list checks, including when no publication comment exists
- **THEN** landing prints `No human checks listed`, performs no checklist edit or empty attestation, and proceeds to the remaining gates

### Requirement: Record checks in place

With `--checks-done`, landing SHALL mark unchecked items complete in their existing sources, changing each affected body or selected comment once and preserving other content. It SHALL identify the authenticated GitHub user before recording completion.

#### Scenario: Tick each source once
- **WHEN** the operator supplies `--checks-done` for unchecked items in the body and selected publication comment
- **THEN** landing reads `gh api user`, edits each affected source once, preserves existing ticks, prose and markers, and records completion through the attestation comment
- **THEN** a retry does not edit sources whose boxes are already ticked

#### Scenario: Partial edits recover
- **WHEN** identity lookup or a source edit fails, including after one source edit succeeded
- **THEN** landing refuses before finish or merge and a subsequent invocation re-reads actual source state and edits only unfinished sources

### Requirement: Stable check attestation

For listed checks and `--checks-done`, landing SHALL create or update one comment keyed by `<!-- nightshift:land-checks:<pr>:<head sha> -->`. It SHALL record the authenticated actor, checked items, source identities and pre-finish head, and reconcile ambiguous creates before any retry.

#### Scenario: Repeated attestation updates
- **WHEN** `--checks-done` is repeated for the same PR head, including after checklist edits succeeded but attestation failed
- **THEN** checked items remain eligible for attestation and the matching comment is updated rather than reposted, identifying the current authenticated actor as recording checks through `land`
- **THEN** a different head uses a different marker

#### Scenario: Lost comment response
- **WHEN** an attestation POST succeeds remotely but its response is lost and its marker is on a later comment page
- **THEN** reconciliation finds and reuses that comment without a second POST

#### Scenario: Uncertain attestation refused
- **WHEN** marker lookup fails, is ambiguous or untrusted, or an ambiguous POST cannot be reconciled
- **THEN** landing refuses without a blind POST retry or proceeding to finish or merge

### Requirement: Stable landing inputs

Landing SHALL revalidate PR head, selected check sources and applicable run eligibility before finish and before merge. Changes beyond its own validated finish or branch update SHALL stop the invocation; newly introduced checks SHALL require another operator decision.

#### Scenario: Inputs change during landing
- **WHEN** another push changes the expected head, a new publication replaces the selected comment, checklist text changes or gains unchecked items, the run becomes ineligible, or the PR closes
- **THEN** landing refuses without accepting the changed inputs as already reviewed or silently ticking new items

### Requirement: Current base at round start

Each CI round SHALL record the current `main` SHA. Landing SHALL ensure the first round's head incorporates that base, requesting a validated update before CI when needed.

#### Scenario: Stale branch on entry
- **WHEN** the initial PR head does not incorporate the recorded `main`, even if older checks are green
- **THEN** landing updates the branch using the expected-head REST request, validates the resulting merge, and waits for that head's CI

### Requirement: Bounded CI wait

Landing SHALL wait for checks on the expected PR head with a shared timeout of 1800 seconds by default, configurable by `--timeout`. It SHALL require at least one passing check and all checks to finish as pass or skipping, and SHALL refuse failure, cancellation, invalid results or timeout.

#### Scenario: Pending then green
- **WHEN** checks for the expected head progress from pending to a complete set with at least one pass and otherwise pass or skipping
- **THEN** landing proceeds to final revalidation and the base comparison, using head reads before and after check queries

#### Scenario: Failing CI refused
- **WHEN** any check fails or is cancelled, or check output is malformed or has an unknown classification
- **THEN** landing exits nonzero with the offending check or read error and never merges

#### Scenario: Shared deadline expires
- **WHEN** checks remain pending or empty, all checks are skipped, or branch-update observation exhausts the shared deadline
- **THEN** landing exits nonzero without merging, and another CI round does not reset the deadline

### Requirement: Guarded REST branch update

Landing SHALL request updates through `PUT repos/{repo}/pulls/{number}/update-branch` with `expected_head_sha` set to the verified previous head. It SHALL read `main` immediately before the request and retain that SHA as the round's baseline.

#### Scenario: Expected head supplied
- **WHEN** a branch update is required
- **THEN** the injectable runner receives the REST PUT with `expected_head_sha` and no rebase option or unsupported `gh pr update-branch` flag
- **THEN** an expected-head conflict or rejected request refuses without merging

### Requirement: Exact update provenance

Landing SHALL accept an update result only when its observed commit has exactly two parents, the previous head first and the `main` SHA read immediately before the request second. It SHALL refuse any other changed head and SHALL reconcile an ambiguous update response without blindly issuing another update.

#### Scenario: Exact merge accepted
- **WHEN** the asynchronous update produces a commit whose parents are exactly the previous head and recorded main in that order
- **THEN** that commit becomes the expected head, and its checks must pass before merge

#### Scenario: Intervening push refused
- **WHEN** another push arrives between the update request and first observed changed head, including a descendant of the expected merge or a commit merely containing the recorded main
- **THEN** parent inspection rejects it and landing performs no merge
- **THEN** reversed or additional parents and a merge against a different main SHA also refuse

#### Scenario: Lost update response
- **WHEN** the PUT response is lost but the requested update may have occurred
- **THEN** landing observes the head and applies exact parent validation without another PUT, refusing if the result cannot be established within the deadline

### Requirement: Main movement restarts CI

After green CI and final input revalidation, landing SHALL reread `main`. A different SHA SHALL require a branch update and another CI round; results for the previous head SHALL NOT authorize merge of the updated head.

#### Scenario: Main moved while waiting
- **WHEN** main changes during CI or while an asynchronous update is being observed
- **THEN** the changed SHA is detected against the retained round baseline, another round updates against a fresh main read, and landing waits for the new head's checks

### Requirement: Three round limit

Landing SHALL run at most three CI rounds, counting the initial round. If main differs from the baseline after round three, landing SHALL refuse without a fourth round or merge.

#### Scenario: Three moves refuse
- **WHEN** main moves during each of three otherwise-green CI rounds
- **THEN** landing performs the two intervening updates, exits nonzero with the observed base movements, and never starts round four or merges

### Requirement: Head conditioned merge

Landing SHALL merge with a merge commit using `gh pr merge --merge --match-head-commit <verified head>` only after all gates pass. It SHALL respect GitHub protection rules and confirm the PR is merged before reporting success.

#### Scenario: Verified PR merged
- **WHEN** human checks, run eligibility, CI and final base comparison all pass
- **THEN** the merge command names the verified head and uses no admin, auto, squash, rebase or branch-deletion option
- **THEN** confirmed merged state yields exit zero and the PR URL, without a deploy command hint

#### Scenario: Merge not confirmed
- **WHEN** a competing head change or protection rule rejects merge, or the result leaves the PR open or cannot be read
- **THEN** landing exits nonzero with the reason and does not claim success
- **THEN** a lost merge response is considered successful only if a subsequent read confirms merged state

### Requirement: Actionable refusal output

Every landing refusal SHALL return nonzero and explain its cause. Operator documentation SHALL describe flags, check sources, attestation, retries after finish, the accepted final-base race, and the need to watch the first real merge.

#### Scenario: Operator can recover
- **WHEN** a gate or external call refuses landing
- **THEN** output identifies the failed stage and reason, and documented rerun behavior does not require resetting a done run
- **THEN** documentation explains that head conditioning does not prevent main moving between its final read and merge, that checks are operator assertions, and that live merge validation remains a human check

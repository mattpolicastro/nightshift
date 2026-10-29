## Purpose

Extend reviewed PR landing to configured Gitea while preserving existing human, lifecycle and concurrency protections. These deltas replace GitHub-only wording or interfaces; unlisted requirements and their scenarios remain unchanged.

## ADDED Requirements

### Requirement: Forge specific operations preserve gates

Landing SHALL keep authorization, lifecycle, source selection, provenance and CI policy in `nightshift/land.py`, using named forge-specific operations. New Gitea operations SHALL not change GitHub argv, validation, error handling or gate ordering.

#### Scenario: Operation boundary retains behavior
- **WHEN** GitHub operations are routed through the landing boundary and Gitea operations use an injected opener
- **THEN** existing runner tests observe the same GitHub transcript, Gitea has no gh fallback, and both use the same landing gates wherever the forge-specific deltas do not prescribe a different transport

## MODIFIED Requirements

### Requirement: Operator entry point

`nightshift.land` SHALL expose the standalone module command for a person or an explicitly delegated session. The daemon and supervisor SHALL never import or invoke it. All GitHub and specloop calls SHALL use an injectable runner; Gitea HTTP SHALL use an injectable confined opener.

#### Scenario: Explicit invocation
- **WHEN** an operator invokes `uv run python -m nightshift.land <pr-url-or-number>` with optional `--repo`, `--config`, `--checks-done`, and positive `--timeout` arguments
- **THEN** the module parses the request and all subprocess operations use the supplied runner, or the subprocess runner by default, while Gitea uses its selected injectable HTTP operation boundary

#### Scenario: Background paths excluded
- **WHEN** daemon and supervisor execution paths are inspected
- **THEN** neither imports `nightshift.land` nor dispatches the landing command, including through intake or another background helper

### Requirement: Enrolled PR identity

Landing SHALL resolve a unique GitHub.com PR or configured Gitea PR and require its repository to be enrolled on that forge in the selected `config.toml` before any mutation. It SHALL operate only on open PRs targeting `main`. Gitea selection SHALL require a full configured-origin URL; existing GitHub number resolution SHALL remain unchanged.

#### Scenario: Repository resolves
- **WHEN** an enrolled GitHub PR is supplied by URL, by number with `--repo`, or by number in a repository resolved by `gh repo view`, or an enrolled Gitea PR is supplied by its configured-origin URL
- **THEN** all subsequent forge calls target that explicit repository and PR, with unchanged GitHub argv

#### Scenario: Invalid target refused
- **WHEN** identity is malformed or contradictory, enrollment is absent or ambiguous, configuration cannot be read, the PR is closed, or its base is not an existing `main`
- **THEN** landing exits nonzero with the reason before any check edit, finish, update or merge

### Requirement: Reliable run discovery

Landing SHALL identify a front-door PR only by a matching run's canonical `pr` URL from version-1 `nightshift status --json`. It SHALL treat no match as direct only after complete successful discovery, and SHALL refuse ambiguous or unreadable discovery. Canonical identity SHALL retain the selected forge's origin and path prefix.

#### Scenario: Direct PR skips finish
- **WHEN** valid status contains no run matching the open non-draft PR URL
- **THEN** landing uses the body checklist and proceeds through CI and merge gates without invoking finish

#### Scenario: Other forge rows are unrelated
- **WHEN** a status row has a well-formed HTTPS PR URL on another forge host, or a valid configured loopback HTTP Gitea PR URL unrelated to the target
- **THEN** it is unrelated to the target PR; a malformed target-host PR URL still refuses discovery, including malformed github.com URLs for GitHub landing

#### Scenario: Uncertain discovery refused
- **WHEN** status fails, has an unsupported version or malformed data, contains any error row, or contains multiple matching runs
- **THEN** landing refuses before mutations instead of assuming a direct PR

### Requirement: Record checks in place

With `--checks-done`, landing SHALL mark unchecked items complete in their existing sources, changing each affected body or selected comment once and preserving other content. It SHALL identify the authenticated operator before recording completion: GitHub through unchanged `gh api user`, Gitea through the selected human token's user endpoint and landing allowlist.

#### Scenario: Tick each source once
- **WHEN** the operator supplies `--checks-done` for unchecked items in the body and selected publication comment
- **THEN** landing reads the forge's authenticated user, edits each affected source once, preserves existing ticks, prose and markers, and records completion through the attestation comment
- **THEN** GitHub retains `gh api user` and all existing edit argv, and a retry does not edit sources whose boxes are already ticked

#### Scenario: Partial edits recover
- **WHEN** identity lookup or a source edit fails, including after one source edit succeeded
- **THEN** landing refuses before finish or merge and a subsequent invocation re-reads actual source state and edits only unfinished sources

### Requirement: Guarded REST branch update

GitHub landing SHALL request updates through `PUT repos/{repo}/pulls/{number}/update-branch` with `expected_head_sha` set to the verified previous head. Gitea SHALL use the single-attempt merge-style POST and pre-read expected-head check specified by gitea-landing. Both SHALL read `main` immediately before the request and retain that SHA as the round's baseline, requiring exact ordered parent provenance before adopting a head.

#### Scenario: Expected head supplied
- **WHEN** a GitHub branch update is required
- **THEN** the injectable runner receives the unchanged REST PUT with `expected_head_sha` and no rebase option or unsupported `gh pr update-branch` flag
- **THEN** an expected-head conflict or rejected request refuses without merging

### Requirement: Head conditioned merge

Landing SHALL merge with a merge commit only after all gates pass, using unchanged `gh pr merge --merge --match-head-commit <verified head>` on GitHub or Gitea POST with `Do=merge` and `head_commit_id=<verified head>`. It SHALL respect forge protection rules and confirm the PR is merged before reporting success.

#### Scenario: Verified PR merged
- **WHEN** human checks, run eligibility, CI and final base comparison all pass
- **THEN** the merge operation names the verified head and uses no admin, auto, squash, rebase or branch-deletion option
- **THEN** confirmed merged state yields exit zero and the PR URL, without a deploy command hint

#### Scenario: Merge not confirmed
- **WHEN** a competing head change or protection rule rejects merge, or the result leaves the PR open or cannot be read
- **THEN** landing exits nonzero with the reason and does not claim success
- **THEN** a lost merge response is considered successful only if a subsequent read confirms merged state

### Requirement: Landing interpreter prerequisites

The prerequisites passage of `docs/landing.md` SHALL require the interpreter running landing to import `nightshift.cli` without requiring its console script on PATH. It SHALL describe omitted or explicit specloop enrollment and explain that explicit worker enrollment fails configuration loading because the engine is retired. Gitea support SHALL preserve current-interpreter execution, operator authentication, canonical PR run discovery and landing gates while documenting forge-specific credentials.

#### Scenario: Operator prepares the environment
- **WHEN** an operator follows the landing prerequisites
- **THEN** the guide explains the interpreter import and applicable run-environment requirements without requiring virtual-environment activation or a nightshift executable on PATH
- **THEN** it retains `uv` and, for GitHub, `gh` on PATH, operator authentication with permission to read/edit PRs and issue comments, update the branch and merge under repository protection rules, and exactly one case-insensitive enrollment
- **THEN** Gitea instead uses its configured URL and human land_token_env, with separate #129 finish credentials where applicable
- **THEN** omitted and explicit specloop engines are supported, explicit worker enrollment is refused with migration guidance, and the current interpreter's `-m nightshift.cli status --json` must still read the applicable runs
- **THEN** front-door discovery still uses canonical PR URL identity independently of labels or branch names, with canonical nightshift.cli status/finish argv and unchanged validation and landing gates

### Requirement: GitHub kind enrollment boundary

`enrolled_repo` SHALL count only rows with no forge selection or a selected forge of kind github when authorizing GitHub landing. The function SHALL retain its existing identity and ambiguity diagnostics. Gitea landing SHALL require enrollment on its independently resolved configured forge and SHALL not broaden GitHub authority.

#### Scenario: Gitea enrollment grants no GitHub authority
- **WHEN** landing resolves a GitHub PR whose owner/name appears only in a Gitea enrollment
- **THEN** it refuses as not enrolled before any mutation, even if the names match exactly
- **THEN** legacy and explicitly GitHub-kind rows remain eligible for all existing gates, and existing invalid-identity and ambiguous-enrollment assertions still pass

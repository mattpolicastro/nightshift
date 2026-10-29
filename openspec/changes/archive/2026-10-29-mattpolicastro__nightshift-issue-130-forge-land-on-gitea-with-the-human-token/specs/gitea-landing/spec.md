## Purpose

Let a human land a reviewed Gitea PR with a separate human credential, preserving existing landing gates and confirming a merge pinned to the verified head.

## ADDED Requirements

### Requirement: Configured landing target

Landing SHALL select Gitea by a full PR URL's hostname against exactly one configured Gitea forge and then verify its full configured origin, prefix, enrolled repository and PR identity. It SHALL refuse ambiguous or foreign destinations without GitHub fallback. GitHub number and URL behavior SHALL remain unchanged.

#### Scenario: Configured URL selects Gitea
- **WHEN** a PR URL uniquely matches a configured Gitea hostname, scheme, effective port and prefix and its repository is enrolled on that forge
- **THEN** landing uses the configured repository spelling and confined API paths, supports configured loopback HTTP, and requires the returned matching PR to be open and target existing main
- **THEN** the configured case-sensitive WIP prefix determines draft state and canonical run discovery retains origin and prefix

#### Scenario: Invalid or ambiguous target refuses
- **WHEN** host matching is absent or ambiguous, the origin or repository contradicts configuration or `--repo`, the URL is unsafe, or returned PR identity/base/state is invalid
- **THEN** landing refuses before mutations, without sending credentials to the supplied URL or invoking gh as a fallback
- **THEN** a bare number cannot select Gitea and a Gitea enrollment cannot authorize a GitHub PR

### Requirement: Human credential source

Gitea landing SHALL use only the variable selected by `land_token_env`, read by land from the operator environment overlaid by `~/.config/nightshift/land.env`. File values SHALL win even when empty. Parsing SHALL not execute shell text. Loaded values SHALL remain invocation-local; errors SHALL not expose secrets.

#### Scenario: Landing file overrides process token
- **WHEN** the selected human variable appears in land.env and the process, alongside bot credentials and unrelated variables
- **THEN** every landing Gitea read and write uses the selected file value, without consulting token_env, loading the bot env file, mutating os.environ or adding secrets to child argv/environment
- **THEN** quoted export assignments are parsed without expansion, and absent land.env permits the selected process variable

#### Scenario: Missing human token refuses
- **WHEN** the selected human value is absent or empty, including an empty file value shadowing a nonempty process value, or land.env is unreadable
- **THEN** landing refuses before HTTP even if bot credentials or ambient gh authentication are available
- **THEN** diagnostics may identify the variable and stage but never its value, remote response bodies or underlying secret-bearing errors

### Requirement: Allowlisted landing evidence

Gitea publication and attestation trust SHALL derive solely from case-insensitive membership of `user.login` in the selected forge's `trusted_logins`. A listed publication bot SHALL be eligible; server associations, repository ownership and bot identity alone SHALL confer no trust. A human recording listed checks SHALL be authenticated, allowlisted and different from `bot_login` before source writes.

#### Scenario: Listed authors supply evidence
- **WHEN** complete comment pages contain publication rounds by a listed bot, an unlisted owner and listed human attestations
- **THEN** land selects the newest valid listed publication and may update the unique listed attestation, independently of adapter-projected or server-supplied association
- **THEN** the actual human token's user is recorded as actor and landing-specific writes do not use bot-owned comment mutation helpers

#### Scenario: Untrusted evidence blocks attestation
- **WHEN** the recording actor is unlisted, missing or the configured bot, or an attestation marker matches an unlisted author, duplicate comments or multiple occurrences
- **THEN** actor failure precedes source edits and marker uncertainty blocks creation/continuation before finish or merge
- **THEN** an empty allowlist or an unlisted repository owner grants no exception

### Requirement: Existing human check semantics

Gitea landing SHALL preserve the existing checklist line rule, publication token matching, source selection, in-place edits, attestation format, one-create reconciliation, source revalidation and duplicate recovery. It SHALL use complete comment pagination and SHALL NOT infer Markdown or HTML structure to hide candidate evidence.

#### Scenario: Physical lines and literal markers
- **WHEN** selected sources contain checks or marker lookalikes inside fences, HTML, blockquotes or indented text
- **THEN** every line matching `^[ \t>]*[-*+] \[( |x|X)\] ` counts, and only the matched checkbox state changes during completion
- **THEN** publication uses the existing body-prefix and literal change/optional-round token rules; attestation uses exact marker substring/occurrence counting, with duplicate or untrusted matches refusing
- **THEN** prose, line endings, existing ticks and unrelated comments remain intact, and copied checklist text is HTML-escaped in attestation

#### Scenario: Partial and uncertain writes recover
- **WHEN** a checklist edit fails, a single attestation POST loses its response, or a later invocation sees a late duplicate
- **THEN** completed source edits persist, within-invocation reconciliation reads all comment pages without repeating POST, and an unresolved result blocks finish/merge
- **THEN** a later invocation rereads actual state, edits only unfinished sources, updates a unique trusted marker or refuses duplicates with existing recovery guidance; no cross-attempt create guard is added

### Requirement: Landing lifecycle parity

Gitea landing SHALL preserve canonical complete run discovery, eligibility, head-guarded finish, done-run continuation and stable-input revalidation. It SHALL invoke unchanged status and finish argv through the current interpreter; finish SHALL retain its separately provisioned #129 credentials and behavior.

#### Scenario: Finish and direct continuation
- **WHEN** discovery establishes a matching human_checks run, an initially done run or no matching run
- **THEN** only the human_checks run receives finish once with the validated `--expect-head`, and its successor is accepted only after the same run is done and its head equals the remote PR head
- **THEN** an initially done run or direct non-WIP PR skips finish and repeats all applicable human-check, CI, base and merge gates

#### Scenario: Changed inputs refuse
- **WHEN** discovery is unreadable or ambiguous, eligibility is wrong, finish fails, or head, check sources or run identity change outside validated finish/update paths
- **THEN** landing refuses before the next mutation rather than assuming a direct PR, adopting an unrelated head, ticking new checks or resetting done state

### Requirement: Initial Gitea base ancestry

Before first-round CI, landing SHALL establish whether the expected head includes captured main using exact-SHA commit parents, with each visited SHA read at most once and all reads subject to the shared deadline. Unknown evidence SHALL refuse rather than authorize an update.

#### Scenario: Ancestry determines update need
- **WHEN** the head equals main, a complete parent traversal reaches main, or complete ancestry is exhausted without main
- **THEN** equality needs no ancestry request, proven inclusion skips the initial update, and proven absence requires the guarded merge-style update before CI

#### Scenario: Ancestry cannot be established
- **WHEN** a commit SHA or parent list is malformed or mismatched, a read fails, or the shared deadline expires during traversal
- **THEN** landing refuses without treating unknown ancestry as stale or green and without dispatching an update or merge

### Requirement: Single attempt update provenance

Each required Gitea update SHALL first re-read the expected head H and main B, then send exactly one POST to `/api/v1/repos/{o}/{r}/pulls/{n}/update` with query `style=merge`. It SHALL never retry that POST or use rebase. Only a matching new commit SHA with exactly ordered parents `[H, B]` SHALL advance the expected head.

#### Scenario: One update establishes exact parents
- **WHEN** an open non-WIP PR still has expected head H and an update against immediately read main B is accepted or its response is uncertain
- **THEN** exactly one merge-style POST is sent for that update and read-only observation accepts only the first changed head whose commit has exactly parents `[H, B]`
- **THEN** B remains the round baseline and the accepted successor requires fresh CI, even if main moved while observation ran

#### Scenario: Races and update rejection refuse
- **WHEN** the pre-update head changed, the request is definitely rejected, or the observed new head is a descendant, has missing/reversed/extra parents, merges another base or has malformed commit facts
- **THEN** a pre-update head mismatch sends no POST, definite rejection does not poll, and invalid provenance refuses before successor CI or merge
- **THEN** no failed or uncertain update is replayed; unreadable/closed/WIP observations or an unchanged head through the deadline refuse

### Requirement: Exact head combined CI

Gitea landing SHALL query combined status for the exact expected full head SHA through `checks_for_sha`, with head reads before and after each query. It SHALL preserve #122 mapping, require passing evidence, and retain the existing shared deadline, polling cadence and three-round limit.

#### Scenario: Combined states control progress
- **WHEN** exact-head combined statuses are success, pending, failure, error, warning, skipped or proven empty
- **THEN** success maps to pass, pending to pending, failure/error/warning to fail, skipped to skipping and empty to not yet registered
- **THEN** only valid passing evidence proceeds; pending, empty and skipped-only wait, failure refuses, and no branch, merge-ref or Actions query substitutes for the pinned SHA

#### Scenario: CI uncertainty and deadline refuse
- **WHEN** status evidence has the wrong SHA, malformed/unknown/inconsistent pages, a bracketing head mismatch, or the shared deadline expires during requests, GET retries or polling
- **THEN** landing refuses without merge, no HTTP attempt starts after expiry, and neither a retry nor a new round resets the deadline

#### Scenario: Main movement requires fresh CI
- **WHEN** main differs from the retained baseline after green CI and stable-input revalidation
- **THEN** a further permitted round uses a new single-attempt update and fresh successor-head status; movement after round three refuses without a fourth round or merge

### Requirement: Pinned confirmed Gitea merge

After every landing gate passes, landing SHALL POST once to `/api/v1/repos/{o}/{r}/pulls/{n}/merge` with JSON `Do=merge` and `head_commit_id` equal to the verified head. It SHALL respect server protections and confirm matching PR identity and boolean `merged: true` through a subsequent read before reporting success.

#### Scenario: Merge is pinned and confirmed
- **WHEN** all gates pass for head H and a subsequent PR read confirms merged after the request, including after a lost acknowledgement
- **THEN** the sole merge payload is `{"Do":"merge","head_commit_id":H}`, using the human credential, with no force/admin, auto, squash, rebase or branch-deletion option
- **THEN** landing reports the merged PR URL and exit zero without a deploy hint

#### Scenario: Unconfirmed merge refuses
- **WHEN** protection or a competing push rejects merge, or confirmation is unreadable, contradictory, open, merely closed or lacks boolean merged true
- **THEN** landing reports nonzero without claiming success or repeating the merge POST, retaining done-run recovery guidance
- **THEN** merge and confirmation retain separate ordinary bounded call timeouts after the CI budget ends

### Requirement: Offline compatibility evidence

Automated verification SHALL exercise Gitea through socket-free injected transport and clocks and preserve all existing GitHub assertions. GitHub argv, payload text and operation order SHALL remain byte-identical. The full pytest suite SHALL pass and the existing type baseline SHALL not grow.

#### Scenario: GitHub transcripts remain exact
- **WHEN** legacy and explicitly GitHub-kind landing exercise direct, finish, attestation, update, CI, merge and refusal paths, including Factory preflight/handoff
- **THEN** recording runners observe the unchanged full argv and ordering, with no Gitea credential load or additional GitHub calls
- **THEN** the four issue-named regression tests and existing assertions pass without live network or a logged-in host

### Requirement: Gitea operator guidance

`docs/landing.md` SHALL document Gitea URL selection, human token file/precedence, trusted logins, independent finish provisioning, single-attempt updates, exact-head CI, pinned merge confirmation and recovery limits. It SHALL distinguish terminal Gitea support from unchanged Factory UI and disabled daemon dispatch and retain the live validation checklist.

#### Scenario: Operator can prepare and recover
- **WHEN** an operator follows the landing guide
- **THEN** examples explain the configured land_token_env assignment in land.env, no bot fallback, expected trust membership, custom config alignment and unchanged GitHub commands
- **THEN** the guide describes update/provenance and final-base races, existing recovery cases and the pending human observation of the first real Gitea merge commit, without claiming CI establishes live permissions or merge behavior

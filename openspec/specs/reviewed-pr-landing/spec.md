# reviewed-pr-landing Specification

## Purpose
Let an operator land a reviewed PR in an enrolled repository with recorded human checks, eligible specloop completion, bounded CI and base synchronization, and a merge conditioned on the verified head.

## Requirements

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

### Requirement: Eligible run state

A matching run SHALL be paused at `human_checks` or already `done` before landing may mutate anything. Landing SHALL invoke finish only for the former state, after the human-check gate passes, supplying the validated PR head through `--expect-head` for verification under the run lock.

#### Scenario: Finish at human checks
- **WHEN** the matching run is paused at `human_checks` and human checks pass
- **THEN** landing refreshes eligibility and invokes `nightshift finish <change> --expect-head <validated-pr-head>` once through the runner before waiting for CI
- **THEN** it accepts the resulting head only after status reports the same run done and its finished head equals the remote PR head

#### Scenario: Wrong state refused
- **WHEN** the matching run is running, aborted, or paused for any reason other than `human_checks`, including `ready_to_publish`
- **THEN** landing refuses immediately with that state and makes no checklist edit, finish, update or merge call

#### Scenario: Finish fails safely
- **WHEN** finish fails or its refreshed result does not establish the same done run and finished remote head
- **THEN** landing exits nonzero without waiting for CI or merging; direct finish/act without the optional head guard retains existing run recovery, while guarded landing refuses a local/remote head mismatch

#### Scenario: Revision wins the finish handoff
- **WHEN** revision republishes after land's final identity check but before finish obtains the run lock, including with unchanged pause_id
- **THEN** finish receives the head land validated, refuses the new branch head under the lock, and no archive, finish push, ready transition or merge occurs
- **THEN** this protection applies to terminal and Factory invocations without changing land's attestation or successor-head gates

#### Scenario: Archived commit was not pushed
- **WHEN** finish archives local head H into A but its remote push fails, leaving human_checks and remote head H, and terminal landing is retried
- **THEN** landing passes H to finish again and locked finish refuses local A before another archive, push or ready call, and landing does not wait for CI or merge
- **THEN** Factory also refuses this local/remote mismatch rather than adopting A or falling back to unguarded finish
- **WHEN** the operator inspects the local archive and remote state and completes direct finish or act finish without --expect-head
- **THEN** existing recovery reuses A without rearchiving, pushes it and marks the run done; a subsequent terminal landing skips finish and applies fresh human-check, CI, base and merge gates

#### Scenario: Archived commit was already pushed
- **WHEN** the archive push succeeded but ready or state completion failed, leaving human_checks with matching local and remote head A
- **THEN** landing passes A to finish and guarded recovery retains the existing archive/push/ready recovery and validated successor-head checks

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

With `--checks-done`, landing SHALL mark unchecked items complete in their existing sources, changing each affected body or selected comment once and preserving other content. It SHALL identify the authenticated operator before recording completion: GitHub through unchanged `gh api user`, Gitea through the selected human token's user endpoint and landing allowlist.

#### Scenario: Tick each source once
- **WHEN** the operator supplies `--checks-done` for unchecked items in the body and selected publication comment
- **THEN** landing reads the forge's authenticated user, edits each affected source once, preserves existing ticks, prose and markers, and records completion through the attestation comment
- **THEN** GitHub retains `gh api user` and all existing edit argv, and a retry does not edit sources whose boxes are already ticked

#### Scenario: Partial edits recover
- **WHEN** identity lookup or a source edit fails, including after one source edit succeeded
- **THEN** landing refuses before finish or merge and a subsequent invocation re-reads actual source state and edits only unfinished sources

### Requirement: Stable check attestation

For listed checks and `--checks-done`, landing SHALL look up `<!-- nightshift:land-checks:<pr>:<head sha> -->`, update its unique trusted match or POST once when absent, and record actor, items, sources and pre-finish head. Within each invocation it SHALL reconcile ambiguous creates before continuing and SHALL NOT repeat the POST. Fresh invocations SHALL repeat lookup without a cross-attempt create guard; late remote commits can produce duplicates.

#### Scenario: Repeated attestation updates
- **WHEN** `--checks-done` is repeated for the same PR head, including after checklist edits succeeded but attestation failed, and lookup finds one trusted matching comment
- **THEN** checked items remain eligible for attestation and the matching comment is updated rather than reposted, identifying the current authenticated actor as recording checks through `land`
- **THEN** a different head uses a different marker

#### Scenario: Lost comment response
- **WHEN** an attestation POST succeeds remotely but its response is lost and its marker is on a later comment page
- **THEN** reconciliation finds and reuses that comment without a second POST

#### Scenario: Uncertain attestation refused
- **WHEN** marker lookup fails, is ambiguous or untrusted, or an ambiguous POST cannot be reconciled
- **THEN** that invocation refuses without a blind POST retry or proceeding to finish or merge

#### Scenario: Late create crosses invocation boundaries
- **WHEN** invocation A loses its POST response and refuses after reconciliation finds none, invocation B finds none and posts, and A's POST subsequently commits remotely
- **THEN** two keyed comments may exist even after A's local process group has drained; B's valid acknowledgement need not cause another lookup and no cross-attempt guard is added
- **THEN** a subsequent invocation observing both refuses with `duplicate marker matches; resolve duplicates before retrying` before finish or merge
- **WHEN** the operator inspects effects, deletes one duplicate and explicitly retries while eligible
- **THEN** land updates the sole remaining trusted comment and may land only through all existing head, finish, CI and merge gates

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

Landing SHALL wait for checks on the expected PR head with a shared timeout of 1800 seconds by default, configurable by `--timeout`. Except when the Bounded docs only exemption is established and attested, it SHALL require at least one passing check and all checks to finish as pass or skipping, and SHALL refuse failure, cancellation, invalid results or timeout. The response defined by `CI registration delay` SHALL be pending rather than invalid; no other malformed-response exception is introduced.

#### Scenario: Pending then green
- **WHEN** checks for the expected head progress from pending to a complete set with at least one pass and otherwise pass or skipping
- **THEN** landing proceeds to final revalidation and the base comparison, using head reads before and after check queries

#### Scenario: Failing CI refused
- **WHEN** any check fails or is cancelled, or check output is malformed or has an unknown classification, excluding the recognized pending registration response
- **THEN** landing exits nonzero with the offending check or read error and never merges

#### Scenario: Shared deadline expires
- **WHEN** checks remain pending or empty without an established docs-only exemption, all checks are skipped, or branch-update observation exhausts the shared deadline
- **THEN** landing exits nonzero without merging, and another CI round does not reset the deadline
- **THEN** persistent recognized registration delay receives the specific missing-check diagnostic, while other timeout refusals retain their existing behavior

### Requirement: Guarded REST branch update

GitHub landing SHALL request updates through `PUT repos/{repo}/pulls/{number}/update-branch` with `expected_head_sha` set to the verified previous head. Gitea SHALL use the single-attempt merge-style POST and pre-read expected-head check specified by gitea-landing. Both SHALL read `main` immediately before the request and retain that SHA as the round's baseline, requiring exact ordered parent provenance before adopting a head.

#### Scenario: Expected head supplied
- **WHEN** a GitHub branch update is required
- **THEN** the injectable runner receives the unchanged REST PUT with `expected_head_sha` and no rebase option or unsupported `gh pr update-branch` flag
- **THEN** an expected-head conflict or rejected request refuses without merging

### Requirement: Exact update provenance

Landing SHALL accept an update result only when its observed commit has exactly two parents, the previous head first and the `main` SHA read immediately before the request second. It SHALL refuse any other changed head and SHALL reconcile an ambiguous update response without blindly issuing another update.

#### Scenario: Exact merge accepted
- **WHEN** the asynchronous update produces a commit whose parents are exactly the previous head and recorded main in that order
- **THEN** that commit becomes the expected head, and its checks must pass or its own Bounded docs only exemption must be freshly established and attested before merge

#### Scenario: Intervening push refused
- **WHEN** another push arrives between the update request and first observed changed head, including a descendant of the expected merge or a commit merely containing the recorded main
- **THEN** parent inspection rejects it and landing performs no merge
- **THEN** reversed or additional parents and a merge against a different main SHA also refuse

#### Scenario: Lost update response
- **WHEN** the PUT response is lost but the requested update may have occurred
- **THEN** landing observes the head and applies exact parent validation without another PUT, refusing if the result cannot be established within the deadline

### Requirement: Main movement restarts CI

After green CI or an established and attested Bounded docs only exemption, and final input revalidation, landing SHALL reread `main`. A different SHA SHALL require a branch update and another CI round; results for the previous head SHALL NOT authorize merge of the updated head.

#### Scenario: Main moved while waiting
- **WHEN** main changes during CI or while an asynchronous update is being observed
- **THEN** the changed SHA is detected against the retained round baseline, another round updates against a fresh main read, and landing evaluates the new head through fresh checks or a newly established and attested Bounded docs only exemption

### Requirement: Three round limit

Landing SHALL run at most three CI rounds, counting the initial round. If main differs from the baseline after round three, landing SHALL refuse without a fourth round or merge.

#### Scenario: Three moves refuse
- **WHEN** main moves during each of three otherwise-green CI rounds
- **THEN** landing performs the two intervening updates, exits nonzero with the observed base movements, and never starts round four or merges

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

### Requirement: Actionable refusal output

Every landing refusal SHALL return nonzero and explain its cause. Operator documentation SHALL describe flags, check sources, attestation, retries after finish, the accepted final-base race, and the need to watch the first real merge.

#### Scenario: Operator can recover
- **WHEN** a gate or external call refuses landing
- **THEN** output identifies the failed stage and reason, and documented rerun behavior does not require resetting a done run
- **THEN** documentation explains that head conditioning does not prevent main moving between its final read and merge, that checks are operator assertions, and that live merge validation remains a human check

### Requirement: CI registration delay

Landing SHALL treat `gh pr checks` exit 1 with exactly empty stdout and stderr containing the case-sensitive literal `no checks reported` as pending CI registration unless the Bounded docs only exemption is established and attested, polling within the existing shared deadline with unchanged head validation.

#### Scenario: Checks register after push
- **WHEN** finish pushes a new head and checks queries return exit 1, exactly empty stdout, and stderr containing the case-sensitive literal `no checks reported`, then pending checks, then at least one pass with all checks pass or skipping before the deadline
- **THEN** landing treats the registration response as pending rather than green or malformed JSON, retains the existing poll cadence, reads the expected new head before and after every query, and proceeds through the existing final revalidation, base, and merge gates only after green checks

#### Scenario: No checks before deadline
- **WHEN** no docs-only exemption has completed before the existing shared deadline expires and the most recent successfully classified, head-bracketed poll returned the recognized registration response
- **THEN** landing exits nonzero with `no CI checks were reported for head <sha> within <timeout>s; inspect checks and rerun land`, identifying the expected full head SHA and configured total CI timeout in seconds, without a malformed-JSON diagnosis, another poll after expiry, or a merge
- **THEN** previous command time and CI rounds consume the same budget, and the reported timeout is the configured total rather than a new registration grace period

#### Scenario: Other responses retain validation
- **WHEN** a response has another exit code, stdout that is not exactly empty (including whitespace-only stdout), or stderr without the case-sensitive literal `no checks reported`, including after a recognized registration delay
- **THEN** landing applies its existing JSON, check-schema, classification, and exit-status validation, refusing malformed or inconsistent results immediately
- **THEN** an ordinary pending, empty-array, or skipped-only response restores ordinary waiting and its existing deadline refusal unless a validated empty array qualifies for the Bounded docs only exemption; any registered check permanently disables that exemption for this head, and failing or cancelled checks still refuse without merging

#### Scenario: Head changes during registration
- **WHEN** the head read before a query, after a registration-delay response, or before the next poll differs from the expected head
- **THEN** landing refuses with the existing head-change reason and review guidance, without accepting the new head, merging, or replacing that reason with a missing-check timeout

### Requirement: Discovery through the interpreter

Landing SHALL invoke every specloop status read as `[sys.executable, "-m", "nightshift.cli", "status", "--json"]` through the existing injectable runner.

#### Scenario: Console script absent
- **WHEN** the interpreter running landing can import `nightshift.cli` but PATH contains no nightshift executable
- **THEN** initial discovery and later status refreshes use that interpreter and exact module argv with existing timeout and JSON validation behavior
- **THEN** no console-script lookup or fallback is attempted, including for a direct PR

#### Scenario: Module invocation fails
- **WHEN** the module cannot be imported or status exits unsuccessfully
- **THEN** landing refuses through the existing run-discovery error path before subsequent mutations and does not assume the PR is direct

### Requirement: Finish through the interpreter

Landing SHALL invoke eligible specloop finish as `[sys.executable, "-m", "nightshift.cli", "finish", change, "--expect-head", validated_pr_head]` through the existing injectable runner.

#### Scenario: Eligible finish uses the current interpreter
- **WHEN** a matching run paused at human checks passes all existing pre-finish gates
- **THEN** landing invokes finish once using the current interpreter, passing the just-validated pre-finish PR head and preserving the change argument and 300-second limit
- **THEN** finish failure or failure to establish the same done run and matching remote head refuses before CI or merge; direct PRs and initially done runs still skip finish

### Requirement: Landing interpreter prerequisites

The prerequisites passage of `docs/landing.md` SHALL require the interpreter running landing to import `nightshift.cli` without requiring its console script on PATH. It SHALL describe omitted or explicit specloop enrollment and explain that explicit worker enrollment fails configuration loading because the engine is retired. Gitea support SHALL preserve current-interpreter execution, operator authentication, canonical PR run discovery and landing gates while documenting forge-specific credentials.

#### Scenario: Operator prepares the environment
- **WHEN** an operator follows the landing prerequisites
- **THEN** the guide explains the interpreter import and applicable run-environment requirements without requiring virtual-environment activation or a nightshift executable on PATH
- **THEN** it retains `uv` and, for GitHub, `gh` on PATH, operator authentication with permission to read/edit PRs and issue comments, update the branch and merge under repository protection rules, and exactly one case-insensitive enrollment
- **THEN** Gitea instead uses its configured URL and human land_token_env, with separate #129 finish credentials where applicable
- **THEN** omitted and explicit specloop engines are supported, explicit worker enrollment is refused with migration guidance, and the current interpreter's `-m nightshift.cli status --json` must still read the applicable runs
- **THEN** front-door discovery still uses canonical PR URL identity independently of labels or branch names, with canonical nightshift.cli status/finish argv and unchanged validation and landing gates

### Requirement: Narrow uncertain update responses

Landing SHALL reconcile the specified nonzero parser-error and silent-output PUT responses as uncertain only after ruling out definite failures. Existing transport-loss reconciliation SHALL remain supported.

#### Scenario: Parser error after accepted update
- **WHEN** update-branch exits nonzero, stderr contains the case-sensitive literal `unexpected end of JSON input`, stdout is empty or unparseable, and no definite-failure rule applies
- **THEN** landing observes the head under the existing reconciliation rules instead of refusing solely for that parser diagnostic
- **THEN** the rule is not restricted to exit 1 and does not apply to other GitHub operations

#### Scenario: Silent update result
- **WHEN** update-branch exits nonzero with exactly empty stdout and stderr and no known authentication exit
- **THEN** landing treats the result as uncertain and observes the head without another PUT
- **THEN** whitespace-only streams do not qualify as a silent response

#### Scenario: Existing transport loss
- **WHEN** an existing recognized transport-loss diagnostic or subprocess exception makes the PUT outcome uncertain and no definite rejection is established
- **THEN** landing retains its existing bounded reconciliation and single-request behavior

### Requirement: Definite update failures refuse promptly

Landing SHALL refuse nonzero PUT results reporting an HTTP status, a JSON document, a known authentication exit, or an otherwise unrecognized diagnostic before polling. These refusals SHALL take precedence over parser-error and transport-loss wording.

#### Scenario: JSON error takes precedence
- **WHEN** a nonzero PUT returns an expected-head mismatch or conflict JSON document in stdout or a complete JSON document in stderr, including alongside parser-error wording
- **THEN** landing immediately reports a branch-update refusal without observation, sleep, another PUT or merge
- **THEN** any other parseable JSON document on a nonzero exit also refuses conservatively

#### Scenario: HTTP error takes precedence
- **WHEN** a nonzero PUT's stderr carries an HTTP status, including 403, 409 or 422, with or without `unexpected end of JSON input` or transport-loss wording
- **THEN** landing refuses immediately with the branch-update diagnostic and performs no observation, sleep, repeat PUT or merge
- **THEN** status detection is case-insensitive and accepts an optional HTTP version

#### Scenario: Other failures do not wait
- **WHEN** the nonzero PUT has a plain-text authentication or permission error, a known authentication exit of 4 even without output, or any output outside the recognized uncertain cases
- **THEN** landing refuses immediately without observation or sleep and preserves the branch-update diagnostic and exit code
- **THEN** empty stdout alone cannot hide an unrelated stderr diagnostic

### Requirement: Uncertain update observation preserves gates

An uncertain PUT result SHALL use the existing exact ordered two-parent check and shared deadline before advancing the expected head. Landing SHALL NOT repeat that PUT to resolve uncertainty.

#### Scenario: Proven update proceeds through fresh gates
- **WHEN** observation after an uncertain PUT first sees the previous head H and then a commit with a matching SHA and exactly ordered parents `[H, B]`, where B is main read immediately before the PUT
- **THEN** landing adopts that head, retains B as the round baseline and requires fresh head-bracketed CI and all existing final checks before a head-conditioned merge
- **THEN** exactly one PUT was issued for that update, carrying `expected_head_sha=H`

#### Scenario: Unexpected parents refuse
- **WHEN** the first changed head after an uncertain PUT is a descendant, has reversed or extra parents, merges another base, or cannot be established as the exact requested merge
- **THEN** landing refuses provenance without CI for an accepted replacement head, merge or another PUT

#### Scenario: Observation cannot establish the update
- **WHEN** the head stays unchanged until the shared deadline or a reconciliation read fails, returns invalid metadata, or reveals a closed or draft PR
- **THEN** landing refuses without another PUT or merge and reports the original uncertain-response error with the reconciliation cause
- **THEN** command time and capped sleeps consume the original deadline across CI rounds, with no reset, extra grace period or read after expiry

### Requirement: GitHub kind enrollment boundary

`enrolled_repo` SHALL count only rows with no forge selection or a selected forge of kind github when authorizing GitHub landing. The function SHALL retain its existing identity and ambiguity diagnostics. Gitea landing SHALL require enrollment on its independently resolved configured forge and SHALL not broaden GitHub authority.

#### Scenario: Gitea enrollment grants no GitHub authority
- **WHEN** landing resolves a GitHub PR whose owner/name appears only in a Gitea enrollment
- **THEN** it refuses as not enrolled before any mutation, even if the names match exactly
- **THEN** legacy and explicitly GitHub-kind rows remain eligible for all existing gates, and existing invalid-identity and ambiguous-enrollment assertions still pass

### Requirement: Forge specific operations preserve gates

Landing SHALL keep authorization, lifecycle, source selection, provenance and CI policy in `nightshift/land.py`, using named forge-specific operations. New Gitea operations SHALL not change existing GitHub argv, validation, error handling or relative gate ordering. The docs-only extension MAY add named changed-path/workflow reads after a validated empty-check response and the distinct exemption attestation before final revalidation/base comparison; nonempty-check paths SHALL retain their exact transcripts.

#### Scenario: Operation boundary retains behavior
- **WHEN** GitHub operations are routed through the landing boundary and Gitea operations use an injected opener
- **THEN** existing runner tests observe the same existing GitHub operations in order, with only the specified empty-check evidence and exemption-attestation additions, Gitea has no gh fallback, and both use the same landing gates wherever the forge-specific deltas do not prescribe a different transport

### Requirement: Base workflow exclusion proof

Landing SHALL establish exclusion only from a complete nonempty PR changed-path set and supported CI `paths-ignore` patterns read at the recorded base SHA. It SHALL include deletions and both names of renames and SHALL require every changed path to be ignored for every supported declared event. It SHALL use the single-workflow selection and deterministic physical-line/glob rules in the design, and SHALL retain ordinary CI waiting whenever proof is unavailable or ambiguous.

#### Scenario: Base content controls exclusion
- **WHEN** the PR changes root and nested Markdown files and the sole base workflow is `ci.yml` with `paths-ignore: ['**.md']` for its pull_request and optional push events
- **THEN** landing establishes exclusion using that base SHA, never the local checkout or PR-head workflow content
- **THEN** a different ignore list on the head cannot authorize the exemption

#### Scenario: Mixed and renamed paths need CI
- **WHEN** any changed path is not ignored, including a deletion or the old name of a code-to-Markdown rename
- **THEN** landing cannot establish exclusion and retains ordinary CI rules
- **THEN** matching is case-sensitive against full paths, so `*.md` does not ignore nested Markdown while `**.md` does

#### Scenario: Incomplete or ambiguous evidence needs CI
- **WHEN** paths are empty, missing, malformed, duplicated or incomplete, pagination/counts cannot establish completeness, a provider cap is reached, or workflow selection/content/syntax/globs are unsupported or ambiguous
- **THEN** landing cannot use the exemption, including for multiple workflows, missing ignore lists, negation, duplicate trigger fields or unreadable base content
- **THEN** a deadline or head/draft failure still refuses through the existing gate rather than becoming permission to skip CI

### Requirement: Bounded docs only exemption

For proven excluded paths, landing SHALL accept the CI gate without passing checks only after two head-bracketed empty observations at least one existing 10-second poll interval apart, before the unchanged shared deadline. Evidence SHALL be evaluated on the first empty poll before sleeping. Any observed registered check SHALL disable this exception for that head for the rest of the invocation. Each validated successor head and base round SHALL require fresh proof and registration grace.

#### Scenario: Docs only PR lands without CI
- **WHEN** an otherwise eligible excluded PR reports no registered checks at the initial poll and again at least 10 seconds later within the budget
- **THEN** landing records and prints `CI not required: docs-only`, completes final input/base validation, requests the existing head-conditioned merge and reports success only after confirmed merged state
- **THEN** recognized missing-registration responses and validated exit-zero empty arrays qualify as empty, without being called passing CI

#### Scenario: Mixed PR still waits for CI
- **WHEN** a PR changes both `README.md` and `nightshift/land.py` under the base policy `['**.md']` and no checks register
- **THEN** it continues ordinary registration waiting until checks arrive or the shared deadline refuses, without an exemption attestation or merge

#### Scenario: Registered check on docs PR still required
- **WHEN** any check registers on an excluded PR before the exemption decision, including after its initial empty poll
- **THEN** normal CI rules apply: pending waits, failure/cancellation refuses, skipped-only waits and a passing set may proceed
- **THEN** later empty responses cannot restore exemption for that head and no docs-only attestation is created

#### Scenario: Grace does not extend deadline
- **WHEN** the shared deadline expires before the later empty poll or during proof reads, grace, attestation or another base round
- **THEN** landing refuses without merging, resetting the deadline, shortening the grace to fit, or polling after expiry

#### Scenario: Successor and base movement recheck proof
- **WHEN** finish or a validated branch update produces a new head, or the final base differs from the round baseline
- **THEN** the next applicable CI gate uses the resulting head's current changed paths and the new round's pinned base workflow with fresh grace; previous proof cannot authorize it
- **THEN** unexpected head changes still refuse, new nonignored paths require CI and three successive base moves still refuse without a fourth round

### Requirement: CI exemption attestation

Before merging under the exception, landing SHALL print and persist `CI not required: docs-only` in a populated CI attestation identifying the authenticated actor, PR, exact head/base, workflow, paths, patterns and grace. It SHALL use the distinct `<!-- nightshift:land-ci:<pr>:<head>:<base> -->` marker and preserve human-check attestations and their pre-finish heads. The existing trusted lookup and single-create reconciliation rules SHALL apply.

#### Scenario: Exemption without human checklist
- **WHEN** the exemption qualifies and selected sources contain no human checks, with or without `--checks-done`
- **THEN** landing retains `No human checks listed`, edits no checklist, resolves the authenticated actor and writes the populated CI attestation before merge
- **THEN** no human-check completion or empty human attestation is invented

#### Scenario: Human and CI evidence coexist
- **WHEN** listed human checks have been attested before finish and the final head qualifies for the exemption
- **THEN** the human attestation remains unchanged and the separate CI attestation identifies the actual post-finish or post-update head and recorded base
- **THEN** final source/run/head validation and base comparison remain required after recording the CI decision

#### Scenario: Attestation uncertainty blocks merge
- **WHEN** actor lookup fails, a CI marker is duplicate or untrusted, or a write cannot be reconciled
- **THEN** landing refuses without merging; an ambiguous create is looked up across all pages and never repeated within the invocation
- **THEN** a sequential retry updates a unique trusted same-head/base comment, but late cross-invocation duplicates retain the existing refusal and human recovery rules

### Requirement: Docs only operator guidance

`docs/landing.md` SHALL explain base-derived eligibility, supported workflow/glob syntax, the 10-second grace within the existing deadline, registered-check precedence, exact reason, separate CI attestation and conservative fallback. It SHALL retain all existing recovery/protection boundaries and identify the next real Markdown-only landing as a pending human check.

#### Scenario: Operator understands the exemption
- **WHEN** an operator follows the landing guide
- **THEN** it explains when a docs-only PR can land without checks, why mixed or unsupported cases still wait, how to inspect the CI attestation and why server protection may still reject merge
- **THEN** it cites issue #136 and leaves real scheduling, permissions and merge verification to a human without claiming that unit tests complete them

## Purpose

Permit a reviewed PR whose complete changed-path set is excluded by trusted base-branch CI policy to satisfy the CI gate without registered checks, with bounded registration grace and explicit evidence, while preserving all other landing gates.

## ADDED Requirements

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

## MODIFIED Requirements

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

### Requirement: Forge specific operations preserve gates

Landing SHALL keep authorization, lifecycle, source selection, provenance and CI policy in `nightshift/land.py`, using named forge-specific operations. New Gitea operations SHALL not change existing GitHub argv, validation, error handling or relative gate ordering. The docs-only extension MAY add named changed-path/workflow reads after a validated empty-check response and the distinct exemption attestation before final revalidation/base comparison; nonempty-check paths SHALL retain their exact transcripts.

#### Scenario: Operation boundary retains behavior
- **WHEN** GitHub operations are routed through the landing boundary and Gitea operations use an injected opener
- **THEN** existing runner tests observe the same existing GitHub operations in order, with only the specified empty-check evidence and exemption-attestation additions, Gitea has no gh fallback, and both use the same landing gates wherever the forge-specific deltas do not prescribe a different transport

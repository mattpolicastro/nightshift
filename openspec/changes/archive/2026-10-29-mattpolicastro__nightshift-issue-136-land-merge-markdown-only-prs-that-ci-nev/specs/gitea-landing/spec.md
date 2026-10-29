## Purpose

Apply the proven CI exclusion gate to configured Gitea landing through confined human-token reads while retaining exact-head status evidence, trusted attestations and pinned confirmed merges.

## ADDED Requirements

### Requirement: Gitea exclusion evidence

Gitea landing SHALL provide complete changed-path and pinned-base `.gitea/workflows/ci.yml` or `ci.yaml` evidence through named landing operations using the selected human credential. It SHALL apply the same conservative single-workflow, physical-line, glob, grace and attestation policy as reviewed-pr-landing without GitHub fallback or changes to daemon/Factory eligibility.

#### Scenario: Gitea ignored PR uses human credential
- **WHEN** complete Gitea file metadata and the sole supported base workflow prove all paths ignored and exact-head combined status stays empty through the grace
- **THEN** all evidence reads and the separate CI attestation use the confined configured origin and human token, including its allowlisted non-bot actor check
- **THEN** landing may use the ordinary pinned merge only after every other gate passes and confirms merged state before success

#### Scenario: Gitea uncertainty retains existing gates
- **WHEN** Gitea cannot prove complete paths or supported workflow policy, a combined status registers, or exact-head status validation fails
- **THEN** unavailable exclusion evidence retains ordinary waiting, registered checks retain normal classification, and invalid status/head evidence refuses
- **THEN** no bot credential, gh fallback, alternative workflow directory or Actions result authorizes the exemption

## MODIFIED Requirements

### Requirement: Exact head combined CI

Gitea landing SHALL query combined status for the exact expected full head SHA through `checks_for_sha`, with head reads before and after each query. It SHALL preserve #122 mapping, require passing evidence unless the shared Bounded docs only exemption is freshly established and attested, and retain the existing shared deadline, polling cadence and three-round limit.

#### Scenario: Combined states control progress
- **WHEN** exact-head combined statuses are success, pending, failure, error, warning, skipped or proven empty
- **THEN** success maps to pass, pending to pending, failure/error/warning to fail, skipped to skipping and empty to not yet registered
- **THEN** only valid passing evidence or an established and attested docs-only exemption proceeds; pending, nonexempt empty and skipped-only wait, failure refuses, and no branch, merge-ref or Actions query substitutes for the pinned SHA

#### Scenario: CI uncertainty and deadline refuse
- **WHEN** status evidence has the wrong SHA, malformed/unknown/inconsistent pages, a bracketing head mismatch, or the shared deadline expires during requests, GET retries or polling
- **THEN** landing refuses without merge, no HTTP attempt starts after expiry, and neither a retry nor a new round resets the deadline

#### Scenario: Main movement requires fresh CI
- **WHEN** main differs from the retained baseline after green CI or an established and attested docs-only exemption, and stable-input revalidation
- **THEN** a further permitted round uses a new single-attempt update and fresh successor-head status and, if still empty, fresh exclusion proof and grace; movement after round three refuses without a fourth round or merge

### Requirement: Offline compatibility evidence

Automated verification SHALL exercise Gitea through socket-free injected transport and clocks and preserve all existing GitHub assertions. Existing GitHub argv and payload text SHALL remain byte-identical and their relative order SHALL remain unchanged. Only the specified empty-check exclusion reads and exemption-attestation operations MAY be added; nonempty-check transcripts SHALL remain byte-identical. The full pytest suite SHALL pass and the existing type baseline SHALL not grow.

#### Scenario: GitHub transcripts remain exact
- **WHEN** legacy and explicitly GitHub-kind landing exercise direct, finish, attestation, update, CI, merge and refusal paths, including Factory preflight/handoff
- **THEN** recording runners observe the unchanged full argv and ordering, with no Gitea credential load and no additional GitHub calls except the specified empty-check exclusion reads and exemption-attestation operations
- **THEN** the four #130 issue-named regression tests, the three #136 issue-named regression tests and all existing assertions pass without live network or a logged-in host

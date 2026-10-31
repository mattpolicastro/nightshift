## Purpose

Authorize Gitea label actors and explain rejections without changing GitHub relationship resolution or existing intake lifecycle rules.

## ADDED Requirements



## MODIFIED Requirements

### Requirement: Actor relationship resolution

For GitHub, Nightshift SHALL resolve label actors from event identity and positive repository-owner, collaborator-membership, or organization-membership evidence without changing existing validation or behavior. For Gitea, core SHALL resolve a nonblank string actor login as OWNER only when its casefolded value exactly belongs to the selected forge's trusted_logins and differs from bot_login; otherwise it SHALL return no association. The Gitea branch SHALL precede GitHub login validation and owner/membership lookups and SHALL make no relationship request. Server associations SHALL NOT override this projection; membership APIs SHALL remain unsupported. Public read access, issue/PR authorship, feedback authorship, account type, and site-administrator flags SHALL NOT independently authorize a label. Unknown event history or failed relationship lookups SHALL prevent action and retain the existing error/retry behavior.

#### Scenario: Identity without association resolved
- **WHEN** a GitHub label event contains an actor login without an association field
- **THEN** positive personal-owner, repository-collaborator, or organization-member evidence resolves it to OWNER, COLLABORATOR, or MEMBER respectively
- **THEN** negative membership evidence does not become trusted merely because the repository is public

#### Scenario: Lookup failure defers action
- **WHEN** event history or membership lookup has a transport error, authorization error, rate limit, malformed response, missing event ID, or a last event that is an unlabel
- **THEN** the stage performs no triggered action or authorization rejection write and preserves its workflow receipts and labels for the existing failure/retry path

#### Scenario: Other authors cannot authorize
- **WHEN** an issue author, PR author, or feedback author is trusted but the latest trigger actor is untrusted
- **THEN** the trigger is ignored regardless of the other authors' associations

#### Scenario: Gitea allowlist alone authorizes actors
- **WHEN** Gitea latest label events identify a mixed-case allowlisted login, an unlisted repository owner, a login with a misleading server association, a missing identity or an allowlisted bot
- **THEN** only the allowlisted non-bot gets OWNER through casefolded exact comparison without stripping or prefix matching; all others get no association and no membership or repository-owner lookup occurs
- **THEN** Gitea logins are not constrained by GitHub's username regex, both intake aliases must pass independently, and trusted issue or feedback authors cannot authorize another actor's trigger


### Requirement: Rejection comment idempotency

An unauthorized application SHALL receive one explanatory comment keyed by repository, issue/PR number, and label-event ID. Repeated polls and restart SHALL NOT POST or PATCH it again. Lost-response retry SHALL create no second comment; transport reconciliation MAY PATCH the same body to the original. Rejection SHALL leave labels unchanged and SHALL NOT prevent processing later eligible items. Lookups SHALL follow Canonical daemon markers, considering only bot-owned evidence on Gitea. Gitea no-duplicate recovery SHALL assume read-after-write-consistent listings and no concurrent creator for the same marker; if an accepted ambiguous POST remains invisible, a later daemon poll may duplicate it. No persistent create state SHALL be introduced. GitHub rejection wording SHALL remain unchanged; Gitea wording SHALL describe configured allowlist membership and trusted reapplication, using a safe display identity independent of authorization.

#### Scenario: Repeated rejection comments once
- **WHEN** the same unauthorized event is observed across multiple polls and a daemon restart
- **THEN** exactly one rejection comment exists and subsequent polls neither create nor edit it
- **THEN** the comment identifies the label and known actor, explains the permitted GitHub associations or Gitea configured allowlist membership, and describes trusted reapplication
- **THEN** later eligible issues or PRs continue to be processed

#### Scenario: Lost comment response reconciled
- **WHEN** the rejection comment POST succeeds remotely but its response is lost
- **THEN** retry finds the canonical event marker across the complete comment listing and does not create a duplicate under the stated Gitea consistency and single-creator assumptions
- **THEN** a failed comment lookup prevents a blind POST and no triggered work runs

#### Scenario: New rejection has separate explanation
- **WHEN** an untrusted actor removes and reapplies a previously rejected label
- **THEN** the new event gets its own single rejection comment and the previous explanation remains unchanged

### Requirement: Forge mediated intake

Intake SHALL obtain GitHub facts and perform GitHub mutations through named
Forge operations, retaining its existing runner injection and all current
workflow requirements. Git pushes and engine commands SHALL retain their
existing runners. Existing runner-form argv assertions SHALL pass unchanged. The four stage
entry points SHALL accept the optional forge keyword specified by Late bound
runners; Gitea SHALL use its selected adapter, and GitHub callers SHALL retain
existing construction and calls. The sole permitted GitHub inline-marker fake
assertion migration is specified by Core policy preservation.

#### Scenario: Existing workflows retain transcripts
- **WHEN** drafting, approval, revision, publication delivery and their recovery or failure-reporting paths run through GitHubForge
- **THEN** existing runner-form tests pass without assertion or fixture edits, with the same GitHub calls, git operations, outputs and receipt effects
- **THEN** backend construction causes no read and no operation bypasses the forge to construct GitHub argv in intake

# forge-preflight Specification

## Purpose
Provide read-only Gitea enrollment evidence per forge and repository, using
negative controls and explicit failures for unverifiable conditions.

## Requirements

### Requirement: Isolated forge checks

Preflight SHALL use each configured Gitea forge's bot token and transport for its
forge checks and each enrolled repository's checks, reporting independent named
results. Missing credentials SHALL fail without dispatch. Existing GitHub,
alert and retired deep-probe behavior SHALL remain compatible, with GitHub
repository queries restricted to GitHub-kind rows.

#### Scenario: Mixed configuration remains isolated
- **WHEN** two Gitea definitions and implicit or explicit GitHub repositories are checked, including a missing Gitea token
- **THEN** calls use the appropriate supplied bot token and origin, the missing token makes no request, and independent results remain available
- **THEN** GitHub queries never receive Gitea repository names, no landing token is sent, and existing GitHub assertions and CLI failure aggregation remain unchanged

### Requirement: Bot identity negative control

Gitea preflight SHALL require GET /user to establish the configured bot login
case-insensitively and boolean is_admin=false. Missing, malformed, privileged
or unreadable evidence SHALL fail without substituting a personal credential.

#### Scenario: Only a proven nonadmin bot passes
- **WHEN** user responses describe the configured nonadmin bot, an admin, another login, missing fields or a failed request
- **THEN** only the first case passes and diagnostics disclose neither tokens nor remote response bodies

### Requirement: Forbidden repository negative control

Gitea preflight SHALL pass a configured forbidden_probe only when its repository
GET returns HTTP 404. Missing probes, readability and all other failures SHALL
fail. Guidance SHALL require an independently known real forbidden repository.

#### Scenario: Only forbidden 404 passes
- **WHEN** the probe returns 404, 200, 401, 403, a transient/connection failure, or is not configured
- **THEN** only 404 passes and the other cases remain failed checks without credential fallback

### Requirement: Complete unique labels

Gitea preflight SHALL verify exactly one repository label for each distinct
effective Labels value and merge:ready/merge:blocked. It SHALL read all pages
through an empty page within the design's bound, refuse malformed or incomplete
evidence and duplicate names, and SHALL NOT create or modify labels.

#### Scenario: Complete map establishes labels
- **WHEN** labels span short nonterminal pages followed by an empty page
- **THEN** all pages are read and a complete unique map containing each required effective name passes without writes

#### Scenario: Invalid or incomplete map fails
- **WHEN** a required label is missing, a duplicate name or conflicting ID appears on another page, a page repeats, a later page fails, or the bound is exhausted
- **THEN** preflight reports failure without claiming completeness or repairing labels

### Requirement: Base protection evidence

Gitea preflight SHALL require a valid exact base-branch protection record.
A 403 SHALL report `unverified` with ok=false; missing, malformed or unreadable
protection SHALL fail. It SHALL NOT use privileged credentials or change rules,
and SHALL describe the limits of existence-only evidence.

#### Scenario: Protection is established or remains unverified
- **WHEN** the base protection GET returns an exact record, 403, 404, a mismatched branch, malformed evidence or another error
- **THEN** only the exact valid record passes, 403 explicitly reports `unverified`, and every other case fails
- **THEN** a failed check yields the existing nonzero preflight exit rather than a ready summary

### Requirement: Clone credential identity

For each Gitea clone, preflight SHALL verify an HTTP(S) origin matching its
configured forge and repository, then run bounded noninteractive git credential
fill for that origin in the clone. A unique nonempty credential username SHALL
match bot_login case-insensitively. Missing, ambiguous or unavailable credential
evidence SHALL fail; secrets SHALL never appear in output or argv.

#### Scenario: Clone resolves the bot
- **WHEN** the clone has the expected origin and credential fill returns one bot username and nonempty password
- **THEN** preflight passes that identity check with repository path supplied on stdin, the clone as context, supplied environment and disabled prompts
- **THEN** it neither stores nor prints the password and performs no credential approve/reject, fetch, push or remote write

#### Scenario: Wrong or unavailable clone identity fails
- **WHEN** the origin is missing, mismatched, SSH or embeds credentials, or fill times out, fails, returns another username, or has missing, duplicate or malformed fields
- **THEN** the check fails without using a personal-login fallback or disclosing credential output or subprocess stderr
- **THEN** independent forge checks remain reportable and fixtures assert the ten-second timeout and exact input without live helpers

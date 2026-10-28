## Purpose

Allow Factory to display published runs from explicitly configured forges while preserving strict validation, stale fallback and GitHub-only landing.

## ADDED Requirements

### Requirement: Configured forge links

Factory SHALL validate PR and context origins against one allowlist loaded from the local operator file on each uncached status read, always allowing `https://github.com`. Only a wholly valid file SHALL add origins. PR validation SHALL retain GitHub's existing form and accept the configured forge form specified below; other nonempty PR links SHALL fail the whole read. Existing caching and stale fallback SHALL remain unchanged.

#### Scenario: Accepts configured forge links
- **WHEN** a valid file lists `https://forge.example.test` and `https://forge.example.test:8443` among blank lines, full-line comments and duplicate entries, and status mixes GitHub PRs with configured `/owner/repo/pulls/7` PRs and allowed context links
- **THEN** LF and CRLF files are supported by splitting on LF and removing one terminal CR per line; whitespace-only lines and lines whose first non-whitespace character is `#` are ignored, and duplicate origins are harmless
- **THEN** every other line is used whole without trimming or removing inline comments, and is valid only if `new URL(line)` succeeds, its protocol is `https:`, and its origin equals the exact line
- **THEN** the entire Factory snapshot loads as fresh, preserving every run and its validated PR/context destination
- **THEN** GitHub PRs retain the existing `/<owner>/<repo>/pull/<n>` form and configured non-GitHub PRs use `/<owner>/<repo>/pulls/<n>`, with nonempty single-segment owner/repo and one or more decimal digits for n; empty PR fields remain valid
- **THEN** changing, creating or deleting the file is reflected on the next uncached status load without plugin restart, while cached/concurrent reads retain the existing 15-second throttle and shared load

#### Scenario: Rejects foreign origins
- **WHEN** a status supplies a PR from an unconfigured origin, look-alike hostname, unlisted subdomain or port, or a PR with wrong path, scheme, credentials, query or fragment
- **THEN** validation uses exact parsed-origin equality and rejects credentials, query and hash for both GitHub and configured forge PRs
- **THEN** the whole read fails, retaining the last good snapshot marked stale or reporting unavailable when cold, even when other rows are valid
- **THEN** the same foreign origin in a context link alone is blanked without failing the read, and GitHub's `/pulls/` and a configured non-GitHub forge's `/pull/` paths remain invalid PRs

#### Scenario: Invalid origins file allows GitHub only
- **WHEN** the file has a valid extra origin followed or preceded by any invalid line, including HTTP, a path, trailing slash, query, fragment, credentials, surrounding whitespace, noncanonical origin spelling or inline comment
- **THEN** all extras are ignored for that load, valid GitHub PR/context links still load, extra-forge PRs fail the read and extra-forge context links are blanked
- **THEN** missing, unreadable, empty and comment-only files also allow GitHub only, and a formerly valid file becoming invalid does not retain its previous extra origins for fresh validation

## MODIFIED Requirements

### Requirement: Pause display metadata

The Factory shared schema SHALL accept optional nullable pause metadata with optional nullable finite-number `since` in epoch seconds and optional nullable nonnegative-integer `auto_resumes_remaining`. The existing server projection SHALL forward only those two pause fields, preserve supplied nulls, and retain its validation, redaction, caching, and action protections, with PR and context URL validation extended only by Configured forge links.

#### Scenario: Only pause display fields cross the server
- **WHEN** version-1 status supplies pause timestamps and remaining-resume counts alongside reason, task, detail, resume time, or other internal fields
- **THEN** the Factory response includes only the supplied `since` and `auto_resumes_remaining` pause properties with their original numeric or null values
- **THEN** existing validated display fields and action behavior are unchanged

#### Scenario: Older producers remain supported
- **WHEN** status omits pause, supplies null pause, or supplies a pause object missing one or both display fields
- **THEN** the server accepts the otherwise valid response without defaulting a missing timestamp or budget to zero
- **THEN** absent pause and pause objects with no display fields are omitted from the projection, explicit null pause is retained, and any supplied valid display field is retained

#### Scenario: Invalid metadata retains validation behavior
- **WHEN** a status read supplies an invalid timestamp type or a negative, fractional, or incorrectly typed remaining-resume count
- **THEN** existing schema validation fails the read, retaining the last good snapshot marked stale or reporting unavailable when no snapshot exists
- **THEN** existing version, status, action and context-field validation are not relaxed; PR URL validation accepts only existing GitHub PRs or configured forge origins with the specified PR shape, and context URL filtering uses that same allowlist

### Requirement: Human checks landing

Factory SHALL offer separate Land only for a matching non-stale human_checks offer with a validated `https://github.com` PR link and preserve every existing label and behavior. Configuring another forge for display SHALL NOT authorize a landing offer or submission. New controls SHALL use host theme tokens and minimum 44-by-44 targets.

#### Scenario: Land action appears only at human checks
- **WHEN** Factory renders human_checks, other-paused, running, done, aborted and unavailable runs
- **THEN** Land appears only for a verifiable paused human_checks offer with a validated GitHub PR link, separate from unchanged ordered status verbs and Finish
- **THEN** running landing disables another start and exposes Stop landing; ordinary confirmations, notes, waiting time, Details, navigation and Finished retain their labels and behavior

#### Scenario: Land offered only for GitHub links
- **WHEN** otherwise eligible human_checks runs include a configured Gitea PR and a GitHub PR
- **THEN** the Gitea run remains displayable with its Open PR destination but has no usable landing offer or Land button, while the matching non-stale GitHub offer still permits Land
- **THEN** a forged non-GitHub landing confirmation is refused before private start execution, and a cached GitHub offer cannot authorize a run whose PR has changed to Gitea
- **THEN** landing validation, confirmation identity, stale checks, independent cache behavior, retained history and Stop behavior remain unchanged

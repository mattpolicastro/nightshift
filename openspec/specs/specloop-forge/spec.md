# specloop-forge Specification

## Purpose
Enable explicit engine publication and completion on a configured Gitea forge
while preserving GitHub command compatibility and preventing unsafe pushes.

## Requirements

### Requirement: Manifest forge selection

The engine SHALL select its forge from `[run] github`. No host or `github.com` SHALL retain byte-identical `gh` argv and no-helper Git transport. Helper-bearing Git transport SHALL follow publish-base-sync credential requirements. Other hosts SHALL uniquely match configured Gitea URL hostnames and construct GiteaForge using trusted configuration and bot credentials. Invalid or ambiguous selection SHALL fail without GitHub fallback.

#### Scenario: GitHub stays compatible
- **WHEN** the manifest names `owner/repo` or `github.com/owner/repo`
- **THEN** publication, finish and critic retain their existing `gh` argv, including whether `--hostname` is present, without requiring forge configuration or loading Gitea credentials
- **THEN** no-helper Git transport stays unchanged; helper-bearing fetch and push use config environment forwarding, and helper-bearing pushes add SAFE_FLAGS without putting helpers in argv

#### Scenario: Gitea host selects configured credentials
- **WHEN** `forge.example/owner/repo` uniquely matches a Gitea definition with URL `https://forge.example:8443/git`
- **THEN** the engine constructs GiteaForge with that URL, bot token, bot login, trusted logins and WIP prefix, making no request during construction
- **THEN** configuration comes from process-selected `NIGHTSHIFT_FORGE_CONFIG` or the installed default, and the trusted env file overrides the process value of the selected bot variable without exporting secrets

#### Scenario: Invalid selection refuses
- **WHEN** a non-GitHub host has no match, multiple matching definitions, unreadable or invalid configuration, or missing/empty bot credentials
- **THEN** publish and finish fail before pushes or API writes with sanitized diagnostics, while critic returns unavailable-context notes
- **THEN** neither a manifest env file nor a landing token nor ambient `gh` credentials supplies a fallback

### Requirement: Full Gitea origin match

Before any push or API write, Gitea publish and finish SHALL require a credential-free HTTP(S) checkout origin matching the configured URL's scheme, case-insensitive hostname and effective port, plus its prefix and manifest repository path. Mismatched or unverifiable origins SHALL be refused. Existing GitHub origin handling SHALL remain unchanged.

#### Scenario: Matching origin permits publication
- **WHEN** the configured URL is `https://forge.example/git` and origin is `https://forge.example:443/git/owner/repo.git`
- **THEN** normalized origins and the repository path match; the existing git push uses the validated origin URL
- **THEN** an omitted default port equals its explicit value, and the optional terminal `.git` suffix is accepted

#### Scenario: Port or scheme mismatch refuses
- **WHEN** configuration is `https://forge.example:8443` but origin uses port `9443`, or configuration is `https://localhost` but origin uses `http://localhost`
- **THEN** publish and finish refuse before any push or API write even though hostname and repository match

#### Scenario: Unverifiable destination refuses
- **WHEN** origin has the wrong prefix/repository, embedded credentials, query/fragment, malformed escaping, multiple values, or is local, scp-style or SSH
- **THEN** Gitea publish and finish refuse without echoing credentials or attempting publication
- **THEN** SSH ports are never treated as HTTP ports; this change supplies no trusted SSH-to-HTTP installation mapping

### Requirement: Workflow publication refusal

Publish SHALL refuse on every forge when the freshly checked base..head diff touches `.gitea/workflows/`, `.github/workflows/` or `.forgejo/`. It SHALL inspect all changed paths before pushing or operating on PRs, including deletions and both sides of renames. Failed diff inspection SHALL block publication. Refusal SHALL preserve readiness and the recorded PR.

#### Scenario: Protected paths block every forge
- **WHEN** a GitHub or Gitea diff adds, modifies, deletes, or renames into or out of any protected directory, including nested files or unusual filename characters
- **THEN** publication fails before git push, PR lookup, create or update, with the protected paths identified safely and state unchanged

#### Scenario: Safe paths and failed inspection
- **WHEN** the diff touches only ordinary files or similarly named directories such as `.github/workflows-old/`
- **THEN** this guard allows normal publication
- **WHEN** git cannot produce a complete base..head path list
- **THEN** publication refuses without pushing, creating a PR or treating the diff as empty

### Requirement: Gitea draft publication

Publish SHALL retain the existing readiness, base-fetch, PR-body and human-checks contracts, with git-push helper forwarding as defined in publish-base-sync. Gitea publication SHALL reuse a valid recorded PR or find an open same-repository PR with exact head and base refs through complete forge reads before creating one. Creation SHALL use GiteaForge.create_draft_pr and the configured WIP prefix; failed lookup SHALL never authorize creation.

#### Scenario: New draft opens with WIP title
- **WHEN** a ready Gitea run passes origin, base and workflow checks and no matching PR exists
- **THEN** the branch is pushed with source-local helper config environment when present, one WIP-prefixed PR is created with the current title/body/base/head, its URL is saved and the run pauses at `human_checks`
- **THEN** no-helper push argv remains unchanged; helper-bearing push adds SAFE_FLAGS while preserving destination and refspec

#### Scenario: Existing PR is reused
- **WHEN** a run has a valid recorded PR, or a complete listing finds one open PR with the run's head repository and exact head/base refs
- **THEN** publication pushes and reuses that PR without changing its title/body or creating another
- **THEN** fork PRs sharing branch names and PRs for other bases do not match; multiple exact matches or foreign recorded URLs refuse

#### Scenario: Interrupted creation is reconciled
- **WHEN** creation fails or its response/state save is lost
- **THEN** the invocation sends no second POST, and a later publish with no saved PR repeats a complete matching lookup before considering creation
- **THEN** a failed, malformed or incomplete lookup prevents POST and leaves publication retryable; a visible earlier create is reused

### Requirement: Verified Gitea finish

Finish SHALL preserve expected-head, completed-task, archive and push checks. After pushing, it SHALL read the Gitea PR title, remove one exact leading WIP prefix with PATCH when present, then GET and verify the intended title before marking done. An already unprefixed title SHALL require no PATCH. Failed verification SHALL leave the run retryable at human_checks.

#### Scenario: Prefix removal is verified
- **WHEN** a Gitea PR has the configured leading WIP prefix and finish passes its existing gates
- **THEN** finish archives and pushes, reads the title, PATCHes only its title without that prefix, and reads back the exact intended title before saving `done`
- **THEN** custom prefixes are literal and case-sensitive, interior occurrences remain, and finish does not merge

#### Scenario: Retry observes current title
- **WHEN** PATCH loses its response, is silently ignored, or verification fails
- **THEN** finish reads back after an ambiguous write and succeeds only on the intended title; otherwise it leaves `human_checks` without replaying PATCH in that invocation
- **THEN** a retry reuses the archive, rereads current state and skips PATCH for an already unprefixed title

#### Scenario: Existing finish gates remain
- **WHEN** the expected head mismatches, tasks remain unfinished, origin is invalid, the recorded PR URL is foreign, or archive/push fails
- **THEN** finish does not PATCH a title or mark the run done, retaining existing recovery behavior and GitHub ready/read-back argv

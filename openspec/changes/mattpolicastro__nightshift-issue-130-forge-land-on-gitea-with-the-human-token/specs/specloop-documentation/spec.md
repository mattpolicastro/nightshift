## Purpose

Update the explicit forge command guidance contract for #130 terminal Gitea landing. The existing GitHub-only landing guarantee must change because operator-initiated Gitea landing is now supported; publication/finish guarantees and unrelated documentation requirements remain intact. The scoped landing guide supersedes historical terminal landing wording without requiring edits to the concurrently maintained shared guides.

## MODIFIED Requirements

### Requirement: Explicit forge command guidance

README.md and docs/specloop/README.md SHALL continue to explain manifest-host selection, trusted config and bot credential sources, origin matching, workflow refusal and Gitea WIP publication/finish. Guidance SHALL distinguish explicit engine commands from disabled daemon execution and human-initiated landing. For #130, `docs/landing.md` SHALL document terminal landing for GitHub and configured Gitea and explicitly supersede historical GitHub-only terminal landing wording in those shared guides; those guides SHALL remain unedited by this change. Factory Land SHALL remain GitHub-only, and arming and unrelated plugin paths SHALL retain their existing boundaries. Existing runnable examples and unrelated guidance SHALL remain valid.

#### Scenario: Operator selects and publishes
- **WHEN** an operator follows the explicit engine publication guidance
- **THEN** they can configure `[run] github` as `forge.example/owner/repo`, uniquely match a Gitea URL, provision `NIGHTSHIFT_FORGE_CONFIG` and the bot token, and use the existing publish/finish commands
- **THEN** docs explain file-wins bot-token precedence, no landing-token or manifest-env fallback, separate git credentials, scheme/host/effective-port/prefix/repository checks and HTTP(S)-only Gitea origins
- **THEN** docs retain implicit/explicit `github.com` compatibility and identify all three forbidden workflow directories

#### Scenario: Explicit commands do not enable the daemon
- **WHEN** an operator compares forge configuration, preflight and engine instructions with the current landing guide
- **THEN** the docs distinguish WIP-title drafts and finish's title PATCH/read-back from native draft state or merging
- **THEN** all Gitea daemon stages and pending delivery stay disabled, Factory Land remains GitHub-only, and arming and unrelated plugin paths retain their boundaries
- **THEN** `docs/landing.md` explicitly supersedes the shared guides' historical GitHub-only terminal landing wording, describes human-initiated terminal Gitea landing with the configured `land_token_env` sourced by land from `~/.config/nightshift/land.env` and no bot-token fallback, and keeps finish's separate bot/git credentials distinct
- **THEN** existing base preparation and recovery, frozen wrappers, enrollment examples and issue #135 per-step env guidance remain intact, without edits to README.md or docs/specloop/README.md

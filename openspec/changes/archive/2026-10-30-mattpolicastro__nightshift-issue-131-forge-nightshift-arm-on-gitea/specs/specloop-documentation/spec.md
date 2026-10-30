## Purpose

Keep arming instructions consistent with configured forge behavior while retaining
explicit publication guidance and the disabled Gitea daemon boundary.

## ADDED Requirements

### Requirement: Configured arming guidance

The arming guide SHALL document explicit config selection, checkout matching, Gitea bot credentials, client-side label filtering and verified writes. It SHALL retain legacy GitHub commands, lint rules, modes and examples. New examples SHALL be checked with deterministic physical-line rules and the real command parser without network access or exact-prose assertions.

#### Scenario: Operator configures arming
- **WHEN** an operator reads docs/arming.md's prerequisites, Invocation and Configured Gitea passages
- **THEN** they can use --config with a unique matching enrollment and existing spec:draft label, provision file-wins bot credentials, and run check and arming examples without gh on Gitea
- **THEN** instructions retain GitHub and enterprise discovery when the flag is omitted, explain missing completion reasons and verification failure, and preserve the original template and named-test examples

#### Scenario: Arming examples are structurally checked
- **WHEN** new documentation tests select the configured arming section and its shell examples
- **THEN** unique column-zero headings and exact physical fence delimiters select it regardless of surrounding Markdown or HTML; missing, duplicate, indented or reversed delimiters fail closed
- **THEN** shlex tokenization and the real module parser/main with fake transport validate both commands, relative links resolve and all existing documentation assertions remain intact

### Requirement: Gitea arming support boundary

The arming guide, known limitations and engine guide SHALL distinguish verified explicit Gitea arming from disabled daemon stages and pending delivery. They SHALL remove only obsolete GitHub-only arming claims, preserve landing and unrelated limits, and identify real issue arming and eventual drafting as pending human checks without claiming CI proves live operation.

#### Scenario: Operator understands the continuing hold
- **WHEN** an operator compares the updated arming guide, limitations and explicit-publication guidance
- **THEN** all describe configured arming and bot-backed verified labels while retaining unconditional Gitea daemon holds; force, allowlists and preflight do not enable drafting
- **THEN** landing remains GitHub-only, publication guidance and concurrent loopback content stay intact, and only arming claims change in the shared documents
- **THEN** the human checks require a real armed issue and a later drafting observation once separate prerequisites permit it; until then drafting remains pending rather than bypassing the hold

## MODIFIED Requirements

### Requirement: Explicit forge command guidance

README.md and docs/specloop/README.md SHALL explain manifest-host selection, trusted config and bot credential sources, origin matching, workflow refusal and Gitea WIP publication/finish. Guidance SHALL distinguish explicit engine commands from disabled daemon execution and GitHub-only landing. Existing runnable examples and unrelated guidance SHALL remain valid.

#### Scenario: Operator selects and publishes
- **WHEN** an operator follows the explicit engine publication guidance
- **THEN** they can configure `[run] github` as `forge.example/owner/repo`, uniquely match a Gitea URL, provision `NIGHTSHIFT_FORGE_CONFIG` and the bot token, and use the existing publish/finish commands
- **THEN** docs explain file-wins bot-token precedence, no landing-token or manifest-env fallback, separate git credentials, scheme/host/effective-port/prefix/repository checks and HTTP(S)-only Gitea origins
- **THEN** docs retain implicit/explicit `github.com` compatibility and identify all three forbidden workflow directories

#### Scenario: Explicit commands do not enable the daemon
- **WHEN** an operator compares forge configuration, preflight and engine instructions
- **THEN** the docs distinguish WIP-title drafts and finish's title PATCH/read-back from native draft state or merging
- **THEN** all Gitea daemon stages and pending delivery stay disabled; explicit arming supports configured Gitea with verified label writes, while landing and unrelated plugin paths retain their boundaries
- **THEN** existing base preparation and recovery, frozen wrappers, enrollment examples and issue #135 per-step env guidance remain intact

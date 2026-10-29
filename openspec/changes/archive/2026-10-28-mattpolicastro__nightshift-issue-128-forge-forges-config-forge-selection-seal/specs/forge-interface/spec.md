## Purpose

Construct configured adapters while preserving GitHub runner injection and
keeping Gitea daemon execution disabled until its core safety contracts exist.

## ADDED Requirements

### Requirement: Selected forge construction

The daemon's forge_for SHALL return GitHubForge for GitHub and the existing
GiteaForge over its GiteaTransport for Gitea, using the configured bot token
variable. Construction SHALL make no request. GitHub intake calls and local
construction SHALL remain unchanged; Gitea stage forwarding SHALL be deferred.

#### Scenario: GitHub retains runner construction
- **WHEN** an implicit or explicitly GitHub-kind repository is polled
- **THEN** the daemon passes its existing runner arguments without a forge keyword, intake constructs GitHubForge itself, and existing constructor-spy assertions and fixtures remain unchanged
- **THEN** directly invoking forge_for for GitHub returns GitHubForge without a request, retaining late-bound or explicit runner selection

#### Scenario: Gitea uses its configured adapter
- **WHEN** forge_for is directly invoked for a configured Gitea repository
- **THEN** the existing adapter receives its configured instance URL, bot token, trusted_logins, bot_login and wip_prefix
- **THEN** construction makes no request, missing/empty bot credentials raise a sanitized configuration error, and no operation falls back to GitHub
- **THEN** the factory result is not passed into daemon intake, publication/readiness or failure reporting in this change

### Requirement: Gitea dispatch disabled

The daemon SHALL hold every Gitea repository before recovery or stage dispatch,
including admitted work and pending delivery. It SHALL report a nontransient
repository health failure without forge calls, child launch or intake-state
mutation. Configuration and preflight SHALL NOT enable stages. Other repositories
SHALL continue; no configuration flag SHALL bypass this hold.

#### Scenario: Allowlist cannot enable unsafe authorization
- **WHEN** startup or polling sees a Gitea row with an empty or nonempty trusted_logins list, including an unlisted actor matching the repository owner
- **THEN** no intake stage, authorization lookup, admission, revision, publication/readiness or failure-reporting call executes for that row
- **THEN** the daemon explains that Gitea authorization and durable label transitions are not implemented, while GitHub rows keep their existing runner path
- **THEN** a healthy manifest, bot token, passing preflight or absent foreign journal does not lift the hold

#### Scenario: Pending transitions cannot lose their trigger
- **WHEN** a Gitea enrollment has approval triggers or pending successful/refused admission, revision or publication results across repeated polls and restart
- **THEN** no stage dispatch, label DELETE/POST, comment, git push or result acknowledgement occurs, and labels, intake receipts and consumed feedback remain unchanged
- **THEN** a fake prepared to accept DELETE of spec:approved and lose its response receives zero requests, so no refusal transition starts
- **THEN** admitted manifests do not bypass the hold; global job reaping and independent supervisor activity retain their existing local behavior

### Requirement: Human token exclusion

Daemon entry SHALL refuse before startup side effects when any declared
land_token_env variable is present in its environment, regardless of value,
forge use, pause or one-shot mode. Direct startup and polling entry points SHALL
enforce the same boundary. Diagnostics SHALL name only the forge and variable.

#### Scenario: Landing environment refuses startup
- **WHEN** a declared landing variable is present, including empty or for an unused forge
- **THEN** service, one-shot and direct entry paths refuse before heartbeat, recovery, notification or child launch, without revealing its value
- **THEN** removing that variable permits otherwise eligible GitHub startup, while Gitea stages remain disabled

## MODIFIED Requirements

### Requirement: Module layout guidance

The root README's module layout SHALL name the Forge contract, factual records,
GitHub backend, Gitea backend and retained queue transport. It SHALL describe
Gitea configuration, adapter construction and read-only preflight separately
from disabled daemon execution, GitHub-only worker recovery and deferred landing
migration, while preserving unrelated guidance.

#### Scenario: Layout describes staged boundary
- **WHEN** a contributor reads the updated root module-layout table and adjacent explanation
- **THEN** the named modules resolve to implemented files and distinguish backend operations from queue transport and core policy
- **THEN** the table names forge/gitea.py over forge/http.py, configuration and preflight do not imply enabled Gitea workflows, and worker recovery remains GitHub-only
- **THEN** landing migration remains future work and the narrow enrollment guard does not imply a Gitea landing implementation

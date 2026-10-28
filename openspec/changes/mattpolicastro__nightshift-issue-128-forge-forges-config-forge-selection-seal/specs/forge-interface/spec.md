## Purpose

Select configured forges at the daemon boundary while preserving GitHub runner
injection, core workflow policy and guarded dispatch.

## ADDED Requirements

### Requirement: Selected forge dispatch

The daemon's forge_for SHALL return GitHubForge for GitHub and the existing
GiteaForge over its GiteaTransport for Gitea, using the configured bot token
variable. Only Gitea SHALL be passed explicitly into intake stages and failure
reporting. GitHub calls and intake-local construction SHALL remain unchanged.

#### Scenario: GitHub retains runner construction
- **WHEN** an implicit or explicitly GitHub-kind repository is polled
- **THEN** the daemon passes its existing runner arguments without a forge keyword, intake constructs GitHubForge itself, and existing constructor-spy assertions and fixtures remain unchanged
- **THEN** directly invoking forge_for for GitHub returns GitHubForge without a request, retaining late-bound or explicit runner selection

#### Scenario: Gitea uses its configured adapter
- **WHEN** a Gitea repository is polled through drafting, admission, revision, publication/readiness or failure reporting
- **THEN** the existing adapter receives its configured instance URL, bot token, trusted_logins, bot_login and wip_prefix and the same selected forge is passed through the existing stages
- **THEN** construction makes no request, missing/empty bot credentials hold the repository with a sanitized error, and no operation falls back to GitHub
- **THEN** core authorization, comment lookup, label order, receipts and revision snapshot behavior are unchanged

### Requirement: Human token exclusion

Daemon entry SHALL refuse before startup side effects when any declared
land_token_env variable is present in its environment, regardless of value,
forge use, pause or one-shot mode. Direct startup and polling entry points SHALL
enforce the same boundary. Diagnostics SHALL name only the forge and variable.

#### Scenario: Landing environment refuses startup
- **WHEN** a declared landing variable is present, including empty or for an unused forge
- **THEN** service, one-shot and direct entry paths refuse before heartbeat, recovery, notification or child launch, without revealing its value
- **THEN** removing that variable permits otherwise eligible startup using only bot credentials

### Requirement: Guarded Gitea calls

Gitea dispatch SHALL check pause before every transport attempt and reconciliation
and before and after each logical Forge operation. Guard exceptions SHALL
propagate unchanged with no added retries. Existing engine, git and launcher
guards SHALL remain effective.

#### Scenario: Pause preserves pending delivery
- **WHEN** pause appears before a Gitea request, before a GET retry/reconciliation, or during an accepted final write
- **THEN** no later operation runs, the original pause exception propagates, and completion remains pending when the accepted operation finished during pause
- **THEN** tests exercise the existing transport through a fake opener, and GitHub guarded-runner tests pass without edits

## MODIFIED Requirements

### Requirement: Late bound runners

Migrated entry points SHALL retain runner injection and, absent an explicit
forge, resolve GitHubForge(runner or queue._gh) inside the function. Explicit
forges SHALL be used as supplied. Backends SHALL bind their selected transport
at construction; no module-level instance or default SHALL freeze the runner.
GitHub helpers SHALL receive the bound runner explicitly.

#### Scenario: Default runner remains patchable
- **WHEN** modules are imported before queue._gh is patched, and successive intake, readiness or daemon recovery invocations use different patches without explicit runners
- **THEN** each invocation dispatches through the then-current patched callable, including helper-delegated operations, without invoking the original subprocess transport

#### Scenario: Explicit runner wins
- **WHEN** a caller supplies a recording runner while queue._gh is patched to fail if called
- **THEN** all operations and delegated helpers use that supplied runner with no leading gh and no fallback to queue._gh
- **THEN** a protocol fake supplied to an internal helper is used without constructing another backend or making unrelated calls

### Requirement: Module layout guidance

The root README's module layout SHALL name the Forge contract, factual records,
GitHub backend, Gitea backend and retained queue transport. It SHALL describe
configured Gitea selection over forge/http.py and its remaining workflow gaps,
GitHub-only worker recovery and deferred landing migration, while preserving
unrelated guidance.

#### Scenario: Layout describes staged boundary
- **WHEN** a contributor reads the updated root module-layout table and adjacent explanation
- **THEN** the named modules resolve to implemented files and distinguish backend operations from queue transport and core policy
- **THEN** the table names forge/gitea.py over forge/http.py, configured Gitea enrollment/dispatch is distinguished from full workflow support, and worker recovery remains GitHub-only
- **THEN** landing migration remains future work and the narrow enrollment guard does not imply a Gitea landing implementation

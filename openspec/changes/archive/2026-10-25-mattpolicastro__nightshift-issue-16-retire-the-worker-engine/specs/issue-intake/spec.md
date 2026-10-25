## Purpose

Route every enrolled repository through specloop while preserving front-door controls.

## ADDED Requirements

### Requirement: Specloop repository engine

Every repository SHALL use specloop when engine is omitted or explicitly specloop. Worker and unknown engines SHALL fail loading before dispatch. Missing or unusable manifests SHALL hold only that repository; supplied empty/wrong-type paths SHALL remain config errors. Dispatch SHALL preserve Latest label actor authorization and never claim worker work. Legacy recovery SHALL never launch workers.

#### Scenario: Omitted engine uses specloop
- **WHEN** a repository omits engine and has a healthy manifest and an otherwise eligible agent:ready issue authorized by a trusted actor
- **THEN** it drafts through specloop with no worker claim, just as explicit specloop enrollment does

#### Scenario: Specloop repository skips worker claims
- **WHEN** a healthy repository has engine specloop and an eligible issue is labelled agent:ready by a trusted actor
- **THEN** no worker claim is made and the issue is drafted as a spec instead

#### Scenario: Explicit worker fails clearly
- **WHEN** configuration names engine worker
- **THEN** loading fails before dispatch with the repository name, retired-engine explanation and specloop manifest guidance, including when verify or manifest is absent
- **THEN** the CLI reports the configuration error on stderr with exit 2 and no traceback

#### Scenario: Omitted manifest holds only its repository
- **WHEN** a repository omits both engine and manifest alongside a healthy specloop repository
- **THEN** config loading succeeds, the missing-manifest repository holds new work without consuming triggers, and the healthy repository continues


## REMOVED Requirements

### Requirement: Engine per repository

**Reason:** Retirement intentionally removes the worker-default and dual-engine guarantees, including the old "Worker repository unchanged" scenario. The replacement Specloop repository engine requirement preserves explicit-specloop routing and defines omission, rejection and manifest-hold behavior.

**Migration:** Remove explicit worker enrollment and supply a trusted specloop manifest. Omitted engines now select specloop; missing manifests hold only their repository. No state is migrated.

## MODIFIED Requirements

### Requirement: Git process group timeout

Daemon intake git commands SHALL run in a new session with the existing 30-second per-command timeout. On timeout the runner SHALL kill the process group, reap its direct child with bounded output draining, and retain the existing retryable error behavior, pause checks and explicit runner injection.

#### Scenario: Git timeout kills process group
- **WHEN** a daemon git command stalls with its transport descendant in the same group until the timeout
- **THEN** SIGKILL targets that group, the git process and descendant cease executing, the direct child is reaped, and an unrelated group remains alive
- **THEN** timeout handling allows at most one additional second for draining, reports a retryable timeout and retains receipts and arming labels for remote reconciliation
- **THEN** normal exit and nonzero exit retain stdout, stderr, return code and check semantics; signalling failure is diagnosed rather than claimed as successful termination

#### Scenario: Production runner preserves daemon progress
- **WHEN** poll_specloop dispatches git or a git timeout interrupts delivery
- **THEN** the production guarded runner uses the process-group implementation, while explicit injected git runners remain usable and pause is checked immediately before dispatch
- **THEN** other eligible specloop items, heartbeat and background-child reaping continue under existing scheduling and timeout behavior; retry reuses saved results without duplicate delivery
- **THEN** GitHub, shared git utilities, synchronous specloop publication and status runner contracts remain unchanged

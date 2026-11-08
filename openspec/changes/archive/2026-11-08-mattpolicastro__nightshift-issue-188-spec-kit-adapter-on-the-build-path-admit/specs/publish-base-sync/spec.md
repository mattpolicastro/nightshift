## Purpose

Retain source-local Git credential transport and failure handling when native
finish leaves a completion commit instead of an OpenSpec archive.

## ADDED Requirements

### Requirement: Native completion credential boundary

Native Status commit construction SHALL use harness Git object operations
without source-local transport helper environments. External finish pushes
SHALL continue through the existing source-aware push boundary.

#### Scenario: Helpers stay out of completion objects
- **WHEN** native finish uses a source checkout with local credential helpers
- **THEN** only the origin push receives those helpers; internal blob/tree/commit/ref operations do not receive the forwarded helper environment
- **THEN** source config, run repo config, manifests, state and logs receive no copied helper values

## MODIFIED Requirements

### Requirement: Finish push helper forwarding

Finish SHALL forward current source-local helpers on its validated origin push
using the same credential transport as publish. Existing expected-head, task,
format completion and PR-readiness gates SHALL remain in force. OpenSpec
completion SHALL remain archival; native completion SHALL retain live files
and a Complete Status commit. This extends the durable retry artifact, without
changing the transport contract.

#### Scenario: Finish and retry use source helpers
- **WHEN** finish reaches its external push, including a retry after a failed or timed-out push
- **THEN** it rereads source-local helpers and uses their config environment, SAFE_FLAGS, `GIT_TERMINAL_PROMPT=0` and the existing 300-second timeout
- **THEN** failed push retains human_checks and the durable archive or native completion commit without readiness writes or done status; retry reuses it and can succeed after helper correction without merging

### Requirement: Credential failure boundary

Helper-bearing transports SHALL capture child output and catch
CalledProcessError and TimeoutExpired at the subprocess boundary. They SHALL
expose only the operation, exit status or timeout duration and a sanitized
stderr tail, without unsafe exception chaining. Native completion SHALL use the
same boundary as archival finish; no new error formatter is introduced.

#### Scenario: Failed transport diagnostics are safe
- **WHEN** either exception occurs during a helper-bearing fetch or push with a sentinel helper echoed by the child
- **THEN** driver pause diagnostics, direct publish JSON, act publish, direct finish, act finish, displayed tracebacks and logs omit the sentinel and raw exception text
- **THEN** diagnostics retain operation/ref, exit status or timeout duration and at most the last 400 characters of sanitized stderr, never falling back to stdout
- **THEN** publish remains retryable without PR writes or readiness loss; finish retains its archived or native completed retry state without marking the PR ready

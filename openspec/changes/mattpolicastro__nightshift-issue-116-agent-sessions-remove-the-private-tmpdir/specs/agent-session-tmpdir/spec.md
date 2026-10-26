## Purpose

Dispose of each Codex session's private temporary directory after its process
group ends, without changing attempt outcomes or retained evidence.

## ADDED Requirements

### Requirement: Session scratch lifetime

The Codex adapter SHALL continue creating
`<transcript-parent>/<transcript-stem>.tmp` per session with the existing
creation behavior and `TMPDIR` environment value. It SHALL remove that exact
path after the session process group stops executing, whether the agent
succeeds, fails, times out or is killed while the harness remains able to
finalize. It SHALL preserve sandbox flags and all existing result fields and
classification priorities.

#### Scenario: Successful session
- **WHEN** a Codex session writes scratch and valid final output and its process group exits successfully
- **THEN** the original private TMPDIR is available during execution and absent before the adapter returns the unchanged successful result

#### Scenario: Failed or killed session
- **WHEN** a session ends with a nonzero provider exit, invalid or missing final output, or an externally killed agent process group
- **THEN** its private TMPDIR is removed and the existing disposition, output, session, detail and usage semantics remain unchanged

#### Scenario: Timed out session
- **WHEN** a session reaches its wall or idle timeout
- **THEN** the group is terminated before its private TMPDIR is removed and the result retains its existing infra disposition and wall or idle timeout detail

### Requirement: Process group cleanup ordering

Cleanup SHALL remove scratch only after confirming that the spawned Codex
process group has no executing members and reaping its direct child. It SHALL
terminate remaining group members within bounded grace and confirmation
periods, preserving the original result or exception. Unconfirmed group exit
SHALL retain scratch and record a cleanup failure.
Process inspection SHALL preserve existing agent and quota environment,
executable-selection and quota-protocol contracts.

#### Scenario: Leader exits before descendant
- **WHEN** the leader exits but a descendant in the same group remains and ignores SIGTERM
- **THEN** finalization sends SIGTERM, allows ten seconds of grace, then escalates to SIGKILL with one further second for confirmation, without repeating grace already used by timeout handling
- **THEN** it reaps the direct child and removes scratch only after no executing group members remain; zombie rows do not prevent confirmation
- **THEN** the original leader result is preserved, no unrelated group is signalled, and Claude and daemon job-control behavior remain unchanged

#### Scenario: Startup or communication raises
- **WHEN** spawning fails before a child exists or Python raises while communicating with an already spawned child
- **THEN** scratch is removed immediately if no group started, otherwise only after terminating and confirming the spawned group, and the original exception propagates

#### Scenario: Group exit is unconfirmed
- **WHEN** process inspection, signalling or bounded confirmation cannot establish that the group has stopped executing
- **THEN** scratch remains, a group-exit cleanup-failure event is attempted, and the original attempt result or exception is preserved

#### Scenario: Inspection preserves host command boundaries
- **WHEN** a Codex-profile agent spawn performs process inspection before a subsequent quota read with a configured tool directory
- **THEN** agent and quota launches retain their exact sanitized environments, respective PATH suffixes, child environment values and inherited-variable/credential exclusions
- **THEN** the agent return tuple, quota `codex app-server` argv and complete initialize/initialized/rateLimits request sequence remain unchanged, with inspection launches recorded separately by the regression fixture and every existing assertion preserved

### Requirement: Confined scratch removal

Removal SHALL target only the session's private TMPDIR, tolerate read-only
entries by adding owner-write permission where needed, and treat an absent
target as success. It SHALL never follow symlinks during traversal, permission
repair or deletion, and SHALL preserve external targets, attempt evidence and
verify build directories. Unsafe operations SHALL fail cleanup safely.

#### Scenario: Read only scratch
- **WHEN** scratch contains read-only nested directories and files
- **THEN** cleanup adds owner-write permission to directories where needed inside scratch and removes the complete private directory
- **THEN** it preserves other permission bits during repair and unlinks read-only files through their parent without changing hard-linked file contents or permissions

#### Scenario: Symlinks stay confined
- **WHEN** the root or a nested scratch entry is a symlink, dangling symlink, or is replaced by a symlink during traversal
- **THEN** cleanup unlinks the link or safely reports failure without following it, and outside sentinel contents and permissions remain unchanged
- **THEN** uncertain entry identity or unavailable non-following operations cause a cleanup failure without falling back to unsafe traversal or permission repair

#### Scenario: Other artifacts survive
- **WHEN** scratch is removed beside transcripts, schema and output JSON, seals, receipts, review files, another session's scratch and a verify build directory
- **THEN** all those neighboring artifacts remain unchanged and only the selected session's scratch is removed

#### Scenario: Scratch is already absent
- **WHEN** the selected scratch path is absent at cleanup time
- **THEN** cleanup succeeds without creating a failure event or changing the attempt outcome

### Requirement: Non fatal cleanup events

A cleanup failure SHALL attempt to append a structured
`session_tmpdir_cleanup_failed` event to `session-cleanup.<transcript-stem>.jsonl`
beside the transcript without following symlinks or modifying existing attempt
evidence. Cleanup, event-write and fallback-logging failures SHALL NOT change
the original attempt outcome or mask its exception.

#### Scenario: Removal fails
- **WHEN** scratch removal raises a filesystem error after a successful, failed or timed-out session
- **THEN** a removal-failure event is persisted outside scratch and every original result field remains unchanged
- **THEN** the event contains an epoch-seconds timestamp, lexical TMPDIR path, failure phase (`remove` or `group_exit`), exception class and errno or null, without prompt, environment, file contents or provider messages

#### Scenario: Event sink fails
- **WHEN** a cleanup failure occurs and its event sidecar cannot safely be written
- **THEN** a fallback warning is attempted without modifying transcript or stderr, and neither event-writing nor logging failures change the original result or exception
- **THEN** an event-file symlink is never followed, and failure to persist the event is not reported as successful persistence

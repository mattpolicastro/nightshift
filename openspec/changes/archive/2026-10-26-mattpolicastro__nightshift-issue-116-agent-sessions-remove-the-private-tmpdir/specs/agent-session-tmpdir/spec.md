## Purpose

Dispose of Codex session scratch at adapter finalization while preserving
attempt outcomes, symlink targets and retained evidence.

## ADDED Requirements

### Requirement: Session scratch lifetime

The Codex adapter SHALL preserve per-session creation of
`<transcript-parent>/<transcript-stem>.tmp`, its writable `TMPDIR` value and
existing sandbox flags. It SHALL finalize that directory with `try`/`finally`
covering creation, setup, spawning and result interpretation. Finalization
SHALL attempt removal after normal completion or an exception, without
changing shared `spawn()` handling or Claude behavior.

#### Scenario: Successful session
- **WHEN** a Codex session writes scratch and valid output and `spawn()` returns success
- **THEN** the existing private TMPDIR is writable during execution and absent before the adapter returns the original successful result

#### Scenario: Failed or killed session
- **WHEN** `spawn()` returns a nonzero or signal exit, or final output is invalid or missing
- **THEN** finalization removes scratch and preserves the existing disposition, output, session, detail and usage

#### Scenario: Timed out session
- **WHEN** the existing shared runner returns after handling a wall or idle timeout
- **THEN** finalization removes scratch and preserves the infra result and timeout detail without changing timeout or group-kill handling

#### Scenario: Setup or spawn raises
- **WHEN** `_home()`, environment or argv construction, or `spawn()` raises after the private directory exists
- **THEN** finalization attempts removal and the original exception propagates unchanged even if cleanup also fails

### Requirement: Confined scratch removal

Cleanup SHALL use symlink-resistant `shutil.rmtree` for the exact session
directory and SHALL treat an absent root as already cleaned. Its permission
callback SHALL add only owner-write permission to the containing directory
within scratch and retry once; remaining errors SHALL be non-fatal cleanup
failures. Traversal, permission repair and retries SHALL NOT follow symlinks
out of scratch. Other sessions, evidence and verify builds SHALL be preserved.

#### Scenario: Read only entries
- **WHEN** scratch contains read-only nested directories and files with otherwise sufficient access for removal
- **THEN** cleanup adds `u+w` to containing directories as needed, preserves other permission bits and removes scratch without chmodding file targets
- **THEN** a failed retry is reported without an unbounded retry loop

#### Scenario: Symlinks stay confined
- **WHEN** scratch contains dangling and escaping symlinks, or its root has become a symlink
- **THEN** nested links are removed without following targets, a root link is safely refused, and outside sentinel contents and permissions remain unchanged
- **THEN** tests assert `shutil.rmtree.avoids_symlink_attacks is True`, and unsafe permission repair is refused

#### Scenario: Other artifacts survive
- **WHEN** cleanup removes scratch beside transcripts, schema/output JSON, seals, receipts, reviews, another session's scratch and a verify build directory
- **THEN** those artifacts remain unchanged, except for an appended session-stderr diagnostic if cleanup fails

#### Scenario: Scratch is already absent
- **WHEN** scratch is absent when finalization runs
- **THEN** cleanup succeeds without a failure event or outcome change

### Requirement: Non fatal cleanup diagnostics

A removal failure SHALL append a `session_tmpdir_cleanup_failed` JSON event
to session stderr after original result classification or during exception
unwinding. Cleanup and diagnostic-write failures SHALL preserve original
results and exceptions. No new process inspection, descendant termination
or PGID ownership handling SHALL be introduced.

#### Scenario: Removal fails
- **WHEN** removal fails after a successful, failed or timed-out session, or during exception unwinding
- **THEN** a separate JSON line records the cleanup failure in session stderr and the original result or exception is preserved
- **THEN** existing stderr bytes remain intact and the event identifies the lexical scratch path, exception type and errno or null
- **THEN** cleanup diagnostics cannot affect provider classification or result detail, including when failure text contains quota or authentication terms

#### Scenario: Descendant races cleanup
- **WHEN** a descendant remains after the leader exits and a race causes removal to fail
- **THEN** that failure is recorded non-fatally with no new process inspection, waiting or descendant signalling

#### Scenario: Diagnostic write fails
- **WHEN** removal fails and appending its stderr event also raises
- **THEN** the original result or exception is still preserved and no successful durable recording is claimed

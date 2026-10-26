## Context

`specloop/agents/codex.py:run()` creates
`call.transcript.parent / f"{call.transcript.stem}.tmp"` with
`mkdir(exist_ok=True)` and passes it through `base_env(..., TMPDIR=str(tmp))`.
All Codex roles use this path, including drafting outside an admitted run.
There is no teardown today. The issue reports 546 abandoned directories and
approximately 44 GB recovered on 2026-10-26.

`specloop/agents/__init__.py:spawn()` launches a new POSIX session and returns
`(code, timeout_reason)`. It waits for the direct child, sends SIGTERM on wall
or idle timeout, and escalates after ten seconds only if that child remains.
A leader can exit while a child in the same group still uses scratch; simply
placing `rmtree` after `spawn()` is insufficient.

`Call` has no run/event sink, and `Run.event()` requires run ownership and
state context. Inferring a run directory from a transcript would be wrong for
drafts. Existing provider tests replace `spawn(argv, env, call, stdin=None)`
and compare the original `Call`; preserve that interface.

The root README, adapter modules, repository `CLAUDE.md` and relevant existing
specifications were consulted. This checkout has no repository `AGENTS.md`.
The provider-fallback, host-paths, agent-test-rules, background-job-control and
specloop-documentation contracts remain in force. This adds scratch disposal;
it does not change provider results, daemon stopping or retained evidence.

## Goals / Non-Goals

Goals are prompt disposal after a Codex session's group stops, permission-tolerant
and symlink-safe removal, observable failures without outcome changes, and
deterministic tests needing neither Codex credentials nor a live service.

Non-goals are changing sandbox arguments or environment permissions, deleting
evidence or verification trees, adding cleanup to Claude, sweeping old runs,
tracking descendants that leave the session's process group, changing daemon
stop behavior, or recovering after the harness/host is forcibly terminated.

## Decisions

### Preserve creation and the public adapter interface

Add `session_tmpdir(call: Call) -> Path` in `specloop/agents/__init__.py` for
the existing lexical sibling path. `codex.run()` uses it with the same
`mkdir(exist_ok=True)` and `TMPDIR` value. Do not resolve the scratch path to
its target, broaden creation permissions, change `argv()`, or change `Call`,
`Result` or `spawn()` signatures. The directory remains available throughout
the process lifetime, for both write and read sessions as currently configured.

The actual cleanup owner is `spawn()`'s finalization path, where the process
handle is available. Enable the new finalization only for
`call.profile.adapter == "codex"`. Derive the deletion target from
`session_tmpdir(call)`, never from arbitrary environment entries or a glob.
Default Claude behavior stays unchanged. No caller edits outside the allowed
files are needed.

### Establish group quiescence before removal

Add a private group-finalization helper in `specloop/agents/__init__.py`.
Wrap the Codex process lifecycle, including stdin writes, in `try/finally`.
Preserve the original return tuple or raised exception independently of
finalization. A failed `Popen` means no group was started and scratch can be
removed immediately; Python exceptions after process creation require group
termination before removal.

Track the PGID created by `start_new_session=True` (the child's PID). After
leader completion, inspect that group for executing members, rather than
using the leader's exit as proof. Use bounded `/bin/ps -axo pid=,pgid=,stat=`
inspection, excluding zombie rows; malformed, empty or failed inspection is
unconfirmed, never proof of exit. Refuse signalling PGID 0, 1 or the harness's
own group. Only the group created for this invocation is eligible.

If members remain after normal exit, error, timeout or an externally killed
agent, send SIGTERM and allow the existing ten-second grace period for the
whole group; escalate to SIGKILL if members still execute even when the
leader has exited. Reap the direct child. Allow one further second to confirm
that no executing members remain, with inspection subprocess timeouts capped
by the remaining deadline. Do not start a second ten-second grace period
after the timeout path has already used it. Treat process-disappearance
races as success after confirmation. If signalling/inspection fails or the
group remains unconfirmed, retain scratch and record a cleanup failure;
never delete while the group might still use it or replace the original
result. These are adapter cleanup bounds, separate from daemon job-stop bounds.

### Confine permission repair and deletion

Add `_remove_session_tmpdir(tmp: Path) -> None` in
`specloop/agents/__init__.py`. It removes only the named session directory;
absence is a successful no-op. Open the trusted attempt parent once and use
descriptor-relative, non-following operations for its scratch entry and
descendants. A symlink at the root or anywhere below it is unlinked as a
link, including dangling links. Never recursively visit its target.

Use `O_DIRECTORY | O_NOFOLLOW` when opening directories, verify opened
identity against the non-following stat, and use `fchmod` on that opened
directory to add `stat.S_IWUSR` when needed before removing entries. Retain
other permission bits. Read-only files can be unlinked through their writable
parent on POSIX; do not chmod file targets or hard-linked file contents.
Do not use shell `rm`, recursive path-based chmod, or a symlink-following
permission-retry callback. If a safe operation or identity check is unavailable,
fail cleanup instead of falling back to unsafe traversal. An entry swapped
to a symlink during traversal must be unlinked or refused, never followed.

### Record failure separately from attempt evidence

Add a private best-effort event writer in `specloop/agents/__init__.py`.
Append one JSON object per failed cleanup to
`call.transcript.parent / f"session-cleanup.{call.transcript.stem}.jsonl"`:

`{"at": <epoch-seconds>, "event": "session_tmpdir_cleanup_failed", "tmpdir": <lexical-path>, "phase": "group_exit" | "remove", "error_type": <exception-class>, "errno": <number-or-null>}`

The `session-cleanup.` prefix keeps new diagnostics outside protected `review*`
names even for review sessions. Keep diagnostics structural: do not include prompt, environment, file contents
or provider messages. Open this new sidecar without following a symlink;
never append to the provider transcript, stderr, run ledger, outputs or review
files. It works for both draft and admitted-run sessions without locating a
`Run`. Successful cleanup, including an already absent path, creates no event.

Catch cleanup exceptions at the finalizer boundary. If event persistence also
fails, issue a best-effort standard Python logger warning carrying the same
event name and structural fields, containing logger failures too. The original
outcome always wins: no changed disposition, output, session, usage, detail,
timeout reason, retry counters or raised exception. Complete disk/log failure
cannot guarantee a durable event; do not turn that limitation into an attempt
failure or claim the event was saved.

### Validation and documentation

Use new tests under `tests/`, plus the explicitly authorized recorder migration
below, preserving every existing assertion. Test
filesystem confinement with outside sentinels (bytes and modes), read-only
nested directories/files, missing paths, root/nested/dangling symlinks and an
injected replacement race. Test group ordering with process doubles and a
short local Python subprocess tree whose leader exits while a child ignores
SIGTERM; use readiness handshakes and bounded waits, with unconditional test
teardown. No live Codex invocation or host credentials are needed.

`tests/test_host_paths.py:test_agents_and_quota_process_environments` currently
records every `subprocess.Popen` in one `launches` list, then assumes index 0
is the agent and index 1 is `codex app-server`. Its direct `agents.spawn()`
call uses a Codex profile, so finalization will insert `/bin/ps` before the
quota call even though this fixture creates no session TMPDIR. Explicitly
authorize changing this test's recorder setup and `launch` wrapper in task
1.3, alongside the lifecycle integration that invalidates that assumption.

Keep `launches` for non-inspection commands and add a separate `inspections`
list. Classify only exact argv `['/bin/ps', '-axo', 'pid=,pgid=,stat=']` as
inspection; record all other commands in `launches`, so unexpected launches
remain visible. Forward every call unchanged to the saved real `Popen`.
Add assertions that inspections occurred and that `launches` contains exactly
the agent `['path-probe']` followed by `['codex', 'app-server']`; do not depend
on the number of inspections. Preserve all existing assertion statements and
scenario markers unchanged, including both exact launch environments, child
environment values, inherited-variable/credential exclusions, `spawn()`'s
return tuple, quota argv and the complete initialize/initialized/rateLimits
request sequence. Do not bypass finalization, change the profile to Claude,
stub quota replies, or loosen equality checks to make the test pass. This is
a fixture migration preserving the host-paths and agent-test-rules contracts,
not a change to either contract; no MODIFIED or REMOVED delta is needed.

Exercise `codex.run()` through the real shared `spawn()` using a substituted
Python argv. Include the four required test names:

- `test_session_tmpdir_removed_after_success`
- `test_session_tmpdir_removed_after_timeout` (wall and idle)
- `test_session_tmpdir_removal_never_follows_symlinks`
- `test_session_tmpdir_removal_failure_does_not_fail_attempt`

Also cover nonzero provider exits, invalid/missing output, externally killed
agents, spawn/stdin exceptions, unconfirmed group exit and event-write failure.
Compare original result fields and exceptions; preserve evidence and a sibling
verify-directory sentinel. Use scenario markers for the delta IDs. Final
implementation acceptance includes
`uv run pytest tests/test_host_paths.py tests/test_session_group_exit.py tests/test_session_tmpdir_removal.py tests/test_session_tmpdir_lifecycle.py`
with real inspection launches in the migrated fixture, then `uv run pytest`, plus
`uv run python scripts/check_types.py` without changing the frozen baseline.

Update only the run-state discussion in `docs/specloop/README.md` and a focused
open-gap entry in `KNOWN-LIMITATIONS.md`. Explain disposable session scratch,
the failure sidecar, unchanged retained evidence/verify directories, old
leftovers and crash limits. Cite issue #116 without inventing an implementation
PR. No new CLI examples or prose-matching tests are needed.

Human checklist for the implementation PR, not implementing-agent tasks:

- After the first build using this change, inspect one finished real Codex
  attempt directory. Record its path and outcome, confirm that session's
  `<stem>.tmp` is absent, and verify transcripts, outputs, seals, receipts and
  reviews remain. Inspect any cleanup-failure sidecar. CI cannot establish
  this live-host result; keep it pending until observed.
- Review the two documentation edits for readability and verify the issue link.

## Risks / Trade-offs

Process-group inspection adds bounded teardown work, and terminating straggling
group members is necessary before disposing their scratch. Zombie processes
cannot write, so they do not block cleanup. Detached descendants are outside
the group contract. An unconfirmed group or filesystem failure leaves a
directory and a diagnostic instead of risking deletion during active use.

Descriptor-based removal is more involved than plain `rmtree`, but prevents
permission repair from following a symlink to retained evidence or another
tree. Unsupported safe operations must leave scratch behind. Ordinary
read-only entries are supported; immutable flags and ownership/access errors
can still prevent removal.

Per-session failure events are discoverable beside transcripts but are not
aggregated into run-level events. That avoids widening the public interfaces
or modifying evidence. Storage failure can defeat both persistence and logging.

SIGKILL of the harness, power loss and old completed attempts can still leave
scratch. This change adds no startup sweep or background janitor. Existing
evidence and verify storage will continue to grow under their current policies.

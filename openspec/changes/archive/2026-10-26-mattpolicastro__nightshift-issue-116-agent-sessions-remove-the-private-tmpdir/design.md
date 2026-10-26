## Context

`specloop/agents/codex.py:run()` creates
`call.transcript.parent / f"{call.transcript.stem}.tmp"` with
`mkdir(exist_ok=True)`, calls `_home()`, builds the environment and argv, and
calls `spawn()`. Scratch currently survives every completed attempt.

`specloop/agents/__init__.py:spawn()` starts a new session, waits for the
direct child and uses `killpg` on wall/idle timeout. Its handling stays
exactly as it is. The cleanup boundary is `codex.run()` finalization, not a
new guarantee of process-group quiescence. Review explicitly accepts a
descendant racing cleanup after its leader exits.

The root README, engine guide, limitations, adapter code and relevant
`openspec/specs/` contracts were consulted. No applicable `AGENTS.md` exists
in this checkout or its ancestors. Repository `CLAUDE.md` also describes
engine constraints. Existing `agent-test-rules`, `provider-fallback`,
`host-paths` and `specloop-documentation` requirements remain unchanged.
Python 3.12+ is required; review confirms symlink-resistant `rmtree` on the
host's macOS Python 3.12 and 3.14.

## Goals / Non-Goals

Goals: remove newly completed sessions' scratch, including after setup errors;
handle ordinary read-only entries; preserve symlink targets, evidence and
attempt outcomes; verify behavior offline with small tests.

Non-goals: process inspection or additional termination, changing shared
runner interfaces, sandbox permissions or Claude behavior, cleaning historical
directories, deleting evidence or verify builds, and recovering from harness
SIGKILL or host failure. There is no new cleanup sidecar or run-event API.

## Decisions

### Finalize entirely in the Codex adapter

Keep the existing lexical temp path, `mkdir(exist_ok=True)`, `TMPDIR`, `_home`,
`argv`, `Call`, `Result` and `spawn` interfaces. Start a `try` before temp
directory creation and keep `_home()`, environment/argv construction,
`spawn()` and the existing result interpretation inside it. A `finally` calls
a small private cleanup helper in `specloop/agents/codex.py`. Handle an absent
temp path as already cleaned. Do not return or raise from the finalizer for
cleanup errors.

Keeping result interpretation inside the `try` matters: the current adapter
classifies failures from transcript and stderr text. Compute the original
`Result` before finalization appends a diagnostic, so cleanup text mentioning
quota or authentication cannot alter disposition or detail. A setup, spawn
or parsing exception still propagates unchanged after cleanup. In particular,
`_home()` raising after `mkdir` must not leak scratch.

Success, provider error, invalid output, wall/idle timeout and signal exit use
the same finalizer. It does not inspect groups, run `ps`, signal descendants
or wait for group quiescence. A `spawn()` exception also triggers cleanup
without adding process management responsibilities to the adapter.

### Use the standard library for removal

Implement a private `_remove_session_tmpdir(tmp: Path) -> None` using
`shutil.rmtree(tmp, onexc=...)`; `onexc` is available at the Python 3.12 minimum.
Assert `shutil.rmtree.avoids_symlink_attacks is True` in a test. Use the standard
implementation rather than a custom recursive walker. If safe removal is
unavailable, report a non-fatal cleanup failure instead of selecting a
symlink-following fallback.

On a permission failure, the callback adds only `stat.S_IWUSR` to the
containing directory and retries the failed operation once. Other errors,
unsafe repairs and failed retries propagate to the cleanup-failure boundary.
Do not recursively chmod files: POSIX unlink needs a writable parent, not a
writable file. Preserve other permission bits. Restrict repair to real
directories within scratch; never chmod a symlink target or the attempt parent.
Use non-following directory access for permission repair (for example, an
opened directory descriptor and `os.fchmod`), and retain non-following behavior
on retry. Do not replace `rmtree`'s safe traversal with a path-following callback.

Nested and dangling symlinks are removed as links. If the scratch root itself
has become a symlink, allow `rmtree` to refuse it and report failure; never
resolve it and delete the target. A missing scratch root is a no-op. The only
deletion target is this session's exact path, never a glob. Neighboring evidence
and verify trees stay intact.

### Append diagnostics after determining the original outcome

On cleanup failure, append a JSON line to
`call.transcript.with_suffix(".stderr")`, creating it if setup failed before
`spawn()` opened it. Preserve existing bytes and ensure the event starts on a
separate line. Use event name `session_tmpdir_cleanup_failed` and fields
`tmpdir` (the lexical path), `error_type`, and `errno` (or null). This is the
issue's recorded event; no run ledger or sidecar is needed. Success creates
no event.

Catch diagnostic-write errors as well as removal errors. Neither may change
any original `Result` field or mask an exception already propagating. If the
stderr sink is unwritable, durable recording cannot be guaranteed; preserving
the original outcome still takes precedence. Do not claim that an event was
saved then. Do not truncate provider diagnostics or modify stdout transcripts,
output JSON, seals, receipts or reviews.

### Focused offline tests and acceptance

Put implementation tests in new `tests/test_session_tmpdir.py`, using isolated
homes, filesystem fixtures and a substituted local Python command or `spawn`
double. No Codex binary, credentials or service is needed. Parameterize related
cases to keep code and tests together within about 300 changed lines. Include
the required names:

- `test_session_tmpdir_removed_after_success`
- `test_session_tmpdir_removed_after_timeout` (wall and idle)
- `test_session_tmpdir_removal_never_follows_symlinks`
- `test_session_tmpdir_removal_failure_does_not_fail_attempt`

Also cover nested read-only directories/files, absent scratch, and setup
raising after creation (at least `_home()`, with environment/argv and spawn
seams in a shared parameterized test). The symlink test asserts the stdlib
safety property and includes dangling and escaping links with outside sentinel
bytes and modes unchanged; a root link is refused safely. Check writable
scratch during the call, deletion before return/exception, unchanged
evidence/verify sentinels, and original result fields for nonzero, signal and
invalid-output cases. Inject persistent removal failure, including `ENOTEMPTY`
as the deterministic descendant-race case, and stderr-write failure. Verify
preserved provider stderr and parsed event fields, comparing complete original
results/exceptions, including errors with quota/authentication terms. Do not
introduce live descendant-tree management tests.

Preserve every existing assertion and existing test file; no host-path fixture
migration is needed. Add scenario markers for the delta IDs. Run focused tests,
then `uv run pytest` and `uv run python scripts/check_types.py` for implementation
acceptance, without modifying the type baseline.

### Documentation placement and structural validation

Update the run-state passage in `docs/specloop/README.md` and add one focused
entry under `KNOWN-LIMITATIONS.md`'s Open gaps. Describe disposable Codex
scratch, diagnostics in session stderr, retained evidence/verify builds,
accepted descendant races, historical leftovers and harness/host crash limits.
Cite [issue #116](https://github.com/mattpolicastro/nightshift/issues/116)
until an implementation PR is known. Add no CLI examples or artifact table.

Use physical-line placement: the guide's start is the unique line beginning
`**Run state** lives in `, and its end is the unique following line exactly
`The guarded migration uses these build-state mappings:`. For limitations,
use the unique exact `## Open gaps` line, stopping at the next column-zero
`## ` line. Missing, duplicated or reversed boundaries block the edit/check;
do not infer headings from fences, HTML or indentation. Apply these rules
literally even if a boundary appears inside an example.

New structural checks in `tests/test_session_tmpdir_docs.py` use those slices
and physical-line inline links of the form `[label](target)` with no whitespace
or nested parentheses in the target. Refuse malformed or ambiguous link syntax
rather than guessing. Check local link destinations exist and issue/PR URL
forms agree with their labels; run existing structural link and command checks
as regression coverage. Do not require prose phrases. Meaning and readability
remain review work.

Human checklist for the implementation PR (not implementing-agent tasks):

- After the first build running this change, inspect one finished real Codex
  attempt directory, record its path/outcome and confirm its `<stem>.tmp` is
  absent. Check session stderr for cleanup failures and confirm transcripts,
  outputs, seals, receipts, reviews and verify builds were retained. CI does
  not establish this host result; leave it pending until observed.
- Review the two documentation edits for readability and the live issue link.

## Risks / Trade-offs

A descendant may continue using scratch after the leader exits. Removal may
race it, fail, or be followed by recreation; there is no quiescence guarantee.
A removal error is recorded without changing the attempt outcome. No additional
process-group machinery is added to avoid this accepted race.

Read-only entries with otherwise sufficient access are supported; immutable
flags, ownership restrictions or inaccessible directories can still prevent
cleanup. Symlink-resistant stdlib traversal does not justify following links
in permission repair. Unsafe repair must fail non-fatally.

Harness SIGKILL, host failure and previously finished attempts can leave
scratch. Cleanup failure plus an unwritable stderr sink can leave no durable
diagnostic. Evidence and verify build storage continue under current policies;
this is not general run retention management.

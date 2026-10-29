## Context

`specloop/manifest.py` uses the same frozen `Step` dataclass and `_steps()` parser for prepare, verify, acceptance and mutation-only commands. #135 appended `env` and added validation and literal substitution. Unknown step keys are currently ignored. `template()` preserves parsed TOML through overrides and reloads.

`specloop/verify.py:run_extracted()` currently writes one `verify.sb` and, when needed, one `prepare.sb`, then passes `(Step, Path)` pairs to `_run_commands()`. That loop constructs each child's environment, calls `wrap()` and launches `Popen`. `wrap()` deliberately bypasses sandbox-exec inside an enclosing sandbox. An opted-in step cannot use that bypass: the outer sandbox cannot be assumed to enforce a newly sampled deny list.

Existing specifications require credential denials, read-only extracted source, confined writes, isolated environments, preparation ordering, dependency snapshot isolation, process-group cleanup and stable evidence behavior. All remain. The modification to **Independent mutant workspaces** permits only this new opt-in; default steps retain the existing restrictions. References to offline verification continue to exclude external networking; affected operator text will explicitly describe the localhost exception.

There is no `AGENTS.md` in this checkout or its ancestor directories. Relevant current coverage is in `tests/test_specloop_seal_verify.py`, `tests/test_verify_credentials.py`, `tests/test_step_env.py`, `tests/test_step_env_docs.py` and the evidence tests. Preserve their assertions, including exact default profile filenames and preparation order.

## Goals / Non-Goals

Enable trusted manifests to run Flutter-style localhost TCP test exchanges while excluding TCP ports already listening when each opted-in step is about to start. Make the engine behavior testable with Python sockets and controlled process output, without Flutter, a forge or model servers.

Do not alter existing manifest steps, prepare networking, non-network sandbox policy, evidence scoring, command environments, plugin/daemon behavior or external-network restrictions. Do not add a Flutter-specific runner, a background port watcher, a new receipt schema, a general network-policy language or explicit `network-bind` permission.

## Decisions

### Schema and applicability

Append `loopback: bool = False` after `env` in `specloop/manifest.py:Step`. `_steps()` reads only a real boolean (`type(value) is bool`), rejects strings/numbers/null/containers with a `ValueError` naming the kind, step and setting, and continues ignoring other unknown keys. Old positional calls and independent env defaults remain valid. Test parsing, `template()` overrides and reloads for all four step kinds.

The flag applies whenever a step executes in verification mode: ordinary verify, acceptance, selected mutation-only commands, baselines and confirmation runs. Prepare steps accept the common field but it has no effect in prepare mode: their existing profile and networking remain identical, and they take no listener snapshot. Internal snapshot warming remains unchanged. Directly constructed steps receive the same boolean validation before launch.

### Pure profile generation

Extend `profile()` with keyword-only `loopback: bool = False` and `listening_ports: frozenset[int] | None = None`. Non-prepare loopback generation requires an explicit successful snapshot, including an explicitly empty set; `None` is an error. Validate ports as integers from 1 through 65535. Profile construction itself performs no process or network discovery.

For default verification retain the entire existing output byte for byte, including `(deny network*)`. Prepare mode takes precedence and returns its unchanged profile regardless of the flag. An opted-in verification profile retains `(deny network*)` and adds exactly:

```scheme
(allow network-inbound (local tcp "localhost:*"))
(allow network-outbound (remote tcp "localhost:*"))
```

After both allows, append one `(deny network-outbound (remote tcp "localhost:PORT"))` per sorted, unique snapshot port. The deny covers the port through localhost across address families, even if only one family supplied the listener. No later network allowance may override it. Use Seatbelt's `localhost` spelling, never numeric `127.0.0.1:*`; add no UDP or bind allowance. Existing credential, source, writable-path and stored-dependency restrictions remain identical.

### Listener discovery

Add `_listening_loopback_ports() -> frozenset[int]` in `specloop/verify.py`. Use fixed argv `/usr/sbin/lsof -nP -iTCP -sTCP:LISTEN -Fpn`, without a shell, PID/user restriction, candidate PATH or candidate step env. Run outside the child sandbox, with captured stdout/stderr, numeric endpoints, strict decoding and a five-second timeout. `-Fpn` selects machine-readable process/name fields rather than a human column layout.

Parse process records and listener name records deterministically. Each `n` endpoint is a numeric address plus decimal port (brackets around IPv6 when emitted by lsof), or `*:PORT`. Include IPv4 loopback addresses in 127/8, IPv6 `::1`, IPv4-mapped loopback, and wildcard binds `*`, `0.0.0.0` and `::`, which can accept loopback traffic. Exclude syntactically valid specific non-loopback addresses. Deduplicate and sort only when rendering. Reject missing/invalid ports, malformed or incomplete records, unexpected field structure and nonnumeric hosts rather than silently dropping uncertain listeners.

Exit zero with valid records is success. lsof's no-match outcome (exit one, empty stdout and empty stderr) is a successful empty snapshot. Any other nonzero exit, nonempty diagnostic stderr, launch/permission error, timeout or decoding/parsing failure raises a contextual `RuntimeError`; partial output never authorizes launch. Fixture tests cover both families, wildcards, duplicates, non-loopback addresses, empty results and each failure class. This helper is separate from the existing dependency `SnapshotContext` and does not use its storage or cache.

### Resolve policy at each launch

Keep `verify.sb` and `prepare.sb` and existing call sites for unflagged steps. Add an optional `profile_for_step: Callable[[Step, Path, int], Path] | None` keyword to `_run_commands()`. `run_extracted()` supplies a closure with manifest/build/snapshot context; the index is the position in the scheduled sequence. The closure returns the existing path for preparation and unflagged commands. For an opted-in verification command it rejects enclosing sandboxes, samples listeners, renders the profile and writes `out/verify-loopback-<index>.sb`, outside the child-writable build directory. Do not derive the pathname from arbitrary step IDs or overwrite the shared default profile.

Invoke the resolver after env/argv validation and immediately before `wrap()`/`Popen`, after preceding commands and their process-group cleanup have completed. Only profile rendering/writing and launch bookkeeping intervene; never compute snapshots when building the schedule or reuse them across steps, retries, acceptance, workers or confirmation phases. Preserve each generated profile as diagnostics. Test launch ordering with changing snapshot results and a recorded `Popen` seam, and separate execution directories with the same step IDs. Existing `dataclasses.replace()` timeout copies retain the new field without changing evidence code.

If `_run_commands()` sees a loopback request for a verification profile without its resolver, refuse rather than running a default or stale profile. A snapshot, profile-write or enclosing-sandbox failure writes a diagnostic in the affected step log, persists already-completed receipts and raises `RuntimeError` naming the step. Do not fabricate an exit code, timeout or passing receipt for a command that never ran, and do not launch later commands. No unsandboxed retry is introduced. Ordinary `wrap()` behavior is unchanged for old steps.

### Separate sandbox startup from command outcomes

Launching `sandbox-exec` does not establish that the command ran: profile compilation can exit 65 before executing its argv. For opted-in steps only, add a private `_run_loopback_command()` helper in `specloop/verify.py` that returns the actual command's `int | None` outcome or raises contextual `RuntimeError` for setup/transport failure. Never compare the sandbox wrapper's status directly with `Step.expect`, and never reserve an exit number or match stderr prose to distinguish setup errors: a real command may also exit 65.

Run a small trusted Python supervisor through `sandbox-exec` using the harness's absolute `sys.executable`, `-I -S -c` and a fixed code string owned by `verify.py`. Pass the expanded argv as separate arguments, without shell interpolation. The supervisor uses `subprocess.Popen(argv, close_fds=True)` in the inherited process group and cwd, with the exact configured step environment. Python's exec-error reporting ensures a failed command exec raises before Popen returns. The supervisor sends `started\n` only after Popen successfully starts the command, waits for it, sends `exit:<signed-decimal-returncode>\n`, then exits zero. A failed exec or supervisor exception produces no completed result and fails closed. Isolated Python startup must not import candidate modules or honor candidate Python startup settings; the command itself still receives its configured environment. No read or write allowance is added for this helper; an inaccessible harness interpreter is a setup error.

Use a fresh anonymous pipe per launch as the control channel, inherited only by sandbox-exec and the trusted supervisor via `pass_fds`. The command and its descendants must not inherit either end. stdout/stderr remain the ordinary command log and cannot supply control records. The parent closes its write end immediately after launch, closes all descriptors on every path, and reads at most 65 bytes after process cleanup; a valid protocol is at most 64 bytes. Accept only the exact ordered ASCII records above with a valid subprocess returncode and EOF, rejecting missing, duplicate, malformed, oversized or extra records. No shared marker file, candidate-writable path, stderr heuristic or separate preflight sandbox authorizes a receipt. The same sandbox instance must launch the command under the freshly generated profile.

Keep one `step.timeout` deadline for sandbox/supervisor/command execution and existing elapsed-time accounting; do not add a second full command budget. Always terminate and reap the process group before interpreting results, including wrapper errors and timeouts. Record the expanded manifest argv in receipts, not the supervisor argv. Apply these outcomes:

| Observed launch outcome | Harness result |
| --- | --- |
| Wrapper cannot launch, rejects the profile, fails sandbox application, or exits without a valid complete protocol | Setup `RuntimeError`; preserve wrapper status/output as diagnostics, no affected-step receipt, no later command |
| Supervisor cannot exec the command, or the control channel fails | Same setup error, regardless of `expect` |
| Wrapper exits zero with valid start and command-exit records | Receipt uses the reported command exit, including genuine 65 or negative signal codes; existing expected-exit and evidence rules apply |
| Deadline expires with a valid start record and no completion record | Existing command-timeout receipt (`exit: null`), never a passing expected exit |
| Deadline expires without a valid start record | Setup error, not a test timeout |

A complete command result received at the deadline is usable only if normal wrapper completion is established; an inconsistent or broken transport fails closed. A supervisor/wrapper failure after command start without an authoritative result remains an infrastructure error, not a mutant kill. Retain earlier receipts and the step log/profile for all setup errors. In `specloop/evidence.py`, existing exceptions already propagate out of baseline, mutant and confirmation execution and trigger cleanup; do not edit its scoring or receipt schema. A baseline setup error dispatches no mutants. A setup error after a passing baseline must propagate through worker-error handling, leave that mutant unjudged and prevent a successful partial evidence file. Other workers retain existing completion/cleanup behavior.

### Tests and validation

Use new focused test files so existing assertions stay intact. Task 1 covers schema, discovery and pure profile behavior; task 2 wires that policy into launch with command-result provenance; task 3 covers real enforcement and operator guidance. Together they must include these exact test names:

- `test_loopback_step_profile_allows_localhost_tcp`
- `test_loopback_denies_ports_listening_at_launch`
- `test_steps_without_loopback_keep_deny_network`
- `test_loopback_snapshot_failure_fails_closed`
- `test_loopback_sandbox_allows_own_port_and_blocks_existing_listener`

Add `test_loopback_sandbox_startup_failure_is_not_command_exit`: simulate a wrapper exiting 65 without control records, parameterized with `expect = 65` and `expect = 0`, and assert setup error, no command sentinel, no affected receipt, retained previous receipts/diagnostics, no later command and complete cleanup. Include command-exec failure, missing/malformed control records, wrapper launch errors and pre-start timeout cases. Pair it with `test_loopback_command_exit_retains_expected_exit_semantics`, where actual commands exit 65 with matching and nonmatching expectations, signal or time out after start; assert ordinary receipts/classifications and that stdout resembling control records is inert. Exercise the trusted supervisor with real local Python children without Seatbelt in portable tests, including descriptor closure and candidate Python-env isolation, so process mocks alone do not validate the protocol.

Add `test_loopback_sandbox_startup_failure_is_not_mutant_kill` against the real evidence path with controlled launch seams: pass the baseline, then fail sandbox startup in a mutant or its confirmation. At concurrency one and two, assert propagation rather than killed/survived/timeout/invalid fabrication, no receipt for the affected command, no successful partial evidence, and normal worker/baseline cleanup. Also cover baseline startup failure with no mutant dispatch. Keep `specloop/evidence.py` unchanged.

The socket integration test starts a real Python TCP listener in the parent before invoking `run_extracted()`, retains it until the test finishes and proves it accepts an unsandboxed control connection. The opted-in child binds `127.0.0.1:0` after launch, listens, connects back and exchanges a payload, then attempts the pre-existing listener and must receive a sandbox permission denial. Use bounded socket timeouts and cleanup in `finally`; a dead listener, connection refusal or timeout alone does not prove sandbox enforcement. Repeat the exclusion probe for an IPv6-only `::1` listener on hosts with IPv6, and a wildcard listener; the child's own IPv4 ephemeral exchange remains mandatory. Capture and inspect the actual generated profile. On the same eligible hosts, inject malformed profile text into the real launch path and verify that actual sandbox-exec failure cannot pass even with `expect = 65`, leaves the command sentinel absent and produces no receipt. Pair that with a valid profile and real command exiting 65 to prove the distinction. These integration cases skip explicitly on non-macOS or when `already_sandboxed()` prevents nesting; do not skip unexpected policy failures on an eligible host. IPv6 unavailability may skip only its parameterized case, with a reason.

Unit tests run without sandbox-exec by controlling the discovery and process seams. They check no snapshot for default/prepare steps, byte-identical default profiles, exact clause ordering, fresh snapshots at successive launches, isolated profile paths, direct-construction validation, and no affected or later `Popen` after failures, even with nonzero `expect`. Keep existing source/credential/env and evidence tests green. Final implementation acceptance is `uv run pytest` plus `uv run python scripts/check_types.py` with no new type errors; do not edit the committed type baseline.

### Operator documentation and deterministic example checks

Update only invalidated verification passages in the three allowed documents. Explain false defaults, preparation's unchanged behavior, per-launch exclusions, failure diagnostics and unsupported nested execution for opted-in steps. Explain that sandbox startup failures are infrastructure errors regardless of expected exit and cannot count as mutant kills; only actual command outcomes reach receipts. The guide includes a small Flutter verification fragment, with analyze unflagged and test flagged, invoking `{flutter_root}/bin/cache/dart-sdk/bin/dart` and `{flutter_root}/bin/cache/flutter_tools.snapshot` directly, with `--suppress-analytics`, `--no-pub` and step env `FLUTTER_ALREADY_LOCKED = "true"`. Since the runner's cwd remains `{build}`, use a trusted `/bin/sh -c` wrapper whose fixed script is `cd "$1" && exec "$2" "$3" --suppress-analytics test --no-pub` (substitute `analyze` for the analysis step). Supply the shell's `$0` label followed by the project, Dart and snapshot paths as separate argv elements, so paths containing spaces remain literal. Dependency fetching, SDK initialization and writable preparation of `{build}/project` are prerequisites, not new engine behavior. This fragment must not modify the existing env example or any enrolled manifest.

Surround the new fragment with exact physical lines `<!-- loopback-example:start -->` and `<!-- loopback-example:end -->`. Count each marker globally, including lines whose stripped form matches: require exactly one occurrence, unindented and without trailing whitespace, in start/end order. Between them require the first line to consist of three backticks immediately followed by `toml`, and the last to consist of exactly three backticks, with no padding. Reject any interior line containing three backticks or three tildes. Missing, duplicate, reversed, padded or malformed delimiters and invalid TOML fail the test. No Markdown/HTML/fence-context or indentation interpretation may hide a marker. Parse the interior with `tomllib` and `manifest.parse()` using a minimal fixture, then assert argv, env, placeholders and the two distinct loopback values without invoking Flutter or requiring exact explanatory prose.

### Human checklist for the implementation PR

On the actual macOS host, run one prepared real Flutter project's manifest test step with `loopback = true` and the new env support. Confirm `flutter test` passes, inspect its retained profile and listener snapshot evidence, and from that sandboxed test code attempt TCP connections to the pre-existing model server, forge and agent-daemon listeners. Confirm those services were listening before launch and remained healthy, while their connection attempts were denied. Record Flutter/Dart/macOS versions and redacted outcomes. Confirm analyze still passes without opt-in. CI cannot establish this project-specific result or real service isolation; keep it pending until performed. Review the scoped prose and issue links for readability and correctness. These checks are not implementing-agent tasks.

## Risks / Trade-offs

- The lsof observation and process launch are not atomic. Another service can start during that interval or later and be reachable. This deliberately protects the observed launch-time ports, not service identity or all future host services. A port observed at launch remains blocked even after its original listener exits or the step reuses it.
- lsof is a host dependency and its visibility is limited by host privileges and platform behavior. Reject reported incomplete/denied discovery; do not claim fixture tests prove complete visibility on every host. Real-host checks must include the services this feature is intended to protect.
- Sibling opted-in executions can observe one another's listeners; fresh profiles remain independent, but availability of a chosen fixed port can differ. No concurrency or mutation-classification changes are proposed.
- The spike established IPv4 Flutter operation and the two Seatbelt clauses only. IPv6 listeners must be excluded now, but this does not claim Flutter's IPv6 own-port exchange was measured. Integration failures on supported hosts block implementation acceptance rather than justifying broader allows.
- Nested sandboxes cannot apply this new policy. Reject opted-in execution there while preserving old nested-test behavior; explicit integration skips are absence of enforcement evidence, not a pass.
- The trusted supervisor adds one process for opted-in commands and depends on inherited pipe descriptors surviving sandbox-exec. Portable protocol tests and real macOS launch tests must establish this; channel failures block execution results without weakening the sandbox. Missing results are conservatively infrastructure errors even if a command may have run.
- Three tasks separate schema/pure rendering, launch-time enforcement with portable regressions, and real-host integration coverage with operator guidance. Target roughly 180–260 changed lines for task 1 and at most about 300 each for tasks 2 and 3 using parameterized fixtures. Tests accompany each executable change; the final task adds platform-specific validation and documentation without changing runtime code.

## Context

`loop._fetch_publish_base` fetches the source origin into `repo.git` with
`seal.SAFE_FLAGS`, `GIT_TERMINAL_PROMPT=0`, inherited global configuration and a
caller-provided timeout. It does not inherit source-local helpers. Its current
no-helper error formatter uses stripped first-400-character stderr/stdout and
chains the original exception. Tests assert both exact text and cause identity.

`cli._publish` and `cli._finish` call `_push(repo, url, ref)`. That function
currently passes only `check=True, timeout=300` to an uncaptured Git push; it
has neither SAFE_FLAGS nor an explicit prompt override. Publish JSON serializes
`str(exception)`, and act uses the same exception in refusal detail. Adding
secret values to argv would expose them in process listings and exception text;
letting child stderr stream would bypass application redaction even with env.

The source path is already available as `ctx.m.repo`; use it directly. No new
manifest field, state field or forge capability is needed. Existing requirements
in publish-base-sync, specloop-forge, run-actions and specloop-documentation
remain binding except the two explicitly qualified git-argv promises below.
No AGENTS.md was present in the repository or inspected parent directories.

## Goals / Non-Goals

Make source-local authentication available to base fetch, publication and finish
without writing helpers into repository configs, state, logs or manifests.
Preserve byte values and reset semantics, make failures safe to display, and
retain current behavior when there are no source-local helpers.

Do not change seal.git, internal local archive pushes, agent credentials, API
bot tokens, origin validation, merge/verification scheduling, daemon or land.
Do not import URL-scoped credential sections, global helpers as local entries,
source include files, other Git config or resolved credential secrets. Do not
rewrite relative helper paths, execute helpers while reading config, or promise
that arbitrary helper-produced secrets can be discovered and redacted.

## Decisions

### Shared interfaces in specloop/loop.py

Keep the small shared functions in `loop.py`, which `cli.py` already imports:

- `_source_credential_helpers(source: Path, *, timeout: float) -> tuple[bytes, ...]`.
- `_credential_transport_env(helpers: tuple[bytes, ...]) -> dict[str, str]`.
- `_credential_transport_error(operation: str, exc: subprocess.CalledProcessError | subprocess.TimeoutExpired, helpers: tuple[bytes, ...]) -> RuntimeError`.

Names may follow local conventions, but retain these data and ownership
boundaries. Helpers stay raw bytes until conversion solely for subprocess env.
There is no cache: every fetch/push rereads source-local config, including retries.

Read with `git -C <source> config --local --null --get-all credential.helper`,
`capture_output=True`, binary output, and a finite timeout of `min(timeout, 20)`.
Do not use seal.git or shell interpolation. `--local` without enabling includes
selects only the local helper entries. Exit 1 with empty stdout/stderr means no
entries. Exit 0 requires nonempty, NUL-terminated output. Remove exactly the final
NUL and split on NUL: `b"\0"` is one empty reset, not absence. Preserve spaces,
newlines, duplicate entries, order and non-UTF-8 bytes; never strip or splitlines
when parsing values. Other statuses, malformed output, timeout or OS failure
block transport with a fixed helper-read diagnostic and `from None`; never echo
config output, argv with values, or the raw exception. Config stderr may contain
unknown credentials before a complete helper list is available.

Build a fresh copy of `os.environ`, without changing the parent environment.
With helpers, append `credential.helper` pairs to the existing valid indexed
Git config environment: if inherited count is M and there are N helpers, set
count M+N and indices M through M+N-1. Preserve the first M pairs and unrelated
environment, including global-helper configuration; an empty forwarded value
lets Git reset earlier helpers at its exact position. An absent count means 0.
Reject nondecimal counts or missing/empty keys or missing values in active
inherited pairs with a fixed, value-free error before transport. Empty inherited
values are valid. Do not print the environment. Use `os.fsdecode` only when
assigning helper values to the string env mapping; on supported macOS/Linux its
filesystem round trip preserves the original bytes. Redaction always uses the
original byte tuple, never those decoded environment strings. Set
`GIT_TERMINAL_PROMPT=0` on helper-bearing transport.

No-helper calls bypass this builder: do not validate, normalize or replace the
inherited config environment on the legacy path. In particular, do not set
`GIT_CONFIG_COUNT=0` and inadvertently erase ambient per-command config.

### Redact bytes before decoding or truncating

For helper-bearing subprocesses capture BOTH stdout and stderr as bytes
(`capture_output=True`, no `text=True`); never inherit streams or log successful
output. Catch CalledProcessError and TimeoutExpired immediately around the
transport subprocess. Display only operation/ref, numeric exit status or timeout
with its duration (no exit status exists on timeout), and a sanitized stderr tail.
Do not fall back to stdout or `str(exc)`. With no stderr, omit the tail.

Replace every occurrence of every nonempty raw helper value in stderr with
`b"[REDACTED]"`. Also redact nonempty byte lines of multiline values, using
longest patterns first so shorter patterns do not expose the remainder of a
longer match. Empty resets are never replacement patterns. Redact the complete
buffer BEFORE decoding with UTF-8 `errors="replace"`, stripping and selecting its
last 400 characters; otherwise truncation can cut a secret before matching it.
Use the same formatter for fetch and push. For OSError or invalid env on the
helper-bearing path, report a fixed operation-specific error without arbitrary
exception text. Suppress original exception chaining (`raise safe_error from
None`); do not attach raw subprocess exceptions, stdout, stderr or env to a new
error or serialize traceback locals. Application logs, events, state and JSON
must receive only the sanitized message.

The non-UTF-8 regression uses `b"!printf 'synthetic-\xff-credential'"` as a real
Git config value and as raw injected stderr in both exception types. Assert
neither the original bytes nor the UTF-8 replacement-decoded helper nor its
surrogate-escaped representation appears in rendered outputs or saved artifacts.
Also test repeated values, multiline helper lines, a helper crossing the tail
boundary, empty resets and stdout containing the sentinel. Do not decode stderr
first: that was the previous draft's disclosure bug.

### Base fetch integration

Keep `_fetch_publish_base(ctx, remote=None, *, timeout)` unchanged for callers.
Read helpers from `ctx.m.repo` after resolving the source origin and before the
network subprocess. Preserve relative-origin resolution, fetch argv, SAFE_FLAGS,
refspec, no-tags/no-submodules options, revision verification and prompt override.
Only a nonempty tuple (including a tuple containing an empty reset) selects
credential-bearing transport with the config environment and safe boundary.
No-helper fetch retains `text=True`, existing env, exact diagnostic formatting,
stdout fallback, and `__cause__` identity. Catch credential-bearing failures
inside their branch and raise outside the legacy broad formatter, or explicitly
bypass that formatter, so it cannot rewrap or chain unsafe subprocess failures.

Do not change driver `base_fetch` pause/recovery, the publish 20-second fetch
limit, its one-fetch/no-inline-retry policy, or publish's retryable JSON and
unchanged readiness on fetch failure. A credential read failure also blocks
fetch; it is never treated as evidence of an empty helper list or stale success.

### Publish and finish push integration

Extend `_push(repo: Path, url: str, ref: str, *, source: Path | None = None)`.
Publish and finish pass `source=ctx.m.repo` only after their existing eligibility
and target checks. Omitted source retains the old low-level interface for other
callers; both production origin pushes must supply it. Read helpers immediately
before pushing, after finish's existing durable archive step. A failed finish
push leaves human_checks and the archived branch retryable; it does not rerun
archive or mark the PR ready. A corrected helper is reread on the next attempt.

With helpers, use `["git", *seal.SAFE_FLAGS, "-C", str(repo), "push", "-q", url,
f"{ref}:{ref}"]`, the credential config environment, binary capture and existing
300-second timeout. Apply the shared safe exception boundary before control
returns to publish/finish/act. Neither failure may trigger PR API operations or
completion; remote acceptance after a timeout remains ambiguous.

With no source or no helpers, keep the exact old push argv and subprocess kwargs,
stream behavior and exceptions. This is a deliberate compatibility boundary:
existing push had no SAFE_FLAGS or explicit prompt override; adding those to the
legacy branch would contradict the requested no-helper behavior and existing
exact assertions. Fetch always retains its existing safety settings; the new
helper-bearing push uses both. The specloop-forge MODIFIED deltas qualify only
Git transport argv for that branch; `gh` command argv remains byte-identical.

### Regression migration and budgets

Four tasks share one section and target about 250 changed lines each (300 maximum
per session). Land shared primitives, then fetch integration, then preparatory
push test migrations, then push integration. Tests accompany their production
behavior; task 1.3 is an independently green compatibility preparation, not a
postponement of tests for already changed production code. Reuse fixtures and
parameterization rather than dropping coverage to meet the ceiling.

In task 1.2 narrowly make fetch-only subprocess doubles delegate the new config
read to real Git or a helper-reader stub, retaining every existing fetch assertion,
including `tests/test_specloop_base_sync.py` exact diagnostics and cause identity.

In task 1.3 inventory `_push` replacements/wrappers in
`tests/test_specloop_act.py`, `tests/test_specloop_command_json.py`,
`tests/test_specloop_base_sync.py`, `tests/test_specloop_base_publication.py`,
`tests/test_specloop_forge_selection.py`, `tests/test_specloop_forge_finish.py`,
`tests/test_specloop_design_publication.py`, `tests/test_finish_expect_head.py`
and `tests/test_status_json_forge_link.py`. Accept and forward optional keywords
without weakening positional/effect/order assertions. In the status-link suite,
`test_archived_link_quotes_resolved_path` installs a `push(*args)` double that
must also accept the new keyword. Preserve both `failed_push` parameterizations:
the failure still raises `RuntimeError("external push failed")`, success still
finishes, and all archived URL, head/tasks, status and available-action assertions
remain intact. Run this suite in task 1.3 and again after task 1.4 supplies
`source=ctx.m.repo`; a signature TypeError must not replace either intended path.
Keep no-helper exact argv/kwargs in
`tests/test_specloop_forge_publication.py` intact. Prepare reusable offline
credential fixtures in `tests/credential_transport_support.py`, exercised by
no-helper compatibility tests in this task. Do not add a permissive catch-all
that hides an accidental missing source argument in the new integration tests.

Put new tests in `tests/test_specloop_credential_transport.py`,
`tests/test_specloop_fetch_credentials.py`, and
`tests/test_specloop_push_credentials.py`. Required named tests are
`test_base_fetch_uses_source_local_credential_helpers`,
`test_publish_push_uses_source_local_credential_helpers`,
`test_run_repo_config_never_gains_credential_helpers`, and
`test_no_local_helpers_leaves_fetch_unchanged`; add finish equivalents.
Use real temporary source/run/bare-origin Git repositories and isolated global
config. A local `git config --get-all` child probe using the built environment
proves ordered config transport; intercepted binary subprocess failures exercise
both exception classes through driver, direct publish JSON, act publish, direct
finish and act finish. Verify stderr, output, captured logs, state and event
artifacts omit sentinels, and displayed tracebacks omit raw causes. Assert no
helper in transport argv and no helper config entries after success, failure,
timeout or retry. Snapshot source config and parent env to prove no writes.
Reuse existing fake forge services; never contact a hosted origin in tests.

Run focused tests per task. Final acceptance is `uv run pytest` and
`uv run python scripts/check_types.py` with no baseline rewrite or new type
errors. Preserve all existing assertions; only compatibility signatures and
config-read routing change in existing tests.

### Operator guidance and concurrent files

Update only `docs/specloop/README.md` under the exact column-zero heading
`### Explicit forge publication`, ending before the next physical heading with
one to three hashes and a space or EOF. Require a unique exact start; if tooling
encounters missing, duplicate or indented start candidates, block rather than
selecting a guess. Treat physical lines literally regardless of fences or HTML;
no semantic Markdown parser is needed. Preserve the heading, existing command
fences, links and adjacent guidance. Keep any automated check structural, with
no new prose-exact assertions or runnable examples.

Clarify that the unchanged argv promise concerns `gh` and legacy no-helper Git.
Explain source-local helper forwarding for base fetch, publish AND finish,
ordered empty resets, command-environment-only lifetime, no manual run-config
copying, separate API bot credentials and absolute helper executable paths when
needed. Cite issue #174, leave live authentication pending, and retain current
finish/land and daemon boundaries. This scoped overlap is necessary despite
#163 touching the same guide. Do not edit `README.md`, `KNOWN-LIMITATIONS.md`,
`specloop/seal.py` or `tests/test_specloop_seal_verify.py`; do not change manifests,
daemon, land, or unrelated steering-file/sandbox guidance.

## Risks / Trade-offs

Environment transport avoids argv disclosure but is not a security boundary
against a same-user process inspecting child environments. Git runs trusted
source-local helpers with host privileges, as it already does in that checkout.
Byte matching covers helper values and their lines, not arbitrary credentials a
helper computes or arbitrary transformations of its output. Binary capture and
redaction before decoding prevent the known invalid-UTF-8 diagnostic leak.

Helpers using relative executable paths or cwd assumptions can still fail from
repo.git; values are intentionally not rewritten. Required binaries, keychains
and credential stores must be available to the service account. Local fixtures
prove propagation, safety and recovery, not live Gitea authentication. Remote
push timeouts may have taken effect; no new automatic push retry is introduced.
The preparatory migration task reduces the push task's size without cutting
failure or retry assertions. Reassess fixture reuse if a task approaches 300
lines; do not weaken required coverage to fit an estimate.

Human checklist for the implementation PR, after merge:

- [ ] Admit a small change for an enrolled self-hosted Gitea repo whose only Git authentication is its source-local helper; observe base preparation and publication without hand-copying credentials.
- [ ] Complete the PR's human checks, run finish, and confirm its archive push and WIP-prefix removal succeed without helper copying; finish must not merge.
- [ ] Before and after these operations, inspect `repo.git/config` with a key-only query and confirm no `credential.helper` entries; confirm the source config is unchanged without publishing secret values.
- [ ] Review the scoped guide passage for readable operator instructions and confirm the host helper's executable paths and credential-store access suit the actual service account.

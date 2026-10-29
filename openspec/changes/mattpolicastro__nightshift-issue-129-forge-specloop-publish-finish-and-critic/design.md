## Context

This is issue #129, blocked by #128 and part of #122 wave 5. The checkout
already contains #128's `ForgeConfig`, `parse_forges`, trusted config source
convention and unconditional Gitea daemon holds. No `AGENTS.md` is present in
this checkout or its ancestor directories.

`specloop/cli.py` currently calls `_gh` directly for existing PR lookup,
creation and readiness. `_push` sends the run ref to the source checkout's
origin using git and its existing credential helpers. `_repository` drops
scheme and port, so it cannot establish a Gitea installation identity.
`nightshift/preflight.py:_clone_identity` already checks HTTP(S) scheme,
authority and repository; this change follows that boundary, comparing
effective ports so an explicit default port is equivalent to its omission.

`specloop/critic.py:open_pr_files` currently owns GitHub pagination, a
3000-file ceiling and clamped subprocess timeouts. `GiteaForge` supports
construction with `guard` and `timeout`, draft creation and confined requests.
Its `_pages` requires numeric record IDs and fails an entire read on bad later
pages. Gitea file rows need filename identity and the critic needs partial
evidence, so that helper is unsuitable for critic context.

Existing contracts to preserve include PR adoption, exact branch matching,
publish's single 20-second base fetch and driver handoff, partial-archive
recovery, expected-head checks, critic snapshot reuse and once-prefixed risks.
The `spec-critic/Concurrent file context` delta changes only forge selection,
backend completeness and Gitea timing promises. The documentation deltas
qualify existing blanket publication limitations; no daemon hold is removed.

## Goals / Non-Goals

Enable explicit engine publish, finish and proposal critic reads on a configured
Gitea host, with offline regression evidence. Keep no-host and `github.com`
behavior and `gh` argument bytes unchanged. Refuse protected workflow changes
on both forges before publication effects.

Implementation may edit `specloop/cli.py`, `specloop/critic.py`, focused tests,
spec deltas, and affected passages in `README.md`, `KNOWN-LIMITATIONS.md` and
`docs/specloop/README.md`. Helpers stay in those Python modules. Do not edit
`nightshift/land.py`, daemon implementation, `nightshift/forge/`, service
wrappers or type baselines. Do not add a Forge Protocol method, hard HTTP read
deadlines, native drafts, title conflict detection or a publication journal.

## Decisions

### 1. Resolve the engine target once from the manifest

Add a small typed `_forge_target(github, *, guard=None)` helper in
`specloop/cli.py`, returning repository identity and either the legacy GitHub
selection or the configured GiteaForge plus its ForgeConfig. Keep `_remote`
returning a string and `_existing_pr` returning a URL string; optional internal
target arguments may avoid repeated selection. Critic imports the selector
locally when collecting context, avoiding import-time cycles through propose.

Parse the existing `[HOST/]OWNER/REPO` form, allowing its existing trailing
slash. The host component is a hostname, not a URL, port selector or forge
name. Missing host or case-insensitive `github.com` takes the legacy path
before reading Gitea configuration. Preserve original manifest bytes in all
GitHub argv. Another host must match exactly one Gitea definition by
case-insensitive `urlsplit(definition.url).hostname`. Two definitions on the
same host are ambiguous even if their ports/prefixes differ; refuse instead
of guessing. `repos.forge` does not override the engine's manifest selection.

Use `NIGHTSHIFT_FORGE_CONFIG` from the actual process environment when set,
requiring a nonblank absolute path; otherwise use `nightshift.config.DEFAULT_PATH`.
Read TOML and call `config.parse_forges`, not the daemon's full enrollment
loader. An absent default means no definitions; a selected missing file or
invalid/unreadable source refuses. This follows `specloop/seal.py`'s source
convention without modifying it.

Use only the selected `token_env`: read `config.parse_env_file()` and let its
value override `os.environ` as existing operator tooling does. An empty
override is a missing token, not permission to fall back. Do not export that
mapping, source a shell, use manifest step/env settings or read a landing token
for authentication. Pass URL, bot token, trusted_logins, bot_login and wip_prefix
to GiteaForge; construction is network-free. Git retains separate credential
helpers and unchanged push argv. Configuration errors are sanitized; critic
turns them into context notes, while commands refuse.

### 2. Validate the complete Gitea destination before effects

For Gitea, `_remote` reads all origin values and accepts exactly one. Parse an
HTTP(S) URL with no credentials, query, fragment, whitespace/control characters,
backslashes, malformed percent escapes or traversal. Compare normalized scheme,
case-insensitive hostname and effective port (443 for HTTPS, 80 for HTTP).
Require the exact encoded configured prefix plus `/owner/repo`, permitting
only an optional terminal `.git`. Preserve repository path case and do not
accept arbitrary redirects or extra path components. Decode only for rejecting
unsafe path content, not to broaden identity equivalence.

Keep GitHub's existing origin behavior, including existing SSH and local fixture
handling. Gitea local/scp/SSH origins refuse: no configured SSH installation
mapping exists, and an SSH port cannot be equated with an HTTPS port. This is
consistent with the existing Gitea preflight requirement. Do not infer an
SSH host/port mapping from hostname equality.

Run target/origin validation before `_publish` can push or call a PR operation.
In `_finish`, retain locked head/task preconditions, then validate before
archiving (which itself pushes to the run repository), external push or PATCH.
Validate a recorded Gitea PR URL through `_pr_url_number` against the configured
origin/prefix/repository before effects; never request a supplied absolute URL.
Port- and scheme-mismatch tests must spy on all pushes and API writes and see
zero effects for both commands.

### 3. Preserve publication ordering and add workflow protection

Keep `_fetch_publish_base(..., timeout=20)` and `_publish_needs_prepare` exactly
in their existing roles. If preparation is needed, return the existing retryable
handoff before publication. Otherwise inspect the captured fresh `base..head`
with `git diff --no-renames --name-only -z <base>..<head> --`. Disabling rename
detection exposes both deleted and added names. Compare NUL-delimited path
bytes to the three literal directory prefixes; do not split on newlines or
use shell/glob expansion. Failed/malformed inspection refuses. Render unsafe
filename characters escaped in diagnostics. This guard runs even when `st.pr`
already exists and before `_push` or any PR operation on either forge.

`_push` remains byte-for-byte the existing git command, including refspec,
quiet flag, timeout and credential-helper behavior. GitHub `_existing_pr`,
`pr create --draft`, `pr ready` and fallback `pr view` argv remain unchanged.
In particular explicit `github.com` retains `--hostname github.com` where
currently supplied, and an omitted host does not acquire it.

Gitea `_existing_pr` reads complete `state=open` pages using the existing
`GiteaForge._pages` and `_repo_path`. Validate head/base refs and head repository
identity; compare exact refs and same-repository identity, exclude forks and
other bases, and refuse ambiguous exact matches. Do not use
`latest_pr_number`, whose all-state/head-only semantics are different. A saved
valid PR is reused without a new listing or title/body edit. Create only after
a successful absent lookup, via `create_draft_pr`, using the existing title
and `_pr_body`; the adapter prepends the configured prefix at most once.
Validate returned PR URLs before saving them. Preserve published events,
JSON results, run-state persistence and `human_checks`.

The adapter sends POST once. On failure, retain the normal retryable command
failure; the next invocation performs lookup before creating. This handles
ordinary lost responses and interrupted state saves. Concurrent callers or a
POST that becomes visible only after the next absent lookup can still duplicate
PRs. Do not claim exactly-once creation; guidance tells operators to inspect
remote effects before retrying an ambiguous failure.

### 4. Finish verifies the resulting Gitea title

Preserve archive detection and reuse, expected-head and task checks, and the
existing git pushes. After the external push, GET the PR via the confined
`_repo_path`/`_detail` boundary. Remove one literal, case-sensitive leading
`wip_prefix`; preserve all other title bytes. If there is no prefix, the fresh
read establishes readiness without PATCH. Refuse an empty resulting title or
one still starting with the prefix rather than reporting a still-WIP PR done.

PATCH only `{"title": intended_title}` via `_request`, then GET and require
exact equality with the intended unprefixed title. After an ambiguous PATCH
failure, attempt the same read-back and accept only that exact result; do not
replay the write within the invocation. A failed/mismatching read leaves the
run at `human_checks`. A retry reuses the existing archive and reads the current
title, so a previously accepted prefix removal needs no further PATCH.

There is deliberately no ETag/version check. A human title edit between GET
and PATCH can be overwritten, and successful read-back cannot detect the loss.
Document this explicitly in `KNOWN-LIMITATIONS.md`. Finish is not merge.

### 5. Give critic collection a backend-specific completeness policy

Retain `open_pr_files(github, branch, *, runner=None)` and its tuple of sorted
file attribution plus notes; tests may inject additional private seams. Keep
the existing GitHub collector as a backend helper, preserving argv, short-page
termination, malformed-row behavior, 3000-file ceiling and subprocess timeout
assertions. The dispatcher selects a backend from the manifest, not from the
presence of an injected runner. No-host unconfigured context makes no request.

Add a private Gitea context reader in `specloop/critic.py` taking the selected
GiteaForge, repository and effective branch. Use its `_request` and `_repo_path`
for GET `/pulls?state=open` and `/pulls/{number}/files`. Backend-specific
pagination stays here; no adapter or Protocol changes are needed. Do not call
GiteaForge `_pages` for this collector, and do not interpret shared normalized
output length as proof of completion.

Request page numbers from one with limit 50, independently for each endpoint,
through an empty page, at most 1000 pages per endpoint. Short pages are not
terminal. Use positive PR numbers for PR identity and `filename` for file
identity; `previous_filename`, when supplied, also contributes a path. Compare
relevant normalized record fields for conflicting duplicates, deduplicate
identical overlap and refuse repeated nonempty page signatures. Missing numeric
file IDs are valid. Validate optional X-Total-Count consistency, X-Next-Page and
Link-next evidence like the adapter; a contradictory empty page or declared
end is incomplete. Never follow response URLs or impose GitHub's file ceiling.

Keep valid records from earlier pages and valid paths in a partly malformed
page. A failed file listing stops that PR's pagination and allows later PRs
while time remains; failed PR-list pagination retains earlier evidence. Include
all bases, WIP PRs, production/root files and renamed paths. Exclude the proposal
branch only with positive same-repository evidence; null/unknown head repos
do not exclude it. Output only filenames, PR numbers/URLs and sanitized notes,
never bodies, patches, credentials or raw server errors. Existing critic
rendering, snapshot reuse and once-prefixed risk delivery need no new schema.

### 6. Bound request starts, not response elapsed time

At Gitea collection start, set `deadline = time.monotonic() + 120`. Construct
GiteaForge with `timeout=30` and a guard raising a private budget exception when
`time.monotonic() >= deadline`. The transport already calls this guard before
every attempt, including GET retries after backoff. Catch it only at the
collector boundary and produce one budget note. Check the same boundary before
starting another page or PR operation; do not add an outer retry loop.

Every started request uses a 30-second socket timeout, even if less than 30
seconds remain before the scheduling cutoff. Unlike the GitHub subprocess
timeout, urllib's socket timeout is not an elapsed-time limit. A response that
keeps delivering bytes can take longer than 30 seconds and can pass the overall
120-second cutoff. Consume and retain valid returned file evidence, record
budget exhaustion after return, and dispatch nothing further. A returned PR
list after the cutoff cannot justify new file requests. No clamping opener,
read watchdog, thread, stream cancellation or `nightshift/forge/` edit is planned.

Use fake monotonic time and an in-process opener to prove attempts at the
boundary stop, retries are guarded, and all open calls receive timeout=30.
A fake response's `read()` advances the clock beyond the cutoff before returning
valid file JSON; assert retained evidence and zero later dispatch. This tests
the documented overrun behavior without a sleeping/network test or a false
claim that slow live responses are forcibly interrupted.

### 7. Documentation, regression preservation and verification

Update the three allowed operator documents wherever this change invalidates
claims, especially the engine-PR-command limitation and blanket Gitea
publication holds. Say **daemon** when describing those retained holds.
Explain manifest-host selection, unique host matching, trusted config/env
sources, HTTP(S) full-origin checks, separate git credentials, all workflow
prefixes, WIP finish, title races, slow responses and ambiguous-create retries.
Cite issue #129 until an implementation PR is known. Do not rewrite unrelated
sections or claim changes to arming, landing or plugin-specific GitHub paths.
Preserve README's GitHub credential restriction while explaining the engine's
new pre-push refusal; do not imply the Gitea guard is a server permission.

Put the new engine command examples in a `### Explicit forge publication`
subsection of `docs/specloop/README.md`. Scope new example checks to that exact
physical heading and stop at the next column-zero ATX heading of equal or lower
hash count. Missing/duplicate headings fail; existing surrounding examples keep
their existing checks. Within the selected passage, recognize
standalone command lines after stripping surrounding whitespace and inline
single-backtick spans beginning with `nightshift`, `uv run nightshift` or
`specloop`, independent of fences, HTML and indentation. Unmatched backticks,
unrecognized placeholders, or commands requiring shell semantics refuse
validation rather than being skipped. Replace only documented `<change>` with
a safe fixture name and parse using the real argument parser without dispatch.
Check simple relative Markdown links against disk; ambiguous link syntax in
the selected passage blocks validation. Avoid new multiline executable examples
or raw `[[repos]]` guide blocks, preserving existing whole-guide extraction.

Preserve every existing assertion. The intentional host-policy migration is
the explicit-host fixture in `tests/test_specloop_critic_context.py`: replace
the `github.example.test` input/expected-host fixture value with `github.com`,
keeping argv, attribution, pagination and other assertions. Add separate
non-GitHub configured/unknown-host tests. Minimal context/runner fixtures in
publication tests may supply the new target/diff facts or stub the new local
guard in tests solely about transport failures; do not remove existing checks
or make production validation optional. New real git fixtures exercise the
guard independently, including rename/deletion and unusual filenames.

Required tests are `test_publish_on_gitea_opens_wip_pr`,
`test_finish_on_gitea_drops_wip_prefix`, `test_publish_refuses_workflow_paths`,
`test_github_publish_argv_unchanged` and `test_critic_reads_gitea_pr_files`.
Use existing FakeGitea/openers and state helpers where practical, with no live
service dependency. Each task runs its focused regressions. Final acceptance
is `uv run pytest` and `uv run python scripts/check_types.py`, with no new
type errors, skips hiding failures, assertion removals or baseline increases.

Six tasks are used to keep each session near or below 300 changed lines:
selection/origin, publication, finish, critic pagination, critic dispatch/timing,
and documentation. Pagination and timing have distinct failure modes and
test seams; combining them would recreate the oversized critic task.

## Risks / Trade-offs

- Private GiteaForge helpers are a deliberate narrow dependency because the
  shared adapter and Protocol are out of scope. Fake-opener tests must exercise
  the real request boundary to detect changes in those helper contracts.
- Full HTTP(S) matching intentionally refuses Gitea SSH origins and ambiguous
  same-host definitions. It cannot establish bot scope; preflight and separate
  credential provisioning retain their limitations.
- PR listing is not a server snapshot. Partial context stays advisory, and
  lookup-before-create cannot prevent concurrent or late-visible duplicates.
- Finish can overwrite a concurrent human title edit. Read-back establishes
  the resulting title only; no conflict detection is promised.
- A slowly arriving response can exceed both time numbers. The 120-second
  rule prevents new dispatch, not an already-started body read.
- Existing documentation shares passages with #135 / PR #152. Preserve their
  per-step env examples, markers and parser tests when resolving overlap.
  This is merge-conflict risk, not an incompatible feature or blocker.

### Human checklist for the implementation PR

- On the first real Gitea publication, use a disposable ready run with the
  configured origin and bot environment. Run publish, inspect the pushed
  branch and WIP-prefixed PR, perform the run's human checks, then run finish
  and verify archive publication and prefix removal on the same PR. Record
  redacted results. CI cannot prove this real publish; leave it pending until
  performed. Do not enable daemon stages as part of this check.
- Read the changed operator passages and verify issue/reference links on the
  live host. Confirm the title-race and slow-response wording is clear and
  existing daemon restrictions remain explicit. These human checks are not
  implementing-agent tasks.

## Context

Dependencies #128 and #129 are present in this checkout. `nightshift/arm.py`
currently owns subprocess reads and writes, invocation-local `IssueCache`, and
the join from critic PR-file labels to source issues. Its public helpers accept
subprocess-compatible runners. Existing tests assert full argv, kwargs, ordering,
cached failures, enterprise GitHub routing and absence of local state writes.

The shared `Forge` interface cannot replace those calls verbatim: its GitHub
issue projection, queue listing and label operations use different argv, while
`GiteaForge.issue_for_draft` also collects comments that arming does not need.
The Gitea backend already provides strict pagination, label resolution and
read-back; `critic._gitea_open_pr_files` supplies advisory partial file evidence.
Its file records are identified by filename, not numeric API IDs.

No AGENTS.md is present in this checkout. The existing issue-arming specification
and tests remain authoritative for lint and mode behavior. This draft changes
only the explicitly identified forge and documentation boundaries.

## Goals / Non-Goals

Enable explicit Gitea arming without `gh`, retain byte-identical legacy GitHub
calls, and exercise real adapter/helper behavior offline. Keep label writes
confined to adding `spec:draft`, preserve all independent findings, and make
documentation distinguish a verified label from daemon execution.

Do not change parsing of issue sections or dependencies, authorization,
reachability, run discovery, transport retry policy, daemon holds, landing,
publication, shared forge interfaces, or state formats. Do not turn arming
into an automatic drafting command or an origin-discovery system.

Implementation may edit only `nightshift/arm.py`, new `tests/test_arm_forge.py`,
`tests/test_arm_gitea_context.py`, `tests/test_arm_forge_docs.py`,
`docs/arming.md`, the arming-related limitations passage, and the engine guide's
explicit-publication arming boundary. These spec deltas are read-only inputs;
no task edits `openspec/**`.

Do not edit `README.md`, `specloop/manifest.py`, `specloop/verify.py`,
`tests/test_loopback_docs.py`, `tests/test_loopback_evidence.py`,
`tests/test_loopback_launch.py`, `tests/test_loopback_profile.py`, or
`tests/test_loopback_sandbox.py`.
Preserve the concurrent Flutter/loopback work in both shared documentation
files. Do not edit existing arm/forge/critic tests or their shared fake; extend
coverage in the named new files using existing fixtures or small local helpers.

## Decisions

### Selection and credentials

Add optional `--config PATH` to the existing module parser; do not add a public
`nightshift arm` command. Expand `~` in PATH and resolve relative paths against
the invocation directory. Without the flag, perform exactly the current
`git rev-parse --show-toplevel` and `gh repo view --json nameWithOwner,url`
sequence, with no config, origin or credential-file lookup. Retain enterprise
GitHub host spelling and ambient `gh` authentication.

With the flag, resolve the root with the same Git command and load the selected
file through `nightshift.config.load`. Match the canonical root against
`Repo.clone_dir().resolve()` for each enrollment, using the existing clone-dir
semantics (including `~`, defaults and invocation-relative configured dirs).
Require exactly one match; document absolute `repos.dir` as the unambiguous
setup. A different worktree requires its own matching configuration. Do not
infer a repository from a URL, basename or forge hostname. An explicit missing,
invalid, unmatched or ambiguous config exits 1 before issue/API reads or writes;
`--force` cannot override selection failure.

Use `Config.forge_for` for that row. For GitHub, retain `gh repo view` and all
subsequent existing argv; require its owner/repository to match the enrollment
case-insensitively. Named GitHub still uses ambient host/authentication. For
Gitea, use the enrolled `owner/repo` and construct `GiteaForge` with configured
URL, bot login, trusted logins and WIP prefix. Read only its `token_env` from
`config.parse_env_file()` over process environment, with file presence winning
even when empty. Do not export file values. Missing/empty credentials refuse
with sanitized diagnostics; never use landing tokens, manifest env or `gh` as
fallbacks. Construction makes no request. No Gitea command invokes `gh`.

### Local operation interface and compatibility

Define a small `ArmingForge` Protocol and private GitHub/Gitea implementations in
`nightshift/arm.py`, with named operations for `read_issue`, `armed_issues`,
`open_pr_context`, `add_intake_label` and `confirm_intake_label`. The records
remain `Issue`, `ConcurrentContext` and existing result/exception shapes.
Keep lint, graph, mode and reporting policy outside these implementations.
This is an arming facet, not a generic command escape hatch or a replacement
for the shared front-door `Forge` Protocol.

Move the existing GitHub bodies behind that interface without rewriting their
argv. Keep public `read_issue`, `read_pr_context`, `confirm_intake_label` and
`IssueCache(runner=...)` compatible, permitting an optional injected arming
forge. Bind the explicit runner when provided; otherwise resolve
`subprocess.run` inside the invocation. Do not construct global adapters or
capture a runner in a default argument. Continue calling
`critic._github_open_pr_files(repository, "", runner=runner)` for GitHub;
the manifest-host selector would misroute enterprise GitHub hosts.

Keep the exact `number,body,state,stateReason,labels` projection, issue/list/PR
order, query strings, field order, host flags, subprocess kwargs and timeouts.
Successful GitHub writes still have no extra label GET; errors still have one
fresh confirmation. Do not use queue helpers that add retries or change argv.
Leave the existing AST allowlist for `arm.py` intact; add no allowance or
command-construction disguise elsewhere.

Keep issue successes and failures, armed snapshots, PR context and directed
reachability caches invocation-local. Bind one selected forge to each cache;
retain repository/number keys and the original first-read-wins behavior.
Confirmation always bypasses this snapshot. New invocations start fresh.

### Gitea facts and concurrent context

Read individual issue facts through `GiteaForge._detail` and `_repo_path`,
validate the requested number, reject PR rows and malformed state/labels, and
normalize a null body to an empty string. Preserve explicit `state_reason`
when present; missing reasons remain unknown. Never manufacture COMPLETED
from `state=closed`: existing dependency lint reports incomplete context.
No title/comment lookup is needed for lint.

Use `GiteaForge._label_candidates(repo, "spec:draft", "issues")` for armed
issues. It refreshes repository label IDs and reads all pages without `label`
or `labels` query parameters. Validate and normalize candidate facts locally,
exclude PR/closed rows, and cache them under the existing rules. Missing or
duplicate label names, malformed rows, repeated/conflicting pages and later
failures produce incomplete-context notes, never a clean empty result. The
strict adapter returns no partial candidate list on failure; preserve already
available targets and independent run/PR evidence instead of inventing a prefix.

For PR files, call the real `critic._gitea_open_pr_files(forge, repo, "", ...)`
once. Preserve its returned paths, links and distinct notes. For each distinct
PR number in its `#N URL` attribution, read metadata through the same forge's
confined `pulls/N` detail endpoint once, validating number and head repository/ref.
Do not follow the displayed URL. Join only same-repository `spec/` heads using
`front_door_issue(repo, change)`, including owner-qualified and legacy names;
exclude fork/ordinary/self evidence exactly as before. Missing or malformed
metadata adds incomplete-context findings; retain all independently attributable
evidence. Do not treat missing metadata as proof that the PR is unrelated.

Reuse the critic's filename identity and rename handling directly. Do not feed
file rows to `_pages` with synthetic numeric IDs. Test identical duplicates,
conflicting old/new rename records, short intermediate pages, repeated pages,
contradictory termination and later failures through the real helper and
`FakeGitea` opener. No helper rewrite or weakening of completion checks is needed.

For PR context only, install an invocation-local 120-second request-start
deadline checked by the Gitea adapter guard and helper; clear it in `finally`
before issue or label operations. Keep the default 30-second socket timeout,
1000-page bound and transport GET retries. Exhaustion retains partial advisory
context with a limitation note. These are not elapsed-response guarantees.
Tests inject time and an opener; they use no sockets, daemon or host application.

### Writes and outcomes

After the entire batch has been checked, call the selected forge's label
operation. Gitea delegates to `relabel_issue(repo, number, add=["spec:draft"],
remove=[])`, preserving unrelated labels and the adapter's current-ID resolution
and complete label read after a successful POST. The adapter may skip a write
already satisfied by its fresh read. Do not create repository labels.

On a reported Gitea operation error, retain arming's uncertain-write behavior:
attempt one fresh logical `issue_labels` read, bypassing `IssueCache`; a logical
read may paginate and use the transport's existing GET retry policy. Only
confirmed presence can report `armed (label confirmed after write failure)`.
Absent, malformed or unavailable final evidence reports unconfirmed and exit 1.
This also handles an error during the adapter's initial verification; it adds
no mutation retry. Silent writes with absent labels never become success.
An injected guard exception must propagate unchanged, with no recovery request.

Preserve `--check` precedence, force with visible findings, closed/unreadable
target refusal, already-armed no-op, deduplicated argument order and aggregate
exits. A failed target does not prevent independent later targets; it does not
authorize another write for the failed target. No receipts or local state writes.

### Documentation and validation

Update `docs/arming.md` prerequisites, invocation/discovery, dependency reasons,
write confirmation and coverage limits. Add a separate `## Configured Gitea`
section so the existing Invocation examples and their assertions remain valid.
Show `uv run python -m nightshift.arm --config /absolute/path/to/config.toml --check 76`
and the corresponding non-check invocation. Explain the matching enrollment,
bot-token precedence, existing-label prerequisite, client-side filtering,
verified writes, missing completion reasons and unconditional daemon hold.
Link existing forge configuration guidance instead of duplicating config schemas.

In `KNOWN-LIMITATIONS.md`, remove only arming from the GitHub-only command list
and explain explicit configured arming, bot credentials/read-back and the
continuing daemon hold with a link to the arming guide. In
`docs/specloop/README.md`, replace only the obsolete arming clause and add that
link, leaving Land GitHub-only and publication/daemon text intact. This shared
file overlap is necessary to satisfy #129's modified requirement. Do not add
arming command examples inside its explicit-publication passage: its existing
tests require that passage's executable commands to remain publish/finish only.
The root README stays unchanged. Cite issue #131, not an invented PR number.

New documentation tests use physical lines: a selected heading must appear
exactly once at column zero; its passage ends at the next column-zero ATX
heading of equal or lower depth. Treat these lines literally even inside fences
or HTML. In the new Gitea section require one exact ` ```sh ` opener and one
exact ` ``` ` closer (without the spaces shown here); missing, duplicated,
indented or reversed delimiters and interior fence tokens fail closed. Parse
each nonblank enclosed command with `shlex.split`, allow only the documented
`uv run python -m nightshift.arm` prefix, and exercise the real parser/main with
fake discovery and forge. Refuse shell operators, continuations or unexplained
placeholders. Validate relative links with existing structural test helpers;
do not assert exact explanatory prose or introduce semantic Markdown parsing.
No new test parses issue Markdown: existing parser functions and tests remain.

Required new tests: `test_arm_gitea_lists_armed_issues_client_side`,
`test_arm_gitea_label_write_read_back`, `test_arm_github_argv_unchanged`.
Keep tests with each behavioral task. Preserve every existing assertion; run
focused arm/forge/critic/docs regressions, then `uv run pytest` and
`uv run python scripts/check_types.py`. Do not update the type baseline to
accept new errors. Three tasks target roughly 250–300 changed lines each by
reusing the adapter, helper and fake; avoid rebuilding their internals.

### Verification and human rollout checklist

- [ ] On a real configured Gitea checkout, run check mode, then arm one suitable issue. Inspect the remote spec:draft label and unchanged unrelated labels; record sanitized outcomes and verify the docs match the actual setup.
- [ ] Watch that issue draft when the separately tracked daemon authorization and durable-transition prerequisites have landed. Until then, confirm the daemon retains its hold and mark the end-to-end drafting observation pending. Do not bypass the hold or claim label verification proves drafting.
- [ ] Review the narrow documentation edits and linked references for readability and correctness. CI proves fixtures, not the real Gitea or host experience.

These checks are human acceptance evidence, never implementing-agent tasks.

## Risks / Trade-offs

- A local arming interface duplicates some shape adaptation but avoids changing
  shared GitHub contracts or exceeding the permitted runtime scope. Private
  Gitea/critic helpers remain coupling points; integration tests must invoke
  their real code, including filename conflicts and partial-context behavior.
- Explicit config and checkout-directory matching are intentionally opt-in.
  Operators using another worktree must configure that path; no hidden origin
  or default-config discovery changes the legacy GitHub transcript.
- Gitea closed issues may lack completion reasons. Preserve incomplete context
  and documented force behavior instead of falsely declaring dependencies complete.
- Checks are snapshots, not reservations. Concurrent label changes, PR metadata
  races and slow socket responses remain possible. Read-back proves label
  presence, not actor authorization or eventual daemon execution.
- The requested live drafting observation remains blocked by the continuing
  daemon hold. This change enables only explicit arming and must not silently
  expand into the authorization/transition work that keeps that hold necessary.
- The limitations and engine guide are shared with #151. Restrict edits to
  arming claims and preserve loopback text and runnable examples verbatim.

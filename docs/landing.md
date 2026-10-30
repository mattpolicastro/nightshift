# Landing a reviewed PR

This guide is the current authority for human-initiated terminal landing on
GitHub and configured Gitea ([issue #130](https://github.com/mattpolicastro/nightshift/issues/130)).
It supersedes the historical GitHub-only terminal landing wording and landing
future-work notes in [README.md](../README.md#layout) and
[docs/specloop/README.md](specloop/README.md#explicit-forge-publication).
Their explicit publication/finish guidance still applies.

Run `land` explicitly as an operator or an explicitly delegated session, after
reviewing the current PR head and performing its human checks:

```sh
uv run python -m nightshift.land https://github.com/owner/repo/pull/123
uv run python -m nightshift.land 123 --repo owner/repo --checks-done
uv run python -m nightshift.land 123 --repo owner/repo --config ./config.toml --checks-done --timeout 900
uv run python -m nightshift.land 123
uv run python -m nightshift.land --help
```

The number-only example resolves the repository with `gh repo view` in the
current working directory. A GitHub.com HTTPS pull URL supplies the repository;
if you also pass `--repo`, it must agree with that URL.

## Prerequisites and flags

Use a working Nightshift checkout with `uv` on PATH and, for GitHub, `gh`. The
interpreter running `nightshift.land` must be able to import `nightshift.cli`;
the `nightshift` console script need not be on PATH, and virtual-environment
activation is not required. The selected configuration must contain exactly one
case-insensitive enrollment for the repository. Omit `engine` or set
`engine = "specloop"`, and configure a trusted manifest. Explicit
`engine = "worker"` fails configuration loading because the worker engine is
retired; migrate to specloop with a trusted manifest before landing.
For GitHub, authenticate `gh` as the operator
with permission to read and edit the PR and issue comments, update the branch,
and merge under the repository's protection rules. Run in the environment where
that interpreter's `-m nightshift.cli status --json` can read the applicable runs.
Only open PRs targeting an existing `main` are eligible.
Gitea uses the configured origin and human credential described below, with the
same read/edit, comment, branch-update and protected-merge permissions.

| Argument | Meaning |
| --- | --- |
| `<pr-url-or-number>` | GitHub.com HTTPS pull URL or positive decimal PR number; Gitea requires a full configured-origin `/owner/repo/pulls/number` URL. |
| `--repo owner/name` | Explicit repository; otherwise inferred from the URL or `gh repo view`. |
| `--config PATH` | Enrollment file; defaults to `nightshift.config.DEFAULT_PATH`, the `config.toml` at the package root. |
| `--checks-done` | Assert that you performed the listed human checks; tick and attest them. Without it, unchecked items refuse landing. |
| `--timeout SECONDS` | Positive finite CI/update budget, including fractional seconds; default 1800 seconds (30 minutes). |
| `--help` | Show usage without performing external operations. |

The daemon, intake helpers, and `nightshift serve` supervisor never invoke this
command. Factory also offers a separate GitHub-only explicitly confirmed
[Land control](../plugins/nightshift/README.md#confirmed-landing), using this same
flow. Its private worker holds a per-PR flock, but independent terminal invocations
do not take that lock. Run only one landing invocation per PR at a time;
there is no server-enforced uniqueness for concurrent operators. Invoking the command is
your decision to land the current reviewed head. It does not perform human
checks or grant review approval.

## Gitea setup and credentials

Configure a Gitea definition and enroll the repository on that same forge. This
illustrative `config.toml` fragment uses a path-prefixed installation:

```toml
[[forges]]
name = "review"
kind = "gitea"
url = "https://forge.example/gitea"
token_env = "GITEA_BOT_TOKEN"
land_token_env = "GITEA_LAND_TOKEN"
bot_login = "nightshift-bot"
trusted_logins = ["operator", "nightshift-bot"]
wip_prefix = "WIP: "

[[repos]]
name = "owner/repo"
forge = "review"
manifest = "~/.config/specloop/manifests/repo.toml"
```

The URL hostname must match exactly one configured Gitea definition,
case-insensitively; different ports do not disambiguate duplicate hostnames.
The scheme, effective port, installation prefix and repository must then agree.
Use HTTP(S) origins only; HTTP is allowed only for a configured loopback origin.
Userinfo, query/fragment, traversal and contradictory `--repo` are refused.
Enrollment is case-insensitive, but API paths retain its configured spelling.
A Gitea enrollment never authorizes landing the same owner/repository on GitHub.

Place the human credential under the configured `land_token_env` name in
`~/.config/nightshift/land.env`. Replace this illustrative value with the human
operator's token, keeping the file private:

```sh
export GITEA_LAND_TOKEN='replace-with-human-token'
```

`land` itself parses this file; do not source it into the shell. Its physical
`export NAME=VALUE` format strips matching outer quotes without expansion or
command execution. File assignments override the invocation's environment,
including an empty assignment, which refuses rather than falling back. A missing
file or missing assignment permits an already supplied selected variable;
an unreadable file or absent/empty selected token refuses before HTTP.
There is no bot-token fallback: landing does not load `~/.config/nightshift/env`,
consult `token_env` or use `gh` authentication for Gitea. File values remain local
to landing; it does not mutate the process environment or pass newly loaded
secrets to status/finish arguments or child environments. Avoid exporting the
human token globally if it should be isolated from those children.

After reviewing the PR and performing its listed checks, use its full URL:

```sh
uv run python -m nightshift.land https://forge.example/gitea/owner/repo/pulls/123 --checks-done
uv run python -m nightshift.land https://forge.example/gitea/owner/repo/pulls/123 --repo owner/repo --config ./config.toml --checks-done --timeout 900
```

### Separate finish provisioning

A front-door run may still need the explicit finish operation from
[issue #129](https://github.com/mattpolicastro/nightshift/issues/129). Provision
its bot token and git push credentials separately, following
[explicit forge publication](specloop/README.md#explicit-forge-publication).
The manifest's `[run] github = "forge.example/owner/repo"` selects that forge.
Set `NIGHTSHIFT_FORGE_CONFIG` in the actual process environment to the trusted
configuration's absolute path; when landing uses a custom `--config`, align this
variable with that file. Landing does not forward `--config` to finish or
substitute the human token for finish's credentials.

Finish's selected `token_env` comes from `~/.config/nightshift/env` over the
process environment, with file values winning even when empty. Neither
`land.env`, `land_token_env` nor manifest per-step env is a fallback. Git push
authentication is separate from API tokens. Origin validation still checks
scheme, host, effective port, prefix and repository. Implicit and explicit
`github.com` manifests remain supported. Publication still refuses changes in
`.gitea/workflows/`, `.github/workflows/` and `.forgejo/` on either forge.
Gitea drafts are titles with the configured case-sensitive WIP prefix; finish
removes it via title PATCH and read-back. That operation is neither native draft
state nor merging, and its concurrent title-edit risk remains.

## Support boundaries

Human-initiated terminal Gitea landing does not enable Gitea Factory Land: the
Factory UI's GitHub-only eligibility and confirmation remain unchanged. Arming
and unrelated plugin GitHub paths retain their existing boundaries, and legacy
worker recovery remains GitHub-only. Explicit engine publish/finish and critic
context use the manifest-selected forge.

Forge configuration, adapter construction and read-only preflight do not enable
daemon execution. Gitea drafting, admission, revisions, publication and pending
delivery all remain disabled; daemon status skips Gitea queue queries. Named
GitHub definitions still use ambient `gh` authentication and host selection.
The [module layout](../README.md#layout) separates factual backend operations
(`forge/gitea.py` over `forge/http.py`) from queue transport and core policy;
landing-specific operations and gates live in `nightshift/land.py`. This scoped
milestone leaves the shared guides' base preparation/recovery, frozen wrappers,
enrollment examples and [per-step env guidance](specloop/README.md) from
[issue #135](https://github.com/mattpolicastro/nightshift/issues/135) unchanged.

## Check sources and attestation

The PR body is always checked. A front-door PR is identified by a matching
canonical PR URL in version-1 `python -m nightshift.cli status --json`, for omitted
or explicit specloop enrollment, independently of labels or branch names.
Gitea identity includes the configured origin and prefix. A well-formed URL on
another host is unrelated; malformed target-host identity blocks discovery.
For that PR, `land` also reads every issue-comment
page and selects the newest trusted publication for the matching change, ordered
by creation time and then numeric comment ID. On GitHub, trusted authors have
OWNER, MEMBER, or COLLABORATOR association. On Gitea, publication and attestation
trust uses case-insensitive membership in the selected `trusted_logins` only;
an unlisted owner or administrator is untrusted. List the intended publication
author, including the bot when it publishes, and the human attester. With listed
checks and `--checks-done`, the authenticated Gitea actor must be a trusted
non-bot user before any edit; empty trust authorizes nobody.
Publications start with `**nightshift** —`
and carry `<!-- nightshift:publish:<change>:<round> -->`; legacy
`<!-- nightshift:publish:<change> -->` markers also qualify. Older publications
and unrelated comments are left alone. A direct PR uses only the body.

Every line matching `^[ \t>]*[-*+] \[( |x|X)\] ` counts as a task-list check,
wherever it appears. Optional indentation/blockquotes precede `-`, `*`, or `+`,
a space, `[ ]`, `[x]`, or `[X]`, and a space. This includes fenced code examples
and HTML blocks, regardless of headings. Deliberate over-detection avoids
missing real checks but can require ticking examples. All unchecked items in
either selected source block landing without `--checks-done`.

If neither selected source lists checks, `land` prints `No human checks listed`
and continues without an empty attestation. A missing publication comment does
not prove there were no intended checks: daemon delivery may be delayed.
Inspect publication before invoking the command, even when the body is empty.

With `--checks-done` and listed checks, `land` identifies the authenticated user
with `gh api user` on GitHub or `GET /api/v1/user` on Gitea using the human token,
ticks incomplete sources in place, and creates or updates an
attestation comment with this exact marker format:

```text
<!-- nightshift:land-checks:<pr>:<head sha> -->
```

`<pr>` is the decimal PR number within the repository; `<head sha>` is the full
head observed for this human-check pass, **before finish**. The comment includes
`**nightshift** —`, the actor login, PR URL, full head, body/comment source
identities, and checked item text, stating that the user recorded completion
through `land`. It is an operator assertion, not automated evidence that the
checks were performed. Finish's archive commit and a validated branch update
can advance the head without rewriting that original attestation.

A sequential retry at the same head updates a unique trusted marked comment,
including when all boxes were already ticked. A different head gets a new marker.
All comment pages are searched before creation. Within one invocation, an
ambiguous create is reconciled by marker with at most one POST; failure to
reconcile refuses that invocation. Duplicate or untrusted marker matches refuse.

Terminal and Factory landing share a late-POST exception: after an ambiguous POST
and unsuccessful reconciliation, a fresh invocation may see no marker and create
one before the earlier request commits remotely. Its valid acknowledgement need
not trigger another lookup or detect duplicates. A subsequent marker lookup
refuses `duplicate marker matches; resolve duplicates before retrying`.
Local process-group drainage cannot establish completion of a request already
accepted by the forge. There is no cross-invocation uniqueness guarantee or retained
create guard. Inspect prior effects and delete one duplicate comment, then use
Land again if eligible or terminal land after resolving outstanding attempts;
see the `duplicate-markers` recovery case below. All other landing gates remain.

Sources already complete are not edited again. Prose, existing ticks, markers,
and line endings are preserved. Source edits and attestation are separate API
operations: completed writes survive failures. Full-body/comment read-modify-write
is not atomic and can overwrite a concurrent edit in its residual write window.

## Finish, CI, and merge

A matching run must be paused at `human_checks` or already `done`. Landing
refreshes eligibility and invokes finish with the validated PR head in
`--expect-head` only for the former. After reloading under the existing run lock,
finish reads the local branch head and refuses an unreadable or mismatching head
before archiving, pushing or marking ready. A revision republished between
landing's identity check and finish therefore requires reviewing the new head
before retrying. Existing repository and unfinished-task checks still apply.
Landing then requires the same run to be `done` with its reported finished head matching
the remote PR. An initially done run skips finish. A direct PR skips finish and
must already be ready for review; a front-door draft can become ready through
finish. Running, aborted, and other paused states refuse before any mutation.
Unreadable or ambiguous discovery, including any status error row, refuses
instead of treating the PR as direct.

The optional guard is also available to direct callers. This parser example uses
an illustrative full SHA; substitute the actual reviewed head before execution:

```sh
nightshift finish <change> --expect-head 0123456789abcdef0123456789abcdef01234567
```

Ordinary direct Finish and act finish omit the optional guard. Use that recovery
path only after inspection when a local archive exists but its push failed; the
keyed recovery cases below distinguish this from a pushed archive with a ready
failure. Landing never substitutes the local head or automatically drops the
guard. Finish's archive successor and any base-update successor still undergo
landing's existing validation before the final head-conditioned merge.

CI and branch-update observation share one monotonic timeout across at most
three rounds, **including the initial round**. The budget starts after finish
or done-run continuation validation; another round does not reset it. Polls
wait up to 10 seconds, capped by the remaining budget. GitHub/status calls have
30-second limits and finish has a 300-second limit. Gitea HTTP attempts use
30-second socket timeouts; within CI/update polling these and subprocess calls
are capped by the remaining budget, and retries/pages consume that same budget.
This is not a total invocation timeout: merge and confirmation use their ordinary
timeouts outside the CI budget (separate 30-second calls on GitHub).

Each round records `main`. In round one, an ancestry comparison detects an
already-stale branch and updates it before evaluating CI. When needed, `land`
refreshes the head H and reads main B immediately before requesting, on GitHub:

```text
gh api --method PUT repos/owner/repo/pulls/123/update-branch -f expected_head_sha=<H>
```

Here `<H>` stands for the full verified head SHA; substitute its value before
using this illustrative API command. The endpoint uses its default merge
behavior. After an asynchronous update, only a commit with exactly two parents
`[H, B]`, in that order, is accepted. A descendant, reversed/extra parents, an
unrelated push, or a merge against a newer base refuses. A lost update response
is reconciled by observing the head and parents without a second PUT.

On GitHub, CI is read with `gh pr checks --json name,bucket,state,link` for the
explicit PR/repository, bracketed by head reads. Gitea reads combined status for
the exact head SHA, as detailed below. Unless the docs-only exclusion below is
established and attested, at least one check must pass and every
other check must pass or be skipping (neutral/skipped). Pending, empty, or
entirely skipped results wait within the shared budget; failures, cancellations,
malformed results, and unknown classifications refuse. Every new head needs
fresh checks or its own proven exclusion. Check sources, run state, and head are revalidated before finish and
before merge; new or unticked checks, changed checklist text, a replacement
publication, or unexpected head changes require another operator decision.

After green CI or an attested exclusion and final input validation, `main` is read again. A move starts
another round and update against a fresh base; the prior round's base remains
fixed even during asynchronous update observation. Three successive moves
produce two intervening updates and a refusal with all observed movements,
without a fourth round or merge. A stale branch may also need an initial update.

With a stable final base, GitHub landing requests a merge commit using
`gh pr merge <pr> --repo owner/repo --merge --match-head-commit <verified-head>`.
It uses no admin, auto, squash, rebase, or branch-deletion option; GitHub branch
protection remains authoritative.
On either forge, exit zero and `Merged <PR URL>` mean a subsequent read confirmed
merged state. Queue admission alone returns nonzero. A lost merge response succeeds
only when that read confirms the merge. The command does not deploy or print a
deploy command hint. Operational refusals exit 1; invalid syntax exits 2.

### Docs-only CI exclusion

[Issue #136](https://github.com/mattpolicastro/nightshift/issues/136) permits
landing when the recorded base workflow proves CI intentionally excludes every
changed path. The complete, nonempty PR file list must be available, including
deleted paths and both names of renames. Eligibility comes from base policy,
not file extensions alone, the PR body, local files or the head's workflow.
A mixed Markdown/code PR under `**.md` still needs CI.

At the pinned base SHA, the selected forge's workflow directory must contain
exactly one regular YAML workflow, named `ci.yml` or `ci.yaml`: GitHub uses
`.github/workflows`, Gitea uses `.gitea/workflows`, without fallback to another
directory. All Gitea evidence reads use the configured origin and human token.
This supported trigger example excludes root and nested Markdown:

```yaml
on:
  push:
    branches: [main]
    paths-ignore: ['**.md']
  pull_request:
    paths-ignore: ['**.md']
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:
```

The narrow physical-line grammar accepts one column-zero `on:` (optionally
quoted), two-space `pull_request:` and optional `push:` headings, and four-space
`paths-ignore`, `branches` or `branches-ignore` fields without duplicates.
Lists use quoted strings in brackets or six-space `- 'value'` lines; branch
lists also accept simple unquoted names/globs such as `main`. Each declared
`push`/`pull_request` event needs a nonempty ignore list matching every changed
path; `pull_request` is required.

Optional `schedule` and bare `workflow_dispatch` events contribute no ignore
list. Schedule requires one or more exact four-space `- cron: ` lines with
single- or double-quoted scalars, without escapes or embedded matching quotes;
cron timing semantics are not validated. Event headings and cron items allow
trailing spaces. Blank lines and whole-line comments are allowed, except that
dispatch permits no nested physical lines: even comments or space-only lines
beginning with three or more spaces are refused. Truly empty lines and comments
at column zero or two-space event indentation remain allowed under dispatch.
Duplicate events, empty/comment-only or flow schedules, nested/flow dispatches,
other events/fields, inline comments, tabs, escapes, aliases, anchors, tags and
multiline scalars are unsupported. Missing, incomplete, ambiguous or unsupported
evidence retains ordinary CI waiting.

Globs match whole case-sensitive repository-relative paths: `*` excludes `/`,
`**` includes `/`, and `**/` matches zero or more directory components.
Thus `*.md` covers only root files while `**.md` covers nested files too.
Negation, `?`, character classes, braces, backslash escapes, absolute paths and
traversal patterns are unsupported. Multiple workflows cannot establish this
exclusion even if one ignores all paths.

Proof starts after the first valid head-bracketed empty poll, before sleeping.
A later empty poll must finish at least 10 seconds later within the original
CI budget. Both GitHub's recognized missing-registration response and validated
empty arrays qualify, as does Gitea's proven empty combined status. No timeout
is extended and short budgets cannot shorten the grace. Any registered check
permanently disables the exception for that head during this invocation:
pending waits, failing/cancelled refuses, skipped-only waits, and passing checks
use the ordinary gate even if later polls would be empty. Finish/update
successors and new base rounds require fresh proof and grace.

After validating inputs, landing records a separate populated CI comment,
validates inputs again, and prints `CI not required: docs-only`. This means CI
was excluded, not that checks passed. The comment identifies the authenticated
actor, PR URL, full head/base SHAs, workflow path, patterns, changed paths and
observed grace. Inspect its marker:

```text
<!-- nightshift:land-ci:<pr>:<head sha>:<base sha> -->
```

This comment is required even with no human checklist and regardless of
`--checks-done`; `No human checks listed` still applies and no empty human
attestation is invented. Listed human-check evidence remains separate, retaining
its pre-finish head. Gitea requires an allowlisted non-bot actor for the CI
comment too. The final base comparison, three-round limit, protected pinned
merge and merged-state confirmation still apply. Server protection may reject a
required check that the workflow never schedules; landing does not override it.

Sequential retries update a unique trusted same-head/base CI marker. Actor
failure, duplicate/untrusted markers and unreconciled writes block merge.
An ambiguous create searches all comment pages and never repeats its POST in
that invocation. The existing late-POST cross-invocation limitation and
`duplicate-markers` recovery below apply to both kinds of attestation. An older
CI comment is historical head/base evidence, not proof of merge or permission
for a later invocation to skip its fresh gates.

Live validation remains pending: land the next real docs-only PR, confirm
`CI not required: docs-only` in the output and attestation, and verify it merges
without waiting out the CI deadline. Record that PR's number on
[issue #166](https://github.com/mattpolicastro/nightshift/issues/166); offline
tests cannot complete the live scheduling, protection and merge checks below.

## Gitea update, exact-head CI, and merge requests

The following are illustrative HTTP request shapes sent by `land`, relative to
the configured installation root. H denotes the reviewed full head SHA and B
the captured full main SHA; do not manually replay writes to recover a refusal.

Gitea initial ancestry walks exact-SHA commit parents with a visited set. Equal
head/base proves inclusion without a request; complete exhaustion proves the
branch stale. Unreadable facts or deadline expiry refuse. Before each necessary
update, landing re-reads the open, non-WIP PR and requires its head to remain H,
then reads main B immediately before sending one request:

```text
GET /api/v1/repos/owner/repo/branches/main
POST /api/v1/repos/owner/repo/pulls/123/update?style=merge
```

The update has no body and no server-side expected-head condition. A competing
push or main move can change the remote branch before provenance is checked.
Landing accepts only the first changed head C whose exact commit has exactly
the ordered parents `[H, B]`. Missing, extra, reversed or other-base parents,
descendants, closed/WIP PRs and failed reads refuse. This detects an unexpected
result after a write; it cannot prevent or roll back that mutation. A definite
rejection stops immediately; an uncertain response gets read-only observation
within the shared deadline, never another POST. A later independently required
base-update round may send its own single POST.

CI reads the combined-status endpoint, including all pages, for H (or the newly
validated C), with expected-head reads before and after:

```text
GET /api/v1/repos/owner/repo/commits/H/status
```

It does not use PR-latest, branch, merge-ref or Actions-job evidence. `success`
maps to pass, `pending` to pending, `failure`/`error`/`warning` to fail, and
`skipped` to skipping. Proven absence can use the established and attested
docs-only exclusion; otherwise it waits, as do skipped-only results.
Unknown states, wrong SHAs and malformed/inconsistent pages refuse. Every new
head needs fresh CI within the same 1800-second default, ten-second capped polls
and three-round budget described above. Source/run revalidation and the final
main comparison still apply.

After all gates, landing sends one pinned merge-commit request:

```text
POST /api/v1/repos/owner/repo/pulls/123/merge
{"Do":"merge","head_commit_id":"H"}
```

There are no force, auto-merge, squash, rebase or branch-deletion options.
Repository protection remains authoritative. Even after a lost acknowledgement,
landing reads the same PR and requires matching identity, closed state and
boolean `merged: true` before success. Closed alone, queued/open or unreadable
state is insufficient. Merge is never replayed within the invocation, and merge
and confirmation retain their ordinary HTTP timeouts after the CI budget ends.

## Recovering from a refusal

Failures identify the stage and reason on stderr, for example
`land: CI check unit tests: fail (FAILURE)` or
`land: unticked human checks remain:` followed by source references and items.
Inspect the named check, run state, head change, API error, or protection rule.
A refusal stops later mutations; previous successful writes are not rolled back.
Before retrying, resolve outstanding attempts and inspect local and remote effects.
Use the applicable case below; a local archive whose push failed needs explicit
direct Finish before terminal landing. Do not reset the run or bypass the guard
automatically. All retries retain the human-check, finish, CI, base and merge gates.

| Recovery case | Inspect and resolve | Next step |
| --- | --- | --- |
| `lost-response` | Refresh Factory status and inspect retained attempt evidence; submission may already have started. | Observe the existing attempt; do not automatically resubmit. |
| `stopped` | Stop verifies worker PID/start time/group and requests SIGTERM without rollback, SIGKILL escalation or guaranteed termination. Inspect local/remote effects and any resistant descendants. | Wait for termination; a fresh confirmation must independently verify prior group drainage under flock. There is no intake hold or manual lock release. |
| `worker-death` | The worker alone can die and release flock while children survive. Stop explicitly refuses a missing or unverifiable worker, even with surviving descendants. Wait for them or manually terminate them after inspection. | A fresh attempt must independently prove group absence under flock; only ESRCH from the safe kernel probe proves drainage. A free lock, unchanged head or terminal result is insufficient, including zombies or unverifiable groups. |
| `duplicate-markers` | A late POST can duplicate another invocation's marker even after local groups drain; a valid acknowledgement need not detect it, but a subsequent lookup refuses. Inspect effects and delete one duplicate comment. | After resolving outstanding attempts, Land again if eligible or use `uv run python -m nightshift.land <url> --checks-done`; the remaining unique trusted marker is updated through all existing gates. |
| `revised-head` | Locked finish refuses a changed or unreadable local head before archive/push/ready. Inspect with `nightshift status <change> --events 20` and review the newly published head and checks. | Refresh and confirm the new Factory offer, or deliberately retry terminal landing; existing finish protections and successor-head validation remain. |
| `unpushed-archive` | Finish created local archive A but its push failed; remote remains H and the run remains at human_checks. Guarded landing passes H and refuses A; Factory may refuse the local/remote offer mismatch earlier. After all attempts stop, inspect prior local/remote effects, the failed push, A's archive/tasks and remote refs. | Explicit direct Finish without --expect-head: use Factory Finish, `python -m nightshift.cli finish <change>` or `nightshift act <change> finish --json`. Recovery reuses A without rearchiving, pushes and marks done. Review the remote head, then `uv run python -m nightshift.land <url> --checks-done` on the done run; it skips finish and repeats fresh landing gates. |
| `pushed-archive-ready-failure` | The archive push already succeeded and only ready/state completion failed; inspect matching local and remote A and the existing archive/tasks. | Matching heads permit guarded landing recovery; ordinary direct Finish/act recovery also remains available. No reset or automatic guard bypass is needed. |
| `done-run` | Finish succeeded before a later refusal; inspect remote head, checks, CI, update or merge effects. Factory offers no Land for done. | For an open PR, `uv run python -m nightshift.land <url> --checks-done` skips finish and repeats human-check, CI, base and merge gates. Never reset done. |

Factory's lock does not coordinate terminal invocations. Its worker does not pass
the lock descriptor to descendants, and status cannot clear exclusion evidence.
Do not prune undrained-group records, even for a terminal result or an attempt
older than the newest 20. See [persistent evidence](../plugins/nightshift/README.md#landing-evidence).

Done-run continuation applies after CI failure or timeout, an accepted branch
update, or merge failure. The current remote head may differ from the done run's saved head;
the new invocation authorizes that reviewed remote head and still requires
fresh gates. Prior green CI is never a durable bypass. For incomplete checkbox
edits or attestation, rerun with `--checks-done` after resolving the reported
error; already-completed source edits remain intact. If a head or checklist
changed, review it and perform any new checks before retrying. If the command
cannot establish an update's exact parents, inspect the resulting branch first.

For a merge rejection, satisfy protection or review a competing push before
retrying. For a queued-but-open PR, inspect its queue status; the command has
not confirmed completion. If confirmation was unreadable, inspect the forge first:
the merge may already have happened. An already merged PR needs no rerun.
Landing never blindly repeats an uncertain merge request within an invocation.

For Gitea credential or trust refusal, inspect the selected configuration,
`land.env` assignment and `trusted_logins`; do not switch to the bot token.
If finish fails, inspect its separately provisioned bot/git credentials and
`NIGHTSHIFT_FORGE_CONFIG` alignment before choosing the existing archive recovery
case. After an uncertain update, inspect the exact head and ordered parents;
an already accepted unexpected update needs human review, not an automatic retry.
The partial-edit, duplicate-marker and done-run cases apply to terminal Gitea
landing too; Factory control cases apply only to its GitHub worker.

## Remaining human validation

GitHub's `--match-head-commit` and Gitea's `head_commit_id` protect the PR head.
Neither prevents `main` moving between its final read and the merge. This accepted
base race remains subject
to forge protection; landing does not provide atomic base conditioning.

Live merge validation remains a human check. Automated tests cannot establish
real permissions, update parent shape on either forge, check scheduling, branch-protection
interactions, or an actual merge. Leave this checklist open until a person has
observed the first real landing:

- [ ] Watch an operator run `land` end to end on a reviewed enrolled PR. Confirm
  the expected body/publication checks are edited in place and one marked
  attestation attributes completion to the authenticated user and pre-finish head.
- [ ] Observe finish and resulting CI; when `main` moves, inspect the REST
  update's exact ordered parents and fresh CI before the head-conditioned merge.
  Confirm GitHub records a merge commit and the command reports the merged PR
  URL without a deploy hint.
- [ ] Record the live outcome and any GitHub permission or protection restrictions
  in the operator's review record. A green unit suite does not complete these checks.
- [ ] Review Factory Land on desktop and native phone: confirmation/Cancel, stale
  identity, touch targets/wrapping, foreground refresh and retained notices in
  visible, folded and absent rows. Inspect real-host Stop and persistent evidence.
- [ ] A human reviews and lands the first real Gitea PR using the configured
  human token from `land.env`. Verify the attestation actor and pre-finish head
  where checks are listed, with finish's separate credentials where applicable.
- [ ] Inspect an actual Gitea merge-style branch update when needed: the accepted
  head has exactly the reviewed head and recorded main as its ordered parents,
  and fresh CI belongs to that new SHA.
- [ ] Inspect the resulting Gitea merge commit and PR's merged state, including
  real human-token permissions and protection behavior. Record the observed PR
  URL and merge SHA without secrets in the operator's review record. These live
  checks remain pending; CI fixtures cannot establish real permissions or merge
  behavior, and do not complete the separate #129 live publication checks.
- [ ] A human reviews and lands the next real Markdown-only PR for #136.
  Confirm no checks register, observe the grace, and inspect the output and
  separate CI attestation's reason, paths, workflow and exact head/base.
- [ ] Inspect that PR's resulting merge commit and protection outcome; record
  its URL and merge SHA in the review record. For Gitea, also verify live
  workflow filtering and human-token permissions. These scheduling, permission
  and merge checks remain pending; offline tests cannot complete them.

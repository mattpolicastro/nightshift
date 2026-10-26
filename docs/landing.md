# Landing a reviewed PR

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

Use a working Nightshift checkout with `uv` and `gh` available on PATH. The
interpreter running `nightshift.land` must be able to import `nightshift.cli`;
the `nightshift` console script need not be on PATH, and virtual-environment
activation is not required. The selected configuration must contain exactly one
case-insensitive enrollment for the repository. Omit `engine` or set
`engine = "specloop"`, and configure a trusted manifest. Explicit
`engine = "worker"` fails configuration loading because the worker engine is
retired; migrate to specloop with a trusted manifest before landing.
Authenticate `gh` as the operator
with permission to read and edit the PR and issue comments, update the branch,
and merge under the repository's protection rules. Run in the environment where
that interpreter's `-m nightshift.cli status --json` can read the applicable runs.
Only open PRs targeting an existing `main` are eligible.

| Argument | Meaning |
| --- | --- |
| `<pr-url-or-number>` | GitHub.com HTTPS pull URL or positive decimal PR number. |
| `--repo owner/name` | Explicit repository; otherwise inferred from the URL or `gh repo view`. |
| `--config PATH` | Enrollment file; defaults to `nightshift.config.DEFAULT_PATH`, the `config.toml` at the package root. |
| `--checks-done` | Assert that you performed the listed human checks; tick and attest them. Without it, unchecked items refuse landing. |
| `--timeout SECONDS` | Positive finite CI/update budget, including fractional seconds; default 1800 seconds (30 minutes). |
| `--help` | Show usage without performing external operations. |

The daemon, intake helpers, and `nightshift serve` supervisor never invoke this
command. Run only one landing invocation per PR at a time; there is no lock or
server-enforced uniqueness for concurrent operators. Invoking the command is
your decision to land the current reviewed head. It does not perform human
checks or grant review approval.

## Check sources and attestation

The PR body is always checked. A front-door PR is identified by a matching
canonical PR URL in version-1 `python -m nightshift.cli status --json`, for omitted
or explicit specloop enrollment, independently of labels or branch names.
For that PR, `land` also reads every issue-comment
page and selects the newest trusted publication for the matching change, ordered
by creation time and then numeric comment ID. Trusted authors have OWNER,
MEMBER, or COLLABORATOR association. Publications start with `**nightshift** —`
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
with `gh api user`, ticks incomplete sources in place, and creates or updates an
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

A sequential retry at the same head updates the marked comment, including when
all boxes were already ticked. A different head gets a new marker. All comment
pages are searched before creation. An ambiguous create is reconciled by marker
and never blindly posted twice; duplicate or untrusted marker matches refuse.
Sources already complete are not edited again. Prose, existing ticks, markers,
and line endings are preserved. Source edits and attestation are separate API
operations: completed writes survive failures. Full-body/comment read-modify-write
is not atomic and can overwrite a concurrent edit in its residual write window.

## Finish, CI, and merge

A matching run must be paused at `human_checks` or already `done`. Landing
refreshes eligibility and invokes `python -m nightshift.cli finish <change>` only
for the former, then requires the same run to be `done` with its reported finished head matching
the remote PR. An initially done run skips finish. A direct PR skips finish and
must already be ready for review; a front-door draft can become ready through
finish. Running, aborted, and other paused states refuse before any mutation.
Unreadable or ambiguous discovery, including any status error row, refuses
instead of treating the PR as direct.

CI and branch-update observation share one monotonic timeout across at most
three rounds, **including the initial round**. The budget starts after finish
or done-run continuation validation; another round does not reset it. Polls
wait up to 10 seconds, capped by the remaining budget. GitHub/status calls have
30-second limits and finish has a 300-second limit; calls within CI/update
polling are also capped by the remaining budget. This is not a total invocation
timeout: merge and confirmation each have their own bounded 30-second call.

Each round records `main`. In round one, an ancestry comparison detects an
already-stale branch and updates it before evaluating CI. When needed, `land`
refreshes the head H and reads main B immediately before requesting:

```text
gh api --method PUT repos/owner/repo/pulls/123/update-branch -f expected_head_sha=<H>
```

Here `<H>` stands for the full verified head SHA; substitute its value before
using this illustrative API command. The endpoint uses its default merge
behavior. After an asynchronous update, only a commit with exactly two parents
`[H, B]`, in that order, is accepted. A descendant, reversed/extra parents, an
unrelated push, or a merge against a newer base refuses. A lost update response
is reconciled by observing the head and parents without a second PUT.

CI is read with `gh pr checks --json name,bucket,state,link` for the explicit
PR/repository, bracketed by head reads. At least one check must pass and every
other check must pass or be skipping (neutral/skipped). Pending, empty, or
entirely skipped results wait within the shared budget; failures, cancellations,
malformed results, and unknown classifications refuse. Every new head needs
fresh CI. Check sources, run state, and head are revalidated before finish and
before merge; new or unticked checks, changed checklist text, a replacement
publication, or unexpected head changes require another operator decision.

After green CI and final input validation, `main` is read again. A move starts
another round and update against a fresh base; the prior round's base remains
fixed even during asynchronous update observation. Three successive moves
produce two intervening updates and a refusal with all observed movements,
without a fourth round or merge. A stale branch may also need an initial update.

With a stable final base, `land` requests a merge commit using
`gh pr merge <pr> --repo owner/repo --merge --match-head-commit <verified-head>`.
It uses no admin, auto, squash, rebase, or branch-deletion option; GitHub branch
protection remains authoritative.
Exit zero and `Merged <PR URL>` mean a subsequent GitHub read confirmed merged
state. Queue admission alone returns nonzero. A lost merge response succeeds
only when that read confirms the merge. The command does not deploy or print a
deploy command hint. Operational refusals exit 1; invalid syntax exits 2.

## Recovering from a refusal

Failures identify the stage and reason on stderr, for example
`land: CI check unit tests: fail (FAILURE)` or
`land: unticked human checks remain:` followed by source references and items.
Inspect the named check, run state, head change, API error, or protection rule.
A refusal stops later mutations; previous successful writes are not rolled back.
Resolve the cause, review any new head and checks, then rerun the same landing command
while the PR remains open. Do not reset a done run: landing repeats the human
check, CI, base and merge gates and skips finish. A partial finish still paused
at `human_checks` uses specloop's existing finish recovery.

This applies after CI failure or timeout, an accepted branch update, or merge
failure. The current remote head may differ from the done run's saved head;
the new invocation authorizes that reviewed remote head and still requires
fresh gates. Prior green CI is never a durable bypass. For incomplete checkbox
edits or attestation, rerun with `--checks-done` after resolving the reported
error; already-completed source edits remain intact. If a head or checklist
changed, review it and perform any new checks before retrying. If the command
cannot establish an update's exact parents, inspect the resulting branch first.

For a merge rejection, satisfy protection or review a competing push before
retrying. For a queued-but-open PR, inspect its queue status; the command has
not confirmed completion. If confirmation was unreadable, inspect GitHub first:
the merge may already have happened. An already merged PR needs no rerun.
Landing never blindly repeats an uncertain merge request within an invocation.

## Remaining human validation

`--match-head-commit` protects the PR head. It does not prevent `main` moving
between its final read and the merge. This accepted base race remains subject
to GitHub protection; landing does not provide atomic base conditioning.

Live merge validation remains a human check. Automated tests cannot establish
real permissions, GitHub update parent shape, check scheduling, branch-protection
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

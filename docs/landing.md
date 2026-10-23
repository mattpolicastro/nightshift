# Landing a reviewed PR

An operator or explicitly delegated session can run:

```sh
uv run python -m nightshift.land https://github.com/owner/repo/pull/123
uv run python -m nightshift.land 123 --repo owner/repo --checks-done
```

The repository must be enrolled in `config.toml`. Authenticate `gh` as the
operator with permission to read and edit the PR, update its branch, and merge
under the repository's protection rules. Make `specloop` available on PATH.
The daemon and supervisor do not invoke this command. Run only one landing
invocation per PR at a time.

Use `--config PATH` to select another enrollment file and `--timeout SECONDS`
to change the positive CI budget (default 1800 seconds). A bare PR number uses
`--repo owner/name`, or the current repository reported by `gh repo view`.
Only open PRs targeting an existing `main` are eligible.

Before invoking, review the current head and perform the human checks. Checks
come from the PR body and, for a matching specloop run, its newest trusted
publication comment. Every task-list line counts, including examples inside
code fences and HTML: optional indentation/blockquotes followed by `-`, `*`, or
`+`, a space, `[ ]`, `[x]`, or `[X]`, a space, and item text. This deliberate
over-detection can require ticking examples. With no listed checks the command
prints `No human checks listed`; a delayed publication comment can still be
missing, so inspect publication before landing.

`--checks-done` records your assertion that you performed the checks. It ticks
them in place and creates or updates a head-specific attestation attributed to
the authenticated GitHub user. The attestation is an operator assertion, not
automated evidence or review approval. Sequential retries reconcile the same
attestation and preserve completed edits. Body/comment updates are separate
operations and have a residual concurrent-edit window.

A matching run must be paused at `human_checks` or already `done`. Landing
finishes the former, verifies its finished remote head, and skips finish for
the latter. A direct PR skips finish and must already be ready for review.
Unreadable or ambiguous run discovery refuses rather than assuming a direct PR.

CI and branch-update observation share one timeout across at most three rounds.
Stale branches are updated with the expected head; only the exact merge parents
are accepted. Each resulting head requires fresh green CI, final run/checklist
revalidation, and a stable final `main` comparison. Landing requests a merge
commit conditioned on the verified head, without bypassing branch protection.
Exit zero and `Merged <PR URL>` mean a subsequent GitHub read confirmed merged
state. Queue admission alone returns nonzero. A lost merge response succeeds
only when that read confirms the merge.

## Recovering from a refusal

Failures return nonzero and identify the stage and reason on stderr. Inspect
the named check, run state, head change, API error, or protection rule. Resolve
the cause, review any new head and checks, then rerun the same landing command
while the PR remains open. Do not reset a done run: landing repeats the human
check, CI, base and merge gates and skips finish. A partial finish still paused
at `human_checks` uses specloop's existing finish recovery.

For a merge rejection, satisfy protection or review a competing push before
retrying. For a queued-but-open PR, inspect its queue status; the command has
not confirmed completion. If confirmation was unreadable, inspect GitHub first:
the merge may already have happened. An already merged PR needs no rerun.
Landing never blindly repeats an uncertain merge request within an invocation.

## Remaining human validation

`--match-head-commit` protects the PR head. It does not prevent `main` moving
between its final read and the merge. This accepted base race remains subject
to GitHub protection; landing does not provide atomic base conditioning.

Live merge validation remains a human check. Observe the first real landing
end to end: check source selection and attribution, finish, fresh CI, exact
branch-update parents when needed, and GitHub's recorded merge commit. Record
the live outcome and any permission or protection restrictions in the operator's
review record. Automated tests cannot establish those live GitHub behaviors or
mark these checks complete.

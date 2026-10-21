# Command outcomes and repository templates

`propose`, `admit`, `revise`, and `publish` accept `--json`. Each prints exactly one
JSON document to stdout with `"version": 1`; progress, notifications, and
inherited child-process output (including Git hooks) go to stderr. Without
`--json`, the existing human-readable output is preserved.

| Command | Outcome and exit code | Other fields |
| --- | --- | --- |
| `propose` | `drafted` (0), `needs_input` (2), `rejected` (1) | `branch`, `commit`, `questions`, `contradictions`, `risks`, `problems` |
| `admit` | `admitted` (0), `refused` (1) | `head`, `reasons`, `risks` |
| `revise` | `revised` (0), `refused` (1) | `commit` on success; `reasons` on refusal |
| `publish` | `published` (0), `refused` (1) | `pr` on success; `reasons` on refusal |

Publication exceptions (such as a failed or timed-out push) exit 1 with
`{"version":1,"error":"..."}` instead of a domain outcome. The daemon records
the failure in repository health and keeps publication eligible for a later
poll; it does not cache the failure as a delivered refusal. A `needs-human`
label still holds retries until removed.

Unused lists are empty; an unavailable head or commit is an empty string.
Draft contradictions retain their `task`, `problem`, and `evidence` fields.
Admission refusal reasons are strings including the contradiction and evidence.
A rejected draft can have a candidate commit; only `drafted` means its branch
was made available in the source repository.

Use one manifest for a repository and override its `[run]` values:

```sh
specloop propose repo.toml --change issue-7-y --branch spec/issue-7-y \
  --idea-file idea.md --json
# After human spec approval:
specloop admit repo.toml --change issue-7-y --branch spec/issue-7-y --json
specloop publish issue-7-y --json
```

Each omitted override inherits the manifest's value. Admission stores the
effective values in the run's trusted `manifest.toml`; the original template
is unchanged. Existing provider overrides (`--implement` and `--review`)
continue to work.

To revise an existing spec, include the original idea and new feedback in the
idea file and start from its local branch (or fetched ref):

```sh
specloop propose repo.toml --change issue-7-y --branch spec/issue-7-y \
  --from-branch spec/issue-7-y --idea-file revised-idea.md --json
```

`--from-branch` overrides the drafting starting point (`--base`) so the revised
spec is committed on top of the existing tip. It must resolve in the manifest's
source repository; `propose` still only publishes locally. If the destination
branch is checked out in any source worktree, the revised commit is retained
under `refs/specloop/proposals/<change>` instead, preserving the checkout and
its edits. The returned commit can still be pushed to the PR branch.

For an unadmitted spec PR, the daemon handles `agent:revise` by adding review
feedback newer than the PR tip to the original idea and running this command
as a background job. It fetches and checks the PR tip first, pushes the result
without force, comments the feedback addressed and commit, then removes
`agent:revise`. Saved results and marked comments make delivery retryable.
Questions or rejected drafts are commented with `needs-human` added; resolve
them and remove `needs-human` to retry. Empty feedback is explained and the
revise label removed without drafting. GitHub read failures preserve the label
for retry instead of treating the feedback as empty.

If the change already has a PR, pass `--pr <url>` to `admit`. Admission
records the URL in run state. When the run reaches `ready_to_publish`,
`publish` pushes the branch and reports that same URL without creating a PR:

```sh
specloop admit repo.toml --change issue-7-y --branch spec/issue-7-y \
  --pr https://github.com/example/repo/pull/42 --json
specloop publish issue-7-y --json
```

Without `--pr`, publication continues to create a draft PR.

For an approved PR, the daemon fetches its branch into
`refs/nightshift/admit/<change>` in the manifest's source repository, checks
that it matches the PR tip, and passes `--head <full-commit-id>` to `admit`.
This sets the branch in the run's bare repository to that commit while
preserving the source branch, index, and working files, even if the spec is
checked out or was rebased during review. The commit must exist in the local
source repository. Omitting `--head` uses the source branch as before.

Successful admission comments that the build has started and removes
`spec:approved` and `spec:review`. Refusal comments the reasons and evidence,
adds `needs-human`, and removes `spec:approved`. Removing `needs-human` and
applying `spec:approved` again requests a new admission attempt.
Admission receipts record the approval label-event ID: delivery retries reuse
the refusal for that event, while reapproval retries admission even if the PR
tip is unchanged and the previous label-write response was lost.

Drafting and admission run in background subprocesses. The daemon records each
job in its proposal or admission receipt under `~/.nightshift`, then harvests
the result on a later poll. Worker claims, worker completion handling, and the
heartbeat continue while these agent sessions run. Job locks and saved results
let a restarted daemon resume polling and delivery without launching the same
session again. Failed jobs are reported through repository health before retry.

The daemon publishes its admitted runs when they pause `ready_to_publish`,
using `specloop publish <change> --json` and the recorded PR. It comments the
remaining human checks and the hosted CI reminder on that PR. Publication
receipts preserve the checks before the run transitions to `human_checks`, so
an interrupted notification can be retried without publishing again. Comments
use a hidden marker; lost create responses are reconciled before retrying,
and each later publication round posts its own comment (a retried delivery of the same round edits it). Unmanaged
runs and PRs waiting on `agent:revise` or `needs-human` are left for a human.

While `~/.nightshift/pause` exists, the daemon starts no specloop commands and
makes no specloop intake, admission, revision or publication changes. It checks
again before each external operation, including GitHub retries and comment
reconciliation after a lost response.
Drafting and admission subprocesses already running may finish and save their
results; PR and issue delivery waits until the pause file is removed.
Runs already admitted continue under the separate specloop supervisor; the
Nightshift pause file does not stop that supervisor.

Queue a review round for an admitted change with:

```sh
specloop revise issue-7-y --feedback feedback.md --json
```

Separate feedback blocks with a line containing only `---`. Each nonempty
block becomes one unchecked task in a new `## N. Review round K` section.
The task quotes the entire block as a JSON string, preserving file/line
references and escaping newlines so embedded checklists remain one task.
Empty files and files containing only whitespace or separators are refused.
The harness commits tasks.md to its run branch and leaves the run running
for `serve` or `specloop run`; it preserves the recorded PR. A run currently
being driven must be retried once its lock is available.

For comments containing `---` lines, use `--feedback-format json` with a
feedback file containing a JSON array of strings, one complete comment per
string. The daemon uses this format for `agent:revise` on admitted PRs,
preserving each new comment and its file/line reference. It removes the label
after the revision commit is reported. With no feedback newer than the PR tip,
it comments that there is nothing to revise and removes the label without
running a specloop command.

The original `propose repo.toml change --idea "..."` syntax still defaults to
`spec/change`. An explicit `--change` takes precedence over the positional
name, and an explicit `--branch` takes precedence over that default.

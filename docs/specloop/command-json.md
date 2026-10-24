# Command outcomes and repository templates

`propose`, `admit`, `revise`, and `publish` accept `--json`. Each prints exactly one
JSON document to stdout with `"version": 1`; progress, notifications, and
inherited child-process output (including Git hooks) go to stderr. Without
`--json`, the existing human-readable output is preserved.

| Command | Outcome and exit code | Other fields |
| --- | --- | --- |
| `propose` | `drafted` (0), `needs_input` (2), `rejected` (1) | `branch`, `commit`, `questions`, `contradictions`, `risks`, `problems` |
| `admit` | `admitted` (0), `refused` (1) | `head`, `reasons`, `risks` |
| `revise` | `revised` (0), `refused` (1) | `commit` on success; keyed success also has `round_id`, `replayed`, `status`, `pause`; `reasons` on refusal |
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

## Keyed build revisions

Queue a review round for an admitted change with an optional run-local key
([issue #7](https://github.com/mattpolicastro/nightshift/issues/7)):

```sh
specloop revise issue-7-y --feedback feedback.md --round-id review:1 --json
```

Keep that key and the saved feedback for retries. Keys are 1–128 ASCII
characters matching `[A-Za-z0-9][A-Za-z0-9._:-]*`; empty keys are invalid.
Scope is the admitted run and its recorded branch. Matching normalized blocks
return the original round commit, even when the branch tip is a descendant;
changed blocks under the same key are refused. Order and duplicates matter.
Filenames, JSON whitespace and text/JSON encoding do not change identity when
the parsed nonempty blocks are identical. Different keys can append identical
feedback intentionally.

Keyed success retains version 1 and adds these fields:

```json
{
  "version": 1,
  "outcome": "revised",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "round_id": "review:1",
  "replayed": false,
  "status": "running",
  "pause": null
}
```

`replayed` is boolean: true means recovery/replay of an existing journal,
including landing its prepared candidate. `commit` always names that round's
original commit. `status` is `running`, `paused`, `aborted` or `done`;
`pause` is null or the observed pause object (`reason`, `task`, `detail`,
`resume_at`). State is observed under the run lock, not promised at delivery
time. Replay preserves later pause, abort or completion decisions and pending
pause requests; human output uses “finished” for `done`. Invalid keys, empty
feedback, conflicting payloads and unverifiable evidence exit 1 with
`outcome: refused` and `reasons`; Git operational failures remain identifiable
in those reasons. Busy runs must be retried once their lock is available.

Separate feedback blocks with a line containing only `---`. Each nonempty
block in a new round becomes one unchecked task in `## N. Review round K`.
The task quotes the entire block as a JSON string, preserving file/line
references and escaping newlines so embedded checklists remain one task.
Empty files and files containing only whitespace or separators are refused.
For a new round, the harness commits tasks.md to its run branch and leaves
the run running for `serve` or `specloop run`, preserving the recorded PR.

For comments containing `---` lines, use `--feedback-format json` with a
feedback file containing a JSON array of strings, one complete comment per
string. The daemon uses this format for `agent:revise` on admitted PRs,
preserving each new comment and its file/line reference. It removes the label
after durable success consumes the selected IDs. With no unconsumed feedback,
it comments that there is nothing to revise and removes the label without
running a specloop command.

New daemon build snapshots save a UUID key and feedback before dispatch.
Ordinary crashed attempts detach and report health errors; a later eligible
attempt uses the same snapshot/key with a new job ID and deadline. Explicit
`nightshift jobs release <id>` after confirmed stop and inspection instead
permits selection of current unconsumed feedback with a new key, even under
the same label event. The historical stopped result stays suppressed and the
new round can repeat already committed effects. Release launches nothing,
does not roll back effects and does not stop independent admitted builds; see
[recovery, timing and cleanup](README.md#pause-stop-and-the-supervisor)
([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).

Omitting `--round-id` retains the unkeyed version-1 fields and human success
wording (`run is running`), with a fresh append on each eligible call. Legacy
receipts and unkeyed calls have no retrospective deduplication; a commit whose
result was lost can be duplicated. Keyed recovery is tested with deterministic
process exits, not physical machine crashes. Retain journals and Git evidence;
incomplete backups or deletion lose the guarantee. See [remaining limits](../../KNOWN-LIMITATIONS.md#open-gaps).

The original `propose repo.toml change --idea "..."` syntax still defaults to
`spec/change`. An explicit `--change` takes precedence over the positional
name, and an explicit `--branch` takes precedence over that default.

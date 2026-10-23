# specloop — Nightshift's build engine

Nightshift turns approved OpenSpec changes into reviewed, verified pull requests.
specloop is the engine that does the building: it takes an admitted change through
its `tasks.md` one task at a time, and the daemon is its GitHub front end. For the
JSON contract see [command-json.md](command-json.md); for dependency setup see
[prepare.md](prepare.md).

## GitHub front door: Nightshift → specloop

The Nightshift daemon can now drive specloop through its versioned JSON CLI.
Repository enrollment is a separate human edit to local `config.toml`:

```toml
[[repos]]
name = "owner/sample"
engine = "specloop"
manifest = "/absolute/path/to/sample.toml"
base = "main"
```

Use one trusted regular-file manifest per repository. Relative manifest paths
resolve beside `config.toml`; `~` expands. Set its `[run]` source `repo`,
`github` and `pr_base` to match the repository, and supply provider profiles,
roles, allowed/frozen paths and verification commands. Template `run.change` and
`run.branch` are optional: intake supplies both per change, overriding any
defaults. Admission saves the effective manifest with the run. The worker's
`verify` setting is not needed for specloop.
See [enrollment and preparation](../../README.md#enroll-a-repository) and the
[CLI contract](command-json.md).

| Label | Stage and action |
| --- | --- |
| `spec:draft` or `agent:ready` on an issue | Draft its title, body and comments into an OpenSpec change. |
| `spec:review` on the draft PR | The daemon requests human spec review. |
| `spec:approved` on the PR | A human requests admission; the daemon records that PR and starts the build. |
| `agent:revise` on the PR, before admission | Re-draft the spec on the same branch with new review feedback. |
| `agent:revise` on the PR, after admission | Append a review round of tasks and resume the run. |
| `needs-human` on the issue or PR | Read and resolve the questions or refusal comment, remove this label, and reapply the stage's trigger label as described below. |

An issue-body line such as `blocked-by: #9, #12` defers a new draft round
while any dependency is open or unreadable. The trigger stays in place, so a
later poll can draft after all blockers close. Existing jobs, saved results
and open draft PRs can still be collected or delivered under normal
eligibility rules, including authorization
([issue #24](https://github.com/mattpolicastro/nightshift/issues/24),
[PR #29](https://github.com/mattpolicastro/nightshift/pull/29)).

For `spec:draft`, `agent:ready`, `spec:approved` and `agent:revise`, the actor
on the latest application of each trigger must be an OWNER, MEMBER or
COLLABORATOR. If both intake aliases remain, both must be authorized. An
untrusted application gets an explanation and does not authorize work; have
a trusted actor remove and reapply the label (or remove an unauthorized
intake alias). A failed actor lookup defers action for retry. Feedback authors
are checked independently: a trusted trigger does not make untrusted comments
eligible ([issue #5](https://github.com/mattpolicastro/nightshift/issues/5),
[PR #42](https://github.com/mattpolicastro/nightshift/pull/42)).

The daemon writes the issue title, body and trusted comments to a durable
UTF-8 file and passes its absolute path with `--idea-file`; the idea text
does not go in argv. The file survives delayed background reads, later polls
and restarts; each round keeps its own input
([issue #12](https://github.com/mattpolicastro/nightshift/issues/12),
[PR #35](https://github.com/mattpolicastro/nightshift/pull/35)).

Draft questions or rejection remove both issue intake labels. Answer the
questions or resolve the rejection on the issue, remove `needs-human`, and
reapply `spec:draft` or `agent:ready` to retry. Removing `needs-human` alone
does not requeue the issue.

For issue 7, "Add a gate", in `owner/sample`, the change is
`owner__sample-issue-7-add-a-gate`, on `spec/owner__sample-issue-7-add-a-gate`. The draft PR
is titled `Spec: Add a gate`, links the issue with `Closes #7`, and carries
the feasibility risks. Successful admission removes `spec:review` and
`spec:approved`. Refusal removes `spec:approved` and adds `needs-human`;
remove `needs-human` and reapply `spec:approved` after resolving the refusal.

New identities use `<owner>__<repository>-issue-<number>-<title-slug>`.
Owner/repository spelling and safe dots, underscores and hyphens are preserved;
the title becomes lowercase ASCII alphanumerics separated by hyphens, at most
40 characters, falling back to `issue`. Unsafe spellings such as
`owner/widget..js` are unsupported for new work: repository health holds
drafting, admission and spec re-drafting without encoding or sanitizing names.
Recorded legacy identities remain authoritative after title edits; existing
safe branches stay usable. Without a proposal receipt, lookup tries exact
repository-scoped qualified and then legacy branches using the current title.
An open legacy PR is reused; a new round after a closed legacy PR uses the
qualified name. See [remaining discovery and migration limits](../../KNOWN-LIMITATIONS.md#open-gaps).

Revision selects trusted, actionable feedback by source-qualified GitHub ID,
with consumed IDs tracked per repository and PR: `conversation:123`,
`review:123` and `inline:123` are distinct. Inline feedback keeps its file and
line. Each spec or build round saves its selected IDs and feedback before
starting. Comments arriving during that round remain eligible for the next
authorized request, even if older than the new branch tip. Edits cannot change
the saved snapshot or make a consumed ID eligible again; post a new comment
with a new ID for further requests
([issue #6](https://github.com/mattpolicastro/nightshift/issues/6),
[PR #61](https://github.com/mattpolicastro/nightshift/pull/61)).

The saved IDs are consumed after a successful spec push or a durable,
successful build revision result with a landed commit, before notifications
and without waiting for build publication. Relabeling or restarting does not
replay them. Empty revision requests are explained and cleared without an
agent ([PR #61](https://github.com/mattpolicastro/nightshift/pull/61)).

Legacy revision receipts initialize consumed IDs once, after authorization.
Trusted records created at or before the receipt's saved time are treated as
consumed, including blank and daemon-prefixed records. This uses comment
creation and review submission times, not edit times. If the saved time is
absent, the current PR head's committer time supplies the cutoff once. The
approximate baseline is saved before further processing and is never
recomputed on restart or tip advance; later IDs stay eligible. No manual
initialization is needed. With no receipt, eligible historical feedback is
available ([PR #61](https://github.com/mattpolicastro/nightshift/pull/61)).

The separate `specloop serve` supervisor executes admitted tasks and prepares
the current base before `ready_to_publish`. The daemon then attempts
[publication](#publication-and-base-recovery) to the same branch and PR,
commenting the remaining human checks and hosted CI reminder after success.
A human reviews, checks and merges; merging closes the linked issue.

`specloop status --json` isolates unreadable runs: version-1 JSON still
includes healthy runs and exits 0. Each unreadable run has a non-actionable
`status_error` row with a bounded diagnostic and compatibility placeholders
(`paused`, zero tasks, empty head/PR). It offers no actions and does not persist
a pause. Top-level failures, such as an unreadable run listing or supervisor
status, instead return a version-1 error document and exit 1
([issue #10](https://github.com/mattpolicastro/nightshift/issues/10),
[PR #32](https://github.com/mattpolicastro/nightshift/pull/32)).

`nightshift attention` lists human pauses with reason, change and PR link.
Per-run `status_error` rows refresh alongside healthy entries; a failed
top-level status read instead retains the previous snapshot marked `STALE`
and exits 1 ([PR #32](https://github.com/mattpolicastro/nightshift/pull/32)).
JSON carries `specloop` beside worker
`tasks`. The Paseo plugin source now includes the Factory section described
below. Daemon quota pacing for these runs remains separate work.

Optional manifest `[[prepare]]` steps fetch dependencies before offline
verification. They have the same `id`, argv-list `run`, `expect` and `timeout`
fields as `[[verify]]`. After candidate extraction, they run in order from the
build directory, under their own sandbox allowing network but confining writes
to that directory and denying credential-directory reads. The extracted
source stays read-only; copy it into the build directory if installation needs
writes. Receipts and logs accompany verification, and an unexpected exit or
timeout fails verification before later steps run. Commands are trusted;
use a frozen lockfile for reproducible fetching. See the
[pnpm preparation example](prepare.md).

**Rollout:** `worker` remains the default when `engine` is omitted, with its
existing behavior; retiring it is a later change. See [operations](#operations)
for pause, job control and supervisor boundaries.

## Operations

### Enrollment recovery

Each unpaused poll validates the enrollment template, checking regular-file
status before opening it. Symlinks to regular files are supported; directories,
FIFOs and devices are rejected without reading. Missing, unreadable or invalid
templates, or an omitted manifest path, cause repository-local health failures.
The diagnostic identifies the repository, path and repair action. A continuing
outage is notified once; recovery is logged and rearms notification. Healthy
repositories continue ([issue #13](https://github.com/mattpolicastro/nightshift/issues/13)).

New drafting, admission and spec-stage re-drafting hold with triggers, receipts
and jobs retained, without `needs-human` or an agent session. Repair the file at
the same path to retry on a later poll. Adding an omitted manifest entry or
changing configuration requires a daemon restart; there is no hot reload.
Malformed config TOML, invalid engines and empty or wrongly typed manifest
values remain configuration-loading errors.

Admitted runs continue publication and build-stage revisions with their copied
manifest and recorded branch, even if enrollment becomes unhealthy. Durable run
state counts as admission even while its notification is pending. Existing jobs
can finish and be collected; delivery needing an unavailable manifest-derived
source path retains the successful result until repair, without re-drafting.
Authorization, publication checks, pause and stopped-job holds still apply.

Worker-to-specloop recovery runs before new intake on unpaused startup and polls,
even with an unhealthy manifest. Orphaned `agent:working` issues with no claim or
surviving worktree return to `agent:ready` with a `needs_decision` outcome.
Surviving branches/worktrees are preserved for human inspection, with their
phase and location in an issue comment; `agent:working` becomes `needs-human`.
Recovery does not resume or delete that work. A durable journal lets interrupted
recovery finish after restart. Unreadable claims, unknown GitHub state or
conflicting human label changes hold new intake and report repository health;
eligible admitted publication/build revisions and other repositories continue.

### Pause, stop and the supervisor

`nightshift pause` creates `~/.nightshift/pause`, holding new daemon launches
and completion delivery, including publication. In-flight drafting, admission
and revision jobs can finish locally; an already-dispatched external operation
may finish too. `nightshift resume` removes the pause and delivers eligible
retained results without rerunning the job.

`nightshift jobs` lists local background attempts and history offline, without
a running daemon or valid repository configuration. Copy the exact full job ID
into `nightshift jobs stop <id>`; prefixes and paths are not accepted. Stop
verifies process-start identity, process group and job-directory ownership
before sending SIGTERM, then SIGKILL if needed, to that group. It records
`stopped` only after no executing group members remain; unverifiable identity
is refused ([issue #8](https://github.com/mattpolicastro/nightshift/issues/8),
[PR #28](https://github.com/mattpolicastro/nightshift/pull/28)).

Logs, requests, results and receipts remain on disk without automatic cleanup.
Stopped jobs stay held across polling, daemon restart, resume and label
removal/reapplication. Only explicit release can enable a replacement;
the stopped result remains ineligible for delivery.
Pause first, list jobs, stop the selected exact full ID and confirm `stopped`
in inventory. Inspect the log, request/result, stop record and parent receipt,
local checkout and admission/run state, and remote refs, PRs, comments and
labels. Then run `nightshift jobs release <id>` with that same full ID
([issue #34](https://github.com/mattpolicastro/nightshift/issues/34)). It works
offline without daemon, configuration or credentials, requiring durable stop
confirmation, matching process identity, an idle lifetime lock and a consistent
receipt. It removes only the matching `job` reference, preserving other fields
and evidence. Historical attempts remain stopped and their results stay
suppressed. A repeated release cannot detach a replacement attempt.

Release launches nothing and leaves pause unchanged. After safe resume, the
next poll uses normal eligibility: an open, armed, authorized item with
`needs-human` clear, applicable dependency and health blockers clear, and
approval-head checks satisfied. Existing PR/admission recovery precedes new
work; no fresh label event is required. See the
[release outcomes and recovery instructions](../../README.md#pause-and-job-control)
for success/no-op (exit 0), refusal (exit 1) and invalid syntax (exit 2).
There is no automatic retry or rollback of committed effects. A revision that
commits before saving its result can append a duplicate round on retry; release
does not fix that [crash boundary](../../KNOWN-LIMITATIONS.md#open-gaps).

Admission can finish while paused and commit runnable state. The independent
`specloop serve` supervisor can start or continue that build while the daemon
withholds delivery. Neither daemon pause nor job stop cancels admitted runs.
Control the supervisor and active run processes separately; unloading its
launchd job alone does not prove detached run processes exited.

### Touchpoint report

Run `uv run python -m nightshift.touchpoints` for a read-only local report.
Add `--since YYYY-MM-DD` to select changes drafted on or after that UTC date,
and `--json` for versioned JSON; either option can be used alone or together
([issue #50](https://github.com/mattpolicastro/nightshift/issues/50),
[PR #53](https://github.com/mattpolicastro/nightshift/pull/53)).

The seven counters cover drafter question rounds (not individual questions),
spec revisions, build revisions, human pauses, manual resumes, human accepts
and admission refusals. Pause and resume counts also break down by reason.
Totals sum category observations, not unique human actions: a question during
spec revision counts in both categories; a pause and its resume count separately.

Coverage is observed local history in `~/.nightshift` receipts/retained jobs
and `~/.specloop/runs` event logs. Missing or overwritten history can undercount.
Draft-date filtering uses approximate filesystem timestamps from the earliest
retained proposal request, falling back to proposal receipts. Unknown draft
dates are excluded with a diagnostic when `--since` is used. The merge boundary
is unknown; this is not a complete draft-to-merge audit. Read the warnings and
errors alongside totals: unreadable records leave healthy rows visible but
exit 1, with diagnostics in both table and JSON output.

### Tests and scenario evidence

Run `uv run pytest` for implementation acceptance. For parallel execution,
`uv run pytest -n 4` uses pytest-xdist with four workers
([PR #54](https://github.com/mattpolicastro/nightshift/pull/54)). When loaded
with `-p scenario_plugin` and its directory on `PYTHONPATH`, the existing
[scenario plugin](../../examples/specloop/scenario_plugin.py) forwards passed
scenario IDs through test report `user_properties` from workers to the
controller, which emits deduplicated `SCENARIO` lines. Failed tests contribute
no passed IDs. CI currently invokes `uv run pytest -q`, not four workers by
default, and ignores Markdown-only changes.

## Publication and base recovery

Base preparation implements [issue #46](https://github.com/mattpolicastro/nightshift/issues/46)
in [PR #59](https://github.com/mattpolicastro/nightshift/pull/59).
After tasks land, the driver's `finals` / `prepare_publish` fetches the
configured `pr_base` from the source repository's current `origin`. If that
base is not already incorporated, it creates a two-parent merge on the run
branch. Trusted `prepare` and `verify` steps check the integrated head before
final coverage and acceptance checks permit `ready_to_publish`. Pending
integration verification still runs when the base is already incorporated.

`specloop publish <change>` refetches once with a 20-second limit. If the base
moved or integration verification is outstanding, it saves `running` state
with no pause and returns a retryable error; nothing is published. Publish
does not merge or verify inline. `specloop serve` drives preparation; without
it, run `specloop run <change>`. Retry publication after the driver restores
`ready_to_publish`; the daemon retries on a later poll. A successful publish
updates the recorded branch and PR and pauses at `human_checks`. Complete the
human checks and hosted CI review before `specloop finish <change>` archives
the change and marks the PR ready. A human merges it.

Inspect `specloop status <change>` for the pause detail. Base preparation can
stop at three distinct pauses:

- **`base_conflict`:** the run head stays unchanged. The detail names the
  conflicting paths and base/head commits; inspect those paths and
  `final/base-sync/attempt-*/diagnostic.txt` under the run directory.
- **`base_drift`:** the merge remains on the run branch, but trusted preparation
  or verification failed. The detail names the base and retained head, failed
  checks, `receipts.json` and step logs in `final/base-sync/attempt-*/`.
- **`base_fetch`:** the driver cannot fetch the configured base. Inspect the
  fetch diagnostic and repair access to the source origin or the missing base
  branch, then run `specloop resume <change>` to fetch and prepare again.

For conflicts or drift, bring the run branch from its `repo.git` into the
source checkout's configured branch before committing a repair. Keep the
current run head in its ancestry; resolve conflicts against the configured
base or fix the failed checks. `specloop resume <change>` imports a repair
that fast-forwards the run head and restarts the driver. It re-verifies even
if the base has not moved or a manual merge already incorporated it. Resuming
without a repair retries the checks; it does not accept a failure. Old
receipts remain available, and publication waits for readiness.

A fetch failure during **publish** instead leaves readiness and saved checks
intact and returns a retryable error. Restore base access and retry
`specloop publish <change>`; the daemon can retry on a later poll. This does
not create a driver `base_fetch` pause or permit a push using a stale base.

## Paseo Factory section and `specloop act`

The Nightshift plugin now includes a **Factory** section for specloop runs,
with paused runs first, change, status, tasks done/total and PR links. Paused
rows use the same action headline and explanation as Slack, and offer exactly
the verbs listed in `specloop status --json`. Resume has an optional note
field (up to 2,000 characters); Accept is available for `stuck` and
`needs_human`, Publish for `ready_to_publish`, Finish for `human_checks`, and
Abort at every pause. Worker task rows remain read-only.

**Publish and Finish both push to GitHub, so Paseo asks for confirmation
before sending either action.** Publish pushes to the recorded PR or opens a
draft PR. After the human checks, Finish archives the change, pushes and
marks the PR ready for review; it does not merge. Cancel sends nothing.
The page shows each action's result and refreshes the list.

```sh
uv run specloop status --json
uv run specloop act <change> <verb> [--note TEXT] --json
uv run specloop act example-change resume --note "Use the revised acceptance criteria" --json
```

The verbs are `resume`, `accept`, `publish`, `finish` and `abort`. `act`
reloads the run under its lock and checks the current pause; unknown changes,
unavailable verbs, notes over 2,000 characters and busy locks are refused.
Its version-1 JSON outcome contains `version`, `outcome` (`done` or `refused`),
`verb` and `detail`, with exit status 0 for done and 1 for refused. Resume and
Accept behave as with `--no-run`; the separate `specloop serve` supervisor
continues execution. Direct CLI calls do not prompt: interfaces must obtain
confirmation before invoking Publish or Finish.

Factory refreshes every 60 seconds while visible and active, on opening and
on return to the foreground. Server reads are cached for 15 seconds; actions
invalidate the cache. A failed read retains the last good list marked stale
with an error. The server uses an argv array and checks the change and verb
against its last good status before calling `act`, which rechecks live state.
Only display fields cross to the client, with home directories replaced by
`~` and raw command errors excluded.

The supported range remains `>=0.10.1 <0.13.0`, built
against SDK 0.12.0. See the [plugin README](../../plugins/nightshift/README.md#factory)
for usage, refresh behavior and installation.

## How the engine works

`specloop/` (in this repo) drives an admitted OpenSpec change through its
`tasks.md`, one task at a time.

1. A fresh agent session from a named provider profile implements the task.
2. The harness seals its files into a commit on the change branch (the agent's
   own git is ignored).
3. Trusted manifest commands prepare dependencies if needed, then verify that
   commit offline under `sandbox-exec`.
4. A separate session reviews it under rules that limit what can block.
5. A failed review or verification feeds the next attempt; a pass ticks the
   task in the same atomic ref update.

It pauses only at defined checkpoints:
- phase gates (`[gates] review = "human"`, the default);
- a stuck, blocked or ambiguous task;
- a repeated violation;
- an infra or usage limit.

**Two of those can be handed to agents** (2026-10-20), and manifests going
forward should set both:
- **`[gates] review = "agent"`:** a fresh read-only reviewer judges a finished
  gated section as a whole. It checks integration between tasks,
  requirements no task delivered, and per-task notes that matter together.
  - Blocking findings need a `scenario:`, `requirement:`, `integration:` or
    real-deployment `input:` basis.
  - A PASS passes the gate.
  - A FAIL adds one round of fix tasks to the section, then a second review
    rules on the first's findings.
  - A second FAIL, or anything it says needs a human, is the usual gate
    pause, and `resume` passes it.
- **`[gates] stuck = "agent"`:** when a task exhausts its attempts, an arbiter
  (the optional `[roles] arbiter`, else the review chain) rules once:
  - **accept** the last candidate, only if it passed verification; the
    overruled findings go to the PR's follow-ups;
  - **retry** once more, with a note for the implementer;
  - **hand it to a human**: the usual stuck pause, carrying its reason.

  A second stuck always goes to a human.

With both set, a normal run's human steps are approving the spec, the PR's
human checks, merging, and real escalations.

**Calibration of the agent gate, 2026-10-20.** On `nightshift-on-specloop`
section 2, the gate reviewer (Codex, about 2 minutes) failed the section on
one blocking issue: a publication outage that became a permanent hold. An
independent review, run as a calibration (a Claude subagent with about four
times the effort, which also ran the tests), found that one and three more:
- a closed spec PR stranding its issue;
- comment edits that never notify;
- agent sessions blocking the daemon's poll loop.

All three were added to the section as fix tasks by hand. The prompt now
carries a tracing checklist: wiring from the real entry points,
stage hand-offs, lifecycle, and shared-process blocking.

**A/B test on the same head and profile:**
- with the checklist prompt, the gate review found 3 of the 4 known blocking
  issues and noted the fourth (comment edits) as non-blocking, in 3.4 minutes;
- it also found two new real ones: one stage's error blocks every later stage,
  and an interrupted admission is unrecoverable;
- those became tasks 2.10–2.11.

`[roles] gate` can name a different model. A Claude gate reviewer needs
`CLAUDE_CODE_OAUTH_TOKEN` in `~/.config/specloop/env`, which is your call.
Treat the agent gate as a net, not a proof, until it has been measured on
seeded integration defects.

```sh
uv run specloop admit <repo-manifest.toml> --change <change> --branch spec/<change> [--pr <url>]
uv run specloop serve                 # supervisor: drives every running change
uv run specloop run <change>          # or drive one by hand until the next pause
uv run specloop status [<change>]
uv run specloop pause <change>        # stop at the next task boundary (now, if nothing drives it)
uv run specloop resume <change> [--note "..."]
uv run specloop accept <change> [task]
uv run specloop waive <change> <task> <text>
uv run specloop publish <change>      # push to the recorded PR, or open a draft PR
uv run specloop revise <change> --feedback <file>  # queue a review round
uv run specloop finish <change>       # archive the change, mark the PR ready
uv run specloop propose <manifest> <change> --idea "..." [--source doc]   # draft a change
uv run specloop drill <change>        # seeded defects through the review gate
```

**`propose`** has an agent draft the OpenSpec change. The harness then:
1. validates and lints it;
2. runs the feasibility review;
3. runs an independent read-only spec critic after lint and feasibility pass;
4. feeds problems back within a default budget of four drafting rounds
   ([PR #37](https://github.com/mattpolicastro/nightshift/pull/37));
5. leaves the draft on a local `spec/<change>` branch for a human to approve.
   For daemon-managed issues, Nightshift pushes it and opens the spec review PR.

Size tasks by risk and dependencies: combine work touching the same file for
one concern within the single-session ceiling of about 300 changed lines.
Split when distinct concerns, dependencies or that ceiling require it.
Documentation-only changes update documentation without changing executable
behavior; use one or two tasks in one section within that ceiling, rather
than splitting by document heading. Most changes should use six tasks or
fewer and as few sections as dependencies allow. Six is a normal target,
not a hard rejection limit. Every task incurs a full implement, verify and
review cycle; every section incurs an evidence gate, so unnecessary splits
add overhead ([issue #75](https://github.com/mattpolicastro/nightshift/issues/75)).

**Work in flight.** Before drafting, the harness snapshots paths named in
`tasks.md` by other running or paused runs for the same source repository.
It excludes the current change and skips unreadable runs without stopping
the draft ([PR #36](https://github.com/mattpolicastro/nightshift/pull/36)).
This is a snapshot of planned work, not a file reservation or a complete list
of future edits. References such as `file.py:function` and
`file.py::function` normalize to `file.py`; source-root names such as
`README.md` count when they are existing files in the source checkout
([PR #60](https://github.com/mattpolicastro/nightshift/pull/60), implementing
[issue #58](https://github.com/mattpolicastro/nightshift/issues/58)).

The drafter sees the map and should avoid those files where possible, putting
new tests in new files. On the first drafting round, tasks that edit shared
test files get overlap feedback ([PR #36](https://github.com/mattpolicastro/nightshift/pull/36)).
Explicit hands-off clauses, such as “do not edit `tests/test_calc.py`”, do not
count as edits. Necessary overlap may remain as a merge-conflict risk rather
than an unconditional ban ([PR #37](https://github.com/mattpolicastro/nightshift/pull/37)).
Implementers do not receive the map: any necessary avoidance instruction must
name each file to leave untouched in the draft, for example “do not edit
`tests/test_calc.py`”, rather than referring to “files in the in-flight list”
([PR #43](https://github.com/mattpolicastro/nightshift/pull/43)).

**Feasible tasks.** Human-only checks belong in `design.md`, where they become
the PR's human checklist; every task in `tasks.md` goes to an implementing
agent ([PR #30](https://github.com/mattpolicastro/nightshift/pull/30)).
Lint catches common human-task forms such as “Manually …” or “The operator …”.
Feasibility also rejects named-person checks such as “Matt checks …” and tasks
that are impossible under the allowed paths, frozen files or trusted verify
commands. Preserve existing `openspec/specs/` guarantees: feasibility rejects
a change that removes, hides or breaks one without a justified `MODIFIED` or
`REMOVED` delta for that requirement
([PR #36](https://github.com/mattpolicastro/nightshift/pull/36)).

**Spec critic.** Proposals and spec revisions receive a separate read-only
review after lint and feasibility pass
([issue #51](https://github.com/mattpolicastro/nightshift/issues/51),
[PR #57](https://github.com/mattpolicastro/nightshift/pull/57)). The critic
receives the full in-flight file map, including production and test paths,
and changed files from open PRs. These are snapshots, not proof of another
change's future behavior. Unavailable or partial PR context stays visible as
a risk; missing context does not establish safety.

The checklist covers all eight items
([PR #57](https://github.com/mattpolicastro/nightshift/pull/57), expanded by
[PR #62](https://github.com/mattpolicastro/nightshift/pull/62)):

1. Over-engineering.
2. File collisions and hidden instructions.
3. Invalidated protected assertions.
4. Conflicts with unowned behavior.
5. Tests broken by pending changes.
6. Unplanned migrations.
7. Retry and idempotency gaps.
8. Stale operator docs and manual steps.

Blocking findings get at most one correction within the remaining drafting
budget, followed by lint, feasibility and critic review again. Remaining
blockers are contradictions naming the task, problem and evidence; exhausted
budgets and unchanged corrections leave them unresolved. Non-blocking notes
appear as `Critic: ` risks. Overlap alone is not a blocker: the critic must
explain why the plan is unsafe or unworkable. A failed critic review stops the
draft with an error; it is not a pass
([PR #57](https://github.com/mattpolicastro/nightshift/pull/57)).

**`drill`** plants the manifest's declared one-line defects into landed
candidates. A drill counts only if the defect survives verification, and is
caught only if a fresh reviewer rejects it.

**Feasibility role.** `[roles] feasibility` may name a stronger profile than
`review`. A refusal gate is only as good as its model: a local model refused a
feasible draft that it had passed minutes earlier.

The supervisor runs as the launchd agent `com.mattpolicastro.specloop`, logging
to `~/.logs/specloop.log`.

**Admission** runs a feasibility review, which refuses tasks that are
impossible as written under the frozen paths and verify commands.

### Section evidence

With `[gates] evidence = true`, section boundaries check scenario coverage and
mutate added lines under `mutation_paths`. A pass notifies and continues. A
coverage or measured-score shortfall adds strengthen-tests tasks; a second
shortfall pauses. An unchanged score is called out in the pause.
`mutation_steps` must include a step that runs the candidate's own tests:
trusted checks alone can leave strengthening tasks unable to move the score.

Full unmutated baselines run first and must pass before mutants execute. Each
mutant has an isolated source copy and build directory. The default is
`mutation_parallel = 2`; set `mutation_parallel = 1` for serial execution
([issue #4](https://github.com/mattpolicastro/nightshift/issues/4),
[PR #27](https://github.com/mattpolicastro/nightshift/pull/27)).

**Routed commands.** Routing addresses
[issue #73](https://github.com/mattpolicastro/nightshift/issues/73).
The [self-hosting manifest](../../examples/specloop/self-hosting.toml)
routes `specloop/**` and `nightshift/**` to `python-mutation`. Copy the
[selector](../../examples/specloop/select_tests.py) into the trusted `{checks}`
directory beside `scenario_plugin.py` and `negative_control.py`, then adjust
the manifest's paths. Its `[[mutation_step]]` uses that trusted selector and
keeps the existing interpreter, environment and pytest options. Normal
verification, negative-control and coverage commands stay intact.
Mutation-only steps use the verify-step shape (`id`, argv `run`, `expect`
defaulting to `0`, and `timeout` defaulting to `300` seconds); they do not join
normal verification, preparation, acceptance or coverage.
Give mutation-only steps IDs distinct from verify, prepare and acceptance IDs.
Route `paths` and `steps` are nonempty arrays; each step ID must be defined
and may occur only once in that route.

Routes are ordered: the first matching table wins, with steps in listed order.
Patterns match repository-relative POSIX paths using case-sensitive
`fnmatchcase`: `*` crosses `/`, `?` and character classes work, and `**` has no
extra directory semantics. Absolute paths, backslashes and `..` components are
invalid. These rules do not change `mutation_paths` or allowed/frozen paths.
Unmatched files use the required nonempty `mutation_steps` fallback, in verify
declaration order followed by mutation-only declaration order. With no routes,
existing fallback behavior remains.

For a manifest that already defines a separate `plugin-tests` suite and its
preparation, the route tables can be:

```toml
[[gates.mutation_route]]
paths = ["specloop/**", "nightshift/**"]
steps = ["python-mutation"]

[[gates.mutation_route]]
paths = ["plugins/nightshift/**"]
steps = ["plugin-tests"]
```

`plugin-tests` must name a separately defined `[[verify]]` or `[[mutation_step]]`
that runs the full plugin suite; a route does not define a command. Keep the
combined verification command as the fallback in a combined Python/plugin
manifest, and include plugin paths in `mutation_paths` if they are eligible.
The shipped Python-only example keeps `unit-tests` as its fallback and adds no
plugin installation pipeline. Review route boundaries: full confirmation only
protects the suites selected by that route.

Each distinct ordered step sequence used by selected mutants gets one full
baseline, including a used fallback. Identical sequences share a baseline;
reversed sequences do not. Unused routes get none. Baselines run serially in
first-use order, with ordinary preparation and manifest timeouts; any failure
stops all mutants as invalid. Each mutant prepare or selected step's timeout
comes from its own full baseline: the lesser of its manifest timeout and
`max(30 seconds, 3 * baseline step seconds)`. A missing or zero duration uses
the manifest timeout. Both phases reuse these limits; neither narrowed timings
nor queue waiting reduces confirmation budgets. One worker slot holds a mutant
through narrowing and confirmation.

**Selection and confirmation.** `{mutant_path}` is the repository-relative
mutated path during narrowing and an empty argv value for baselines, ordinary
verification and full confirmation. It can appear in selected verify or
mutation-only commands. Do not configure a nonempty override. Pass it as a
separate argv element, including the empty string; commands must interpret
empty input as the full suite, never zero tests.
The example's Python argv adapter preserves spaces, prefixes selected paths
with `{src}`, and substitutes `{src}/tests` when selection is empty. It
propagates pytest's exit status. Empty or unresolvable input, no matches, a
missing test tree, unsupported or escaping path, or any scan read/parse failure
therefore runs all tests.

The selector parses direct Python imports without executing repository code.
It scans `tests/**/test_*.py` and `tests/**/*_test.py`, including imports inside
functions. It supports repository-root modules/packages, aliases and resolvable
relative imports; package initializers include imports of descendants.
It does not chase transitive or dynamic imports or guess arbitrary
`src/` layouts. A narrowed pass reruns the entire selected sequence with empty
`{mutant_path}`, the same mutation and a freshly prepared build. Only this
full confirmation can establish survival. Confirmation reruns even steps
without the placeholder. In either phase, expected exits pass; the first
unexpected test exit, including signal termination, kills and stops the
sequence. Narrowed timeout or invalid results trigger no confirmation.
Sequences without the placeholder run once in full mode. Scoring and
classifications stay unchanged.

Inspect `mutation-runs.json` at the gate output root for each mutant's route
index (one-based, or null fallback), ordered steps, baseline, result and
`decided_by` (`full`, `narrowed`, `confirmation`, `precheck` or `baseline`).
Entries remain in mutant selection order. Their relative receipt links identify
the deciding run; step receipts supply expanded argv, expected/actual exits,
durations and diagnostics. Initial receipts remain in
`mutant-N/receipts.json`; confirmation uses
`mutant-N/confirmation/receipts.json`. The first baseline uses the gate root's
`receipts.json`; additional baselines use `baseline-2/receipts.json`, etc.
`precheck` decisions (equivalents or source mismatches) and `baseline` failures
have no mutant runs. The index identifies the failed baseline on abort.
Source/build trees are cleaned after use; receipts and artifacts remain.
Existing evidence JSON and step receipts retain their shape.

After merge, Matt copies the reviewed selector to his trusted checks directory
and updates his private manifest with Python and plugin routes, retaining the
combined verify step and a full fallback. This human rollout remains pending:
check actual language boundaries and verify that empty selector input runs
each complete routed suite. Compare the next few real section gates
with the approximately 14-minute baseline using the existing timing report;
record mutant counts, route mix, narrowed kills and confirmations alongside
timings. Extra baselines, survivors and machine load affect the result. This
does not claim a measured speed-up, promise a fixed percentage improvement or
add a timing-report command; CI cannot prove the real-gate speed-up.

Only `killed` and `survived` results enter the score:
`killed / (killed + survived)`. A selected-test timeout is `timeout`, excluded
from both numerator and denominator. Preparation or steps named `configure`
or `build` failing or timing out produce `invalid`, not a kill or test timeout.
For example, two killed, one survived and three timed out score `2/3`, not
`5/6` or `2/6` ([issue #31](https://github.com/mattpolicastro/nightshift/issues/31),
[PR #44](https://github.com/mattpolicastro/nightshift/pull/44)).

**Exclusions and measurement.** Full committed source protects Python
docstrings and comments, including inline comments, in `.py` and `.pyi` files;
adjacent executable code stays eligible. Other languages have limited
line-based filtering: comment prefixes (`#`, `//`, `/*`, `*`) and complete
`/* ... */` blocks whose opening and closing lines contain only comments.
This is not general string or inline-comment parsing. Arbitrary runtime
strings and JSX styles are not automatically excluded
([issue #21](https://github.com/mattpolicastro/nightshift/issues/21),
[PR #48](https://github.com/mattpolicastro/nightshift/pull/48)).

Python docstrings need no `mutation-equivalent:` annotation; the touchpoint
report's docstring annotation was removed in
[PR #63](https://github.com/mattpolicastro/nightshift/pull/63).

`mutation_skip` contains case-sensitive literal substrings, not regexes or
globs. A match anywhere on the whole line excludes every candidate on that
line before sampling, without consuming the mutation budget
([PR #48](https://github.com/mattpolicastro/nightshift/pull/48)). For example:

```toml
[gates]
mutation_parallel = 2
mutation_skip = ["fontWeight", "# cosmetic"]
```

This skips `fontWeight = 600` and `padding = 12  # cosmetic`, but keeps
`FontWeight = 600` eligible. Use narrow patterns to avoid hiding behavior.

Generated mutants require at least one killed or survived result to establish
measured evidence. If all are `equivalent`, `invalid` or `timeout`, evidence
is unmeasured: the run pauses at `gate_evidence` without adding strengthening
tasks. Review equivalence annotations, `mutation_steps` and preparation.
An all-equivalent set is not a successful measurement. A `mutation-equivalent:`
annotation on the same or preceding line is a claim judged by the reviewer;
equivalent results do not enter the score. If exclusions leave no mutable
lines, mutation is instead skipped with no score; scenario coverage checks
still apply ([PR #48](https://github.com/mattpolicastro/nightshift/pull/48)).

**Python guards.** Two operators add candidates for eligible `.py` / `.pyi`
guards ([issue #33](https://github.com/mattpolicastro/nightshift/issues/33),
[PR #64](https://github.com/mattpolicastro/nightshift/pull/64)):

```python
def choose(blocked, fallback):
    if blocked:          # negate-condition: if not blocked:
        return None      # drop-return: pass
    return fallback

while pending:           # negate-condition: while not pending:
    pending = advance()
```

`negate-condition` requires a bare-name `if` or `while` test on a single-line
header with a separate body. It excludes `elif`, compound expressions,
already-negated tests, inline suites and multiline headers. `drop-return`
replaces a standalone, single-line branch-ending return (bare or valued) with
`pass` only when its owning `if` / `elif` / `else` conditional has a following
statement in the same enclosing suite. A final function return, a nested
conditional without its own continuation, inline/semicolon returns and
multiline returns do not qualify. Indentation and trailing comments remain.
Both operators retain added-line/path selection, prose/comment protection,
`mutation_skip`, equivalence handling, seeded sampling and one mutant per line;
eligibility does not guarantee selection.

**Usage limits** move implementation, candidate review or section-gate review
to the first usable profile in the role's configured fallback chain.
Recognized authentication or inactive/expired/cancelled plan failures do so
immediately; ordinary infrastructure failures, including offline local
backends, retry the same profile until failures exceed `max_infra`. Retry
budgets belong to each task/profile pair, shared across roles; a new fallback
gets its own allowance. An unavailable profile has a 15-minute cooldown,
shared across roles and retained across restarts. At expiry it becomes eligible
in the original chain order with a fresh retry allowance. The same task,
pending candidate or gate continues without charging a coding attempt; an
`unavailable` event records the diagnostic and an FYI names the successor.
Provider recovery pauses only when every profile in that role's chain is
blocked, with its earliest deadline as the retry time. Quota-only chains use
`usage_limit`, with uncapped automatic resumes by `serve`; any unavailable
profile, including a mix with quota blocks, uses `infra`, still limited to two
automatic resumes per task. Cooldown expiry alone cannot lift a pause once
that allowance is spent. A manual resume can retry a single unavailable
profile early; multi-profile chains retain their deadlines, and quota
deadlines and the automatic-resume cap remain unchanged. This recovery applies
to admitted build work; drafting, feasibility, the spec critic and the one-shot
arbiter keep their existing behavior
([issue #72](https://github.com/mattpolicastro/nightshift/issues/72)).

**Providers are pluggable.** Two adapters: `codex` (`codex exec`) and `claude`
(`claude -p`). Each can use its CLI's subscription login or a local
`base_url`.

Spike S1 probed, from the harness itself, that each session can write only its
workspace and has no network. The claude adapter with `qwen3-coder-next` on the
a local inference box is proven. The codex adapter against a local backend is untested.

**Run state** lives in `~/.specloop/runs/<change>/`:
- `state.json` and `events.jsonl`;
- a bare `repo.git` that the harness owns and commits into;
- per-attempt transcripts, seal, receipts and review decisions;
- `followups.md`, the non-blocking review findings.

The source repository's checkouts are never written to.

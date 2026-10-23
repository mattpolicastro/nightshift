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

Use one existing trusted manifest per repository. Relative manifest paths
resolve beside `config.toml`; `~` expands. Set its `[run]` source `repo`,
`github` and `pr_base` to match the repository, and supply provider profiles,
roles, allowed/frozen paths and verification commands. Template `change` and
`branch` values are overridden per issue and saved in the admitted run's
manifest. The worker's `verify` setting is not needed for specloop.
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

Draft questions or rejection remove both issue intake labels. Answer the
questions or resolve the rejection on the issue, remove `needs-human`, and
reapply `spec:draft` or `agent:ready` to retry. Removing `needs-human` alone
does not requeue the issue.

For issue 7, "Add a gate", in `owner/sample`, the change is
`sample-issue-7-add-a-gate`, on `spec/sample-issue-7-add-a-gate`. The draft PR
is titled `Spec: Add a gate`, links the issue with `Closes #7`, and carries
the feasibility risks. Successful admission removes `spec:review` and
`spec:approved`. Refusal removes `spec:approved` and adds `needs-human`;
remove `needs-human` and reapply `spec:approved` after resolving the refusal.
Revision uses only feedback newer than the branch tip, including inline file
and line references. Empty revision requests are explained and cleared without
running an agent.

The separate `specloop serve` supervisor executes admitted tasks and prepares
the current base before `ready_to_publish`. The daemon then attempts
[publication](#publication-and-base-recovery) to the same branch and PR,
commenting the remaining human checks and hosted CI reminder after success.
A human reviews, checks and merges; merging closes the linked issue.
`nightshift attention` lists human
pauses with reason, change and PR link; failed status reads retain the previous
snapshot marked `STALE` and exit 1. JSON carries `specloop` beside worker
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
existing behavior; retiring it is a later change. While `~/.nightshift/pause`
exists, there is no intake, approval, revision
or publication; in-flight drafting/admission jobs may finish but delivery
waits. Already-admitted runs continue under the separate specloop supervisor;
unload its launchd job to stop that service.

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
3. feeds both kinds of problem back for up to three rounds;
4. leaves the draft on a local `spec/<change>` branch for a human to approve.
   For daemon-managed issues, Nightshift pushes it and opens the spec review PR.

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

**Section boundaries** gather evidence: coverage of the section's scenarios,
and a mutation check of the code it added. A pass notifies and continues; a
shortfall adds strengthen-tests tasks to the section; a second shortfall
pauses. **`mutation_steps` must include a step that runs the candidate's own
tests.** Otherwise only trusted checks can kill a mutant, and strengthening
tasks cannot move the score: one run's section went from 1/8 to 8/8 once its
package tests were added. A second shortfall with an unchanged score says
so in the pause.

**Usage limits** move a role to the next profile in its fallback chain, or
pause with a resume time that `serve` honours.

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

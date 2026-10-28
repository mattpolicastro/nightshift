# nightshift

[![ci](https://github.com/mattpolicastro/nightshift/actions/workflows/ci.yml/badge.svg)](https://github.com/mattpolicastro/nightshift/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Nightshift turns OpenSpec changes into reviewed, verified pull requests,
running unattended on a machine you own. **Merge is always human.**

You describe a change in an issue. Nightshift drafts it into an
[OpenSpec](https://github.com/Fission-AI/OpenSpec) change and opens a draft PR
for you to review. When you label that PR `spec:approved`, its build engine,
**specloop**, works through the change's tasks one at a time:

- a fresh agent implements the task;
- the harness, not the agent, commits it;
- trusted commands verify it in a sandbox with no network;
- a separate agent reviews it against the spec;
- failures retry with the findings.

When enabled, [section evidence gates](docs/specloop/README.md#section-evidence)
check scenario coverage and score killed/survived mutants. Generated mutants
with no judged result pause; no mutable lines skips mutation but retains
coverage checks ([PR #48](https://github.com/mattpolicastro/nightshift/pull/48)).
The result lands on the same PR, with a checklist of what only a human can check.

Specloop is the sole engine and the default when `engine` is omitted. Explicit
`engine = "worker"` is refused before dispatch; enroll with a trusted manifest
([issue #16](https://github.com/mattpolicastro/nightshift/issues/16)).

Nightshift runs on macOS under launchd.

- **The retired worker engine** drove 79 tasks across two real repositories by
  September 2026: 66 merged, 13 escalated.
- **specloop** has since built several multi-section changes end to end,
  including parts of Nightshift itself.

[docs/specloop/README.md](docs/specloop/README.md) explains how the engine works,
and [KNOWN-LIMITATIONS.md](KNOWN-LIMITATIONS.md) lists what isn't done.

## The idea

Most attempts at autonomous coding agents fail on the same question: *how do
you know the work is any good?* Nightshift's answer is that the agent must not
be able to grade its own homework, and that this has to be enforced by
infrastructure rather than by instructions.

Three things follow, and they are the whole design:

**CI is a held-out verifier.** Every enrolled repo has a CI workflow that runs
on pull requests. The GitHub token is a fine-grained PAT deliberately issued
*without* the Workflows permission, so an agent physically cannot modify the
checks that judge its work — the guarantee rests on the credential, not on the
agent's good behaviour. A task that genuinely needs a CI change fails its push
and escalates, which is correct.

**Review is a separate session.** Specloop gives an independent reviewer the
spec and sealed diff. Trusted verification runs outside the implementing agent.
**CI cannot catch test weakening when the weakened test is what CI runs.**

**Escalation is a success.** An agent that decides its issue is wrong, or
under-specified, or contradicted by the repo's own documentation, is expected
to stop, write up why, and label the issue for a human. Of the first six
escalations, four found defects in the *issue* rather than in the code. A
system that cannot refuse is a system that will confidently do the wrong thing.

## How a specloop change flows

For an enrolled specloop repository, the daemon drives the specloop CLI;
the separate `nightshift serve` supervisor executes admitted tasks.

| Label | Where and stage | Effect |
| --- | --- | --- |
| `spec:draft` | Issue, intake | Draft an OpenSpec change from the title, body and comments. |
| `agent:ready` | Issue, intake | Alias for `spec:draft` on a specloop repository. |
| `spec:review` | Draft PR, spec review | Added by the daemon when the draft is ready for human review. |
| `spec:approved` | PR, approval | Added by a human to request admission and start the build. |
| `agent:revise` | PR, before admission | Re-draft the spec with new review feedback, continuing the existing branch. |
| `agent:revise` | PR, after admission | Append a review round of tasks from new feedback and resume the run. |
| `needs-human` | Issue or PR, intervention | Questions or refusals are explained in a comment; resolve them, remove this label, and reapply the stage's trigger label as described below. |

Create these labels in a repository before enrolling it with
`engine = "specloop"`: the daemon adds and removes them but never creates them, and
adding a missing label fails.

An issue-body `blocked-by: #9, #12` line defers new drafts while any blocker
is open or unreadable, leaving the trigger in place. Existing work can still
be collected and delivered under normal eligibility rules
([issue #24](https://github.com/mattpolicastro/nightshift/issues/24),
[PR #29](https://github.com/mattpolicastro/nightshift/pull/29)).

The latest application of each intake, approval or revision trigger must come
from an OWNER, MEMBER or COLLABORATOR; both intake aliases require authorization
if both remain. After rejection, a trusted actor must remove and reapply the
label, or remove an unauthorized intake alias. Feedback trust is independent
of trigger authorization ([issue #5](https://github.com/mattpolicastro/nightshift/issues/5),
[PR #42](https://github.com/mattpolicastro/nightshift/pull/42)).

Long issue threads go into durable UTF-8 files passed with `--idea-file`,
keeping idea text out of argv
([issue #12](https://github.com/mattpolicastro/nightshift/issues/12),
[PR #35](https://github.com/mattpolicastro/nightshift/pull/35)).
`nightshift status --json` preserves healthy runs alongside non-actionable
`status_error` rows in version-1 JSON with exit 0. Top-level failures exit 1;
per-run errors refresh alongside healthy attention entries, while a failed
top-level status read retains a `STALE` snapshot. See the
[front-door guide](docs/specloop/README.md#github-front-door-nightshift--specloop)
for details ([issue #10](https://github.com/mattpolicastro/nightshift/issues/10),
[PR #32](https://github.com/mattpolicastro/nightshift/pull/32)).

For example, issue 7, "Add a gate", in `owner/sample` produces change
`owner__sample-issue-7-add-a-gate` on `spec/owner__sample-issue-7-add-a-gate`. The daemon
opens a draft PR titled `Spec: Add a gate`, links it back to the issue, includes
`Closes #7` and feasibility risks in the PR body, and removes the intake labels.
Questions or rejected drafts receive a comment and `needs-human` instead,
and both intake labels are removed. To retry, answer the questions or resolve
the rejection on the issue, remove `needs-human`, and reapply `spec:draft` or
`agent:ready`. Removing `needs-human` alone does not requeue the issue.

New names preserve owner/repository spelling, including safe dots, underscores
and hyphens. Unsafe spellings such as `owner/widget..js` are unsupported:
repository health holds new drafting, admission and spec re-drafting, without
encoding names or relaxing branch checks. Recorded legacy identities remain
authoritative, including after title edits; existing safe branches stay usable.
See [naming and migration limits](KNOWN-LIMITATIONS.md#open-gaps).

After spec review, apply `spec:approved` to the PR. The daemon admits the
reviewed tip with `--pr <url>`, records that PR, comments that the build has
started, and removes `spec:approved` and `spec:review`. If admission is refused,
it comments the reasons, adds `needs-human`, and removes `spec:approved`;
after resolving the problem, remove `needs-human` and reapply `spec:approved`.
An approval covers the PR head it was applied to: if the spec branch moves
afterwards (a revision lands, or someone pushes), the daemon removes
`spec:approved` and asks for a fresh review instead of building it.

The harness seals, verifies and independently reviews each task. Before
`ready_to_publish`, the driver fetches the configured base, merges it when
needed and runs trusted preparation and verification. Publish refetches once
with a 20-second limit; a moved base or pending integration checks return the
run to `running` with no pause and a retryable error. `nightshift serve` drives
preparation, then the daemon retries publication to the **same branch and PR**.
It comments the remaining human checks and hosted CI reminder after success.
Review the code and complete those checks before merging; merging closes the
linked issue. See [publication and base recovery](docs/specloop/README.md#publication-and-base-recovery)
for `base_conflict`, `base_drift`, driver `base_fetch` and retryable publish
fetch errors ([PR #59](https://github.com/mattpolicastro/nightshift/pull/59)).

To request changes at either stage, leave PR conversation, review or inline
comments, then have a trusted actor apply `agent:revise` to the PR. The daemon
selects trusted, actionable feedback by GitHub ID and tracks consumed IDs per
repository and PR, separately for conversation comments, reviews and inline
comments. Eligibility does not depend on the branch tip's time; inline feedback
retains its file and line. Before admission, the spec gets another commit;
after admission, each feedback block becomes a task in `## N. Review round K`
([issue #6](https://github.com/mattpolicastro/nightshift/issues/6),
[PR #61](https://github.com/mattpolicastro/nightshift/pull/61)).

Each round saves its selected IDs and feedback before starting. Those IDs are
consumed after a successful spec push or a durable, successful build revision result
with a landed commit, before notifications and without waiting for build
publication. Comments posted during a revision remain available for the next
authorized request, even if they predate the newly published tip. Reapplying
`agent:revise` does not replay a successful round, including before publication
or after a daemon restart. Editing a consumed comment or review does not make
it eligible again: post a new comment, which has a new ID, to request further
changes. Edits after selection also do not alter that round's saved feedback.
The corresponding label application is removed after success. If no eligible
unconsumed feedback remains, the daemon explains this and removes the label
without running an agent ([PR #61](https://github.com/mattpolicastro/nightshift/pull/61)).

New build snapshots also save a UUID `round_id` before dispatch. Ordinary
failed/crashed attempts detach and report repository health as before; a later
eligible dispatch uses a new job ID and deadline with the same key and saved
feedback. A matching retry returns the original round commit, even after the
branch advances, without appending tasks again. Changed feedback under that
key is refused. Keyed results and completion comments report state observed
when acknowledged, preserving later paused, aborted or finished runs; they do
not promise the supervisor has stayed in that state. For local retries, retain
the same optional `--round-id` and feedback file; see the
[keyed CLI contract](docs/specloop/command-json.md#keyed-build-revisions)
([issue #7](https://github.com/mattpolicastro/nightshift/issues/7)).
Legacy/unkeyed revisions have no retrospective deduplication guarantee.
Explicit stopped-job release instead permits selection of a new round after
inspection, as described in [job recovery](#pause-and-job-control).

Existing revision receipts without consumed IDs are initialized automatically
after authorization. Trusted IDs created at or before the receipt's saved time
are treated as consumed, using comment creation times and review submission
times, not edit times; this includes blank and daemon-prefixed trusted records.
If no saved time was recorded, the current PR head's committer time supplies
that cutoff once. This compatibility baseline approximates previously handled
feedback; it is saved before further processing and is never recomputed on a
tip advance or restart. Later IDs remain eligible. No manual initialization is
needed. A PR with no revision receipt starts with no consumed IDs, so eligible
historical feedback is available too
([PR #61](https://github.com/mattpolicastro/nightshift/pull/61)).

### Enroll a repository

Create one trusted specloop manifest for the repository, then add a block to
your local `config.toml` (or update its existing block):

```toml
[[repos]]
name = "owner/sample"
engine = "specloop"
manifest = "/absolute/path/to/sample.toml"
base = "main"
```

`manifest` must name a readable regular file; relative paths resolve beside
`config.toml`, and `~` is expanded. Its `[run]` table supplies the local source
`repo`, `github` repository and `pr_base` (keep these aligned with the repo
block). Template `run.change` and `run.branch` are optional because intake
supplies both per change. The template also defines provider
profiles and roles, allowed and frozen paths, and trusted verification commands.
Admission saves the effective manifest with the run. Omission of `engine` selects
the same engine too; explicit `worker` fails configuration loading. Worker `verify`,
model/endpoint tables, concurrency, task caps and phase budgets are inert, with
sorted key-only ignored-setting notes. They do not configure manifests or
providers. See [manifest templates and CLI outcomes](docs/specloop/command-json.md).

Each unpaused poll checks regular-file status and validates the template before
new work. Symlinks to regular files work; directories, FIFOs and devices are
rejected before reading. Missing, unreadable or invalid templates, including an
omitted manifest path, report repository-local health: healthy repositories
continue. New drafting, admission and spec-stage re-drafting hold with triggers,
receipts and jobs retained, without `needs-human` or an agent session.
Repair the file at the same path to retry on a later poll. Adding or changing
a config entry requires a daemon restart; malformed config TOML, invalid engines
and empty or wrongly typed manifest values still fail configuration loading.

Admitted runs continue publication and build-stage revisions using their copied
manifest and recorded branch. Existing jobs can finish and be collected; delivery
needing the damaged template waits with its successful result retained. Pause
and stopped-job holds still apply. See [enrollment recovery](docs/specloop/README.md#enrollment-recovery)
([issue #13](https://github.com/mattpolicastro/nightshift/issues/13)).

On unpaused startup and polls after a worker-to-specloop switch, orphaned
`agent:working` issues with no claim or surviving worktree return to `agent:ready`,
with a `needs_decision` recovery outcome. Surviving worker branches/worktrees are
preserved for human inspection: an issue comment gives the phase and location,
and `agent:working` becomes `needs-human`. Recovery does not resume or delete that
work, and runs even when the enrollment manifest is unhealthy.

Make `specloop` available on the daemon's PATH and run the separate supervisor
(`uv run nightshift serve`, or its existing launchd job) to drive admitted runs.
For an alternate layout, use the [host path setup](#host-path-setup); the
bundled service wrappers retain fixed bootstrap paths.
Worker quotas and endpoint/model settings do not provide specloop quota pacing;
the build engine uses its manifest's provider profiles.

The daemon's kill switch is `~/.nightshift/pause`: it holds new work and
completion delivery. Running drafting, admission and revision jobs may finish
locally. Use `nightshift jobs` and `nightshift jobs stop <id>` to select and
terminate a tracked background job. An admission that has already committed
runnable state may still start or continue building under the independent
`nightshift serve` supervisor. See [pause and job control](#pause-and-job-control)
for these boundaries and recovery guidance
([issue #8](https://github.com/mattpolicastro/nightshift/issues/8),
[PR #28](https://github.com/mattpolicastro/nightshift/pull/28)).

The [operations guide](docs/specloop/README.md#operations) also covers the local
touchpoint report (`uv run python -m nightshift.touchpoints`, optionally with
`--since YYYY-MM-DD` and `--json`) and its counting limits
([PR #53](https://github.com/mattpolicastro/nightshift/pull/53)). It explains
parallel tests with `uv run pytest -n 4` and scenario reporting under xdist
([PR #54](https://github.com/mattpolicastro/nightshift/pull/54)); `uv run pytest`
remains the implementation acceptance command.

### Build timing report

Read-only build timing and recorded profile outcomes come from local
`~/.nightshift/runs/*/events.jsonl` history; no external services are required:

```sh
uv run python -m nightshift.timings
uv run python -m nightshift.timings --since 2026-10-23 --change NAME --json
```

`--since YYYY-MM-DD` includes whole runs first admitted on or after midnight
UTC on that date; runs without admission are excluded. `--change NAME`
matches an exact change name and combines with `--since`. `--json` emits
the same measurements as JSON instead of the text table.

These are local-history estimates: missing pairs are dropped, evidence/slot
waits are endpoint proxies, and intervals may overlap. Recorded FAIL rates
can differ from harness decisions; finish is not merge. The six phases need
not sum to build hours. Unreadable runs are reported and skipped.

### Prepare dependencies before verification

Put optional `[[prepare]]` steps in the **specloop manifest**, not in
`config.toml`. They share the `[[verify]]` fields: `id`, argv-list `run`,
`expect` (default `0`) and `timeout` in seconds (default `300`). For a pnpm
project whose install needs a writable source copy:

```toml
[vars]
node_bin = "~/n/bin"

[sandbox]
prepare_read = ["~/n"]   # the node toolchain

[[prepare]]
id = "dependencies"
run = ["/bin/sh", "-c", "export PATH=\"{node_bin}:$PATH\" && cp -R \"{src}/.\" \"{build}/project\" && chmod -R u+w \"{build}/project\" && cd \"{build}/project\" && pnpm install --frozen-lockfile"]
timeout = 600

[[verify]]
id = "tests"
run = ["/usr/bin/env", "PATH={node_bin}:/usr/bin:/bin", "pnpm", "--dir", "{build}/project", "test"]
```

Steps use `NIGHTSHIFT_BIN_DIR` (default `/opt/homebrew/bin`) followed by
`/usr/bin:/bin:/usr/sbin:/sbin`, without appending the inherited PATH. Set it in
the Python process environment; see [host path setup](#host-path-setup).
`NIGHTSHIFT_NODE_BIN` affects plugin Factory only. Tools elsewhere, such as
`node` and `pnpm` under `~/n/bin` (from the `n` version manager), can use a
manifest variable and explicit `PATH`, as above; pnpm's `#!/usr/bin/env node`
line needs `node` on it too. PATH selection grants no sandbox read access:
home-local tools still need manifest variables or `[sandbox].prepare_read`,
and credential denials remain. With `n`, `~/n/bin/pnpm` is a Corepack shim that downloads pnpm on first use; prepare
steps have network and share the build's `HOME` with verify steps, so running pnpm in a prepare step
leaves it cached for the offline verify steps.

After candidate extraction, prepare steps run in declaration order with
`{build}` as their working directory. Their own sandbox allows network access,
confines writes to the build directory and denies credential-directory reads;
the extracted `{src}` remains read-only. Verify steps then run offline.
Each prepare step has a receipt and log alongside verification; an unexpected
exit or timeout fails verification and stops subsequent steps. Commands are
trusted manifest content; use the candidate's lockfile and freeze it when the
dependency contract requires that. See [prepare details](docs/specloop/prepare.md).

## How a worker task flows

Historical only: the worker executor and its resume/ship path are retired.
See [the current flow](#how-a-specloop-change-flows) and
[retirement rollout](docs/specloop/README.md#retirement-rollout).

## Quickstart

Create the repository labels, choose a trusted manifest and configure its
providers using [enrollment](#enroll-a-repository), then run directly:

```sh
uv sync
cp config.example.toml config.toml   # edit repositories, manifests and alerting
uv run nightshift preflight
uv run nightshift daemon run --once         # one poll; detached jobs may still be running
uv run nightshift serve                # independent supervisor, in another terminal
```

Python preflight checks scoped `GH_TOKEN`, enrolled-repository reachability,
alerting and the `forbidden_probe` negative control. A token that reaches an
enrolled repo proves nothing about what else it can reach. `--deep` remains
accepted with a retired-probe notice; it launches no model/tool-loop probe.
Direct Python operation needs no worker OAuth, endpoint models or worker
verification string. Manifest providers and verification retain their own
requirements; [bundled service startup](#enabling-the-daemon) has extra guards.

## Documentation

| file | what's in it |
| --- | --- |
| `README.md` | this: setup, operation, and the design decisions behind each knob |
| `docs/specloop/README.md` | how the specloop engine works: the front door, the Paseo Factory section, checkpoints, agent gate review, the arbiter, evidence gates, providers, run state |
| `docs/specloop/command-json.md` | the versioned JSON contract a front end uses to drive specloop |
| `docs/specloop/prepare.md` | preparing dependencies before offline verification |
| `examples/specloop/` | an example trusted manifest and its helper scripts |
| `KNOWN-LIMITATIONS.md` | open gaps and queued follow-ups |
| `CHANGELOG.md` | what changed |
| `CLAUDE.md` | frozen repository instructions; obsolete worker guidance awaits cleanup |
| `openspec/` | the specs this repo is built from, and its archived changes |

> **Scope.** This is a personal system shaped around one person's machine, quota
> and repositories. These controls reduce risk; they are not a security sandbox.
> Enroll trusted repositories, review queued issue content and keep human review
> at merge time. macOS, launchd and a Paseo host remain assumptions. Python and
> plugin paths are configurable; bundled wrappers still use fixed paths. See
> [platform limits and pending checks](KNOWN-LIMITATIONS.md#platform-assumptions).

## Layout

Modules are under `nightshift/`; directory paths are repository-relative.

| Module | Responsibility |
| --- | --- |
| `cli.py`, `config.py`, `preflight.py` | Commands, enrollment and front-door checks |
| `daemon.py`, `specloop_intake.py`, `specloop_job.py` | Polling, durable jobs and delivery |
| `specloop_client.py`, `specloop_attention.py` | Independent engine CLI and attention adapter |
| `forge/__init__.py`, `forge/types.py` | Forge protocol and factual records |
| `forge/github.py` | GitHub operations and queue-helper delegation through the selected runner |
| `queue.py` | Retained GitHub transport, retries and shared queue policy; daemon owns safe leftover recovery |
| `outcomes.py`, `trace.py`, `mirror.py` | Historical outcomes, transcripts and optional import utility |
| `specloop/` | Build engine, supervisor, verification and evidence gates |
| `tests/` | Regression checks; run `uv run pytest` |

The forge boundary is staged ([issue #126](https://github.com/mattpolicastro/nightshift/issues/126)):
intake, merge-readiness and daemon engine-switch recovery use named operations;
authorization, receipts and workflow decisions remain in core. Landing migration
is future work (#130). The standalone `forge/http.py` transport does not enable
Gitea enrollment; a Gitea backend remains future work.

## Usage

`nightshift status` (also the no-argument default) reports build runs;
`nightshift run <change>`, `nightshift pause <change>` and
`nightshift resume <change>` control a build. Daemon controls use
`nightshift daemon run`, `nightshift daemon status`, `nightshift daemon pause`
and `nightshift daemon resume`. The daemon and supervisor remain independent.
The frozen daemon wrapper's bare `nightshift run` is the sole implicit daemon
exception; the frozen supervisor uses `specloop serve`, a one-release alias
that prints a rename notice to stderr while preserving machine stdout.

Build state now uses `~/.nightshift`: legacy `~/.specloop/runs/<change>` maps
to `~/.nightshift/runs/<change>`, `~/.specloop/proposals/<change>` to
`~/.nightshift/drafts/<change>`, and `~/.specloop/homes/<entry>` to
`~/.nightshift/homes/<entry>`. Daemon receipts and job evidence remain at
`~/.nightshift/proposals/<owner__repo>/<number>.json` and
`~/.nightshift/proposals/<owner__repo>/<number>.jobs/`.
Use `nightshift migrate` with all writers quiescent and the supervisor stopped;
guarded relocation retains compatibility links and legacy reads for one release.
See the [human rollout checklist](docs/specloop/README.md#retirement-rollout)
for [issue #40](https://github.com/mattpolicastro/nightshift/issues/40).

```sh
uv sync
uv run nightshift attention   # PRs awaiting merge and decisions needing you
uv run nightshift attention --offline  # cached outcomes, no GitHub calls
uv run nightshift attention --json     # versioned output for a display adapter
uv run nightshift daemon status      # what's running now, queue depth, blocked, alerting
uv run nightshift preflight   # is it safe to leave alone tonight?
uv run nightshift reconcile   # safe leftover-claim recovery; never resume workers
uv run nightshift daemon run --once  # one poll; does not wait for detached jobs
uv run nightshift daemon pause       # hold new work and completion delivery
uv run nightshift jobs        # active, stopped, pending and diagnostic rows
uv run nightshift jobs --all  # full history, including delivered and failed jobs
uv run nightshift jobs compact  # explicit local cleanup of matching delivered history
uv run nightshift jobs stop <id>  # replace <id> with the full ID from jobs
uv run nightshift jobs release <id>  # after confirmed stop and inspection
uv run nightshift daemon resume      # resume eligible delivery; stopped jobs stay held
```

Copy `config.example.toml` to `config.toml` (gitignored) before running the
daemon.

### Host path setup

The [platform settings table](KNOWN-LIMITATIONS.md#platform-assumptions) lists
consumers, path rules and pending manual checks. `NIGHTSHIFT_BIN_DIR` defaults
to `/opt/homebrew/bin`; Python uses it for prepare/verify, agent and Codex quota
tool lookup. The plugin also uses `NIGHTSHIFT_NODE_BIN` (default `~/n/bin`),
`NIGHTSHIFT_UV` (default `<resolved NIGHTSHIFT_BIN_DIR>/uv`) and
`NIGHTSHIFT_CHECKOUT` (default `~/Projects/nightshift`). An unset uv setting
follows the selected bin directory. Install the tools first; these settings
do not discover or install them.

For a **direct CLI** supervisor invocation, substitute your actual paths:

```sh
cd "/alternate/nightshift"
NIGHTSHIFT_BIN_DIR="/alternate/tools" "/alternate/tools/uv" run --no-sync nightshift serve
```

This explicitly selects the checkout and executable: `NIGHTSHIFT_CHECKOUT`
and `NIGHTSHIFT_UV` configure plugin adapters, not shell commands. Host settings
accept absolute paths or leading `~/`, expanded against the host user's home.
Use single quotes in shell assignments to preserve spaces and literal
characters; the resolver does not expand `$VAR`, globs or shell substitutions. Blank/relative paths,
unsupported tilde forms, NUL/line breaks and colons in PATH directories are
invalid rather than falling back to defaults.

Supply settings in the actual Python service environment and separately in
the [Paseo host environment](plugins/nightshift/README.md#installation), then
restart each affected process. Terminal exports do not update running services;
launchd reads no shell configuration, and there is no hot reload.
`bin/nightshift-daemon` exports `~/.config/nightshift/env`, whose file values
win over inherited values for downstream settings. `bin/specloop-serve` does
not source that file; provide its Python settings in the service environment.
Candidate files and manifest env files do not configure the harness resolver.

**Bundled service startup is still fixed.** Both unchanged wrappers use
`$HOME/Projects/nightshift` and `/opt/homebrew/bin/uv`. `bin/nightshift-daemon`
prepends `/opt/homebrew/bin:$HOME/n/bin` to incoming PATH;
`bin/specloop-serve` sets
`$HOME/n/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin`.
Environment settings do not relocate these bootstrap commands, nor does moving
the wrapper pathname in a plist. Wrapper portability is deferred because
`bin/**` is frozen; no new helper fixes it. Full daemon rollout on another
layout remains pending separate wrapper work or an independently provisioned
operator service preserving equivalent credential and preflight guards,
including required tokens, credential isolation, tool checks and startup
refusals. The direct supervisor example does not replace those daemon guards.

Compatibility-only `[daemon].worktree_root` remains in `config.toml`, default
`~/Projects/nightshift-wt`; see [config.example.toml](config.example.toml).
Leading `~/` expands. Relative roots now resolve beside the selected config
file, instead of the daemon's working directory; set the old location's
absolute path before restart if you need to retain it. Absolute paths and
existing worktrees stay in place; loading neither creates nor migrates them.
It enables no worker execution and does not relocate nightshift run state.
Second-install macOS and actual Paseo host validation remain pending.

### Pause and job control

`nightshift daemon status` shows local active background jobs before repository network
queries: full job ID, stage, repository/item, command, execution state, elapsed
seconds and time remaining or `OVERDUE by Ns`. Starting/legacy attempts without
timing say unavailable. These rows are separate from daemon liveness and
independent `nightshift status` run state; `nightshift jobs --all` includes history.

Reporting deadlines are **propose 3600 seconds, admit 300 seconds, revise 1800
seconds**, including startup; spec re-drafting uses propose's budget. Persisted
deadlines survive restart. On the next authorized, eligible, unpaused poll, a
live overdue attempt gets a comment and `needs-human`, retaining its job ID.
This neither terminates nor retries it automatically. Daemon/GitHub outages,
removed triggers and authorization holds delay reporting; wall-clock changes
affect timing. Use the stop, inspection and release procedure below. If the
job finishes late, resolving `needs-human` under normal eligibility allows
delivery of that result ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).

`nightshift daemon pause` creates `~/.nightshift/pause`; `nightshift daemon resume` removes
it. Pause prevents new daemon launches and completion-delivery operations,
including pushes, PR creation, comments, label changes and publication.
An already-running job can finish and write its local result while paused;
its receipt stays undelivered. Resume delivers eligible retained results once
without rerunning the job, including after a daemon restart. An external
operation already dispatched when pause appears may finish. Pause does not
roll back git or GitHub effects, or terminate running jobs or admitted builds.

`nightshift jobs`, `nightshift jobs --all`, `nightshift jobs compact`,
`nightshift jobs stop <id>` and `nightshift jobs release <id>` work offline
without a running daemon, valid repository configuration or credentials.
Inventory includes proposal, admission and revision attempts, with full ID,
stage, repository/item, command, execution state, available PID/PGID and log path.
The default shows `starting`, `running` (including overdue), `stopping`,
`stopped` and finished-undelivered work, plus rows with record/receipt diagnostics.
Ordinary `failed` and finished-delivered attempts are hidden; `--all` shows
every attempt. Hidden rows are counted in
`Hidden historical jobs: N (use --all to show).` With only hidden history,
the default first prints `No active background jobs.` An empty inventory still
prints `No background jobs.` and exits 0. States also include `unknown`.
Unreadable entries produce diagnostics and exit 1 while valid rows remain
visible; diagnostic-only inventory is not called empty.

Finished rows mean execution completed. They read `delivered` only when the
item's current receipt has JSON `delivered: true`; otherwise they read
`finished (delivery pending)`.
This is an item-level label, even if the receipt names a successor or lacks a
job ID, not proof that each historical attempt delivered independently.
Receipt replacement can change older rows' labels. Listing is read-only;
`--all` is only for listing, and `compact` takes no ID
([issue #103](https://github.com/mattpolicastro/nightshift/issues/103)).

After durable authorized delivery, compaction removes the log and safely owned
idea payload, redacts idea arguments from a minimal `request.json`, and keeps
results, control metadata and the directory. Original request mtimes preserve
touchpoint dates and history across receipt replacement. Undelivered and
stopped/released evidence, workflow receipts and build-feedback files remain;
minimal records still grow, and missing history cannot be reconstructed. This
is not secure erasure ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
Automatic interrupted cleanup waits for a later eligible, authorized,
unpaused item visit; its journal proves delivery, not current authorization. Removal of the final
trigger, closure or authorization denial can leave residual artifacts indefinitely.
Do not rearm solely for cleanup: normal new work may follow.

For an explicit one-pass cleanup, run `nightshift jobs compact`, including
while paused and without current GitHub labels. Only already-delivered history
with a current receipt naming that exact attempt can be compacted. Missing or
replaced identity prevents deletion even when inventory says `delivered` or an
old cleanup journal exists. Identity, idle-lock and ownership checks still apply;
unsafe idea targets are retained with warnings. Each attempt reports `compacted`
or `skipped` with context and a reason, followed by `Compacted: N; skipped: M.`
Warnings can accompany a compacted attempt: inspect which targets were retained.
Expected ineligibility is a successful skip; damaged evidence or filesystem
errors exit 1 while independent healthy attempts continue. Repetition safely
skips completed cleanup and resumes interruptions only with a still-matching
current receipt. It does not launch, deliver, change receipts or clear pause
([issue #103](https://github.com/mattpolicastro/nightshift/issues/103)).

The design's real-tree human check remains pending: pause the daemon, capture
`nightshift jobs --all` and relevant receipts/artifacts on `~/.nightshift`, run
`nightshift jobs compact` once, then capture full inventory again. Compare IDs,
execution/delivery labels, counts, request mtimes, results, control records and
skipped artifacts; verify only reported eligible logs/owned inputs and idea
arguments changed. Inspect retained-target warnings and identity skips, confirm
the default view exposes pending/stopped/active work, and record commands,
outcomes and unexplained differences before resuming. Detached jobs may finish
while paused; account for that separately. CI tests synthetic trees, not this
host's historical tree. Live reference and operator-prose review remain pending.

Daemon intake git times out after 30 seconds and SIGKILLs its POSIX process
group, with at most one further second for reaping/output draining. Escaped
descendants and already-accepted remote effects remain possible; see the
[timeout limits and pending human checks](docs/specloop/README.md#pause-stop-and-the-supervisor)
([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).

To stop one job, copy its exact full ID (32 hexadecimal characters as printed;
no prefixes or paths). Stop verifies the wrapper's process-start identity,
process group and job-directory argument before signalling the owned POSIX
group. It sends SIGTERM, allows **5 seconds** for exit, then escalates to
SIGKILL if the same owned group still executes, allowing **1 further second**
to confirm termination. These are termination-phase deadlines; identity
inspection also takes time. `stopped` is recorded only after no executing
group members remain. The wrapper, children and grandchildren in that group
are covered; descendants that create another session/group are outside it.
Other jobs and the caller are unaffected, and stop does not change pause.

A successful or repeated confirmed stop exits 0. A job whose completion won
the race reports `already finished`, exits 0 and retains its delivery
eligibility. Unsafe identity, unknown IDs and operational failures exit 1;
malformed command syntax exits 2. Older jobs without verifiable process
identity remain listable but cannot safely be stopped by this command. A PID
alone is insufficient. Identity mismatches, a missing wrapper leader with
live descendants, and unavailable process inspection cause refusal. If a new
job is still awaiting metadata, inspect it and retry. If signalling or exit
confirmation fails after stop intent was accepted, the job stays `stopping`
and delivery remains held; inspect the diagnostic and log before retrying.

**Stopped jobs stay held until explicit release.** Logs, requests, results and
receipts remain on disk. The intake receipt retains its job reference, so
polling, daemon restart, resume and label removal/reapplication do not deliver
that result or start a replacement attempt. Intentional cancellation does not
count as a health failure or trigger escalation. Internally the receipt remains pending;
`nightshift jobs` is the authoritative view of its stopped state.

For recovery, **pause first**, list jobs, stop the selected full ID and confirm
`stopped` in inventory. Then inspect the job log, request/result, stop record
and parent intake receipt, the local checkout and committed specloop
admission/run state, and remote refs, PRs,
comments and labels. Admission can commit runnable state before returning a
result. Nightshift pause or job termination leaves that state intact, and the
independent `nightshift serve` supervisor may start or continue the build even
while Nightshift withholds delivery. Control that supervisor and its active
run processes separately if they must stop; unloading a supervisor alone is
not proof that detached run processes exited. Job control does not cancel
already-admitted runs or recover interrupted historical worker execution.

After inspection, run `nightshift jobs release <id>` with that exact full ID
([issue #34](https://github.com/mattpolicastro/nightshift/issues/34)). Release
requires durable stop confirmation with matching recorded process identity,
an idle lifetime lock and a readable, consistent intake receipt. It atomically
removes only the matching receipt's `job` reference, retaining every other
field and all attempt evidence. The historical attempt remains `stopped`;
its result is never delivered, even if it arrived after stop intent.

These receipt, inventory, escalation and release rules apply to intake jobs.
Factory landing keeps separate attempt records: a detached worker holds a per-PR
flock until exit, without delivery receipts or a manual release operation.
Landing status and verified Stop remain available independently of ordinary
Factory actions and while the daemon is paused. Overdue is display-only;
jobs inventory, escalation and compaction do not apply. Worker death may release
the lock while descendants survive; Stop explicitly refuses without verifiable
worker identity. After inspecting effects, wait for those descendants or terminate
them manually. A fresh attempt independently verifies group drainage under flock;
lock availability, unchanged head or terminal outcome alone cannot authorize it.
Independent terminal landing does not take the Factory lock. Follow the keyed
[landing recovery cases](docs/landing.md#recovering-from-a-refusal) rather than
using intake release. Landing logs/results persist until manual pruning, preserving
undrained-group records; newest-20 history plus total and separate current progress
bound display, not storage. Reads do not acknowledge evidence.

| Release outcome | Output | Exit |
| --- | --- | --- |
| Matching confirmed stop detached | `<id>: released` | 0 |
| Valid receipt no longer references this stopped attempt | `<id>: already released` | 0 |
| Running, starting, stopping, finishing/completed, failed, collected or unverifiable work; invalid/unknown/ambiguous ID; write failure | Diagnostic on stderr | 1 |
| Invalid command syntax, missing ID or extra arguments | Usage on stderr | 2 |

Repeating an old release writes nothing and cannot detach a replacement
attempt. Release launches nothing and leaves pause unchanged. After safe
resume, a later poll can start new work only for an open, armed, authorized
and otherwise eligible item: `needs-human`, applicable dependencies,
repository health and approval-head checks still apply. Release requires no
fresh label event; existing PR or committed-admission recovery takes precedence
over duplicate work. Resume or relabeling alone never clears a stopped hold.

Release is explicit recovery, not automatic retry or rollback. SIGKILL can
leave partial artifacts; remote effects and admission may already have
committed. After release, a later eligible build revision selects current
unconsumed feedback with a new key, even under the same label event. It can
append tasks already committed by the stopped attempt; inspect those effects
before releasing. Same-key crash recovery does not apply to this new round.
Legacy/unkeyed revisions can still duplicate a commit whose result was lost.
Keyed process-crash recovery is tested; physical machine-crash durability is
unproven ([issue #7](https://github.com/mattpolicastro/nightshift/issues/7)).
Stopped/released job evidence, inputs and revision journals have no
automatic cleanup. See [remaining limits](KNOWN-LIMITATIONS.md#open-gaps) and the
[manual host checks](docs/validation.md#manual-host-checks) for launchd unload
and stopping during a push to a disposable remote.

### What needs you

`nightshift attention` shows one entry per recorded task, keyed by repository
and issue. Merged PRs disappear. A PR closed without merging needs a decision
until its issue is also closed. For escalations, closing the issue or removing
`needs-human` explicitly resolves the item (including re-arming it). A harness
crash released automatically to the queue stays visible until another attempt
starts or the issue is closed.

It also lists specloop runs paused for a human decision, with the change,
pause reason and PR link. Running and finished runs are omitted. JSON output
has a `specloop` list beside historical worker `tasks`; retirement preserves it.

The command reads GitHub and `specloop status --json` through the one-release
alias. Failed checks retain the last known outcome, print `STALE` with the last successful check time,
and return exit code 1.
`--offline` skips those checks and explicitly displays cached data. A successful
check returns 0, even when tasks need attention.

The worker-task refresh checks at most 32 tasks with four concurrent checks
and a 40-second network budget. Outstanding tasks have priority; resolved history gets reserved
capacity and normally waits an hour between checks. Large backlogs rotate across
refreshes, and deferred checks are marked stale. Reopened historical tasks appear
when their next historical check runs. See the [refresh policy](plugins/nightshift/README.md#nightshift-task-refresh-behavior).

Historical events remain in `~/.nightshift/outcomes.sqlite3`, with the same
resolution, stale/offline and exit behavior. Retirement does not backfill,
move or delete history, branches, worktrees or transcripts. Transcript readers
and the mirror utility remain available; automatic phase mirroring is disabled
and `mirror_to_paseo` is inert. Reading attention does not resolve a task.

### Paseo attention page

The optional [Nightshift Paseo plugin](plugins/nightshift/README.md) adds a
**Nightshift** sidebar page on the selected host. It shows the same
outstanding tasks as the CLI, refreshes while open, and links to GitHub. Install
and enable it on the host that owns the Nightshift ledger; it does not create
agent sessions or change task execution.

The page's **Factory** section lists specloop runs, those waiting on you first, with
one button per action that fits each pause (resume with a note, accept, publish,
finish, abort), plus separate **Land** at a verifiable `human_checks` pause.
Finish archives, pushes and marks ready without merging. Land invokes existing
landing through an explicit confirmation that human checks were performed.
The dialog freezes change, PR, pause ID, published head and pause timestamp;
changes invalidate it instead of silently rebinding. Private plugin transport
starts background work independently of ordinary actions, retaining progress,
results and refusals in the row or above the list when hidden. It adds no public
CLI/daemon landing path. See [Factory confirmation](plugins/nightshift/README.md#confirmed-landing)
and [landing recovery](docs/landing.md#recovering-from-a-refusal).

### Watching it work

`nightshift daemon status` reports daemon liveness, alerts, active background jobs and
repository ready/blocked counts and lists, including contradiction diagnostics.
Worker working counts, claim/phase/transcript lines and the off-subscription
model report are removed; other status behavior and exit codes are unchanged.
Use `nightshift jobs --all` for job history and `nightshift status` for independent
run state. Timing and inventory details are in [job control](#pause-and-job-control).
`tail -f ~/.logs/nightshift.log` follows daemon activity; it is not agent reasoning.

## Prerequisites

macOS with Python 3.12 or later, `uv`, `gh` and git. Specloop drafting also uses
Node/`npx` for OpenSpec; install the CLI and credentials required by the trusted
manifest's provider profiles and verification commands.

Direct Python front-door operation requires scoped GitHub access and preflight
checks, not worker OAuth, Claude, pnpm or Swift. The CLI retains env-file
loading. Each enrolled repo needs a local checkout, trusted manifest, an
`AGENTS.md` and PR CI. Use `dir` for a separate daemon checkout; align it with the
manifest's source repository. See [enrollment](#enroll-a-repository).

The frozen bundled daemon wrapper additionally requires
`~/.config/nightshift/env` (chmod 600), `CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN`,
and all of `gh`, `claude`, `pnpm`, `node`, `git`, `swift` before Python starts.
Python retirement does not bypass these shell checks. See
[platform limits](KNOWN-LIMITATIONS.md#platform-assumptions).

## Enabling the daemon

The launchd units are **not** auto-installed. Complete the
[pre-merge retirement checklist](docs/specloop/README.md#retirement-rollout)
and these startup checks before using the bundled daemon:

- [ ] **Scope `GH_TOKEN`.** The `gho_` OAuth credential from `gh auth login`
      reaches _every_ repo the account owns. Replace it with a fine-grained
      PAT limited to the enrolled repos. Unattended is the point at which this
      stops being acceptable. Exact permissions below.
- [ ] **Configure alerting.** Unconfigured is a silent no-op, so an unattended
      night that goes wrong is indistinguishable from one that never started.
- [ ] **`nightshift preflight` comes back clean.** It checks both of the above
      and refuses to call scoping proven without a negative control.
- [ ] **Enroll a throwaway repo first.** `config.example.toml` ships pointing
      at a sandbox; promoting a repo you care about is a deliberate edit, and
      worth leaving a week.
- [ ] **Confirm the pause file works** — `nightshift daemon pause`, then check the log
      shows it holding new launches and completion delivery.
- [ ] **Review manifest provider quotas and costs.** Retired worker caps and
      model settings cannot pace specloop builds.

- [ ] **Check the control boundaries.** Pause holds daemon launches and
      delivery; `nightshift jobs stop <id>` terminates one tracked group.
      On disposable work, unload the daemon, then list and stop the surviving
      detached job from another terminal. Separately, stop during an observed
      push to a disposable remote; inspect remote refs/PRs and local logs and
      record any accepted remote effects. A daemon-owned push outside the
      selected group is not killed by jobs stop. CI cannot establish what the
      remote accepted during interruption. Follow the
      [manual launchd and mid-push procedures](docs/validation.md#manual-host-checks)
      before operational rollout; unperformed host checks remain pending.

```sh
cp launchd/com.mattpolicastro.nightshift.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.mattpolicastro.nightshift.plist
tail -f ~/.logs/nightshift.log
```

To unload the daemon while holding later delivery:

```sh
uv run nightshift daemon pause
launchctl unload ~/Library/LaunchAgents/com.mattpolicastro.nightshift.plist
# From another terminal, inventory and stop each selected surviving job:
uv run nightshift jobs
uv run nightshift jobs stop <id>
uv run nightshift jobs
```

Unloading stops the daemon service, not its detached drafting, admission or
revision jobs, and does not revoke committed admission or stop independent
admitted builds. Keep pause in place until inspection is complete; reloading
the daemon does not clear pause or stopped-job holds.

`bin/nightshift-daemon` still refuses missing env-file/OAuth/GH_TOKEN or any
listed tool (exit 78), and scrubs `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` and
`ANTHROPIC_AUTH_TOKEN`. Wrappers, plists and their comments are frozen, including
stale worker/pause descriptions. A host without legacy prerequisites needs
separate wrapper work or an independently provisioned operator service retaining
trusted env loading, scoped GH_TOKEN refusal, required front-door tool checks,
credential isolation and preflight negative-control/alert checks. No live
installation or restart is implied; control the supervisor and its runs separately.

### Not yet built

- **Digest.** Phase 2.
- Historical worker budgets/model routing are retired; manifest provider policy
  is separate. [Historical documents and deferred cleanup](KNOWN-LIMITATIONS.md#retirement-follow-ups)
  retain the old evaluation evidence.

[KNOWN-LIMITATIONS.md](KNOWN-LIMITATIONS.md) lists unresolved work and validation limits.

## The GitHub token

A fine-grained PAT, **Repository access → Only select repositories → the
enrolled repos**, with exactly these permissions:

| permission    | level          | why                                                                                                                                           |
| ------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Metadata      | Read           | mandatory for every fine-grained PAT                                                                                                          |
| Contents      | Read and write | `git fetch` the base, `git push` the task branch                                                                                              |
| Issues        | Read and write | the queue itself: `issue list/view` to poll and read through, `issue edit` for label transitions, `issue comment` to post escalation findings |
| Pull requests | Read and write | `gh pr create`, and `gh pr list --head` so recovery never opens a second PR                                                                   |

**Do not grant Workflows.** It is not an oversight. Without it the token
cannot push changes under `.github/workflows/`, so an agent physically cannot
edit the CI that judges its own work — the held-out verifier stops depending
on the agent choosing to respect it and starts being enforced by the
credential. A task that genuinely needs a workflow change will fail its push
and escalate, which is the correct outcome.

Nothing else is needed. In particular the daemon never reads Actions logs,
never touches releases or packages, and never administers the repo.

Put it in `~/.config/nightshift/env` (chmod 600); the bundled wrapper also
requires its legacy OAuth token:

```sh
export GH_TOKEN='github_pat_...'
export NIGHTSHIFT_SLACK_WEBHOOK='https://hooks.slack.com/services/...'
```

Then prove it:

```sh
uv run nightshift preflight
```

The check that matters is the **negative control** — `forbidden_probe` in
`config.toml`, pointed at a real repo the token must NOT reach. A token that
reads the sandbox has demonstrated nothing about what else it reads, which is
the same trap as an earlier auth finding: `claude -p` succeeded against a
deliberately bogus token because an interactive login was answering instead.
`preflight` treats a missing negative control as a failure rather than a pass,
because asserted scoping is not proven scoping.

## Dependencies between issues

A `blocked-by:` line in an issue body is an edge the queue enforces:

```
blocked-by: #15
blocked-by: #9, #12
```

An open or unreadable blocker defers new drafting, leaving its trigger intact.
Existing work can still be collected and delivered under normal eligibility rules. `nightshift daemon status` lists blocked issues separately with what they are
waiting on — a queue full of blocked work otherwise looks exactly like an idle
daemon.

An issue counts as satisfied when it is **closed**, which happens when its PR
merges via `Closes #N`. That makes the edge mean "the dependency's code is on
main", which is what a dependent task actually needs — not "someone opened a
PR for it".

The marker is deliberate rather than inferred. Bodies cite issues constantly
("ported from #4", "see #12"), and a parser loose enough to catch _depends on
the voice-count issue (#5)_ is loose enough to block on a citation.

## Theme branches

Historical worker `base:` issue markers no longer select build branches.
Configure the trusted manifest and repository base for the
[current publication flow](docs/specloop/README.md#publication-and-base-recovery).

## Revising a shipped worker PR

The worker revision path is retired. Preserve old branches for inspection;
current specloop revisions use authorized PR labels and consumed feedback IDs
as described in [the front-door flow](#how-a-specloop-change-flows).

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

Each repository chooses an engine: `engine = "specloop"` for that flow, or the
original `worker` engine, which runs headless
[Claude Code](https://claude.com/claude-code) workers on `agent:ready` issues.
The worker engine remains the default until it is retired.

Nightshift runs on macOS under launchd.

- **The worker engine** drove 79 tasks across two real repositories by
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

**Review is a separate session.** The worker engine's reviewer is a fresh Claude Code process
with a read-only tool contract — `Write`, `Edit` and `git add` are explicitly
denied — that sees the diff and the issue, but not the implementer's chain of
thought. It has caught a CI-green diff whose only tell was a `toBe`→`toEqual`
edit weakening the assertion that guarded the behaviour being removed, and
found an unplanted latent bug while doing so. **CI cannot catch test weakening,
because the weakened test is the thing CI runs.**

**Escalation is a success.** A worker that decides its issue is wrong, or
under-specified, or contradicted by the repo's own documentation, is expected
to stop, write up why, and label the issue for a human. Of the first six
escalations, four found defects in the *issue* rather than in the code. A
system that cannot refuse is a system that will confidently do the wrong thing.

## How a specloop change flows

For an enrolled specloop repository, the daemon drives the specloop CLI;
the separate `specloop serve` supervisor executes admitted tasks. Worker
claims never run for that repository.

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
`specloop status --json` preserves healthy runs alongside non-actionable
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
run to `running` with no pause and a retryable error. `specloop serve` drives
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
Admission saves the effective manifest with the run. The worker's `verify`
command is not required for this engine. See [manifest templates and CLI outcomes](docs/specloop/command-json.md).

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
(`uv run specloop serve`, or its existing launchd job) to drive admitted runs.
For an alternate layout, use the [host path setup](#host-path-setup); the
bundled service wrappers retain fixed bootstrap paths.
Worker quotas and endpoint/model settings do not provide specloop quota pacing;
specloop uses its manifest's provider profiles.

The daemon's kill switch is `~/.nightshift/pause`: it holds new work and
completion delivery. Running drafting, admission and revision jobs may finish
locally. Use `nightshift jobs` and `nightshift jobs stop <id>` to select and
terminate a tracked background job. An admission that has already committed
runnable state may still start or continue building under the independent
`specloop serve` supervisor. See [pause and job control](#pause-and-job-control)
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
`~/.specloop/runs/*/events.jsonl` history; no external services are required:

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

```
issue labelled agent:ready
        │
        ▼
  claim  ─── crash-safe: a claim file records the phase, so an
        │    interrupted task resumes by decision, not by guess
        ▼
  git worktree, branch cut from base (or from a `base:` theme branch)
        │
        ▼
  implement  ── claude -p, scoped tool permissions, --max-turns budget
        │
        ▼
  verify  ───── the repo's own command (`pnpm -r test`, `swift test`, …),
        │       checked against the Bash commands the worker actually ran
        ▼                                     rather than against its prose
  review  ───── separate session, read-only, VERDICT: PASS | FAIL
        │
        ├── FAIL ──▶ retry, then escalate
        │
        ▼
  push + PR ──▶ CI runs ──▶ a human merges
```

Anything that goes wrong lands on the issue as a comment and a `needs-human`
label, with the findings that produced it.

## Quickstart

This starts with the default worker configuration; specloop enrollment is
described [above](#enroll-a-repository).

```sh
uv sync
cp config.example.toml config.toml   # then edit: repos, verify commands, caps
uv run nightshift preflight          # is it safe to leave this alone tonight?
uv run nightshift preflight --deep   # …and can each endpoint hold a tool loop?
uv run nightshift run --once         # claim and run exactly one task
```

`preflight` is the one to run first. Its most important check is a **negative
control**: `forbidden_probe` names a repo the token must *not* be able to
reach, and a token that merely succeeds against an enrolled repo has proven
nothing about what else it can touch. Asserted scoping is not proven scoping.
The same shape applies to a second endpoint: preflight builds the environment a
worker on it would actually get and asserts the subscription token is *not* in
it. `--deep` adds a Read → Write tool-loop probe per model — slow, because a
cold model load is minutes, and worth it because serving `/v1/messages` and
holding a tool loop are different claims.

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
| `CLAUDE.md` | the constraints an agent working on *this* repo has to hold to |
| `openspec/` | the specs this repo is built from, and its archived changes |

> **Scope.** This is a personal system shaped around one person's machine, quota
> and repositories. These controls reduce risk; they are not a security sandbox.
> Enroll trusted repositories, review queued issue content and keep human review
> at merge time. macOS, launchd and a Paseo host remain assumptions. Python and
> plugin paths are configurable; bundled wrappers still use fixed paths. See
> [platform limits and pending checks](KNOWN-LIMITATIONS.md#platform-assumptions).

## Layout

| module                                        | state                                                                           |
| --------------------------------------------- | ------------------------------------------------------------------------------- |
| `worker.py`                                   | ✅ validated — the `claude -p` invocations, hand-run end-to-end                 |
| `trace.py`                                    | ✅ validated — stream-json parsing, checked against real transcripts            |
| `prompts.py`                                  | ✅ validated — implement + reviewer prompts, both exercised                     |
| `queue.py`                                    | ✅ validated — claim/reconcile, 16 tests + a live crash-recovery run            |
| `daemon.py`                                   | ✅ loop, reconcile, escalation and crash recovery — all run live                |
| `recovery.py`                                 | ✅ what to do with an interrupted task, by recorded phase                       |
| `preflight.py`                                | ✅ what must hold before an unattended night, incl. a token negative control    |
| `tests/`                                      | regression suite; rerun `uv run pytest` to establish a current count/result    |
| `cli.py`                                      | ✅ `attention`/`status`/`pause`/`resume`/`jobs`/`run`/`reconcile`/`preflight`; `digest` is Phase 2 |
| `specloop_intake.py`, `specloop_job.py`, `specloop_client.py`, `specloop_attention.py` | ✅ the GitHub front door for specloop repositories: drafting, admission, revision, publication, attention |
| `../specloop/` | ✅ the build engine (its own CLI): loop, seal, verify, review, evidence, gates, serve |
| `config.py`, `vcs.py`, `task.py`, `notify.py` | ✅ config, worktrees, state machine, Slack                                      |

## Usage

```sh
uv sync
uv run nightshift attention   # PRs awaiting merge and decisions needing you
uv run nightshift attention --offline  # cached outcomes, no GitHub calls
uv run nightshift attention --json     # versioned output for a display adapter
uv run nightshift status      # what's running now, queue depth, blocked, alerting
uv run nightshift preflight   # is it safe to leave alone tonight?
uv run nightshift reconcile   # repair crash state
uv run nightshift run --once  # claim and run one task
uv run nightshift pause       # hold new work and completion delivery
uv run nightshift jobs        # list local background attempts, including history
uv run nightshift jobs stop <id>  # replace <id> with the full ID from jobs
uv run nightshift jobs release <id>  # after confirmed stop and inspection
uv run nightshift resume      # resume eligible delivery; stopped jobs stay held
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
NIGHTSHIFT_BIN_DIR="/alternate/tools" "/alternate/tools/uv" run --no-sync specloop serve
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

Worker worktrees use `[daemon].worktree_root` in `config.toml`, default
`~/Projects/nightshift-wt`; see [config.example.toml](config.example.toml).
Leading `~/` expands. Relative roots now resolve beside the selected config
file, instead of the daemon's working directory; set the old location's
absolute path before restart if you need to retain it. Absolute paths and
existing worktrees stay in place; loading neither creates nor migrates them.
This setting does not relocate specloop run state. Second-install macOS and
actual Paseo host validation remain pending as described in the platform limits.

### Pause and job control

`nightshift status` shows local active background jobs before repository network
queries: full job ID, stage, repository/item, command, execution state, elapsed
seconds and time remaining or `OVERDUE by Ns`. Starting/legacy attempts without
timing say unavailable. These rows are separate from daemon/worker liveness and
independent `specloop status` run state; `nightshift jobs` includes history.

Reporting deadlines are **propose 3600 seconds, admit 300 seconds, revise 1800
seconds**, including startup; spec re-drafting uses propose's budget. Persisted
deadlines survive restart. On the next authorized, eligible, unpaused poll, a
live overdue attempt gets a comment and `needs-human`, retaining its job ID.
This neither terminates nor retries it automatically. Daemon/GitHub outages,
removed triggers and authorization holds delay reporting; wall-clock changes
affect timing. Use the stop, inspection and release procedure below. If the
job finishes late, resolving `needs-human` under normal eligibility allows
delivery of that result ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).

`nightshift pause` creates `~/.nightshift/pause`; `nightshift resume` removes
it. Pause prevents new daemon launches and completion-delivery operations,
including pushes, PR creation, comments, label changes and publication.
An already-running job can finish and write its local result while paused;
its receipt stays undelivered. Resume delivers eligible retained results once
without rerunning the job, including after a daemon restart. An external
operation already dispatched when pause appears may finish. Pause does not
roll back git or GitHub effects, or terminate current worker-engine tasks.

`nightshift jobs`, `nightshift jobs stop <id>` and `nightshift jobs release <id>`
work offline without a running daemon, valid repository configuration or
credentials. Inventory includes proposal, admission and revision attempts,
with full ID, repository/item, command,
execution state, available PID/PGID and log path. `finished` means execution
completed; delivery may still be pending. States also include `starting`,
`running`, `failed`, `stopping`, `stopped` and `unknown`. Unreadable entries
produce diagnostics and exit 1 while valid rows remain visible; an empty
inventory prints `No background jobs.` and exits 0.

After durable authorized delivery, compaction removes the log and safely owned
idea payload, redacts idea arguments from a minimal `request.json`, and keeps
results, control metadata and the directory. Original request mtimes preserve
touchpoint dates and history across receipt replacement. Undelivered and
stopped/released evidence, workflow receipts and build-feedback files remain;
minimal records still grow, and missing history cannot be reconstructed. This
is not secure erasure ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
Interrupted cleanup waits for a later eligible, authorized, unpaused item visit;
its journal proves delivery, not current authorization. Removal of the final
trigger, closure or authorization denial can leave residual artifacts indefinitely.
Do not rearm solely for cleanup: normal new work may follow.

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
independent `specloop serve` supervisor may start or continue the build even
while Nightshift withholds delivery. Control that supervisor and its active
run processes separately if they must stop; unloading a supervisor alone is
not proof that detached run processes exited. Job control does not cancel
worker-engine tasks or already-admitted runs.

After inspection, run `nightshift jobs release <id>` with that exact full ID
([issue #34](https://github.com/mattpolicastro/nightshift/issues/34)). Release
requires durable stop confirmation with matching recorded process identity,
an idle lifetime lock and a readable, consistent intake receipt. It atomically
removes only the matching receipt's `job` reference, retaining every other
field and all attempt evidence. The historical attempt remains `stopped`;
its result is never delivered, even if it arrived after stop intent.

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
committed. A revision can commit before saving its result, so a later attempt
can append a duplicate round. Stopped/released job evidence and inputs have no
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
has a `specloop` list beside worker `tasks`.

The command reads GitHub and `specloop status --json`. Failed checks retain
the last known outcome, print `STALE` with the last successful check time,
and return exit code 1.
`--offline` skips those checks and explicitly displays cached data. A successful
check returns 0, even when tasks need attention.

The worker-task refresh checks at most 32 tasks with four concurrent checks
and a 40-second network budget. Outstanding tasks have priority; resolved history gets reserved
capacity and normally waits an hour between checks. Large backlogs rotate across
refreshes, and deferred checks are marked stale. Reopened historical tasks appear
when their next historical check runs. See the [refresh policy](plugins/nightshift/README.md#nightshift-task-refresh-behavior).

History starts with this version; this does not backfill old tasks from
ambiguous transcripts. The daemon records immutable events in
`~/.nightshift/outcomes.sqlite3`, including failures before workers start.
Concurrent writes use SQLite transactions, and refresh observations belong to
the exact event they checked so they cannot hide a newer attempt. New runs keep
transcripts under `~/.nightshift/transcripts/<run-id>/`; older files remain
readable by `status`. Recording failures are logged without failing the task.

Automatic phase mirroring is disabled; the legacy `mirror_to_paseo`
setting is accepted but inactive. Reading a task does not resolve it.

### Paseo attention page

The optional [Nightshift Paseo plugin](plugins/nightshift/README.md) adds a
**Nightshift** sidebar page on the selected host. It shows the same
outstanding tasks as the CLI, refreshes while open, and links to GitHub. Install
and enable it on the host that owns the Nightshift ledger; it does not create
agent sessions or change task execution.

The page's **Factory** section lists specloop runs, those waiting on you first, with
one button per action that fits each pause (resume with a note, accept, publish,
finish, abort); see the plugin README.

### Watching it work

```
running
  alerts: slack
  example-owner/sample: 1 working, 3 ready, 1 blocked
    >> #26 sample-ui: carry theme support across from sample-midi  (reviewing, 15m on claude/26)
    #23 sample-ui: default generation toward n-bar phrases
    -- #11 sample-ui: settings become a view  (waiting on #26)
```

The `>>` line comes from the claim file, not GitHub — a claimed issue carries
`agent:working`, so the label query that builds the rest of this listing cannot
see it. `status` is read-only: an unparseable claim file is reported, never
repaired, because that file names the worktree holding a running task's only
copy of its work.

`tail -f ~/.logs/nightshift.log` is the live feed — claims, ships,
escalations, and the blocked-issue skips as they happen.

There is no way to watch a worker think. Transcripts land in
`~/.nightshift/transcripts/<issue>-<step>-<n>.jsonl` and are written from a
captured subprocess, so a file appears only once that step has finished.

## Prerequisites

macOS with Python 3.12 and `uv`, `gh`, and git. For specloop also: Node (the
OpenSpec CLI runs through `npx`) and the Codex CLI or Claude Code for agents.

The worker setup below applies to the default engine. For specloop provider
profiles and manifests, see [repository enrollment](#enroll-a-repository).

- `~/.config/nightshift/env` (chmod 600) with `CLAUDE_CODE_OAUTH_TOKEN`, and
  `GH_TOKEN` scoped to the enrolled repos
- `gh` authenticated
- Each enrolled repo needs an `AGENTS.md` stating the agent rules and a CI
  workflow that runs on pull requests — CI is the held-out verifier, and the
  agent cannot edit what runs there after it pushes
- Each enrolled repo needs a local checkout to cut worktrees from, defaulting
  to `~/Projects/<repo-name>`. Pointing it at the checkout you work in yourself
  is the default and is deliberate: the daemon's `fetch --prune` keeps your
  origin refs fresh and its worktrees stay visible in your `git worktree list`.
  The cost is that a branch held by a running worker cannot be checked out
  locally, and a crashed teardown wants a `git worktree prune`. Set `dir` to
  point the daemon at a checkout of its own if you would rather not have either

See `CLAUDE.md` for the constraints an agent working on this repo must hold
to, `CHANGELOG.md` for what changed, and `KNOWN-LIMITATIONS.md` for what is
currently broken or unverified.

## Enabling the daemon

The launchd unit is **not** auto-installed. Work through this first — the
first item is a real credential problem, not a formality.

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
- [ ] **Confirm the pause file works** — `nightshift pause`, then check the log
      shows it declining to claim.
- [ ] **Watch the first unattended night.** `max_tasks_per_night` is a cap PER
      SUBSCRIPTION WINDOW, not per process — at ~$4–8.50 per shipped PR and a
      five-hour window, a cap of 8 is roughly $35–70 per window and up to ~4.8
      windows a day, so the name undersells it badly. Set it to what you want
      spent between resets, not per night.
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
uv run nightshift pause
launchctl unload ~/Library/LaunchAgents/com.mattpolicastro.nightshift.plist
# From another terminal, inventory and stop each selected surviving job:
uv run nightshift jobs
uv run nightshift jobs stop <id>
uv run nightshift jobs
```

Unloading stops the daemon service, not its detached drafting, admission or
revision jobs, and does not revoke committed admission or stop independent
specloop builds. Keep pause in place until inspection is complete; reloading
the daemon does not clear pause or stopped-job holds.

`bin/nightshift-daemon` exists because launchd reads no shell config — not
`.zprofile`, not `.zshenv` — so credentials must be sourced explicitly. It
refuses to start (exit 78) if the env file or the OAuth token is missing, and
unsets `ANTHROPIC_API_KEY` so an inherited key cannot silently move workers
onto metered billing.

### Not yet built

- **Digest.** Phase 2.
- **Quota awareness, the remaining half.** `max_tasks_per_night` now resets on
  the `resetsAt` the runs themselves report, so the cap is per subscription
  window rather than per process lifetime — the daemon holds at the cap and
  resumes when the window turns, instead of retiring after N tasks however many
  days that took. Still missing: nothing reacts to `isUsingOverage`, which is
  logged loudly and otherwise ignored, so an exhausted subscription would keep
  working on metered billing; and nothing paces _within_ a window.
- **Model routing, the policy half.** The *mechanism* is built —
  `[[endpoints]]` declares where an invocation goes, `endpoint:model` routes a
  phase there, and a per-repo `[repos.models]` can run one repo local while
  everything else stays on the subscription (`SPEC-endpoints.md` Phase A). What
  is not built is anything that *decides*: routing by task shape, cost or
  measured capability still waits on per-run data, because
  choosing a model per task without per-run data is guesswork — the one time a
  model change was made on intuition, opus for implement, a day of measurement
  reverted it. There is also no chores phase for `[models] chores` to route.
- **Retry after a reviewer FAIL.** Built, and fired exactly once — sample #83 on
  2026-08-08, `reviewer failed 2 attempts`, which escalated. So the unattended
  path works and the retry is 0-for-1 at turning a FAIL into a pass. The gate
  itself is validated against a hand-written CI-green bad diff (2026-08-03); what
  it has never faced is a diff from an agent *trying* to get something past it.

[KNOWN-LIMITATIONS.md](KNOWN-LIMITATIONS.md) holds the rest: what is broken, what is unverified,
and what is queued.

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

Put it in `~/.config/nightshift/env` (chmod 600) beside the OAuth token:

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

`claim()` skips any ready issue with an unmet blocker and takes the next one
instead, so the queue is safe to fill in advance and will drain in dependency
order. `nightshift status` lists blocked issues separately with what they are
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

A `base:` line points an issue at a branch other than the repo default. The
work is cut from it, and the PR opens against it:

```
base: narrow-tier
```

**Why this exists.** Measured in sample on 2026-08-09: all four merge conflicts
in one day were two parallel issues editing one file, and every resolution ran
against a `main` that had moved underneath the branch. Separately, a merged PR
cost ~9 billed GitHub Actions minutes across six jobs — the PR check, a second
check on `main`, a deploy, and one Pages build per deploy target. At the rate a
working queue files issues, that is a month's allowance in a week.

Threading a cluster of siblings onto one theme branch changes both halves.
Conflicts resolve **inside** the theme, between changes meant to fit together,
rather than against a trunk unrelated work is also landing on. And the theme
merges to `main` **once**, which is the arithmetic that matters.

The branch must already exist on the remote. A task whose `base:` names a
missing branch **escalates** rather than falling back to the default: silently
branching from `main` is the failure that looks like success — the work lands,
CI is green, the PR merges into the trunk the theme was meant to be kept off,
and nobody learns the marker was a typo until a conflict weeks later.

Pair a theme with an **unarmed tracking issue** holding what the children
share: the design source, the constraints, and a deferred/known-gaps ledger.
Children are written in isolation, so the gaps *between* them are otherwise
found by accident — in sample one issue hid a view below 640px and silently
removed the only way to create a source, which surfaced later through manual
probing rather than from anything written down.

## Revising a shipped worker PR

This section describes worker repositories. For specloop repositories, put
`agent:revise` on the PR as described [above](#how-a-specloop-change-flows).

`agent:ready` always cuts a branch from base. That is right for new work and
wrong for "this PR is close, change these two things" — it restarts work that
was mostly correct, then fails late, because the fresh branch shares no history
with the remote and `vcs.push` is deliberately not a force push.

`agent:revise` is the other door. It resumes the branch the task already
shipped and adds commits on top:

1. **Say what should change.** On the PR — conversation, a review, or an inline
   comment on the line — or on the issue. All of it is read. Inline comments
   keep their `path:line` and are the best input the worker can get, since they
   anchor to the diff instead of describing it.
2. **Close the PR** (optional). A closed PR frees the branch for a fresh one;
   leaving it open also works, and the push just updates it with no second PR.
3. **Add `agent:revise`.** `agent:done` can stay; `claim()` removes both.

Only feedback newer than the branch's last commit counts — anything older is
what the branch was already built from. Only the most recent PR for the branch
is read, so a second revision does not re-litigate the first.

**A revise with nothing to revise refuses.** If no comment, review or inline
note is newer than the tip, the task escalates without running an agent. The
daemon's own escalation write-ups do not count as instructions — it comments as
you, so the `**nightshift** —` prefix is the only thing telling them apart. This
exists because "nothing was asked" and "the issue body is the ask" are
indistinguishable to a worker, and guessing between them means redoing shipped
work while looking like it succeeded.

The worker is told the branch already carries reviewed work, is pointed at
`git diff <base>...HEAD`, and is forbidden to reset, rebase, amend, revert or
force-push it. PR feedback is kept in its own prompt section, separate from the
issue discussion — one is about this diff, the other about the task. A revise
against a branch that exists neither on origin nor locally raises
`NoBranchToResume` rather than quietly starting from scratch and opening a
second PR.

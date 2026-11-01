# specloop — Nightshift's build engine

Nightshift turns approved OpenSpec changes into reviewed, verified pull requests.
The sole/default engine, specloop, takes an admitted change through
its `tasks.md` one task at a time, and the daemon provides its GitHub and configured
Gitea front end. For the JSON contract see [command-json.md](command-json.md);
for dependency setup see
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
defaults. Admission saves the effective manifest with the run. Omit `engine`
or set it to `specloop`; explicit `worker` fails loading before dispatch.
Worker `verify`, model/endpoint, task-cap, phase-budget and concurrency settings
are inert, with key-only ignored-setting notes; no manifest is generated from them.
See [enrollment and preparation](../../README.md#enroll-a-repository) and the
[CLI contract](command-json.md).

Daemon forge selection defaults to GitHub independently of named table order. Optional
`repos.forge` selects an exact name; owner/repository collisions across forges
are rejected ignoring case. See [forge configuration](../../config.example.toml).
Gitea enrollment grants no GitHub landing authority; daemon status reports it
as `not queried (gitea)`. Gitea daemon drafting, admission, revisions, publication,
readiness and pending delivery use the selected bot adapter subject to normal
authorization, health, pause, stopped-job and foreign-journal gates
([issue #150](https://github.com/mattpolicastro/nightshift/issues/150)).

Gitea preflight provides read-only bot, forbidden-repository, label, exact
protection-rule and clone credential-username evidence. It does not authorize
individual label actors, and the daemon does not rerun it each poll. Eligible
dispatch uses allowlist-only actors, bot-owned canonical marker evidence and
add-first verified label transitions. Membership authorization remains unsupported.
The GitHub token/probe checks
remain prerequisites. Follow the [preflight and human enrollment checks](../../README.md#quickstart)
with the real instance, daemon clone and bot environment; CI uses fake services
and helpers and cannot establish live enrollment. Protection `unverified` is a
failure, and matching helper usernames prove neither password ownership nor
scope. Provision the independent supervisor's config and credential sources
via [host setup](../../README.md#host-path-setup); frozen-wrapper prerequisites
still apply. The [remaining Gitea limits](../../KNOWN-LIMITATIONS.md) are tracked in
[issue #128](https://github.com/mattpolicastro/nightshift/issues/128) and
[issue #122](https://github.com/mattpolicastro/nightshift/issues/122).

| Label | Stage and action |
| --- | --- |
| `spec:draft` or `agent:ready` on an issue | Draft its title, body and comments into an OpenSpec change. |
| `spec:review` on the draft PR | The daemon requests human spec review. |
| `spec:approved` on the PR | A human requests admission; the daemon records that PR and starts the build. |
| `agent:revise` on the PR, before admission | Re-draft the spec on the same branch with new review feedback. |
| `agent:revise` on the PR, after admission | Append a review round of tasks and resume the run. |
| `needs-human` on the issue or PR | Inspect and resolve the refusal before removing this hold; distinguish retained-trigger recovery from a fresh round below. |

An issue-body line such as `blocked-by: #9, #12` defers a new draft round
while any dependency is open or unreadable. The trigger stays in place, so a
later poll can draft after all blockers close. Existing jobs, saved results
and open draft PRs can still be collected or delivered under normal
eligibility rules, including authorization
([issue #24](https://github.com/mattpolicastro/nightshift/issues/24),
[PR #29](https://github.com/mattpolicastro/nightshift/pull/29)).

For `spec:draft`, `agent:ready`, `spec:approved` and `agent:revise`, the actor
on the latest application of each trigger must be an OWNER, MEMBER or
COLLABORATOR on GitHub. On Gitea, the login must exactly match `trusted_logins`
ignoring case and excluding `bot_login`; owner shortcuts and organization/team
membership cannot authorize it. If both intake aliases remain, both must be authorized.
An untrusted application gets an explanation and does not authorize work; have
a trusted actor remove and reapply the label (or remove an unauthorized
intake alias). A failed actor lookup defers action for retry. Feedback authors
are checked independently: a trusted trigger does not make untrusted comments
eligible ([issue #5](https://github.com/mattpolicastro/nightshift/issues/5),
[PR #42](https://github.com/mattpolicastro/nightshift/pull/42)).

The daemon writes the issue title, body and trusted comments to a durable
UTF-8 file and passes its absolute path with `--idea-file`; the idea text
does not go in argv. The file survives delayed background reads, later polls
and restarts until durable delivery; each round has a distinct input
([issue #12](https://github.com/mattpolicastro/nightshift/issues/12),
[PR #35](https://github.com/mattpolicastro/nightshift/pull/35)).
Delivered background inputs may then be compacted; undelivered and stopped
inputs remain unchanged ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
The explicit paused cleanup pass also requires the current receipt to match the
attempt; see [job control and retention](#pause-stop-and-the-supervisor)
([issue #103](https://github.com/mattpolicastro/nightshift/issues/103)).

Draft questions or rejection remove both issue intake labels. Answer the
questions or resolve the rejection on the issue, remove `needs-human`, and
reapply `spec:draft` or `agent:ready` to retry. With both triggers removed,
removing `needs-human` alone does not requeue the issue.

Gitea confirms every label addition before removing source labels. An ambiguous
addition can retain the trigger alongside `needs-human`; both labels safely hold
the item across polls and restart. Inspect effects and resolve the refusal, then
remove `needs-human` to permit ordinary authorized receipt processing. Completed
work need not rerun, and clearing the hold does not itself promise a fresh round.
For a fresh draft, admission or refused revision, use the existing edited-input
or trusted remove-and-reapply rules. This adds no automatic recovery through
`needs-human` ([issue #150](https://github.com/mattpolicastro/nightshift/issues/150)).
See [daemon delivery assumptions](../../KNOWN-LIMITATIONS.md#open-gaps): resumed
completion and publication-comment recovery retain results and full checklists,
but comment/PR uniqueness is conditional across pause/resume and restart.

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

New build snapshots persist a UUID `round_id` with their selected feedback
before dispatch. Ordinary failed/crashed jobs still detach and report through
repository health. A later eligible attempt gets a new job ID and deadline,
retaining the saved key and feedback even if newer comments or labels arrive.
This recovers the original logical round before selecting another. Same-key
replay returns its original commit without another task append, including when
the branch has advanced. Changed parsed feedback under that key is refused.
Results and completion comments describe the run state observed when the round
was acknowledged: later paused, aborted or finished state is preserved, and
the supervisor may have advanced again before delivery
([issue #7](https://github.com/mattpolicastro/nightshift/issues/7)).

Local callers can opt in with `--round-id`; retain the same key and saved
feedback on retry. See [key syntax and JSON results](command-json.md#keyed-build-revisions).
Legacy/unkeyed attempts remain compatible but have no retrospective
deduplication guarantee. Restart services onto supporting code before relying
on keyed intake; never retry a rejected `--round-id` by dropping the flag.
Spec re-drafting remains outside this keyed build-revision contract.

Legacy revision receipts initialize consumed IDs once, after authorization.
Trusted records created at or before the receipt's saved time are treated as
consumed, including blank and daemon-prefixed records. This uses comment
creation and review submission times, not edit times. If the saved time is
absent, the current PR head's committer time supplies the cutoff once. The
approximate baseline is saved before further processing and is never
recomputed on restart or tip advance; later IDs stay eligible. No manual
initialization is needed. With no receipt, eligible historical feedback is
available ([PR #61](https://github.com/mattpolicastro/nightshift/pull/61)).

The separate `nightshift serve` supervisor executes admitted tasks and prepares
the current base before `ready_to_publish`. The daemon then attempts
[publication](#publication-and-base-recovery) to the same branch and PR,
commenting the remaining human checks and hosted CI reminder after success.
A human reviews, checks and merges; merging closes the linked issue.

`nightshift status --json` isolates unreadable runs: version-1 JSON still
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
JSON carries `specloop` beside historical worker
`tasks`. The Paseo plugin source now includes the Factory section described
below. Daemon quota pacing for these runs remains separate work.

Optional manifest `[[prepare]]` steps fetch dependencies before offline
verification. They have the same `id`, argv-list `run`, `expect`, `timeout` and `env`
fields as `[[verify]]`. After candidate extraction, they run in order from the
build directory, under their own sandbox allowing network but confining writes
to that directory and denying credential-directory reads. The extracted
source stays read-only; copy it into the build directory if installation needs
writes. Receipts and logs accompany verification, and an unexpected exit or
timeout fails verification before later steps run. Commands are trusted;
use a frozen lockfile for reproducible fetching. See the
[pnpm preparation example](prepare.md).

All four command kinds (`prepare`, `verify`, `acceptance`, `mutation_step`)
accept an optional `env` table, defaulting to `{}`
([issue #135](https://github.com/mattpolicastro/nightshift/issues/135)). Keys
must be nonempty, case-sensitive strings without `=` or NUL; values must be
strings without NUL, including empty strings. Values use the same literal
substitution as argv: `[vars]`, `{src}`, `{build}`, `{sha}` and available
`{snapshot:<id>}` paths. Keys and sibling env entries are not substitution
variables; unknown ordinary placeholders, `$NAME` and shell syntax stay literal.

Each command gets a fresh fixed environment plus its expanded entries, with
no additions carried to other steps and no inherited host variables or
credentials. `PATH` (the harness tool path), `HOME` (`{build}/home`) and
`TMPDIR` (`{build}/tmp`) cannot be overridden, even with empty or identical
values. Other fixed defaults, `LANG=en_US.UTF-8` and `COPYFILE_DISABLE=1`, may
be overridden explicitly. Environment paths grant no additional filesystem
access; sandbox, network, receipt and execution rules remain unchanged.
`{mutant_path}` receives the existing mutation context or an empty default in
ordinary/full runs. An env-only reference does not enable mutation narrowing;
keep the placeholder in argv when narrowing is needed.

This fragment demonstrates matching argv and env substitutions for each kind:

<!-- step-env-example:start -->
```toml
[vars]
env_mode = "offline"

[[prepare]]
id = "env-prepare"
run = ["/usr/bin/printf", "%s\n", "{build}/cache", "{env_mode}"]
env = { CACHE_DIR = "{build}/cache", MODE = "{env_mode}" }

[[verify]]
id = "env-verify"
run = ["/usr/bin/printf", "%s\n", "{build}/cache", "{env_mode}"]
env = { CACHE_DIR = "{build}/cache", MODE = "{env_mode}" }

[[acceptance]]
id = "env-acceptance"
run = ["/usr/bin/printf", "%s\n", "{build}/cache", "{env_mode}"]
env = { CACHE_DIR = "{build}/cache", MODE = "{env_mode}" }

[[mutation_step]]
id = "env-mutation"
run = ["/usr/bin/printf", "%s\n", "{build}/cache", "{env_mode}"]
env = { CACHE_DIR = "{build}/cache", MODE = "{env_mode}" }
```
<!-- step-env-example:end -->

### Retirement rollout

Worker retirement ([issue #16](https://github.com/mattpolicastro/nightshift/issues/16))
preserves attention, outcomes, transcripts and safe leftover-work recovery.
The canonical `nightshift` CLI and build state root `~/.nightshift` implement
[issue #40](https://github.com/mattpolicastro/nightshift/issues/40). Daemon and
supervisor remain independent jobs. The `specloop` CLI/module alias lasts one
release, printing its rename notice to stderr; internal packages, enrollment
values and trusted manifest paths retain their names.

With all writers quiescent and the supervisor stopped, use `nightshift migrate`
before restart. It refuses locked runs, live supervisors and conflicting paths;
interrupted migration resumes from its journal. Supervisor startup also attempts
migration before scheduling. Legacy reads and compatibility links remain for one
release. This does not restart interrupted workers or roll back remote effects.
`[daemon].worktree_root` remains compatibility-only.

Direct Python prerequisites are Python 3.12+, `uv`, `gh`, git and scoped GitHub
access with preflight negative-control/alert checks; providers and verification
commands retain their manifest requirements. Worker OAuth, endpoint models,
verify strings, Claude, pnpm and Swift are not Python front-door prerequisites.
The frozen `bin/nightshift-daemon` still requires `~/.config/nightshift/env`,
`CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN` and `gh/claude/pnpm/node/git/swift` before
Python starts, and scrubs conflicting Anthropic variables. Wrappers/plists and
their stale comments remain unchanged. Hosts lacking these legacy prerequisites
need separate wrapper work or an independently provisioned operator service
preserving trusted env loading, scoped GH_TOKEN refusal, required front-door
tools, credential isolation and preflight guards. See
[startup and host paths](../../README.md#host-path-setup).

**Before merge, a human must complete these live checks; CI cannot establish them:**

- [ ] Confirm proposal blockers are satisfied. Before admission, check the trusted
  implementation manifest allows `docs/landing.md`; preserve all frozen paths.
- [ ] Confirm no active worker process before replacing code; inspect and preserve
  unfinished claims, branches and worktrees.
- [ ] Inspect the actual service config: remove explicit worker enrollment, supply
  trusted manifests, review ignored-key notes and manifest provider credentials.
- [ ] Inspect installed plists and loaded jobs: ProgramArguments, WorkingDirectory,
  environments and wrapper targets for both daemon and supervisor. Record whether
  legacy prerequisites exist; complete and validate separate service work if needed.
- [ ] Arrange a quiet window across every repository, stop all writers and the
  supervisor, prevent KeepAlive restart and back up both state roots. Migrate and
  inspect identities, drafts, receipts/jobs, Git references and credential links
  before restart. Follow the [deployment procedure](../deploy.md); daemon pause
  alone does not stop builds. Keep unperformed host checks pending.
- [ ] Inspect external scripts, aliases, cron/launchd and Paseo calls for retired
  `task.run`, probes, worker status/model output and configuration assumptions.
- [ ] Verify the front door and independent supervisor with intended config/provider
  profiles, historical attention and leftover recovery. Pause/unload does not stop
  admitted builds; inspect supervisor and run processes separately.
- [ ] Review operational prose and live issue/PR references; record pending or failed
  checks on the PR. Merge only after live config/plist/caller inspection completes.

These checks are pending operator work, not performed validation. See
[deferred cleanup](../../KNOWN-LIMITATIONS.md#retirement-follow-ups) and
[pause, job control and supervisor boundaries](#pause-stop-and-the-supervisor).
Frozen `CLAUDE.md` worker instructions and `pyproject.toml` metadata, historical
worker documents with removed-source references and resolved type-baseline
entries remain deferred cleanup outside this change.

## Operations

`nightshift daemon run` polls intake and delivery; `nightshift run <change>`
drives one build. Use `nightshift daemon pause` / `nightshift daemon resume`
for intake and delivery, and `nightshift pause <change>` /
`nightshift resume <change>` for the independent build.

### Enrollment recovery

For GitHub and eligible Gitea repositories, each unpaused poll validates the
enrollment template, checking regular-file status before opening it.
Symlinks to regular files are supported; directories, FIFOs and devices are
rejected without reading. Missing, unreadable or invalid
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

Admitted runs continue publication and build-stage revisions on GitHub and otherwise
eligible Gitea with their copied manifest and recorded branch, even if enrollment
becomes unhealthy. Durable run state counts as admission even while its
notification is pending. Existing jobs
can finish and be collected; delivery needing an unavailable manifest-derived
source path retains the successful result until repair, without re-drafting.
Authorization, publication checks, pause and stopped-job holds still apply.

Gitea's foreign-journal hold overrides that manifest-health bypass, retaining
triggers, receipts and feedback consumption. A direct JSON journal under
`~/.nightshift/engine-switch/<owner>__<repo>/`, even completed or malformed, or an
unreadable location holds all stages and pending delivery and requires human
inspection. The daemon never replays, migrates or deletes that evidence.
Moving the journal out of the active location removes
that gate and permits otherwise eligible dispatch after polls or restart; missing,
empty or nested-only locations do not hold. Worker recovery stays GitHub-only.
Changing the forge to GitHub is not a recovery procedure. Global job reaping and
independent supervisors can still persist local results.

GitHub-only worker-to-specloop recovery runs before new intake on unpaused startup and polls,
even with an unhealthy manifest. Orphaned `agent:working` issues with no claim or
surviving worktree return to `agent:ready` with a `needs_decision` outcome.
Surviving branches/worktrees are preserved for human inspection, with their
phase and location in an issue comment; `agent:working` becomes `needs-human`.
Recovery does not resume or delete that work. A durable journal lets interrupted
recovery finish after restart. Unreadable claims, unknown GitHub state or
conflicting human label changes hold new intake and report repository health;
eligible admitted publication/build revisions and other repositories continue.

### Pause, stop and the supervisor

`nightshift daemon status` reads local active attempts before repository network calls.
Rows show full job ID, stage, repository/item, command, execution state, elapsed
seconds and remaining time or `OVERDUE by Ns`. Starting/legacy jobs without
timing report unavailable. Full history, including failed jobs, is in
`nightshift jobs --all`.
These rows are separate from daemon liveness and independent
`nightshift status` run state ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
Worker working counts, claim/phase/transcript lines and the off-subscription
model report are retired. Other status output and exits, including liveness,
alerts, queue lists/counts, contradiction and inventory diagnostics, are unchanged.

| Command | Budget (seconds) |
| --- | ---: |
| `propose` | 3600 |
| `admit` | 300 |
| `revise` | 1800 |
| daemon intake `git` | 30 |

Background reporting deadlines include startup and persist across restart;
spec re-drafting uses `propose`. On the next authorized, eligible, unpaused
poll, a live overdue attempt gets a comment and `needs-human`, retaining its
job identity. There is no automatic termination or retry. Daemon/GitHub outages,
removed triggers and authorization holds delay reporting; wall-clock changes
affect elapsed/deadline calculations. Follow stop/inspection/release below for
hung work. A late completed result can be delivered after resolving the human
hold under normal eligibility ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).

`nightshift daemon pause` creates `~/.nightshift/pause`, holding new daemon launches
and completion delivery, including publication. In-flight drafting, admission
and revision jobs can finish locally; an already-dispatched external operation
may finish too. `nightshift daemon resume` removes the pause and delivers eligible
retained results without rerunning the job.

`nightshift jobs` lists local active (including starting/overdue), stopping,
stopped, finished-undelivered and diagnostic rows. Ordinary failed and
finished-delivered history is hidden with
`Hidden historical jobs: N (use --all to show).`; `nightshift jobs --all` shows
every attempt without that footer. Only hidden history yields
`No active background jobs.` before the count; empty state says `No background jobs.`
Record/receipt errors remain visible and exit 1 alongside healthy rows.
Finished rows say `delivered` only when the item's current receipt has JSON
`delivered: true`, otherwise `finished (delivery pending)`. A successor receipt
or one without job identity can label older attempts delivered; that item-level
display does not authorize deleting them. Listing is read-only
([issue #103](https://github.com/mattpolicastro/nightshift/issues/103)).

All jobs commands work offline without a running daemon, valid repository
configuration or credentials. Copy the exact full job ID into
`nightshift jobs stop <id>`; prefixes and paths are not accepted. Stop
verifies process-start identity, process group and job-directory ownership
before sending SIGTERM, then SIGKILL if needed, to that group. It records
`stopped` only after no executing group members remain; unverifiable identity
is refused ([issue #8](https://github.com/mattpolicastro/nightshift/issues/8),
[PR #28](https://github.com/mattpolicastro/nightshift/pull/28)).

After durable authorized delivery, validated compaction retains a minimal
request with idea arguments redacted and its original mtime restored, preserving
touchpoint history and date filtering across receipt replacement
([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).

| Delivered attempt artifact | After compaction |
| --- | --- |
| `log` | removed |
| owned `--idea-file` payload | removed |
| `request.json` | redacted |
| `result.json` | retained |
| `process.json` | retained |
| `collected.json` | retained |
| `control.lock` | retained |
| `lock` | retained |

The directory, workflow receipts and build-feedback files remain. Undelivered
and stopped/released evidence is retained. Minimal records still accumulate;
missing history stays missing, and retained text means this is not secure erasure.
Automatic interrupted cleanup waits for a later eligible, authorized,
unpaused item visit; a journal proves delivery, not current authorization. Final-trigger removal,
closure or authorization denial can leave residual logs/inputs indefinitely.
Do not rearm solely for cleanup: normal new work may follow.

Explicit `nightshift jobs compact` authorizes one local pass even while paused,
without current GitHub labels. It takes no ID; `--all` is only for listing.
Cleanup requires the current delivered receipt to name the exact attempt, plus
validated identity, an idle lifetime lock and proven ownership. Missing identity
or a replacement receipt prevents deletion even with an old journal or a
`delivered` display. Each attempt reports `compacted` or `skipped`, with context
and a reason; the summary is `Compacted: N; skipped: M.` Unsafe idea targets
remain with warnings even when other artifacts were compacted. Expected
ineligibility skips successfully; damaged evidence/filesystem errors exit 1
while healthy attempts continue. Repeat safely: completed cleanup is skipped,
and interruptions resume only with a still-matching current receipt. The pass
does not launch, deliver, mutate receipts or clear pause
([issue #103](https://github.com/mattpolicastro/nightshift/issues/103)).

The design's real-tree validation remains a pending human check: pause the
daemon, capture `nightshift jobs --all` and receipts/artifacts on `~/.nightshift`,
run `nightshift jobs compact` once, then capture full inventory again. Compare
IDs, execution/delivery labels, counts, request mtimes, results, control records
and skipped artifacts. Verify only reported eligible logs/owned inputs and idea
arguments changed; inspect retained-target warnings and identity skips. Confirm
the default view exposes pending/stopped/active work, and record commands,
outcomes and unexplained differences before resuming. Detached jobs may finish
while paused; account for that separately. CI validates synthetic trees, not
this host's historical tree. Live references and changed prose still need review.

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
There is no automatic retry of stopped work or rollback of committed effects.
After release, an eligible build revision selects current unconsumed feedback
with a new key, even without a new label event. That new round can append tasks
already committed by the stopped attempt: inspect committed effects before
release. A resolved refusal also permits a new snapshot and key.
Legacy/unkeyed revisions can duplicate a commit if its result was lost.
Keyed retries cover tested process-crash windows, but physical machine-crash
durability remains unproven. Journals and stopped inputs accumulate; deleting
evidence or restoring incomplete backups loses the keyed guarantee. See the
[remaining crash and retention limits](../../KNOWN-LIMITATIONS.md#open-gaps).

Admission can finish while paused and commit runnable state. The independent
`nightshift serve` supervisor can start or continue that build while the daemon
withholds delivery. Neither daemon pause nor job stop cancels admitted runs.
Control the supervisor and active run processes separately; unloading its
launchd job alone does not prove detached run processes exited.

Daemon intake git runs in a new POSIX session. At its 30-second timeout it
SIGKILLs the process group, reaps the child and bounds output draining to at most
one additional second. Descendants that leave the group can escape; a remote
push may already have succeeded. Timeout remains retryable under existing pause
and reconciliation rules; inspect remote refs before retrying
([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
Pending human checks for the implementation PR: stall a real origin on disposable
macOS, inspect git/transport PIDs, daemon responsiveness and remote refs; exercise
an overdue job through pause/resume and status/recovery. CI cannot prove these
live outcomes. Verify issue/PR references and the current issue #17 ledger
(including #34); retain Matt's single readability pass through the three guides.

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
and `~/.nightshift/runs` event logs. Missing or overwritten history can undercount.
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

`nightshift publish <change>` refetches once with a 20-second limit. If the base
moved or integration verification is outstanding, it saves `running` state
with no pause and returns a retryable error; nothing is published. Publish
does not merge or verify inline. `nightshift serve` drives preparation; without
it, run `nightshift run <change>`. Retry publication after the driver restores
`ready_to_publish`; the daemon retries on a later poll. A successful publish
updates the recorded branch and PR and pauses at `human_checks`. Complete the
human checks and hosted CI review before `nightshift finish <change>` archives
the change and marks the PR ready. A human merges it.

Inspect `nightshift status <change>` for the pause detail. Base preparation can
stop at three distinct pauses:

- **`base_conflict`:** the run head stays unchanged. The detail names the
  conflicting paths and base/head commits; inspect those paths and
  `final/base-sync/attempt-*/diagnostic.txt` under the run directory.
- **`base_drift`:** the merge remains on the run branch, but trusted preparation
  or verification failed. The detail names the base and retained head, failed
  checks, `receipts.json` and step logs in `final/base-sync/attempt-*/`.
- **`base_fetch`:** the driver cannot fetch the configured base. Inspect the
  fetch diagnostic and repair access to the source origin or the missing base
  branch, then run `nightshift resume <change>` to fetch and prepare again.

For conflicts or drift, bring the run branch from its `repo.git` into the
source checkout's configured branch before committing a repair. Keep the
current run head in its ancestry; resolve conflicts against the configured
base or fix the failed checks. `nightshift resume <change>` imports a repair
that fast-forwards the run head and restarts the driver. It re-verifies even
if the base has not moved or a manual merge already incorporated it. Resuming
without a repair retries the checks; it does not accept a failure. Old
receipts remain available, and publication waits for readiness.

A fetch failure during **publish** instead leaves readiness and saved checks
intact and returns a retryable error. Restore base access and retry
`nightshift publish <change>`; the daemon can retry on a later poll. This does
not create a driver `base_fetch` pause or permit a push using a stale base.

### Explicit forge publication

The manifest's `run.github` selects the engine destination: `owner/repo` and
`github.com/owner/repo` retain GitHub behavior and unchanged `gh` argv. For Gitea,
set it to `forge.example/owner/repo`; that hostname must uniquely match a Gitea URL in
[forge configuration](../../config.example.toml). Multiple definitions on the
same host refuse even with different ports or prefixes; `repos.forge` does not
route these commands. The host field is a hostname, not a URL or port selector.

Set `NIGHTSHIFT_FORGE_CONFIG` in the actual process environment to a nonblank
absolute TOML path, or use the checkout's default `config.toml`. Provision the
selected `token_env` bot token in `~/.config/nightshift/env` or the process:
the file wins even when its value is empty. There is no landing-token or
manifest step/env fallback. Git fetch/push uses separate git credentials.
Gitea requires exactly one HTTP(S) origin with matching scheme, hostname,
effective port, encoded URL prefix and case-sensitive owner/repository, allowing
an optional terminal `.git`. Explicit default ports are equivalent; SSH, scp
and local origins cannot prove the configured installation identity and refuse.

Base fetch and both publish and finish pushes reread the enrolled source clone's
local `credential.helper` entries before each transport, including retries.
They forward every value in order, including empty resets, through the child
Git config environment for that command's lifetime. Nothing is copied into
`repo.git/config`, the source config, run state or logs; no manual helper copying
is needed. Existing global helpers remain available unless an empty reset clears
them. Git transport with no source-local helpers keeps its existing argv,
options and behavior. Helper-bearing pushes use Git safety flags and disable
terminal prompting; fetch retains those protections.

Helpers run from the isolated run repository. Use absolute executable paths
when needed: relative paths and working-directory assumptions are not rewritten.
The service account needs the helper binaries and access to the host keychain
or credential store; forwarding does not provision credentials or replace the
separate forge API bot token. Live source-local authentication through base fetch,
publish and finish remains pending human validation under
[issue #174](https://github.com/mattpolicastro/nightshift/issues/174).

After existing base preparation and checks, use:

```sh
uv run nightshift publish <change>
uv run nightshift finish <change>
```

Complete the PR's human checks before finish. Publication refuses changes under
`.gitea/workflows/`, `.github/workflows/` or `.forgejo/` on either forge before
push or PR writes. Gitea publish reuses an exact existing PR or creates a
WIP-prefixed title, not native draft state. Finish archives and pushes, then marks
the PR ready by removing one configured WIP prefix with a title-only PATCH and
reading back the result; it does not merge. Inspect remote effects before retrying
an ambiguous create failure: lookup-before-create cannot prevent concurrent or late-visible duplicates.
Title edits between GET and PATCH can be overwritten without conflict detection;
verification proves only the resulting title. Critic uses 30-second socket
timeouts and a 120-second request-start cutoff, not elapsed-time bounds: slow
response bodies can overrun both. See [remaining risks](../../KNOWN-LIMITATIONS.md#open-gaps)
and [issue #129](https://github.com/mattpolicastro/nightshift/issues/129).

These explicit commands and critic reads are separate from daemon eligibility.
Gitea daemon drafting, admission, revisions, publication and pending delivery
retain authorization, health, pause, stopped-job and foreign-journal gates
([issue #150](https://github.com/mattpolicastro/nightshift/issues/150)).
[Explicit configured arming](../arming.md#configured-gitea) supports Gitea with bot
credentials and verified label read-back. [Terminal landing](../landing.md) supports
Gitea with the human `land_token_env`; Factory Land remains GitHub-only.
Read-only preflight is not execution authorization. First real
WIP publication and finish prefix removal, and live Paseo presentation, remain
pending human checks in the approved design; CI fixtures do not prove them.
The [enrollment checklist](../../README.md#quickstart) also requires daemon bot-token
sourcing with every landing variable absent, matching supervisor configuration,
real human-filed issue/WIP-PR observations and refusal of an unlisted label actor.

## Paseo Factory section and `nightshift act`

The Nightshift plugin now includes a **Factory** section for specloop runs,
with paused runs first, change, status, tasks done/total and PR links. Paused
rows use the same action headline and explanation as Slack, and offer exactly
the verbs listed in `nightshift status --json`, plus a separate confirmed Land
control for verifiable human-check pauses. Resume has an optional note
field (up to 2,000 characters); Accept is available for `stuck` and
`needs_human`, Publish for `ready_to_publish`, Finish for `human_checks`, and
Abort at every pause. Worker task rows remain read-only.

**Publish and Finish push to the forge selected by the run manifest, so Paseo
asks for confirmation before either action**, even before a PR exists. Cancel
sends nothing. GitHub retains draft/ready behavior; configured Gitea creates a
WIP-title PR and finish removes that prefix after archive/push and verifies the
title. Finish does not merge. Provision config/bot environment and separate git
credentials in the Paseo host process using [engine setup](#explicit-forge-publication).
Factory's display-origin allowlist does not route or authorize writes: only
HTTPS root `/owner/repo/pulls/N` Gitea PRs display. Engine-supported HTTP loopback
or URL-prefix installations may require direct CLI use. Land remains GitHub-only;
confirmation does not authorize daemon work, which follows its own
[enrollment gates](#enrollment-recovery). The page shows results and refreshes.

### Factory landing

**Land** invokes the existing reviewed-PR landing flow, including human-check
attestation, guarded finish, CI and merge; **Finish** alone does not merge.
Land appears only for a matching non-stale `human_checks` offer. Its dialog
shows PR/head and asks you to confirm that you performed the human checks.
It freezes the five fields below; opening or Cancel sends nothing. Any change
invalidates the dialog rather than authorizing a new head. Admission and worker
startup revalidate the same identity independently.

| Offer field | Bound value |
| --- | --- |
| `change` | Selected run. |
| `pr` | Canonical GitHub PR URL. |
| `pause_id` | Published human-check pause. |
| `head` | Full published commit SHA. |
| `since` | Pause timestamp, including zero and fractional values. |

Private plugin transport starts a detached worker through trusted host settings;
it adds no public command or act verb and is unreachable from canonical CLI,
daemon, intake and supervisor paths. Landing status and Stop have independent
RPC/cache state from ordinary actions. A failed channel retains stale data;
refresh never starts work. Progress and retained refusals/results stay in the
originating row, or above the list when absent/folded. On a lost submission
response, refresh status without automatic resubmission.

The worker holds a per-PR flock for its lifetime without intake receipts, jobs
inventory, escalation or manual release. Daemon pause blocks new attempts but
leaves status and Stop available. Stop requests SIGTERM only after verifying
worker identity, without rollback or guaranteed termination. Worker death can
release the lock while descendants survive; Stop then refuses. Wait for them or
terminate them manually after inspection. Each fresh attempt independently proves
prior group drainage under the lock, even with an unchanged head or terminal
result. Independent terminal landing is outside this lock. Follow the keyed
[recovery cases](../landing.md#recovering-from-a-refusal), including late duplicate
comments and unpushed archive recovery through explicit direct Finish.

History shows newest 20 plus total, with current live progress separate. Overdue
after 2400 seconds is display-only. Logs/results persist until manual pruning;
preserve undrained-group evidence. Reading does not acknowledge or deliver,
and jobs compaction does not apply. Display bounds do not bound storage or
metadata scanning. See [evidence artifacts](../../plugins/nightshift/README.md#landing-evidence)
and [landing limits](../../KNOWN-LIMITATIONS.md#factory-landing).

### Ordinary Factory actions

```sh
uv run nightshift status --json
uv run nightshift act <change> <verb> [--note TEXT] --json
uv run nightshift act example-change resume --note "Use the revised acceptance criteria" --json
```

The verbs are `resume`, `accept`, `publish`, `finish` and `abort`. `act`
reloads the run under its lock and checks the current pause; unknown changes,
unavailable verbs, notes over 2,000 characters and busy locks are refused.
Its version-1 JSON outcome contains `version`, `outcome` (`done` or `refused`),
`verb` and `detail`, with exit status 0 for done and 1 for refused. Resume and
Accept behave as with `--no-run`; the separate `nightshift serve` supervisor
continues execution. Direct CLI calls do not prompt: interfaces must obtain
confirmation before invoking Publish or Finish.

Factory refreshes every 60 seconds while visible and active, on opening and
on return to the foreground. Server reads are cached for 15 seconds; actions
invalidate the cache. A failed read retains the last good list marked stale
with an error. The server uses an argv array and checks the change and verb
against its last good status before calling `act`, which rechecks live state.
Only display fields cross to the client, with home directories replaced by
`~` and raw command errors excluded.

The supported range is `>=0.10.1`, built against SDK 0.14.0. It has no upper
bound because Paseo refuses a plugin outside its declared range, so a cap drops
the plugin on every release; a daily CI canary tests against the latest and
beta SDKs instead. See the [plugin README](../../plugins/nightshift/README.md#factory)
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
uv run nightshift admit <repo-manifest.toml> --change <change> --branch spec/<change> [--pr <url>]
uv run nightshift serve                 # supervisor: drives every running change
uv run nightshift run <change>          # or drive one by hand until the next pause
uv run nightshift status [<change>]
uv run nightshift pause <change>        # stop at the next task boundary (now, if nothing drives it)
uv run nightshift resume <change> [--note "..."]
uv run nightshift accept <change> [task]
uv run nightshift waive <change> <task> <text>
uv run nightshift publish <change>      # push to the recorded PR, or open a draft PR
uv run nightshift revise <change> --feedback <file> --round-id <key>  # retain key and input on retry
uv run nightshift finish <change>       # archive the change, mark the PR ready
uv run nightshift propose <manifest> <change> --idea "Add a gate" [--source doc]   # draft a change
uv run nightshift drill <change>        # seeded defects through the review gate
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
defaulting to `0`, `timeout` defaulting to `300` seconds, and `env` defaulting
to `{}`); they do not join
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
from both numerator and denominator. Preparation failures and timeouts always
produce `invalid`. `mutation_invalid_steps` defaults to `["configure", "build"]`;
failures or timeouts in those selected steps are also `invalid`. An explicit
array replaces these defaults, rather than extending them; `[]` removes only
the configured-step exception, never preparation's precedence. This setting
classifies steps; it neither defines nor selects commands. The first failing
receipt decides the result, in every route and execution phase.
For example, two killed, one survived and three timed out score `2/3`, not
`5/6` or `2/6` ([issue #31](https://github.com/mattpolicastro/nightshift/issues/31),
[PR #44](https://github.com/mattpolicastro/nightshift/pull/44)).

**Exclusions and measurement.** Full committed source protects Python
docstrings and comments, including inline comments, in `.py` and `.pyi` files;
adjacent executable code stays eligible. Other languages have limited
line-based filtering: comment prefixes (`#`, `//`, `/*`, `*`) and complete
`/* ... */` blocks whose opening and closing lines contain only comments.
This is not general string or inline-comment parsing. Python runtime strings
and unsupported-extension strings or JSX styles are not automatically excluded
([issue #21](https://github.com/mattpolicastro/nightshift/issues/21),
[PR #48](https://github.com/mattpolicastro/nightshift/pull/48)).

**Native candidates.** Exact, case-sensitive suffixes `.dart`, `.ts`, `.swift`,
`.kt`, `.c` and `.cpp` select textual C-family operators: `true` ↔ `false`,
`&&` ↔ `||`, `==` ↔ `!=`, `<` ↔ `<=`, `>` ↔ `>=`, and removal of leading
logical `!`. Token guards exclude identifier fragments, longer operators and
postfix `!`. There are no integer, arithmetic or Python control-flow operators
in this set. Python and other extensions retain their existing operators
([issue #133](https://github.com/mattpolicastro/nightshift/issues/133)).

For these six suffixes, any ASCII apostrophe, double quote or backtick anywhere
on a physical line excludes that entire line before sampling, including useful
code beside strings and quotes in comments. Lines whose first non-whitespace
text starts with `//`, `/*` or `*` are also excluded. Existing complete-block
protection and literal skips still apply. No interpolation or division parsing,
language-specific syntax validation or whole-file refusal is performed: other
eligible lines remain candidates. Quote-free interiors of multiline strings
and inline/trailing comment text outside protected blocks can still mutate.
Tokens in generics or overloaded operators may not change useful behavior;
inspect actual mutants and use narrow literal skips where needed.

[Dart manifest](../../examples/specloop/dart.toml)

Copy this trusted template outside the repository and configure its project,
toolchain and provider paths. Preparation copies the current source into writable
`{build}/project` and fetches locked dependencies. Every mutant prepares afresh;
`dart-analyze` precedes `dart-tests`, and `mutation_invalid_steps = ["dart-analyze"]`
classifies analyzer failures as invalid. Tests alone or cached baseline build
state cannot establish that classification. Coverage reporting is project-specific
and must be integrated separately; this template is not turnkey enrollment.

The first real evidence gate on scheming-vizier remains a pending human check
with its actual Dart toolchain, package cache and manifest. Confirm a passing
baseline, invalid analyzer/compilation failures, meaningful killed and survived
mutants, and a useful sample after quote-line skips. Inspect source, receipts,
skips and outcome totals, and record the findings before claiming operational
support. Structural fixtures do not prove real-engine usefulness; no meaningful
candidates is a limitation, not evidence of support.

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
Except for the overage hold below, ordinary provider recovery pauses only when every profile in that role's chain is
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

**Claude quota telemetry** classifies structured rejections as usage limits;
allowed/warning snapshots do not trigger usage matching. Without a recognized
status, failed calls fall back to stderr and explicit provider errors, never
agent-authored prose. Inspect `exhausted.rate_limit` in `events.jsonl` for
`status`, `type`, `resets_at` and `using_overage`, and `exhausted.until` for the
chosen deadline: a future reset plus 60 seconds, or a 30-minute backoff when
missing, invalid or stale. Ordinary quota waits survive restart and `serve`
resumes them when due. Reported overage instead records an `overage` event with
the same telemetry and pauses the run, even with a usable fallback. This hold
has no automatic deadline; inspect billing settings, then explicitly use
`nightshift resume <change>` to continue. Another overage report pauses again.
Telemetry is handled after the CLI returns: it neither interrupts a live call
nor prevents already-incurred charges. Real-limit validation remains pending;
synthetic fixtures do not prove the CLI's behavior at a real limit
([issue #162](https://github.com/mattpolicastro/nightshift/issues/162)).

**Providers are pluggable.** Two adapters: `codex` (`codex exec`) and `claude`
(`claude -p`). Each can use its CLI's subscription login or a local
`base_url`. A codex profile can also name its own login with `auth`, for
example `auth = "~/.codex-api/auth.json"` for API credits created with
`CODEX_HOME=~/.codex-api codex login --with-api-key`. List it after the
subscription profile in a role's chain, and the role moves to it when the
subscription is usage-limited or unavailable. The seal refuses diffs that
contain any `~/.codex*/auth.json` value.

Seal discovery also reads every declared forge's `token_env` and
`land_token_env`, including unused forges, from the supplied environment, the
supervisor process environment and `~/.config/nightshift/env`. Every distinct
nonempty value is protected, including short and shadowed values. Existing
fixed-name and profile-auth scans retain their length filters. Refusal reports
affected paths, never token values.

Only the supervisor's process `NIGHTSHIFT_FORGE_CONFIG` selects a custom forge
config; it must be a nonempty absolute path. Otherwise discovery uses
`nightshift.config.DEFAULT_PATH`, allowing an absent default. Invalid selected
config or unreadable credential files block sealing. Candidate files and manifest
environment values cannot redirect selection, and enrollment is not loaded.
For a custom daemon `--config`, operators must provision the same path and bot
credential sources in the independent supervisor; see [host setup](../../README.md#host-path-setup).
The frozen `bin/specloop-serve` does not source the daemon env file. Discovery
parses it without shell execution or environment injection. It cannot detect
unavailable secrets, including human tokens held only in a keychain; it does
not retrieve them. Never inject landing tokens into the daemon just for scanning.

Spike S1 probed, from the harness itself, that each session can write only its
workspace and has no network. The claude adapter with `qwen3-coder-next` on the
a local inference box is proven. The codex adapter against a local backend is untested.

**Run state** lives in `~/.nightshift/runs/<change>/`:
- `state.json` and `events.jsonl`;
- a bare `repo.git` that the harness owns and commits into;
- per-attempt transcripts, seal, receipts and review decisions;
- `followups.md`, the non-blocking review findings.

Each Codex session uses disposable `<stem>.tmp` beside its transcript as
writable `$TMPDIR`. The adapter attempts removal during finalization, after
the existing runner returns (including failure, signal exit or wall/idle
timeout), or when setup, spawning or result interpretation raises. This
cleanup retains transcripts, schema/output JSON, seals, receipts, reviews and
verify build directories; their storage still follows existing policies
([issue #116](https://github.com/mattpolicastro/nightshift/issues/116)).

Removal failures append a `session_tmpdir_cleanup_failed` JSON event to that
session's `<stem>.stderr`, preserving existing diagnostics. Cleanup and event
write failures leave the original result or exception unchanged; an unwritable
stderr sink can prevent durable recording. Finalization does not confirm that
the process group has exited: a surviving descendant may race removal, cause
it to fail or recreate scratch afterward. Existing runner timeout handling and
Claude behavior are unchanged. Historical scratch is not swept, and harness
SIGKILL or host failure can leave new scratch behind.

The real-host check remains pending: after the first updated build, a human
must inspect one finished real Codex attempt, record its path and outcome,
confirm `<stem>.tmp` is absent, check session stderr for cleanup failures and
confirm transcripts, outputs, seals, receipts, reviews and verify builds remain.
Automated fixtures do not establish this live-host result.

The guarded migration uses these build-state mappings:

| Legacy build state | Canonical build state |
| --- | --- |
| `~/.specloop/runs/<change>` | `~/.nightshift/runs/<change>` |
| `~/.specloop/proposals/<change>` | `~/.nightshift/drafts/<change>` |
| `~/.specloop/homes/<entry>` | `~/.nightshift/homes/<entry>` |

Daemon receipts at `~/.nightshift/proposals/<owner__repo>/<number>.json` and
job evidence at `~/.nightshift/proposals/<owner__repo>/<number>.jobs/` are
unchanged and never disposable drafts.

The source repository's checkouts are never written to.

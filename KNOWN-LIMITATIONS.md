# Known limitations

Open gaps and queued follow-ups for Nightshift and its specloop engine. See the
tracking ledger, [issue #17](https://github.com/mattpolicastro/nightshift/issues/17).
Fixes land through Nightshift's own front door; these gaps remain unresolved.

## Queued follow-ups

- **Sync at task boundaries:** pick up fast-forward human commits on the source
  branch without pausing.
- **CLI/state compatibility:** [issue #40](https://github.com/mattpolicastro/nightshift/issues/40)
  makes `nightshift` canonical and moves build state to `~/.nightshift`.
  The `specloop` alias and legacy-path links remain for one release; their
  removal and frozen-wrapper modernization are follow-ups. Migration requires
  a stopped supervisor and controlled writers; follow the
  [host rollout checklist](docs/deploy.md#human-host-checklist).
  Legacy build `proposals/<change>` moves to `drafts/<change>`; daemon
  `proposals/<owner__repo>` receipts and jobs stay in place.

## Retirement follow-ups

Specloop is the sole/default engine; explicit worker enrollment is refused
([issue #16](https://github.com/mattpolicastro/nightshift/issues/16)). History and
safe leftover recovery remain. Complete the human
[pre-merge config/plist/external-caller checklist](docs/specloop/README.md#retirement-rollout);
tests do not validate an installed host or migrate its configuration.

Frozen `bin/**` and `launchd/**`, including worker/pause comments, await separate
cleanup. Frozen `CLAUDE.md` retains obsolete worker instructions and
`pyproject.toml` its old project description; neither defines a supported engine.
[Provider notes](docs/providers.md), [endpoint design](SPEC-endpoints.md) and
[validation history](docs/validation.md) are excluded historical worker material.
Their evaluation counts and links to removed source remain unchanged; historical
link repair is deferred. Resolved diagnostics in `mypy-baseline.txt` also await
cleanup; check mode permits them without repairing that frozen baseline.
None of these follow-ups establishes live validation or a completed host rollout.

## Open gaps

- **Codex session scratch:** cleanup now attempts to remove disposable
  `<stem>.tmp` at adapter finalization after the existing runner returns or
  setup/spawning/result interpretation raises
  ([issue #116](https://github.com/mattpolicastro/nightshift/issues/116)).
  Transcripts, schema/output JSON, seals, receipts, reviews and verify build
  directories remain under existing retention policies. Removal failures append
  `session_tmpdir_cleanup_failed` events to session `<stem>.stderr`; neither
  cleanup nor diagnostic-write failure changes the original result or exception,
  and an unwritable sink can leave no durable event. There is no confirmed
  process-group exit: surviving descendants may race removal, cause failure or
  recreate scratch. Runner timeout handling and Claude behavior are unchanged.
  Historical leftovers are not swept; harness SIGKILL and host failure can
  bypass finalization. After the first updated build, inspection of one finished
  real Codex attempt remains a pending human check: record its path/outcome,
  confirm scratch absence, inspect stderr and confirm the retained evidence and
  verify builds. Automated fixtures cannot establish live-host cleanup.
- **Revision recovery limits:** keyed build revisions now recover the same
  logical round across tested process-crash windows, including a commit before
  wrapper result persistence ([issue #7](https://github.com/mattpolicastro/nightshift/issues/7)).
  Ordinary crashed attempts detach as before; later eligible attempts retain
  the saved key and feedback. Replay returns the original commit and observed
  state, preserving later pauses, aborts and completion; changed payloads under
  that key are refused. See the [optional `--round-id` contract](docs/specloop/command-json.md#keyed-build-revisions).
  Legacy/unkeyed revisions still risk duplicate appends between commit and
  result persistence; old receipts cannot acquire retrospective deduplication.
  Consumed feedback IDs protect durable successes
  ([PR #61](https://github.com/mattpolicastro/nightshift/pull/61)).
  Deterministic process-crash tests do not prove physical machine-crash
  durability: interruption of a real machine/VM mid-write remains an unperformed
  human check in the design, alongside live-link and reader review. Missing or
  inconsistent journal/Git evidence is refused, not reconstructed. Journals
  accumulate with retained runs; deletion or incomplete backups lose protection.
- **Revision re-runs:** same-round replacement results now get separate comments
  ([issue #137](https://github.com/mattpolicastro/nightshift/issues/137)), but
  `rejected` and `needs_input` still retain `agent:revise` and add `needs-human`.
  To start a fresh round, resolve the questions or contradictions, remove
  `agent:revise`, clear `needs-human`, then have a trusted actor re-add
  `agent:revise` to create a new label event. Applying an already-present label
  creates no fresh event. Normal pause, authorization and eligibility gates
  still apply; relabeling does not release a stopped-job hold (see below).
  On the next live rejected revision, a human must confirm the new reason is
  posted on the PR and the earlier result remains visible, and record the
  comment link. Checking that the remove/clear/re-add sequence creates a new
  event on the next fresh request also remains pending; CI does not establish
  these live interactions.
- **Pause boundary:** jobs already running can finish while the daemon is
  paused. An admission that completes while paused can still build under the
  independent `nightshift serve` supervisor. Jobs have their own sessions, so
  unloading the daemon does not stop them. `nightshift jobs stop <id>` stops
  a background job ([PR #28](https://github.com/mattpolicastro/nightshift/pull/28));
  it does not pause an already admitted run.
- **Stopped-job holds:** stopped jobs stay held across resume, daemon restart
  and relabeling ([PR #28](https://github.com/mattpolicastro/nightshift/pull/28)).
  Explicit `nightshift jobs release <id>` now detaches a confirmed stopped
  attempt's matching receipt reference
  ([issue #34](https://github.com/mattpolicastro/nightshift/issues/34)); follow
  the [pause, inspection and release instructions](README.md#pause-and-job-control).
  The historical attempt stays stopped and its result cannot be delivered;
  evidence and inputs remain on disk. Release launches nothing: a later poll
  uses normal authorization, pause, blockers, health, approval and recovery
  rules. There is no automatic retry or rollback of committed effects; the
  next eligible build revision selects current unconsumed feedback with a new
  key, even under the same label event, and can append tasks the stopped attempt
  already committed. Inspect committed effects before release. The legacy/
  unkeyed crash risk, unproven keyed machine-crash durability and independent
  admitted builds above still apply.
  These are intake holds; Factory landing has separate worker-held locks and
  verified Stop, with no receipt release. See [Factory landing](#factory-landing).
- **Background jobs:**
  - Persisted reporting deadlines are propose 3600 seconds, admit 300 seconds
    and revise 1800 seconds. A live overdue attempt gets `needs-human` on the
    next authorized, eligible, unpaused poll; no automatic termination or retry.
    Restart does not reset timing. Daemon/GitHub outages, removed triggers and
    authorization holds delay reporting; wall-clock jumps affect observation.
    `nightshift daemon status` shows active timing/`OVERDUE`; legacy timing is unavailable.
    Use `nightshift jobs --all` for history and the recovery instructions above
    ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
    This escalation applies to intake jobs. Factory landing's 2400-second overdue
    indication is display-only; it causes no escalation, termination or retry.
  - Default `nightshift jobs` shows active (including starting/overdue),
    stopping, stopped, finished-undelivered and diagnostic rows. Ordinary failed
    and finished-delivered history is hidden with a count; `nightshift jobs --all`
    shows it all. Finished rows say `delivered` only for an item receipt with
    JSON `delivered: true`, otherwise `finished (delivery pending)`. This item-level
    label can reflect a successor receipt; it does not establish deletion identity
    ([issue #103](https://github.com/mattpolicastro/nightshift/issues/103)).
    This inventory/delivery rule excludes Factory landing: it has separate
    records, independent status/Stop and no delivery, hold or release receipts.
  - Delivered attempts lose logs and safely owned idea payloads; minimal
    requests redact idea arguments while retaining original mtimes for
    touchpoint history. Results, control metadata and directories remain.
    Undelivered and stopped/released evidence, workflow receipts and build
    feedback remain too: storage still grows and this is not secure erasure
    ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
    Automatic interrupted cleanup waits for a later eligible, authorized,
    unpaused visit.
    Factory landing logs/results instead persist until manually pruned; reads
    never acknowledge them and jobs compact does not touch them. Preserve
    undrained-group evidence. Neither bounded landing display nor intake log
    cleanup promises bounded disk use or bounded metadata enumeration cost.
    After final-trigger removal, closure or authorization denial, residual
    artifacts can remain indefinitely; a cleanup journal grants no authorization.
    Do not rearm solely for cleanup, because normal new work may follow.
    Explicit offline `nightshift jobs compact` makes one pass, even while paused,
    without current GitHub labels. It requires a current matching delivered
    receipt and the existing identity, idle-lock and ownership checks. Missing
    identity or a replacement receipt prevents cleanup even with an old journal
    or a `delivered` display. Repeats skip completed cleanup; interruptions resume
    only while the current receipt still matches. It launches/delivers nothing
    and leaves receipts and pause unchanged. Per-attempt outcomes end with
    `Compacted: N; skipped: M.`; retained-target warnings can accompany compaction.
    Expected ineligibility skips successfully; damaged evidence/filesystem errors
    exit 1 while healthy attempts continue. Substantial older history may remain
    ([issue #103](https://github.com/mattpolicastro/nightshift/issues/103)).
    The design's real-tree check remains pending: pause, capture full inventory
    and receipts/artifacts, compact once, then compare full inventory, counts,
    mtimes and retained evidence before resuming. Inspect warnings and identity
    skips, accounting separately for detached jobs finishing during pause; see
    the [comparison checklist](README.md#pause-and-job-control). CI covers
    synthetic trees, not this host's history; live references/prose await review.
  - Daemon intake git has a 30-second command timeout, then SIGKILL to its POSIX
    process group with at most one further second for reaping/output draining.
    Descendants that leave the group can escape. Remote acceptance may precede
    timeout; inspect remote refs before retrying. Existing retry/reconciliation
    and pause rules remain ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
    Real stalled-origin fetch/push validation remains a pending human check;
    CI process-group tests cannot establish remote acceptance or live behavior.
- **Touchpoint history:** the report counts observed local history and can
  undercount when records are missing or overwritten; it cannot reconstruct
  missing actions ([PR #53](https://github.com/mattpolicastro/nightshift/pull/53)).
- **JSX styles:** automatic JSX style filtering is descoped. Explicit
  `mutation_skip` patterns are available as case-sensitive literal whole-line
  substring exclusions before sampling; styles are not automatically excluded
  ([PR #48](https://github.com/mattpolicastro/nightshift/pull/48)).
- **GitHub-only automation:** the automated daemon front door and publication use
  GitHub. Local drafting and verification do not require a GitHub host; they
  do not provide automated intake or publication for other hosts. Explicit engine
  publish/finish and Factory confirmations now support configured Gitea; see
  [engine setup](docs/specloop/README.md#explicit-forge-publication).
  Gitea daemon stages and pending delivery are disabled for every allowlist:
  core's owner shortcut bypasses adapter allowlists, and multi-write label
  transitions can lose their trigger after an accepted DELETE with a lost response.
  Foreign engine-switch journals require inspection; clearing them, repairing
  manifests or passing preflight does not enable execution
  ([issue #128](https://github.com/mattpolicastro/nightshift/issues/128),
  [issue #122](https://github.com/mattpolicastro/nightshift/issues/122)).
- **Deferred Gitea workflow guarantees:** core authorization's User-owner
  shortcut bypasses adapter allowlists; organization/team membership
  authorization is unsupported. Label transitions lack durable intent/recovery:
  deleting `spec:approved` before adding `needs-human` can lose the discovery
  trigger if the DELETE succeeds but its response is lost. Core marker lookup
  and reconciliation can accept human marker copies; the standalone adapter
  still restricts comment updates to bot-owned comments. Drafts use a title
  prefix rather than native draft state. These gaps keep daemon drafting, admission,
  revisions, publication and pending delivery unconditionally disabled.
  Foreign-journal inspection adds a diagnostic, not an alternative enabling
  path. GitHub execution and saved revision-round guarantees are unchanged.
- **Gitea evidence limits:** preflight's forbidden 404 cannot establish that the
  repository exists; a human must independently confirm it. Protection evidence
  proves only an exact base rule exists, not adequate policy; wildcard-only rules
  and denied reads remain unverified failures. Clone helpers run with disabled
  prompts and ten-second subprocess bounds, but may use their own storage or
  network. A matching username does not independently prove password account or
  scope. Real-instance preflight, protection-policy and bot-helper inspection
  remain pending human checks, not CI proof of live workflows. See the
  [enrollment checks](README.md#quickstart) and [supervisor provisioning](README.md#host-path-setup).
- **Explicit Gitea publication risks:** finish's GET then title-only PATCH can
  overwrite an intervening human edit. There is no conflict detection; read-back
  proves only the resulting title, not preservation of concurrent edits.
  Critic's 30-second socket timeout and 120-second request-start cutoff are not
  elapsed-time bounds: slowly arriving bodies can overrun both. Lookup before
  create cannot prevent duplicate PRs from concurrent creators or late visibility
  after an ambiguous POST; inspect remote effects before retrying. The first real
  WIP PR creation and finish prefix removal, plus live Paseo confirmation/Cancel,
  remain pending human checks in the design, not CI evidence
  ([issue #129](https://github.com/mattpolicastro/nightshift/issues/129)).
- **GitHub-only command boundaries:** landing and unrelated plugin GitHub
  paths have not migrated to Gitea. Explicit engine publish/finish and critic
  context use the manifest-selected forge. Gitea enrollment cannot
  authorize GitHub landing; daemon status skips its queue queries. Named GitHub
  definitions still use ambient `gh` authentication and host selection.
  [Explicit configured arming](docs/arming.md#configured-gitea) supports Gitea
  with bot credentials and verified label read-back; it does not enable any
  Gitea daemon stage or pending delivery. Real issue arming and later drafting
  remain pending human checks; drafting must await the separate hold prerequisites
  ([issue #131](https://github.com/mattpolicastro/nightshift/issues/131)).
- **Config:**
  - Manifest failures now hold new drafting/admission/spec re-drafting locally;
    admitted GitHub publication/build revisions continue. GitHub worker leftovers are released
    or preserved for human inspection, and new names include the owner
    ([issue #13](https://github.com/mattpolicastro/nightshift/issues/13)). See
    [enrollment recovery](docs/specloop/README.md#enrollment-recovery).
  - Config edits still require a daemon restart; same-path manifest repairs
    retry on a later poll. Manifests remain trusted operator-managed files:
    regular-file checks do not protect against hostile path-replacement races
    or an unavailable filesystem.
  - Unsafe repository spellings such as `owner/widget..js` remain unsupported
    for new work. Names are not encoded; branch safety checks remain in force.
    The naming convention supports GitHub owners, not arbitrary namespaces.
  - Recorded legacy identities remain usable, but deployed runs, branches and
    PRs are not automatically migrated or renamed. Existing global name
    collisions require operator decisions. Exact branch lookup cannot discover
    an old PR after both proposal receipt loss and a title change; it does not
    scan global runs to infer ownership or guarantee universal deduplication.
  - GitHub transport retry risks are unchanged: internal label-write retries
    do not reread labels. Recovery journals cannot guarantee exactly-once
    effects within an ambiguous request, especially if a human rearms during
    a retry. Surviving worker work requires inspection, not automatic resumption.

## Factory landing

Factory's separate Land confirmation freezes change, PR, pause ID, published
head and pause timestamp; Finish still only archives, pushes and marks ready.
Private plugin transport starts a detached worker and preserves canonical
CLI/daemon/supervisor isolation. Status and Stop are independent of ordinary
Factory actions and remain available during daemon pause. Progress and retained
refusals/results appear in the originating row or above the list when hidden.
The worker holds per-PR flock for its lifetime; descendants do not inherit its
descriptor. There is no manual release, intake inventory, escalation or receipt.
Independent terminal landing does not take the Factory lock.

Newest 20 attempts plus total and separate current progress bound display only.
Full logs and small records persist until manual pruning. Preserve process/group
evidence until drainage is proved regardless of outcome or age; status reads
neither acknowledge nor repair it. Storage and metadata enumeration can grow.
See [evidence artifacts](plugins/nightshift/README.md#landing-evidence).

Terminal and Factory landing reconcile ambiguous attestation creates within one
invocation, sending at most one POST. A fresh invocation can see no marker and
create one before an earlier POST commits late. A valid acknowledgement need not
trigger another lookup or detect duplicates; a subsequent lookup refuses
`duplicate marker matches; resolve duplicates before retrying`. Local drainage
does not settle remote requests. There is no retained-create guard or guarantee
of cross-invocation uniqueness; all other landing gates remain unchanged.

Finish checks the validated PR head after locked reload and before archive,
push or ready side effects. A mismatching or unreadable local head refuses even
if the earlier landing identity check passed. Existing repository/task checks,
archive/base-update successor validation and head-conditioned merge remain.
The unpushed local archive exception below requires an explicit operator step;
landing neither substitutes that archive head nor automatically bypasses its guard.

| Recovery case | Inspect and resolve | Next step |
| --- | --- | --- |
| `lost-response` | Submission may have started despite a lost response. Refresh status and inspect retained evidence. | Observe the existing attempt without automatic resubmission. |
| `stopped` | Verified Stop requests group SIGTERM, with no SIGKILL escalation, rollback or guaranteed termination. Inspect effects and resistant descendants. | Wait for termination; a fresh confirmation independently verifies drainage under flock, without manual release. |
| `worker-death` | Worker-only death releases flock while descendants can survive. Stop explicitly refuses missing/unverifiable worker identity. Inspect effects, then wait for descendants or terminate them manually. | A fresh attempt independently proves group absence under flock; only ESRCH from the safe kernel probe proves drainage. A free lock, unchanged head or terminal result is insufficient; zombie or unverifiable groups still block. |
| `duplicate-markers` | Inspect prior effects and delete one duplicate comment after the late-POST exception above. | Resolve outstanding attempts, then Land if eligible or `uv run python -m nightshift.land <url> --checks-done`; the unique trusted remaining comment is updated through existing gates. |
| `revised-head` | Locked finish refuses before archiving. Inspect `nightshift status <change> --events 20`, the revised head and human checks. | Review the new published head before confirming a fresh Factory offer or retrying terminal landing. |
| `unpushed-archive` | Local archive A exists after a failed push, remote remains H and the run is still at human_checks. Guarded landing passes H and refuses A; Factory can refuse the offer mismatch earlier. Once attempts stop, inspect prior local/remote effects, A's archive/tasks, remote refs and failed push. | Explicit direct Finish without --expect-head: Factory Finish, `nightshift finish <change>` or `nightshift act <change> finish --json` reuses A, pushes and marks done without rearchiving. Review the remote head, then `uv run python -m nightshift.land <url> --checks-done` on the done run, skipping finish and repeating fresh gates. No automatic bypass or run reset. |
| `pushed-archive-ready-failure` | Archive push succeeded but ready/state completion failed; inspect the archive/tasks and matching local/remote A. | Equal heads still permit guarded recovery; existing ordinary Finish/act recovery remains available. |
| `done-run` | Finish completed before a later landing refusal; inspect the current remote head and prior effects. Factory no longer offers Land. | For an open PR, `uv run python -m nightshift.land <url> --checks-done` skips finish and repeats all remaining gates; do not reset done. |

See the [landing recovery guide](docs/landing.md#recovering-from-a-refusal).
Desktop/native phone confirmation, progress and retained-notice behavior and
real-host Stop/evidence inspection remain human checks, not fixture evidence.

## Other known gaps

- Agents can read credential files; diffs containing their literal values are
  refused when those values are available to seal discovery. Forge scans cover
  declared bot/landing variables in supplied/process environments and the trusted
  env file, including unused forges. Inaccessible keychain-only credentials cannot
  be detected; discovery does not retrieve them or inject landing tokens into the
  daemon. Custom supervisor config paths and bot credential sources require
  [operator provisioning](README.md#host-path-setup).
- Quota is handled by detecting a usage-limit error, not read ahead of time.

## Platform assumptions

- **macOS only.** Verification runs under `sandbox-exec`, and the services are
  launchd agents.
- **Configurable Python and plugin paths.** The locations below are defaults,
  with operator-owned process environment overrides
  ([issue #15](https://github.com/mattpolicastro/nightshift/issues/15)). They do
  not provide full service-bootstrap portability; see the frozen wrappers below.
- **The attention page and Factory section need a Paseo host.**

| Setting | Default | Consumers |
| --- | --- | --- |
| `NIGHTSHIFT_BIN_DIR` | `/opt/homebrew/bin` | Python preparation, verification, agent subprocesses and Codex quota reads; both plugin command adapters |
| `NIGHTSHIFT_NODE_BIN` | `~/n/bin` | Plugin Factory, including OpenSpec `npx` calls for finish |
| `NIGHTSHIFT_UV` | `<resolved NIGHTSHIFT_BIN_DIR>/uv` | Exact executable for both plugin adapters |
| `NIGHTSHIFT_CHECKOUT` | `~/Projects/nightshift` | Working directory for both plugin adapters and attention's installation check |

Explicit settings override defaults; an unset `NIGHTSHIFT_UV` follows the
selected bin directory. Python consumes only `NIGHTSHIFT_BIN_DIR`; the other
settings do not select a Python CLI's checkout or executable. Paths must be
absolute or begin with `~/`, expanded against the host user's home before
verification substitutes its isolated `HOME`. Spaces and other filename
characters stay literal: no `$VAR`, glob or shell-substitution expansion.
Blank or relative values, unsupported tilde forms, NUL/newline/carriage-return
characters, and colons in the two PATH directory settings are rejected without
falling back. Only settings used by a consumer are validated. Resolution does
not install tools, require paths to exist or create directories.

Preparation, verification, agents and plugin attention use the selected bin
directory followed by `/usr/bin:/bin:/usr/sbin:/sbin`; quota reads use
`/usr/bin:/bin`. Factory prepends the selected Node directory before that bin
directory and the full system suffix. These consumers do not append arbitrary
inherited PATH entries. `NIGHTSHIFT_NODE_BIN` does not add Node to verification;
use a suitable tool directory or the trusted manifest's explicit PATH commands.
Choosing a PATH grants no sandbox read access: home-local tools still need
manifest variables or `[sandbox].prepare_read`, and credential denials and
offline verification remain. See [preparation](docs/specloop/prepare.md).

Supply settings in each actual Python service's and Paseo host process's
environment, then restart the affected daemon, supervisor or Paseo host; there
is no hot reload. A terminal export cannot reconfigure an already-running
service, and launchd reads no shell configuration. The existing
`bin/nightshift-daemon` exports the trusted `~/.config/nightshift/env` file:
file values win over inherited values for downstream settings after bootstrap.
`bin/specloop-serve` does not source that file; supply Python settings in its
service environment. Paseo needs its own host environment, not RPC inputs.
Candidate files and manifest env files cannot configure the harness resolver.
See [direct CLI setup](README.md#host-path-setup) and
[plugin installation](plugins/nightshift/README.md#installation).

Compatibility-only `[daemon].worktree_root` remains in the selected config file,
defaulting to `~/Projects/nightshift-wt`; there is no parallel environment key.
Leading `~/` expands and absolute paths keep their location. Relative values
now resolve beside the selected config file, instead of the daemon's working
directory. To retain an old relative location, set its absolute path before
restarting. Spaces remain literal; wrong types, blank strings, unsupported
tilde forms and NUL/line breaks are rejected. Loading does not create or migrate
worktrees. Existing worktrees, state, credentials and manifests do not move;
this inert root enables no worker execution and is separate from specloop state. See
[the example config](config.example.toml).

**Frozen service wrappers: portability deferred.** `bin/**` is frozen for this
change, as are `launchd/**` and wrapper/plist comments. The unchanged wrappers
retain these bootstrap commands:

| Wrapper | Checkout | Executable and arguments | PATH |
| --- | --- | --- | --- |
| `bin/nightshift-daemon` | `$HOME/Projects/nightshift` | `/opt/homebrew/bin/uv run nightshift run` | `/opt/homebrew/bin:$HOME/n/bin:$PATH` (prepends to incoming PATH) |
| `bin/specloop-serve` | `$HOME/Projects/nightshift` | `/opt/homebrew/bin/uv run --no-sync specloop serve` | `$HOME/n/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin` |

The daemon's bare `nightshift run` is the sole implicit daemon compatibility
form; the supervisor's `specloop serve` uses the one-release alias. Neither
requires a wrapper edit for this rollout.

Neither wrapper consumes the new settings for its own bootstrap. Environment
settings do not relocate these commands; changing a plist's wrapper pathname
does not change their internal paths either. No new helper changes this.
Direct CLI examples select an actual checkout and uv executable explicitly;
they do not configure bundled service startup. Full daemon rollout on another
layout remains pending separate wrapper work or an independently provisioned
operator service preserving trusted env loading, scoped GH_TOKEN refusal,
required front-door tools, credential isolation and preflight negative-control/
alert checks. The frozen daemon requires `~/.config/nightshift/env`,
`CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN`, and `gh/claude/pnpm/node/git/swift`, and
clears conflicting Anthropic API credentials/base URL before Python starts.
Direct Python operation removes worker prerequisites only at the Python boundary;
it cannot bypass these shell guards. See [current prerequisites](README.md#prerequisites).

**Pending manual validation.** Automated tests check configuration propagation,
not end-to-end portability. A real second macOS installation with different
tool, Node, checkout and worktree locations remains unvalidated. Record its
layout, launch method and results using a direct supervisor invocation or an
independently provisioned service with equivalent guards. Run disposable
prepare/verify steps, check agent/quota tool lookup and real sandbox denials,
and confirm existing worktrees did not move. Validate daemon rollout only after
separate service work or confirmation of frozen-wrapper prerequisites; otherwise
leave it pending. In the actual Paseo host, check attention/Factory and a disposable
action requiring `npx` (finish only after its normal human checks), including
checkout selection and confirmation/error behavior. These second-install and
Paseo host integration checks remain pending; a temporary directory on the
original machine or CI does not establish them or validate wrapper portability.

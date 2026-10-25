# Known limitations

Open gaps and queued follow-ups for Nightshift and its specloop engine. See the
tracking ledger, [issue #17](https://github.com/mattpolicastro/nightshift/issues/17).
Fixes land through Nightshift's own front door; these gaps remain unresolved.

## Queued follow-ups

- **Sync at task boundaries:** pick up fast-forward human commits on the source
  branch without pausing.
- **Retire the worker engine,** once one real issue has gone through the
  specloop front door end to end. Then rename specloop's CLI and state into
  Nightshift's, so one name remains.

## Open gaps

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
- **Pause boundary:** jobs already running can finish while the daemon is
  paused. An admission that completes while paused can still build under the
  independent `specloop serve` supervisor. Jobs have their own sessions, so
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
- **Background jobs:**
  - Persisted reporting deadlines are propose 3600 seconds, admit 300 seconds
    and revise 1800 seconds. A live overdue attempt gets `needs-human` on the
    next authorized, eligible, unpaused poll; no automatic termination or retry.
    Restart does not reset timing. Daemon/GitHub outages, removed triggers and
    authorization holds delay reporting; wall-clock jumps affect observation.
    `nightshift status` shows active timing/`OVERDUE`; legacy timing is unavailable.
    Use `nightshift jobs --all` for history and the recovery instructions above
    ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
  - Default `nightshift jobs` shows active (including starting/overdue),
    stopping, stopped, finished-undelivered and diagnostic rows. Ordinary failed
    and finished-delivered history is hidden with a count; `nightshift jobs --all`
    shows it all. Finished rows say `delivered` only for an item receipt with
    JSON `delivered: true`, otherwise `finished (delivery pending)`. This item-level
    label can reflect a successor receipt; it does not establish deletion identity
    ([issue #103](https://github.com/mattpolicastro/nightshift/issues/103)).
  - Delivered attempts lose logs and safely owned idea payloads; minimal
    requests redact idea arguments while retaining original mtimes for
    touchpoint history. Results, control metadata and directories remain.
    Undelivered and stopped/released evidence, workflow receipts and build
    feedback remain too: storage still grows and this is not secure erasure
    ([issue #9](https://github.com/mattpolicastro/nightshift/issues/9)).
    Automatic interrupted cleanup waits for a later eligible, authorized,
    unpaused visit.
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
- **GitHub-only automation:** the automated front door and publication use
  GitHub. Local drafting and verification do not require a GitHub host; they
  do not provide automated intake or publication for other hosts.
- **Config:**
  - Manifest failures now hold new drafting/admission/spec re-drafting locally;
    admitted publication/build revisions continue. Worker leftovers are released
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

## Other known gaps

- Agents can read credential files; diffs containing their literal values are
  refused.
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

Worker placement remains `[daemon].worktree_root` in the selected config file,
defaulting to `~/Projects/nightshift-wt`; there is no parallel environment key.
Leading `~/` expands and absolute paths keep their location. Relative values
now resolve beside the selected config file, instead of the daemon's working
directory. To retain an old relative location, set its absolute path before
restarting. Spaces remain literal; wrong types, blank strings, unsupported
tilde forms and NUL/line breaks are rejected. Loading does not create or migrate
worktrees. Existing worktrees, state, credentials and manifests do not move;
this worker root is separate from specloop run state. See
[the example config](config.example.toml).

**Frozen service wrappers: portability deferred.** `bin/**` is frozen for this
change. The unchanged wrappers retain these bootstrap commands:

| Wrapper | Checkout | Executable and arguments | PATH |
| --- | --- | --- | --- |
| `bin/nightshift-daemon` | `$HOME/Projects/nightshift` | `/opt/homebrew/bin/uv run nightshift run` | `/opt/homebrew/bin:$HOME/n/bin:$PATH` (prepends to incoming PATH) |
| `bin/specloop-serve` | `$HOME/Projects/nightshift` | `/opt/homebrew/bin/uv run --no-sync specloop serve` | `$HOME/n/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin` |

Neither wrapper consumes the new settings for its own bootstrap. Environment
settings do not relocate these commands; changing a plist's wrapper pathname
does not change their internal paths either. No new helper changes this.
Direct CLI examples select an actual checkout and uv executable explicitly;
they do not configure bundled service startup. Full daemon rollout on another
layout remains pending separate wrapper work or an independently provisioned
operator service with equivalent credential and preflight guards: require the
trusted credential file and OAuth/GitHub tokens, clear conflicting Anthropic
API credentials/base URL, check required tools, and preserve startup refusal
and preflight checks. Do not bypass these guards to relocate the daemon.

**Pending manual validation.** Automated tests check configuration propagation,
not end-to-end portability. A real second macOS installation with different
tool, Node, checkout and worktree locations remains unvalidated. Record its
layout, launch method and results using a direct supervisor invocation or an
independently provisioned service with equivalent guards. Run disposable
prepare/verify steps, check agent/quota tool lookup and real sandbox denials,
and confirm existing worktrees did not move. Check new worker placement when
a daemon with those guards is available; otherwise leave that rollout check
pending. In the actual Paseo host, check attention/Factory and a disposable
action requiring `npx` (finish only after its normal human checks), including
checkout selection and confirmation/error behavior. These second-install and
Paseo host integration checks remain pending; a temporary directory on the
original machine or CI does not establish them or validate wrapper portability.

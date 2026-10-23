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

- **Revision crash boundary:** a revise job that crashes after committing but
  before saving its result can append a duplicate round on retry. Consumed
  feedback IDs prevent replay after durable success
  ([PR #61](https://github.com/mattpolicastro/nightshift/pull/61)); they do not
  make the commit and result persistence atomic.
- **Pause boundary:** jobs already running can finish while the daemon is
  paused. An admission that completes while paused can still build under the
  independent `specloop serve` supervisor. Jobs have their own sessions, so
  unloading the daemon does not stop them. `nightshift jobs stop <id>` stops
  a background job ([PR #28](https://github.com/mattpolicastro/nightshift/pull/28));
  it does not pause an already admitted run.
- **Stopped-job holds:** stopped jobs stay held across resume, daemon restart
  and relabeling ([PR #28](https://github.com/mattpolicastro/nightshift/pull/28)).
  There is no retry command
  ([issue #34](https://github.com/mattpolicastro/nightshift/issues/34)); follow
  the [pause and job recovery instructions](README.md#pause-and-job-control).
- **Background jobs:**
  - No deadline for a hung job. `nightshift jobs` shows the local inventory
    ([PR #28](https://github.com/mattpolicastro/nightshift/pull/28)), but does
    not enforce deadlines or automatically retry jobs.
  - Job directories are never cleaned, and their `request.json` keeps the full
    idea text.
  - Daemon git timeouts kill only the direct child, not its process group.
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
- **Homebrew paths.** The verify sandbox's `PATH` starts at `/opt/homebrew/bin`,
  and the Paseo plugin runs `/opt/homebrew/bin/uv`.
- **The Node toolchain is expected under `~/n/bin`.** The `n` version manager
  puts it there. It's used for the OpenSpec CLI through `npx`, and by the
  plugin's `PATH`.
- **Fixed locations.** The Paseo plugin expects the Nightshift checkout at
  `~/Projects/nightshift`, and worker worktrees default to
  `~/Projects/nightshift-wt`.
- **The attention page and Factory section need a Paseo host.**

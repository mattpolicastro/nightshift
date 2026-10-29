## Context

`nightshift/config.py` constructs `Repo` and `Config` directly and ignores unknown
TOML keys. `daemon.poll_specloop` passes a guarded runner to four intake stages;
each constructs `GitHubForge` locally. Existing constructor spies in
`tests/test_forge_github.py` protect this interface. Engine-switch recovery uses
its own GitHub runner, captured errors and durable journals keyed only by
`owner/name`. Those journals came from the retired worker, which never operated
on Gitea.

The existing `GiteaForge(base_url, token, *, trusted_logins, bot_login,
wip_prefix, opener, guard, timeout)` constructs its own `GiteaTransport`. It does
not accept a transport object. This change uses that constructor without changing
either adapter. `GiteaTransport` has a pre-attempt guard, bounded GET retries and
status-bearing `GiteaHTTPError`; `GiteaForge` deliberately does not expose HTTP
status in its public failure. Preflight therefore uses the transport directly.

`specloop/seal.py:credential_values(extra_env)` currently scans profile auth and
fixed environment names. The independent supervisor does not inherit daemon
configuration selection automatically. Landing and daemon status currently match
or query every enrolled `owner/name` as GitHub. No applicable `AGENTS.md` was found.

## Goals / Non-Goals

Implement configuration, adapter construction, a Gitea dispatch hold, token
isolation, secret scan and five preflight controls in five sessions of
approximately 300 changed lines each,
including tests and small documentation edits. Preserve current assertions and
public runner calls. All automated validation uses temporary state, fake HTTP
openers and injected subprocesses, without credentials or live services.

Do not extend the Forge protocol, alter adapters, implement Gitea-specific intake
policy, change revision snapshots/consumption, or add transition intent/recovery.
In particular, a newer authorized revision-label application does not introduce
a new hold on an earlier saved round. Existing round completion and preservation
of the newer trigger remain unchanged. Gitea daemon stages remain disabled until
a later change supplies missing authorization and delivery guarantees;
documentation alone is not a safe retry mechanism. Existing GitHub stages and
revision recovery are unchanged.

## Decisions

### Configuration and compatibility

Add a frozen `ForgeConfig` record, `Config.forges` with an empty default, and
`Repo.forge: str | None = None` at the end of compatible constructor fields.
Provide a pure forge-table parser and a config resolver usable by daemon,
preflight, landing and status. Resolve missing `Repo.forge` to an implicit GitHub
definition even when named Gitea definitions exist; never select the first table.
Names and references are case-sensitive, unique nonblank strings. An explicit
empty/unknown reference, duplicate forge name, invalid kind or wrongly typed
known field is a config error. Engine validation still runs first.

| Field | GitHub default | Gitea default or requirement |
| --- | --- | --- |
| `name` | Required for an explicit table | Required |
| `kind` | Required, exactly `github` | Required, exactly `gitea` |
| `url` | `https://github.com` | Required instance root, including any installation prefix; exclude `/api/v1` |
| `token_env` | `GH_TOKEN` | Required nonempty environment-variable name |
| `land_token_env` | Empty, meaning no declared landing variable | Required nonempty environment-variable name, distinct from `token_env` |
| `bot_login` | Empty | Required nonblank string |
| `trusted_logins` | Empty list | Empty list (adapter projects nobody as trusted; daemon stages disabled for every allowlist) |
| `forbidden_probe` | Empty; legacy daemon probe stays effective | Empty; preflight fails until configured |
| `wip_prefix` | `WIP: ` | `WIP: `; explicitly empty is invalid |

Environment names use `[A-Za-z_][A-Za-z0-9_]*`. No token values belong in TOML.
Validate Gitea URL constraints consistently with the existing transport: HTTPS,
or HTTP only for its supported loopback hosts; no userinfo, query or fragment.
Do not impose GitHub-only punctuation rules on repository names: retain the
existing readiness hold for names such as `owner/widget..js`. Reject non-string,
blank or structurally invalid owner/name values with `invalid repository identity`.
Casefolded owner/name duplicates anywhere in `Config.repos`, including between
implicit GitHub, explicit GitHub and Gitea, fail with `ambiguous enrollment`.
Preserve those diagnostic substrings through `land.load_config`; do not weaken
`tests/test_land.py`. Directly constructed configurations still receive the
landing function's existing ambiguity and identity checks.

Ignore unknown keys at every parsed table rather than unpacking raw mappings
into dataclasses. Preserve existing defaults, obsolete-key notes, manifest health
and path handling. Named GitHub selection keeps the current ambient `gh` runner
authentication and host behavior: these fields do not introduce per-repository
GitHub credentials or GitHub Enterprise routing. Their token names still
participate in daemon exclusion and seal scans. Document that boundary.

Only the matching predicate in `nightshift/land.py:enrolled_repo` changes: a row
counts only when its resolved kind is GitHub. Do not migrate landing to Forge.
Only the repository loop in `nightshift/cli.py` daemon status changes: before
ready/blocker/contradiction queries, print `<repo>: not queried (gitea)` and skip
Gitea rows. Retain liveness, alerts, jobs, GitHub output and return codes.

### Adapter selection and mandatory Gitea dispatch hold

Add `forge_for(repo, *, cfg, runner=None, guard=None)` in `nightshift/daemon.py`.
For GitHub it returns `GitHubForge(runner or queue._gh)`, with no eager requests.
For Gitea it reads exactly `os.environ[definition.token_env]` and constructs the
existing `GiteaForge` with the configured URL, bot, trusted logins and prefix;
that adapter constructs the transport. Missing/empty tokens produce a sanitized
configuration failure, never fallback to `GH_TOKEN` or `gh`. This factory is
tested directly for both kinds without dispatching a request; Gitea polling
does not call it or read bot credentials because the dispatch hold runs first.

At daemon entry, before heartbeat, startup recovery, notifications or child
dispatch, reject presence of any nonempty configured `land_token_env` key in
`os.environ`, even if its value is empty or its forge is unused. Cover direct
startup and poll entry points too. Report forge/key names, never values. This
check runs after the CLI's existing env-file loading and before work, including
when paused or using `--once`. It does not prohibit a human preflight process
from possessing its own credentials, and preflight never sends a landing token.

For GitHub, do not call the factory merely to pass its result into intake: retain
the exact existing `action(..., runner=gh, ...)` and error-reporting calls,
intake-local construction, late-bound runner tests and constructor spies. Leave
`nightshift/specloop_intake.py` unchanged. Explicit Gitea forwarding into its four
stages is dropped from this change, not hidden behind an opt-in flag.

At the start of each repository iteration in `startup` and `poll_specloop`, after
the existing pause check and before readiness, recovery or stage execution,
resolve its forge kind. Every Gitea row is skipped with a nontransient
`ConfigurationError`: `Gitea daemon stages disabled: authorization and durable
label transitions are not implemented`. Use `RepoHealth.record` and logging,
never item failure reporting or a forge comment. Supply a local health tracker
when direct `startup` receives none, so direct reconciliation also escalates.
Inspect foreign journals as below before selecting the diagnostic. A clear
journal location leaves this general dispatch hold in place.

The hold covers drafting, approval/admission, spec and build revisions,
publication/readiness, pending result delivery and failure reporting. Neither a
healthy manifest, configured bot token, nonempty allowlist, successful preflight,
existing receipt nor an already-admitted run bypasses it. Do not pass the error
as ordinary readiness: admitted stages deliberately bypass readiness failures.
Other repositories continue; existing global job reaping and the independent
supervisor may still persist local results, but no held repository's intake
receipt, feedback consumption, git operation or remote label/comment changes.
Existing GitHub pause/engine/git/launcher guards remain unchanged.

This is necessary for two concrete reasons. `_label_actor_association` trusts
matching User ownership without consulting the adapter's NONE association, so
an empty `trusted_logins` does not enforce a core trust boundary. Gitea's label
transition deletes before adding; an accepted DELETE of `spec:approved` with a
lost response can make a refused admission undiscoverable before `needs-human`
is added. Fixing either requires out-of-scope policy or durable intent. A future
enabling change must establish both contracts and their interruption tests;
adding logins or retrying a poll cannot enable the stages in this version.

Add `test_gitea_daemon_stages_are_disabled`: run startup and repeated polls with
empty and nonempty allowlists, an unlisted owner actor, pending approval,
successful revision/publication/admission receipts, and healthy/broken manifests.
Make all Gitea stage/failure/recovery calls and forge-network/child dispatch fail the
test if reached; retain fixture labels and receipt bytes across restart. Include
a fake configured to accept a label DELETE then lose its response, asserting
that no DELETE is attempted at all. Mixed-repo fixtures prove unchanged GitHub
progress. Stub the existing alert sender and assert its health escalation
separately. This tests prevention, not newly implemented transition recovery.

### GitHub-only retired-worker recovery

Before recovery or any intake stage in startup and each poll, resolve the kind.
GitHub takes the original recovery path unchanged, including suppressed-error
capture, pause checks and claim/outcome ordering. Gitea never enters
`_reconcile_engine_switch`, even with no journal, so it never queries GitHub or
creates a recovery intent.

For a Gitea row inspect only the existing local journal location
`STATE_DIR / 'engine-switch' / repo.name.replace('/', '__')` for direct `*.json`
entries. Any such entry, including completed or malformed journals, holds the
entire repository without parsing/replaying it. An unreadable location also
holds; an absent directory is clear only of foreign evidence, not eligible for
Gitea dispatch. Use a nontransient `ConfigurationError`
through `RepoHealth.record` to announce the hold once via the existing configured
alert path and log the repository and inspection location. This is repository
escalation, not an item comment or `needs-human` label write.

The foreign-journal diagnostic takes precedence over the general Gitea dispatch
hold and names the inspection location. The `Health gates new work only`
MODIFIED requirement excepts all Gitea dispatch from admitted delivery; the
foreign-journal hold gives an additional reason, not an alternative route to
enable stages. Do not classify ordinary GitHub configuration errors as this hold
or change the intake stages' health handling.

The daemon retains journals, claims, receipts, pending results and worktrees
without mutation; other repositories continue. This guard does not stop running
jobs or the independent supervisor, which may still persist their own local
results. Pause still prevents external operations. A human inspects the evidence
before correcting enrollment or moving the foreign journal out of the active
location; the program never migrates or deletes it. Manifest repair cannot
release either Gitea hold. Clearing a journal removes only that diagnostic:
the Gitea dispatch hold remains and completed jobs are not relaunched. Changing
a row to GitHub is not a supported migration or recovery procedure.

Parameterize `test_gitea_repo_with_engine_switch_journal_is_held` with admitted
publication, build revision and completion delivery pending, and healthy/broken
enrollment manifests. Assert no stage dispatch or forge operation for the held
repository, unchanged receipts/results, independent repository progress and
continued dispatch refusal after operator removal. Retain the existing
`tests/test_config_resilience.py` health-bypass assertions and fixtures unchanged:
ordinary manifest/name failures still allow admitted stages, and successful
stages cannot clear unresolved health errors. Document the same narrow exception
beside the admitted-run guarantees in README and the engine guide.

### Seal discovery without changing callers

Keep `credential_values(extra_env=None)` and all callers compatible. Within
`specloop/seal.py`, choose the trusted config from the process environment's
`NIGHTSHIFT_FORGE_CONFIG` when set, otherwise `nightshift.config.DEFAULT_PATH`.
An explicit override must be a nonempty absolute path; never discover config
from candidate files, candidate cwd or a manifest's untrusted repository.
Use the pure forge-table parser, not full enrollment loading or daemon startup.

An absent implicit default contributes no forge names. An explicit missing,
unreadable or malformed file, or an existing invalid default, raises a sanitized
error so sealing cannot silently omit declared secrets. Parse without executing
shell text or loading values into the process. For every forge, even unused ones,
collect both named variables from each available source: `extra_env`, process
environment and `config.parse_env_file()`; scan every distinct nonempty value,
including shadowed and short values. A declared credential file that exists but
cannot be read is an error. Unset values add nothing. Keep all previous fixed-name
and profile-auth scanning, including their existing filtering, intact.

Secret detection retains path-only diagnostics and prevents a sealed commit;
never report secret values. Do not obtain a landing credential from a keychain or
put it in the daemon just for scanning. This literal scanner can detect only
values available to the seal process or its trusted env file, not inaccessible
credentials. Tests isolate HOME, ENV_FILE, DEFAULT_PATH and
NIGHTSHIFT_FORGE_CONFIG, including subprocess fixtures where applicable, without
changing existing assertions.

Operators using custom daemon `--config` paths set the same absolute
`NIGHTSHIFT_FORGE_CONFIG` in the independent supervisor environment and provision
its bot credential sources separately. Frozen `bin/specloop-serve` does not
source the daemon env file; no wrapper edit or automatic restart is included.

### Read-only Gitea preflight

Extend `preflight.run(cfg, env, *, runner=None, deep=False)` compatibly with
keyword-only injectable Gitea transport and credential-subprocess seams. The
default transport uses the supplied `env[token_env]`, never ambient personal
authentication. Keep existing GitHub global token/probe checks, deep notice and
alert checks unchanged; filter GitHub reachability to GitHub rows. This bounded
change does not remove the existing global GitHub prerequisite for Gitea-only
configurations. Run forge-level Gitea checks once for every configured Gitea
definition, and repository checks for each of its enrolled repositories. Include
forge/repository names in `Check.name`; all failures remain `ok=False` and make
the existing CLI return nonzero. Missing tokens fail without HTTP dispatch;
independent forges/checks continue and diagnostics exclude bodies and credentials.

Use paths under `/api/v1` appended to the configured installation root, with
owner, repository and branch individually URL-encoded:

1. `GET /api/v1/user` must return a login matching `bot_login` case-insensitively
   and the boolean `is_admin` exactly false. Missing/malformed identity or admin
   evidence fails, as does an admin account.
2. `GET /api/v1/repos/{forbidden_probe}` passes only on `GiteaHTTPError.status ==
   404`. A readable repository, 401, 403, other error or missing probe fails.
   The operator must independently know this is a real off-limits repository;
   the bot cannot distinguish nonexistent from hidden.
3. Enumerate `/api/v1/repos/{owner}/{name}/labels` using `get_page`, page 1 and
   limit 50 through an explicit empty page, with a 1,000-page cap. A short page
   is not termination. Fail on malformed rows, repeated/conflicting IDs/pages,
   duplicate names, incomplete reads or exhaustion. Require exactly one label
   for each distinct effective `Labels` value, including revise, plus the
   existing hard-coded `merge:ready` and `merge:blocked`. Do not create labels.
4. `GET /api/v1/repos/{owner}/{name}/branch_protections/{base}` must return a
   well-formed protection record whose `branch_name` exactly equals `Repo.base`.
   A 404 means missing protection; 403 is `unverified` with `ok=False`; other
   failures/malformed evidence fail. Do not use the human token, grant admin,
   write protection rules or infer protection from successful branch reads.
   This proves an exact rule exists, not that every policy setting is adequate;
   wildcard-only rules are conservatively unverified in this bounded check.
5. For each `repo.clone_dir()`, read `git remote get-url origin` and require an
   HTTP(S) URL matching the configured installation origin/prefix and repository
   (optional `.git` suffix). Refuse SSH, embedded credentials, contradictory or
   missing remotes as unverified. Run `git -C <clone> credential fill` with
   `protocol`, `host` (including port), and repository `path` supplied on stdin,
   never a token in argv. Use the supplied environment, disable terminal and
   askpass prompts, and bound each subprocess to 10 seconds. Parse physical
   `key=value` output lines; require one nonempty username and password, reject
   duplicate/malformed fields, and compare the username to `bot_login` using
   casefold. Discard the password without logging stdout/stderr. Failure,
   timeout, missing or different identity fails. No credential approve/reject,
   config edit, git fetch or push is performed.

Credential helpers are operator-configured executables and may internally use
their own storage/network. Fake subprocess tests prove our argv, stdin, timeout,
redaction and refusal behavior; the host check proves the actual resolution.
Matching username is the requested identity control, not independent proof of
the helper password's account or scope.

### Documentation and regression boundaries

Keep the single runnable GitHub enrollment example in each guide. Describe
additional forge fields in prose/tables and link to `config.example.toml` instead
of adding raw `name =` or `[[repos]]` lines to guides: the unchanged retirement
test extracts those physical lines across the entire document. In the example
config, put any commented forge example after all active tables, use the current
repo's commented `forge` setting, and add no additional literal `[[repos]]`
occurrence; existing tests replace every such occurrence, even in comments.

Correct the root layout, enrollment/recovery, preflight and status passages and
the engine guide's enrollment/recovery statements. Add specific limitation lines
for disabled Gitea stages, the core owner shortcut bypassing adapter allowlists,
unsupported membership authorization, nondurable multi-write label transitions
that can lose their trigger, core marker lookup accepting human marker copies, title-prefix
drafts, exact-rule protection evidence, credential-helper evidence and remaining
GitHub-only CLI/plugin paths. Preserve Gitea adapter ownership guarantees: the
gap is core lookup/reconciliation policy, not permission to edit human comments.
Link #128 and #122 without inventing an implementation PR.

No new Markdown/HTML parser or documentation extraction test is required. Use
existing structural tests unchanged and the real TOML parser for configuration.
If a focused example check is necessary, select between one exact pair of
physical marker lines, refuse missing/duplicate/reversed markers, and parse the
intervening TOML literally; fences, HTML and indentation have no semantic role.

## Risks / Trade-offs

- Shared state keys still omit forge identity; rejecting cross-forge owner/name
  collisions is deliberately stricter than supporting them. No migration is
  attempted for old state after an operator changes a repository's forge.
- Gitea daemon execution and selected-forge stage forwarding are explicitly
  deferred. Config/preflight can prepare an installation but cannot activate it;
  this avoids the proven allowlist bypass and lost-transition failure without
  adding forbidden intake policy or recovery machinery. Core marker ownership
  remains a separate #122 gap. No user-configurable bypass is provided.
- Multi-write Gitea label transitions retain their existing ordering in the
  standalone adapter, which the daemon never invokes for held rows. GitHub-only
  switch recovery and ordinary revision rules stay intact. Human label
  reapplication does not introduce a new conflict hold on GitHub rounds.
- Preflight cannot prove that a 404 target exists, that protection rules are
  sufficient, or that a matching credential username proves token ownership.
  A protection 403 remains explicitly unverified and blocks a clean result.
- Seal discovery adds trusted host inputs. Explicit path agreement and isolated
  tests prevent accidental ambient configuration use, but unavailable landing
  secrets cannot be discovered without provisioning them to an allowed source.
- Budgets include tests. Reuse existing FakeGitea/openers and recovery fixtures;
  parameterize refusal cases. The two preflight sessions separate remote API
  evidence from local credential-helper evidence because combining them would
  exceed a single session. Do not add a sixth task or omit a requested control;
  report a genuine overrun and what optional work was dropped.

Acceptance after implementation is `uv run pytest` and
`uv run python scripts/check_types.py`, with no weakened existing assertions or
new type-baseline entries. In particular run runner-form forge tests,
engine-switch recovery, landing, preflight, seal and retirement documentation/
config suites. This drafting change itself modifies only its OpenSpec directory.

## Human checks

- [ ] Run `nightshift preflight --config <absolute-config-path>` against the real Gitea instance using the daemon's clone and bot environment; record the forge/repositories and redacted results, independently confirm the forbidden probe exists, and treat protection `unverified` as an outstanding failure. A clean result supplies evidence only; Gitea daemon stages remain disabled.
- [ ] Inspect the real base-branch protection policy and the clone credential helper's bot account; confirm the human landing-token variables are absent from the daemon service environment, and that the supervisor selects the same forge config and intended credential sources. Record these live enrollment checks without exposing token values; CI does not prove them.

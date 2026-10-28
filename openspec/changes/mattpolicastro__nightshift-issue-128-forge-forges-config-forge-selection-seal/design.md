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

Implement the requested configuration, dispatch, token isolation, secret scan and
five preflight controls in five sessions of approximately 300 changed lines each,
including tests and small documentation edits. Preserve current assertions and
public runner calls. All automated validation uses temporary state, fake HTTP
openers and injected subprocesses, without credentials or live services.

Do not extend the Forge protocol, alter adapters, implement Gitea-specific intake
policy, change revision snapshots/consumption, or add transition intent/recovery.
In particular, a newer authorized revision-label application does not introduce
a new hold on an earlier saved round. Existing round completion and preservation
of the newer trigger remain unchanged. General Gitea transition gaps are recorded
as limitations, not “fixed” by weakening that contract.

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
| `trusted_logins` | Empty list | Empty list (trust nobody by default) |
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

### Daemon selection and guards

Add `forge_for(repo, *, cfg, runner=None, guard=None)` in `nightshift/daemon.py`.
For GitHub it returns `GitHubForge(runner or queue._gh)`, with no eager requests.
For Gitea it reads exactly `os.environ[definition.token_env]` and constructs the
existing `GiteaForge` with the configured URL, bot, trusted logins and prefix;
that adapter constructs the transport. Missing/empty tokens produce a sanitized
repository-local configuration failure, never fallback to `GH_TOKEN` or `gh`.

At daemon entry, before heartbeat, startup recovery, notifications or child
dispatch, reject presence of any nonempty configured `land_token_env` key in
`os.environ`, even if its value is empty or its forge is unused. Cover direct
startup and poll entry points too. Report forge/key names, never values. This
check runs after the CLI's existing env-file loading and before work, including
when paused or using `--once`. It does not prohibit a human preflight process
from possessing its own credentials, and preflight never sends a landing token.

For GitHub, do not call the factory merely to pass its result into intake: retain
the exact existing `action(..., runner=gh, ...)` and error-reporting calls. Add
optional keyword-only `forge=None` to the four stage entry points in
`nightshift/specloop_intake.py`, replacing only unconditional construction with
`forge if forge is not None else GitHubForge(runner or queue._gh)`. Existing
helpers already pass that object onward. Supply `forge=` only on Gitea paths,
including `report_failure` and publication/readiness; no constructor-spy changes.
The `Late bound runners` delta makes that optional explicit-forge path legal
without changing either existing runner scenario or `Forge mediated intake`.

Use a small daemon-local delegating guard around the Gitea object to check pause
before and after each logical Forge call, and pass the same guard into the
adapter for every transport attempt and reconciliation. Preserve exception
identity and add no retries. The post-call check is necessary when the last
accepted write creates pause: its receipt must remain pending. Keep existing
guards on engine, launcher and git operations. No new rich-text recognition is
introduced anywhere in intake.

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
holds; an absent directory is clear. Use a nontransient `ConfigurationError`
through `RepoHealth.record` to announce the hold once via the existing configured
alert path and log the repository and inspection location. This is repository
escalation, not an item comment or `needs-human` label write.

Skip all stages, including admitted publication and build revision, for this
specific foreign-journal hold. Retain journals, claims, receipts and worktrees
byte-for-byte; other repositories continue. Pause still prevents external
operations. A human inspects the evidence before correcting enrollment or moving
the foreign journal out of the active location; the program never migrates or
deletes it. This new unsupported-state rule is separate from ordinary manifest
health, which continues to gate only new work.

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
for core marker lookup accepting human marker copies, unsupported membership
authorization paths, nondurable multi-write label transitions, title-prefix
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
- Gitea authorization still invokes unsupported membership operations for some
  actors, and core marked-comment lookup can be confused by human copies. Config
  and preflight do not certify end-to-end Gitea intake. These remain #122 gaps.
- Multi-write Gitea label transitions retain their existing ordering and recovery
  limitations. GitHub-only switch recovery avoids exporting its journal contract
  to an incompatible transition sequence. Ordinary revision rules stay intact.
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

- [ ] Run `nightshift preflight --config <absolute-config-path>` against the real Gitea instance using the daemon's clone and bot environment; record the forge/repositories and redacted results, independently confirm the forbidden probe exists, and treat protection `unverified` as an outstanding failure.
- [ ] Inspect the real base-branch protection policy and the clone credential helper's bot account; confirm the human landing-token variables are absent from the daemon service environment, and that the supervisor selects the same forge config and intended credential sources. Record these live enrollment checks without exposing token values; CI does not prove them.

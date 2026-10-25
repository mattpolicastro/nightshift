# worker-retirement Specification

## Purpose
Retire the original worker execution engine without losing historical evidence or changing specloop's public boundaries.

## Requirements

### Requirement: Sole execution engine

The daemon SHALL execute development work only through specloop, with no worker pool, claim dispatch, subscription-window scheduler or automatic worker resume. The four worker execution modules SHALL be removed after caller migration. Shared compatibility utilities MAY remain but SHALL NOT compose a worker run through any production entry point.

#### Scenario: Worker execution is absent
- **WHEN** the installed package and daemon dispatch paths are inspected after retirement
- **THEN** nightshift/task.py, nightshift/worker.py, nightshift/prompts.py, nightshift/recovery.py and daemon worker execution entry points are absent, and no application import or supported command depends on them
- **THEN** specloop agent adapters, mutation workers and test concurrency retain their behavior

#### Scenario: Polling remains responsive
- **WHEN** a proposal is still running or an intake git operation times out
- **THEN** other eligible specloop work, heartbeat and background-child reaping continue under existing timing and error isolation, without worker claim or harvest activity

#### Scenario: Pause and one shot retain control boundaries
- **WHEN** the daemon is paused, receives SIGTERM, or runs with `--once`
- **THEN** pause still holds new launches and completion delivery, orderly service exit cleans its heartbeat, and one-shot polling neither writes nor removes the service heartbeat
- **THEN** `--once` performs one poll without waiting for detached jobs, and neither daemon exit nor pause claims to stop independently admitted builds

### Requirement: Retired configuration is inert

Worker model routing, task caps, phase budgets and concurrency SHALL have no execution effect. Obsolete TOML keys SHALL be accepted with deterministic key-only notes directing operators to trusted manifests. Engine validation SHALL precede obsolete-key handling. Retirement SHALL NOT translate worker settings or credentials into manifests. Compatibility fields and worktree-root validation SHALL follow the design's explicit retained interface.

#### Scenario: Old knobs cannot control builds
- **WHEN** a specloop or default-engine repository supplies worker-only settings, including zero task cap, endpoint URLs, model assignments or a verify string
- **THEN** configuration reports the ignored key names without leaking values and specloop behavior continues to come from its manifest
- **THEN** no worker endpoint is contacted, credential is forwarded or manifest is generated
- **THEN** Repo.verify, Repo.concurrency, Config.mirror_to_paseo and Config.worktree_root can remain only as inert compatibility fields; root validation/defaults are retained

#### Scenario: Worker rejection takes precedence
- **WHEN** a repository sets engine to worker together with missing verify, obsolete settings or no manifest
- **THEN** configuration rejects the retired engine first with the repository name and instructions to use specloop and supply a trusted manifest

### Requirement: Historical evidence remains available

Retirement SHALL preserve historical state, outcomes, transcripts, branches and worktrees without migration. Claim cleanup SHALL occur only under existing durable recovery after saving its outcome. Attention SHALL preserve JSON, stale/offline behavior and exit codes. Startup, polling and reconcile SHALL use safe leftover-claim recovery, never worker execution or destructive legacy reconciliation.

#### Scenario: Historical attention remains readable
- **WHEN** existing worker outcome history and specloop pauses are read through CLI or Paseo attention online or offline
- **THEN** historical entries, refresh and stale behavior remain available with the same JSON schema and resolution rules
- **THEN** reading history does not start a worker, move state or delete work

#### Scenario: Historical mirroring remains usable
- **WHEN** the retained mirror utility is imported and used for a historical transcript after worker.py is removed, with an omitted or explicit config_root
- **THEN** omission still searches `~/.config/nightshift/claude` through a mirror-owned default, and an explicit root still overrides it without importing the worker engine
- **THEN** symlink placement, Paseo import argv/labels/cwd, transcript parsing and best-effort missing-session/worktree/tool behavior remain unchanged, and no worker execution or credential initialization occurs

#### Scenario: Recovery preserves interrupted work
- **WHEN** retirement encounters surviving, missing or unreadable worker claim evidence
- **THEN** existing durable recovery rules preserve surviving work for human inspection, release known missing work and retain uncertain evidence without restarting the worker

### Requirement: Service prerequisites match the surviving engine

The Python CLI and preflight SHALL operate without worker-only prerequisites while preserving env-file loading and front-door checks. Manifest providers SHALL retain their own requirements. Frozen wrappers and plists SHALL remain unchanged; their shell startup checks remain outside the Python prerequisite guarantee.

#### Scenario: Codex enrollment needs no worker login
- **WHEN** Python CLI one-shot polling and preflight are exercised directly with a valid specloop enrollment and front-door prerequisites but no worker OAuth token, Claude executable, pnpm or Swift
- **THEN** worker-only prerequisites do not prevent Python operation, and the CLI retains existing env-file loading
- **THEN** deep preflight remains accepted, emits an explicit retired-probe notice and launches no model or tool-loop probe or worker verify-allowlist check
- **THEN** missing GH_TOKEN, token scoping or alert failures still fail Python preflight, and manifest providers retain their own credential/tool requirements
- **THEN** this does not assert successful startup through the unchanged daemon wrapper, which still requires its environment file, worker OAuth, GH_TOKEN and legacy tool list before Python starts

#### Scenario: Services retain independent roles
- **WHEN** bundled launchd jobs and wrappers are inspected read-only
- **THEN** the daemon still invokes `nightshift run` and the independent supervisor still invokes `specloop serve`, with existing labels, bootstrap paths, state locations and lifecycle policy
- **THEN** no live service is installed, unloaded or restarted by retirement code
- **THEN** wrappers, plists and their comments remain unchanged; any service adaptation is separate operator work, with host readiness checked before rollout

### Requirement: Client command boundaries remain stable

Paseo attention SHALL retain its `nightshift attention --json` command, installation check and historical data projection. Factory actions and specloop notification commands SHALL retain the existing `specloop` commands. Host path resolution, argv boundaries, allowed-action validation, caches, redaction and child cleanup SHALL remain unchanged.

#### Scenario: Existing clients use surviving commands
- **WHEN** attention, Factory status or an allowed action runs and a specloop pause notification is rendered
- **THEN** their argv or suggested command resolves to the existing surviving CLI, with the same JSON and action contract and no worker invocation
- **THEN** no package rename, CLI merge, RPC migration or state move is required

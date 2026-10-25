## Context

The repository still defaults `Repo.engine` and `config.load()` to worker. The daemon contains both the specloop front door and a worker executor pool; `task.run` composes queue, git, worker invocation and review into a second engine. `worker.py` also supplies preflight probes. The original end-to-end prerequisite was met on 2026-10-21. All blockers named in the proposal remain merge prerequisites; this draft does not assert their live issue states.

This checkout contains no AGENTS.md. This revision resolves the feasibility findings against the existing draft. README.md, CLAUDE.md, the engine and operational code, tests and applicable base specifications inform this design. The user's latest decision supersedes the earlier eight-task ceiling: use one change with as many approximately 300-line tasks as retirement needs, counting deletions and tests. The implementation remains one PR; do not deploy intermediate task commits.

## Goals / Non-Goals

Goals: one executable engine; a useful retired-engine diagnostic; green `uv run pytest` after each task; preserved front-door controls and historical evidence; no required worker credentials at the Python CLI/preflight boundary; explicit operator checks before merge.

Non-goals: #40's rename, CLI merge or state move; removal of all occurrences of the word worker; wholesale deletion of shared compatibility utilities; data migration or cleanup; provider/agent policy changes; changes to mutation concurrency, test workers, quotas, plugin RPC or job lifecycle contracts.

## Decisions

### Implementation path boundary

The feasibility review and `examples/specloop/self-hosting.toml` constrain implementation edits to `nightshift/**`, `specloop/**`, `tests/**`, `README.md`, `KNOWN-LIMITATIONS.md`, `config.example.toml` and `docs/specloop/**`. Frozen paths remain frozen, including `openspec/**`, `bin/**`, `launchd/**`, `plugins/**`, `CLAUDE.md` and `pyproject.toml`. Root `mypy-baseline.txt`, `SPEC-endpoints.md`, `docs/providers.md` and `docs/validation.md` are outside the allowance too. Reading them and running existing checks is permitted; changing the manifest or adding a helper to bypass a frozen path is not part of this plan. Drafting edits are confined to this change directory.

Run `uv run python scripts/check_types.py` in check mode only. Its multiset comparison rejects new diagnostics but permits resolved/deleted diagnostics to remain in the baseline. Do not run `--update` or edit `mypy-baseline.txt`; baseline cleanup is deferred, including when the checker suggests it. The feasibility constraint takes precedence over CLAUDE.md's general baseline-shrink guidance. Fix new diagnostics in allowed code rather than changing the checker or suppressing failures.

### Engine selection and configuration

In `nightshift/config.py` change both `Repo` and TOML defaults to `specloop`. Reject worker in `Repo.__post_init__` with `<repo>: worker engine is retired; use engine = "specloop" (or omit engine) and configure a trusted manifest`. Validate repository engine values before accessing worker fields or parsing obsolete endpoint tables. Unknown values remain errors. In `nightshift/cli.py` report configuration ValueErrors on stderr with exit 2, without traceback or dispatch. Pause/resume and offline jobs retain their existing early paths.

Do not require `verify` on a default enrollment. A missing manifest remains a per-repository health hold, including with no engine set; wrong-type/empty supplied paths and malformed TOML remain strict failures. Existing manifest template validation, authorization, feedback consumption, copied manifests, admission and publication remain untouched.

Remove Endpoint/Assignment, active model resolution and worker scheduler settings once all consumers disappear. Accept obsolete TOML keys by presence, without interpreting their values: top-level `models`/`endpoints`, `repos.models`/`repos.verify`/`repos.concurrency` and daemon `max_tasks_per_night`, `concurrency`, `review_attempts`, `implement_max_turns`, `review_max_turns` and `mirror_to_paseo`. Emit one sorted, deduplicated `Config.notes` entry per qualified obsolete key, then log those notes at configuration load without values. No automatic manifest generation or provider selection.

Keep `Repo.verify` and `Repo.concurrency` as inert constructor compatibility fields (old shared-utility tests instantiate Repo positionally), `Config.mirror_to_paseo` as always-false compatibility, and `Config.worktree_root` with its existing parser/default. Retire `Repo.models` and all endpoint/model and task-budget fields. Worktree-root parsing survives because the host-paths contract promises validation and no movement; it no longer places new worker work. `base`, `dir` and `clone_dir` remain available to shared utilities.

### Daemon and leftover work

Keep `daemon.loop(cfg, repo_dirs, once=False)` and `startup(cfg, repo_dirs, health=None)` signatures during this change so existing CLI and injection boundaries remain usable. `repo_dirs` becomes compatibility input. Loop eventually returns None; update CLI run output and its jobs-command test double together when Tally is removed. Replace the worker summary with a concise poll-complete message for one-shot use, without inventing specloop shipped/cost counts.

A service pass beats, reaps background children, obeys pause, and runs `poll_specloop`; idle passes sleep `poll_seconds`. One-shot mode makes one pass and leaves detached jobs to existing collection. Retain SIGTERM handling, startup failure visibility, heartbeat-before-startup ordering and one-shot heartbeat ownership. Remove worker executor scheduling, quota windows, claim dispatch, run/harvest, task outcome accounting and automatic worker recovery.

`_reconcile_engine_switch` is retained under its existing name and journal paths. Every valid repo now uses its durable release-or-hold path. Preserve saved intents, comment markers, label conflict checks, outcome-before-cleanup ordering, unknown fields, replay, pause guards and health isolation. Never call legacy `queue.reconcile` from production startup: it can discard evidence. Legacy queue helpers and their tests remain as compatibility utilities; they cannot invoke an agent and have no production worker scheduler. Retain `queue.pr_feedback` and timestamp behavior explicitly required by pr-revision, as well as shared vcs, trace, outcomes and mirror readers. Remove `nightshift/recovery.py` because its worker execution decision tree has no remaining caller.

### Status, attention and client boundaries

Remove the off-subscription loop in `cli.main`, worker working counts, claim/phase/transcript lines and `_elapsed`/`_progress_of` once their tests migrate. Keep liveness, alerts, local active-job ordering/timing, inventory diagnostics, ready/blocked counts and lists, contradiction diagnostics, configuration loading, options and exit behavior. Repository summaries become `<repo>: N ready, M blocked`. No claim inspection or recovery is performed by status.

The background-job-control MODIFIED delta replaces the explicit worker-output guarantee. It preserves all three existing status scenarios. Removing model reporting is intentional because it describes retired worker sessions, not manifest-selected specloop agents.

Historical `attention` remains unchanged, including worker `tasks` beside `specloop`, bounded refresh, stale snapshots, offline reads and outcome resolution. Preserve `~/.nightshift` and `~/.specloop` byte locations and schemas. State cleanup occurs only under already-authorized durable recovery, never as installation cleanup.

`plugins/nightshift/server/attention.ts` still calls `uv run --no-sync nightshift attention --json` and checks the existing outcomes installation. `factory.ts` still calls `specloop`; `specloop/messages.py` already suggests valid specloop commands. Verify these boundaries rather than rewriting them. Preserve host settings, subprocess argv, allowlists, timeouts, caching, redaction and process cleanup. Neither package nor console-script name changes.

### Preflight and launchd

Remove endpoint catalog/tool-loop/read-only probes and worker verify-allowlist checks from `nightshift/preflight.py`. Keep token shape, enrolled-repo reachability, negative-control and alert checks. Remove worker-specific ambient-variable failure checks, but preserve the daemon wrapper's existing Anthropic-variable scrubbing. Provider isolation continues to be enforced by specloop's unchanged manifest/adapter environments.

`--deep` remains accepted with a retired-probe notice; it never invokes a model. Remove Python preflight's worker OAuth requirement as well as its worker probes. Test `nightshift run --once` and preflight directly with a valid specloop enrollment, isolated environment and injected external runners. Neither Python entry point requires worker OAuth, Claude, pnpm or Swift; manifest-selected providers and verification commands retain their own requirements.

Leave `bin/nightshift-daemon`, `bin/specloop-serve` and `launchd/**` byte-for-byte unchanged, including comments. The daemon wrapper still refuses a missing env file, worker OAuth token, GH_TOKEN or any of gh/claude/pnpm/node/git/swift before invoking Python. It still scrubs ambient Anthropic variables and uses fixed uv/cwd/PATH locations. Python changes cannot remove those shell checks. Read-only plist parsing and existing client tests verify the retained daemon/supervisor command split; do not execute or install services as tests.

README and the guide must distinguish the Python prerequisite guarantee from this frozen-wrapper limitation. A host without those legacy prerequisites needs separately authorized wrapper work or an independently provisioned operator service before rollout, retaining trusted env loading, scoped GH_TOKEN refusal, required front-door tool checks, credential isolation and preflight negative-control/alert checks. Do not recommend dummy OAuth tokens, bypassing guards or an agent-created replacement wrapper. Explain actual pause/stop behavior in allowed docs without correcting frozen plist comments. Installed service readiness remains a human merge check.

### Requirement preservation and documentation

The deltas change only the guarantees that retirement invalidates:

| Capability | Changed guarantee | Preserved behavior |
| --- | --- | --- |
| issue-intake | Worker default and worker scheduling references | Authorized labels, blocked-by, manifest holds, durable delivery, git timeouts |
| config-resilience | Mixed engines and executable worker recovery | All manifest validation scenarios, admitted-run progress, durable switch journals |
| background-job-control | Worker status and model report | All remaining status, job control, deadlines, inventory and compaction |
| host-paths | Healthy-worker fixture and root's active role | Path parsing, defaults, no migration and all host/plugin settings |
| specloop-documentation | Current dual-engine instructions | All unrelated publication, provider, revision, evidence and operational guidance |

No attention or pr-revision delta is needed: historical worker results and the feedback compatibility interface remain. Requirements about mutation/test workers are unrelated and remain intact.

Update README.md in bounded regions; preserve existing anchors used by live guides, rewriting a retired section to a short historical note/link when deleting its heading would break a link. Update retirement and rollout passages in `docs/specloop/README.md`, relevant limitations and `config.example.toml`. Identify `docs/providers.md`, `SPEC-endpoints.md` and `docs/validation.md` as historical worker material in allowed guidance; preserve their bytes and evaluation counts. Explain that frozen `CLAUDE.md` still contains obsolete worker instructions and `pyproject.toml` retains its old description; neither defines a supported execution path. Their cleanup and historical links to removed source are deferred, not silently represented as complete. Validate changed links/examples in allowed docs; do not expand link cleanup into excluded files. Plugin README and source remain unchanged. Keep #40's rename/state move limitation explicit. Do not edit archived OpenSpec history or base specs during implementation.

Documentation checks use existing link/example checks and TOML/CLI parsers, not prose assertions. No new Markdown parser is needed. If a new structural check is necessary, headings are only raw lines matching `^#{1,6} [^\r\n]+$` and inline local links only the single-line literal forms already used by these documents. Count matching lines even inside fences/HTML; duplicate target headings, unsupported link syntax or ambiguous target selection block that check instead of guessing. Do not infer fence, HTML or indentation semantics. Do not alter the application's existing rich-text parsers.

### Caller inventory and migration authorization

Removal of the following pre-change tests is explicitly authorized only for behavior retired here. Preserve independent shared-utility assertions; no blanket test deletion, skipping, relaxed monkeypatch validation or replacement with no-op production stubs.

| Removed dependency | Named callers to migrate before removal |
| --- | --- |
| Daemon claim/pool/window helpers | tests/test_concurrency.py, test_daemon_loop.py, test_endpoints.py, test_run_telemetry.py; worker portions of test_engine_routing.py, test_github_flakiness.py, test_outcomes.py |
| Mixed-worker progress assertions | tests/test_specloop_background.py and test_specloop_timeouts.py: replace worker claim/harvest with a second eligible specloop item and observed heartbeat/reap progress; retain timeout, receipt, trigger, retry and no-duplicate assertions |
| Startup and default-engine seams | tests/test_engine_switch_recovery.py, test_config_resilience.py, test_worktree_root_config.py, test_engine_routing.py, test_liveness.py |
| Successful explicit worker enrollment | Task 1.14 migrates tests/test_land.py::test_discovery_uses_pr_identity_regardless_of_engine (original lines 341–376) to omitted/explicit specloop enrollment while preserving canonical PR identity, matching/nonmatching discovery and landing gates. In the same task, tests/test_specloop_publication.py::test_only_managed_eligible_runs_are_published (225–250) changes only its worker case to constructor rejection before polling with no publication/comment effects; all other publication eligibility cases remain. |
| Claimed annotation dependencies | nightshift/daemon.py::run_claimed and _run_claimed (original lines 764 and 773) still annotate Claimed after claim_next is removed in 1.16. Keep the dataclass and required imports through 1.16; delete it with both consumers in 1.17. |
| Tally CLI return | nightshift/cli.py and tests/test_jobs_cli.py's run stub |
| task.run and task helpers | tests/test_task.py, test_theme_base.py, test_base_ref.py, test_workflow_scope.py, test_run_telemetry.py, test_endpoints.py, test_progress.py, test_max_turns.py; tests/test_push_guard.py:98–116 (import/source inspection at 109–111), tests/test_verify_is_runnable.py:89–110 (task import at 100) |
| Worker invocation and probes | nightshift/task.py, nightshift/preflight.py; tests/test_endpoints.py, test_reviewer_readonly.py, test_deletion_permitted.py, test_verify_is_runnable.py |
| Worker recovery/teardown helpers | tests/test_recovery.py and test_escalation_keeps_work.py; retain branch-preservation tests in test_rearm_keeps_work.py and vcs safety tests |
| Worker status helpers | tests/test_status_shows_work.py's elapsed tests; tests/test_jobs_cli.py's status fixture and worker-output assertions; retain claim-reader/title tests and all independent inventory behavior |
| Endpoint and budget config | daemon, task, preflight and CLI consumers above; tests/test_endpoints.py, test_reviewer_readonly.py, test_max_turns.py; retain test_mirror.py's inert flag and shared Repo fixtures |
| Allowed documentation | README layout/setup links and docs/specloop/README.md current guidance; excluded docs/providers.md and docs/validation.md source links and frozen CLAUDE.md instructions are historical-reference limitations recorded in allowed docs, not executable callers |

`tests/test_specloop_approval.py` and `test_specloop_publication.py` call loop with the retained signature; preserve them, applying only the explicit worker-enrollment migration above to publication coverage. Landing's canonical PR identity and run-discovery requirements and all surviving publication eligibility assertions remain unchanged; these fixture migrations need no additional capability delta. `test_specloop_messages.py`, `test_specloop_attention.py`, plugin factory/reader/host-path tests and documentation integrity tests remain acceptance coverage. `tests/test_prompt_test_rules.py` imports specloop prompts, not nightshift prompts, and must remain.

Caller scans include qualified references, `from nightshift import ...`, `from nightshift.<module> import ...`, monkeypatch targets and source-path reads. The inventory names all currently found breaking callers; any incidental import cleanup stays with its assigned concern and budget.

### Task sizing and verification

Tasks are ordered caller-first, then unreachable source deletion, then docs/integration. Earlier test-retirement tasks leave the old code intact until its remaining callers are gone. Replacement tests use explicit specloop enrollment before the default flips, so they pass on both intermediate and final code. Source-only deletions rely on callers already retired in this same atomic change; keep their remaining imports valid until the last source slice is removed.

The landing fixture's omitted-engine variant and publication's worker-rejection assertion arrive with the default/rejection change in 1.14, not before it. Task 1.16 retains Claimed even though its producer is gone: its remaining annotated consumers are removed together in 1.17. Both boundaries must pass pytest and the unchanged type-baseline check independently; the later task cannot repair an earlier task's undefined-type diagnostics. Their updated line allowances include these fixture edits and the moved dataclass deletion within the approximately 300-line ceiling.

The task list has more than twelve sessions because the measured source alone includes task.py (563 lines), worker.py (542), hundreds of daemon lines and endpoint config/probes; test_endpoints.py adds 646 and test_concurrency.py 356. Twelve 300-line tasks would not cover source, test deletions, replacement coverage and docs. Every task names fixed symbol/test boundaries and a total changed-line allowance; no implementation-time OpenSpec resplitting is required or authorized. One section avoids extra evidence gates for intermediate deletion slices. Each task runs `uv run pytest` and relevant type checks in check mode, leaving the baseline unchanged.

Final automated acceptance: `uv run pytest`; `uv run python scripts/check_types.py` without `--update`; existing plugin `npm run typecheck` and `npm test` from plugins/nightshift without modifying plugin files; read-only example TOML/plist parsing; direct Python CLI/preflight checks with isolated environments and fake external runners; import/CLI checks for absence of the retired engine and unchanged package entry points. Audit the final diff against the implementation allowlist. Do not claim Python tests prove an external script has migrated or frozen wrapper prerequisites disappeared.

### Human checklist for the implementation PR

These are pre-merge checks for the operator, not agent tasks:

- Confirm all proposal blockers are satisfied and the host has no active worker process before replacing its code. Inspect claims/branches/worktrees and preserve unfinished work.
- Inspect the actual config used by the service. Remove explicit worker enrollment, provide trusted manifests, review ignored-key notes, and verify chosen provider credentials through the manifest rather than worker OAuth assumptions.
- Inspect installed launchd plists and loaded jobs, including ProgramArguments, WorkingDirectory, environment and wrapper targets. Compare both jobs with the retained front-door/supervisor split; do not infer installation state from repository templates. Record whether the unchanged wrapper's legacy prerequisites are present or a separately provisioned service is required. A host without them must have that separate service work completed and validated before rollout; Python retirement does not make the frozen wrapper credential-free.
- Inspect external scripts, aliases, cron/launchd entries and Paseo installation calls for worker entry points or old assumptions. Confirm no external caller still depends on task.run, worker probes, worker status/model output or worker configuration.
- On the host, confirm front-door daemon and independent specloop supervisor operate with the intended config and provider profiles. Check historical attention and leftover-work recovery, without treating pause or daemon unload as stopping admitted builds.
- Read changed operational guidance and verify live issue/PR references. Record pending or failed checks on the PR; merge only after the live config/plist/caller inspection is complete.

## Risks / Trade-offs

Deleting executable modules can break source-inspection tests and monkeypatch seams long before imports fail. Fixed caller-first slices and full-suite checks after every task address this; retaining shared utilities avoids expanding retirement into a git/queue API rewrite.

Old config without a manifest now holds instead of starting a worker. Explicit worker enrollment fails immediately. Inert legacy fields are a compatibility compromise, accompanied by visible key-only notes; they cannot select an engine or silently become manifest configuration.

Historical attention still displays worker outcomes. That is evidence, not a second engine. Keeping its schema and old state locations avoids folding #40 into this change.

The additional sessions are deliberate line-budget boundaries, not new subsystems or multiple PRs. Intermediate commits are verified but not deployable milestones. Freeze conflicts or new prerequisite churn must be resolved before applying this fixed plan rather than silently exceeding the ceiling.

CI cannot discover external callers or prove installed launchd state. The human checklist is mandatory before merge; this draft performs no live host migration.

Frozen wrappers retain redundant worker prerequisites and stale comments. Frozen metadata/instructions and excluded historical docs retain obsolete descriptions or source links, and the type baseline retains resolved diagnostics. These are explicit follow-up limitations, not permission to widen the implementation paths. Allowed current guidance must expose them so a green suite is not mistaken for a completed host migration.

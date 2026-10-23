## Context

`Repo.__post_init__` in `nightshift/config.py` requires a manifest and checks `is_file()` during configuration loading. An availability failure aborts all repositories; full schema validation happens later. `daemon.startup` skips specloop repositories and their old worker claims. `specloop_intake.propose_pending` derives names from repository basenames, although receipt directories already include the owner.

The checkout includes #6's feedback ledger, #8's pause/cancellation boundaries and #67's docs. Preserve those contracts. `poll_specloop` already isolates stages and aggregates repository health. Admission and revision reject unsafe branch components. In particular, retain `tests/test_specloop_approval.py::test_ineligible_pr_is_not_admitted`'s `spec/widget..js-issue-7` case and its no-command/no-comment assertions.

## Goals / Non-Goals

Keep healthy repositories running; report enrollment failures with a repair path; reconcile worker leftovers safely; distinguish new changes across owners; document the actual behavior. Core behavior is testable with temporary files, injected GitHub/CLI boundaries and persisted receipts without a live host.

Do not gate the supervisor or rename deployed runs. Do not add name encoding, modify transport retries or implement hot config reload. Editing a config entry takes effect on the usual restart; repairing its manifest at the same path takes effect on a later poll.

## Decisions

### 1. Configuration shape and readiness are separate

Keep config TOML, engine enum and worker-setting validation in `config.load`. A supplied manifest must remain a nonempty string/path; wrong types and empty strings remain shape errors. An omitted specloop manifest becomes `None` and a repository health failure. Missing, unreadable, non-regular and invalid manifest artifacts must not fail construction. Preserve tilde expansion and config-relative resolution independent of daemon cwd; direct `Repo` construction keeps its existing cwd convention. Defer filesystem failures during resolution to repository health too.

Add one shared readiness helper in `nightshift/specloop_intake.py`, returning the parsed manifest or a typed configuration error. Before every enrollment-manifest open/read, retain the existing `Path.is_file()` regular-file check. Reject directories, FIFOs and devices without opening them; symlinks to regular files remain supported. Use `specloop.manifest.load` for schema validation without changing or duplicating that parser. Do not execute verification commands or agents during validation. Diagnostics identify repository, path and repair action, without dumping manifest contents.

Evaluate readiness afresh on every unpaused poll, sharing at most one result per repository per poll; direct stage callers use the same helper. Do not cache failed file contents across polls. Catch expected path/read/parse/schema errors as readiness failures without disguising programming bugs. Repair at the same path removes the hold automatically; adding an omitted config entry needs the normal restart.

Extend `RepoHealth` to report configuration failures immediately, deduplicate notifications for a continuing outage, log recovery and rearm notification after repair. Avoid its token-access advice for configuration errors. Aggregate readiness and stage failures once per poll: successful publication or claim recovery cannot erase the configuration failure. Retain existing GitHub outage thresholds.

### 2. Gate new commands, not whole stages

Pass readiness into `propose_pending`, `admit_pending` and `revise_pending`. Check before new `propose`/`admit` dispatch, before manifest-dependent git operations and before writing a new workflow receipt or approval-head record for that attempt. A hold preserves triggers and receipts/jobs, creates no item escalation or `needs-human` label, and spends no agent session.

`revise_pending` still authorizes before feedback/status/state changes, honors #6's selection/consumption rules and handles empty feedback without calling status. For nonempty feedback, its existing status lookup distinguishes an admitted run before applying readiness only to the unadmitted `propose` path. Admitted `revise`, consumption and delivery must not open the enrollment manifest or regenerate the branch. An unsafe current repository spelling cannot invalidate an existing safe branch; actually unsafe recorded branches still fail existing validation.

Always reach `publish_pending` for admitted runs despite readiness failures. Preserve its status-error, PR identity, base synchronization, pause and retry checks. Publication uses the run's own manifest and recorded branch. Admission is complete when durable run state exists, even if its notification is pending: detect that state and complete delivery without another admit or a healthy enrollment template.

Existing jobs can finish and be collected. Delivery that does not require a damaged template continues under existing rules. If pre-admission delivery needs an unavailable manifest-derived source path, retain its successful result and defer that operation until repair as a repository health failure, without redrafting or item escalation. An unsupported new-name policy does not forbid delivery to an existing safe branch. Dependency-blocker-independent recovery stays intact. Pause continues to gate every launch and external delivery; durable stop intent continues to win over results.

Only Engine per repository, Drafting from an issue, Approval admits and Spec-stage revision need MODIFIED deltas. Publication and Build-stage revision remain unchanged; added regression scenarios exercise those guarantees while enrollment is unhealthy.

### 3. Reconcile worker leftovers before new intake

Add a helper in `nightshift/daemon.py` for currently specloop repositories on unpaused startup/polls, before new intake, even if readiness fails. No engine-history database is needed, so switches made while stopped are detected. Never invoke worker claims or `_recover`'s worker execution/PR-creation paths for these repositories. Keep normal worker reconciliation unchanged.

Use existing `queue.in_flight`, working-label listing and `queue.labels_of` read-through APIs with injected, pause-guarded GitHub calls. Do not run destructive `queue.reconcile` for specloop repositories: its litter cleanup can discard the final claim after a remote transition succeeds but its response is lost.

For a working issue with no claim or no surviving worktree, replace working with ready and record a `needs_decision` interrupted-recovery outcome. For a surviving worktree, preserve branch/worktree, explain phase and location in a marked issue comment, and replace working with `needs-human`. Do not resume or delete that work. Preserve unrelated labels. Unreadable claims or unknown GitHub state cause repository health failure, not blind release. Read labels before mutations so stale search results cannot undo completed transitions.

Save a small versioned journal under `STATE_DIR/engine-switch/<owner>__<repo>/<issue>.json` before remote mutation. Include action, claim snapshot, stable comment marker and progress. Use atomic replacement like `_save`; preserve unknown fields. The explanation can reuse an existing transport-recognized draft marker with `engine-switch-<issue>` as its change key and a journal ID, without changing the transport. Complete local outcome bookkeeping from the journal before clearing the claim. Read unfinished journals even when working is absent.

On subsequent polls reconcile labels and marker-bearing comments, finish missing local bookkeeping and avoid repeating completed effects. Failed reads/writes retain evidence and retry later. Conflicting human label changes are preserved and reported instead of blindly rearmed. Recovery failure holds new intake for that repository but leaves eligible admitted-run publication/build revision and other repositories available. Pause also guards recovery comments and label transitions.

### 4. Qualify new identities, preserve recorded ones

Centralize generation in `specloop_intake.py`: split `owner/repository` once, preserve both spellings/punctuation, join with `__`, then append `-issue-<number>-<title-slug>`. Keep the current slug algorithm: lowercase ASCII alphanumerics separated by hyphens, at most 40 characters, trimmed, fallback `issue`. Example: `owner__sample-issue-7-add-a-gate`. GitHub owner names cannot contain underscores, so the delimiter distinguishes supported GitHub owners without lossy normalization. This does not support arbitrary non-GitHub namespace strings.

Share the existing predicate: `[a-zA-Z0-9_][a-zA-Z0-9_.-]*`, no `..`, no trailing dot or `.lock`, with `spec/` prefixed to form the branch. Validate an issue-shaped representative at repository-readiness time and the actual generated name before dispatch. `owner/widget..js` is unsupported for new work: report health and dispatch no new drafting/admission/spec re-drafting. Valid dots, underscores and hyphens remain unchanged. Preserve the existing unsafe-branch assertions, without encoding or sanitizing repository names.

Use qualified names for new proposal receipts, CLI arguments, branches and PR lookups. Keep receipt directory namespaces unchanged. Existing active receipts remain authoritative after title edits; admission, publication and revision retain recorded PR/run identities, including ownerless names. A genuinely new round after a closed legacy PR uses the qualified name while preserving replacement bookkeeping.

Without a proposal receipt, try exact repository-scoped lookup for the qualified candidate and then the legacy basename candidate built with the current title slug. Lookup errors are not absence and prevent new drafting. Recover an open legacy PR without renaming or creating another PR; preserve closed/merged lifecycle decisions and authorization. Do not scan or rewrite global runs to guess ownership.

### 5. Verification and operator documentation

Add focused regressions in `tests/test_config_resilience.py`, `tests/test_engine_switch_recovery.py` and `tests/test_owner_qualified_changes.py`. Existing tests may change only for intentional config-error timing, new-name expectations and schema-complete manifest fixtures that preserve their intended boundary. Complete the fixture used by `tests/test_specloop_intake.py::test_github_lookup_failure_reaches_repo_health_without_proposing`; keep its assertions unchanged and prove the fake GitHub lookup is reached. Keep ownerless fixtures that exercise legacy identity, rather than replacing all basenames. Preserve unsafe-branch assertions.

Required names: `test_bad_manifest_marks_repo_unhealthy_not_config`, `test_engine_switch_recovers_working_issues`, `test_change_names_are_owner_qualified`, and `test_unhealthy_repo_still_publishes_admitted_run`. Cover invalid TOML/schema, missing/unreadable/non-regular files, repair between polls, healthy worker progress, admitted revisions and crash/restart recovery. Use command/lookup spies and schema-complete fixtures, not a blanket readiness-success mock. Use scenario markers. Implementation acceptance is green `uv run pytest` and required repository type checks without growing or editing the baseline.

Update only the relevant portions of `README.md`, `docs/specloop/README.md` and `KNOWN-LIMITATIONS.md`: holds and repair, switch outcomes, qualified examples, unsupported names, admitted-work continuity and legacy limits. Remove only the three resolved Config bullets; retain residual limitations. Cite verified issue #13 and an implementing PR only once known. Preserve #67's remaining guidance. Overlap with #72, #73, #74 and #76 is necessary only for these passages; preserve adjacent changes.

Do not edit `nightshift/queue.py`, `nightshift/cli.py`, `nightshift/arm.py`, `nightshift/deploy.py`, `nightshift/land.py`, `nightshift/timings.py`, `pyproject.toml`, `mypy-baseline.txt`, `docs/arming.md`, `docs/deploy.md`, `docs/landing.md`, `examples/specloop/select_tests.py` or `examples/specloop/self-hosting.toml`. Do not edit executable files under `specloop/`. Do not edit `tests/test_arm.py`, `tests/test_deploy.py`, `tests/test_land.py`, `tests/test_timings.py`, `tests/test_select_tests.py`, `tests/test_mutation_narrowing.py`, `tests/test_mutation_route_baselines.py`, `tests/test_mutation_route_examples.py`, `tests/test_mutation_routes.py`, `tests/test_provider_cooldown.py`, `tests/test_provider_fallback.py`, `tests/test_provider_fallback_continuation.py`, `tests/test_provider_fallback_docs.py`, `tests/test_provider_retry_accounting.py` or `tests/test_provider_unavailable_adapters.py`.

## Risks / Trade-offs

- GitHub transport retries label writes internally without rereading labels. Poll-level reconciliation cannot prove exactly-once effects within an ambiguous request, particularly if a human rearms during a retry. This accepted residual risk remains; do not change transport behavior here.
- Exact legacy lookup cannot find an old PR after both receipt loss and title change. Existing global collisions may require operator decisions. No automated migration or universal deduplication claim is made.
- Regular-file checking prevents reading a configured FIFO/device, but does not promise protection from hostile path-replacement races or an unavailable filesystem. Manifests remain trusted, operator-managed files.
- Fixture updates and integration coverage are substantial. Seven tasks separate readiness from stage integration and initial reconciliation from restart recovery to target about 300 changed lines each. These distinct failure boundaries justify the extra cycle.
- Human checklist for the PR: inventory deployed ownerless runs and their PRs/receipts; confirm recorded identities remain usable and decide any collision/renaming migration outside this change. Check live references and read the three changed operator passages for clarity. Local tests cannot prove migration of deployed runs. These checks are not implementation tasks.

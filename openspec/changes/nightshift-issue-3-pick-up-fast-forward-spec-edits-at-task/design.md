## Context

Each run owns a bare `repo.git`. `Ctx.ref` names its run branch; `Ctx.m.repo` names the local source clone, whose branch of the same name is the source of human corrections. Today `cli._sync(ctx)` fetches that branch into `refs/specloop/source`, checks ancestry, and conditionally updates the run ref. Only `_resume` calls it, under the run lock and before making the run runnable. The helper silently ignores divergence and prints fetch failures.

`loop.run(ctx)` checks for a requested pause, reads the branch head and tasks, then chooses evidence, a phase gate, a task attempt, or final checks. `_land` advances the candidate plus task tick through `_advance` using an expected old head. `step` only resumes pending review or sealed verification when its saved base matches the current head. These safeguards must remain.

The prerequisite is issue #14. Its archived CLI-polish change is present, including exact matching of runtime scenario references. Read guidance includes README, `CLAUDE.md`, engine documentation, known limitations, and the existing `specloop-interface`, `run-actions`, and `run-context` specifications. No `AGENTS.md` exists in this checkout or its ancestor chain. No existing requirement needs a MODIFIED or REMOVED delta.

## Goals / Non-Goals

Import a source tip that strictly descends from the run head before the next scheduling decision, without a sync-induced pause. Preserve landed commits, provide durable events for ignored divergence, and keep resume using the same ancestry and ref-update rules. Tests must use temporary local Git repositories and controlled driver collaborators, without Paseo, GitHub, live providers, or `sandbox-exec` for the new coverage.

Do not edit the daemon, `specloop/serve.py`, state schemas, status rendering, manifests, publication, agent prompts, or evidence/gate algorithms. Do not fetch GitHub or `origin` on behalf of the source checkout, push to it, modify its worktree, merge, rebase, or force-move the run branch. Uncommitted edits and commits not yet available on the local source branch are not eligible. Do not add a background synchronization thread, new CLI command, configuration switch, task-state migration, or gate-cache invalidation policy.

## Decisions

### Shared source import in the engine

Add `sync_source(ctx: Ctx) -> None` in `specloop/loop.py`. Retain `cli._sync(ctx) -> None` as a small delegating wrapper, preserving its existing call site and interception points used by CLI tests. Do not import the CLI from the loop. The helper records outcomes with `Run.event`; it does not change run status, pause state, attempts, task identities, notes, evidence state, or gate state.

Fetch the explicit branch from `str(ctx.m.repo)` into `refs/specloop/source`, as today, using `+<ctx.ref>:refs/specloop/source`. The leading plus permits replacement only of this tracking ref, including when source history was rewritten. Never fetch directly into `ctx.ref`. Resolve the fetched source SHA once and use that immutable SHA throughout the attempt. Read the current run head after the fetch.

Classify Git history explicitly:

| Relationship / outcome | Action and event |
| --- | --- |
| Source equals run head | No branch update and no sync event. |
| Source is an ancestor of run head | No branch update and no sync event; this is normal after landing. |
| Run head is a strict ancestor of source | Attempt `git update-ref <ctx.ref> <source> <old-head>`; on success emit `synced` with existing `head=<source>` and additive `previous_head=<old-head>`. |
| Neither tip is an ancestor of the other, including unrelated histories | Leave the run ref untouched; emit `sync_skipped` with `reason=diverged`, `head=<old-head>`, and `source_head=<source>`. |
| Fetch fails, including missing source branch or repository | Emit `sync_failed` with `stage=fetch` and a nonempty diagnostic bounded to 500 characters; return without consulting a stale tracking ref. |
| An ancestry query fails with an exit other than 0 or 1 | Emit `sync_failed` with `stage=ancestry` and a bounded diagnostic; do not treat it as divergence or update the run ref. |

One divergence event per attempted boundary is sufficient; no persistent deduplication state is introduced. Retain resume's useful success/fetch diagnostic output through the shared helper, without introducing notifications. Existing CLI JSON capture remains responsible for keeping command stdout a single document.

### Boundary ordering

At the top of each running iteration in `loop.run`, keep the pause-request check first. If a pause is requested, take the existing `requested` pause without fetching. Otherwise call `sync_source`, then read `ctx.head()` again and parse `ctx.tasks_text(head)`. Every subsequent evidence, gate, task, and final decision in that iteration uses that refreshed head and task list.

A boundary includes the first iteration, the return from a task attempt (including a retry), and iterations after evidence or gate work returns control. The helper is never called within implementation, sealing, verification, review, or landing. There is no extra fetch inside `finals`: a source edit arriving after the boundary snapshot waits for a later boundary if there is one. Final publication and completion retain their existing pauses.

An imported unchecked task must be visible before deciding that a section or run is complete. Existing evidence and gate policy still applies to the resulting task list; sync neither bypasses required checks nor resets prior gate state. A changed head makes stale pending review/sealed candidates ineligible through the existing base comparisons in `step`; preserve those comparisons and `_land`'s guarded update.

### Ref-update races and errors

Production callers already hold `Run.lock()` around driver execution or resume; use that lock instead of acquiring a second one inside the helper. Sync is synchronous on the driver thread. Nevertheless, retain the expected-old-head argument on every run-ref update to protect against writers outside the lock.

If the guarded update fails and rereading the run ref shows it moved from the expected head, emit `sync_skipped` with `reason=run_moved`, the expected `head`, `source_head`, and `actual_head`. Do not retry the update within this boundary, emit `synced`, or undo the winning ref update. The loop rereads the actual head before scheduling. If the ref did not move, propagate the update failure rather than disguising a repository write error as a race; this retains resume's existing failure-before-unpause semantics. Do not swallow event-write errors or arbitrary programming exceptions.

If the source advances again after fetch, this attempt may import only the captured source SHA; a later boundary considers the newer source. A fetch or ancestry failure and known divergence/race do not consume attempts or introduce pauses. No guarantee is made that a new source commit is imported if it no longer descends from the current run head.

### Tests and implementation boundaries

Use new `tests/test_specloop_source_sync.py` for ancestry, fetch/ancestry errors, resume delegation and resume state compatibility; new `tests/test_specloop_boundary_sync.py` for driver scheduling; and new `tests/test_specloop_sync_races.py` for deterministic ref interleavings. Use fixtures local to those files, with Git identities configured only in temporary repositories. Tag tests with `pytest.mark.scenario` for the scenarios they cover.

The named fast-forward regression must exercise one uninterrupted `loop.run`: let a first task land, fetch that landed run tip into the temporary source, create a human task edit on top, and observe the new task text at the next step. This constructs a real strict descendant, not a sibling of the landed commit. Assert event ordering, preservation of the task tick, and that no sync-induced pause/resume occurs. The named divergence regression must create sibling source/run commits, assert a `sync_skipped` event, and observe the driver executing the run's original next task without the source edit or a merge commit. Mock expensive agent/verification collaborators, not the ancestry checks or branch updates under test.

For races, inject another valid run-ref update just before sync's expected-head update, and separately advance source after fetch. Verify actual Git refs and events, then verify scheduling uses the winning run head. Check that a source edit during a controlled step does not trigger a fetch until the step returns. Preserve every existing test and assertion. Run focused new files per task and `uv run pytest` after the final task; the definition of done is a green full suite with no existing test weakened.

Do not edit files reserved by other changes: `nightshift/specloop_intake.py`, `nightshift/touchpoints.py`, `plugins/nightshift/tests/factory.test.ts`, `tests/test_engine_routing.py`, `tests/test_specloop_act.py`, `tests/test_specloop_approval.py`, `tests/test_specloop_background.py`, `tests/test_specloop_label_authorization.py`, `tests/test_specloop_label_authorization_recovery.py`, `tests/test_specloop_state_reads.py`, `tests/test_specloop_status_json.py`, or `tests/test_touchpoints.py`. Do not import fixtures from these files. The implementation needs no changes to them.

## Risks / Trade-offs

- Fast-forward eligibility is deliberately narrow. After a task lands in the private run repository, a human edit based on an older source tip usually diverges. It will be recorded and skipped; this feature does not reconcile branches or publish intermediate landings.
- Each running iteration adds a local Git fetch. The source remains the manifest's local repository; remote freshness and hung-fetch policy are unchanged.
- Whole-commit import retains resume's trust model. This does not add admission, changed-path filtering, or retrospective re-verification of previously passed sections. Normal verification, review, and final checks remain in place.
- Events and Git ref updates are not a transaction. A process death after a successful update but before the event append can leave the new head without its `synced` event, as with the existing helper. Crash-transaction redesign is outside this change.
- Controlled tests establish ordering and compare-and-swap behavior, but CI cannot prove the absence of a race with a landing commit on a live run. The pull request's human checklist is: on a disposable live admitted run, arrange source corrections descending from the latest run head near a task landing; inspect Git ancestry, task ticks, and the `landed`/`synced`/`sync_skipped` sequence; confirm no landed work is lost and no unexpected pause occurs. Also try a sibling source correction and confirm it is logged and ignored. Capture observations rather than claiming that one live check proves all timings. This is a human check, not an implementation task.

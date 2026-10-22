## Context

`specloop_job.start()` records a UUID in the intake receipt, writes a request
under `<stage>/<repo>/<number>.jobs/<uuid>/`, and launches a wrapper with
`start_new_session=True`. The wrapper and CLI inherit a lifetime flock. Only
the poller writes the intake receipt; the wrapper writes `result.json`.
`_children` is an in-memory reaping aid, not durable process identity.

`daemon.poll_specloop()` already guards GitHub, git, specloop calls and launches
with pause checks. `tests/test_specloop_background.py` already covers a result
finishing while paused and subsequent delivery. Strengthen that behavior with
the requested named admission test and checks at delivery boundaries.

The intake functions in `nightshift/specloop_intake.py` call `collect()` before
delivery. Returning `None` preserves the job receipt and reports pending work
without a health failure. Raising a normal job error instead removes the job
reference and permits retry, which is inappropriate for an intentional stop.

`specloop.cli._admit_locked()` commits `state.json` before returning success;
`specloop.serve.pass_once()` discovers that state independently. Neither a held
Nightshift notification nor terminating a completed intake job revokes that
admission.

## Goals / Non-Goals

Provide an offline inventory and bounded, explicit termination of tracked
background process groups. Make intentional stops durable across polls and
daemon restarts. Preserve paused completion results for later delivery and
retain the existing behavior of worker tasks and independent build supervision.

Pause remains a scheduling/delivery hold; it does not automatically signal
running jobs. No new retry command, automatic cleanup, supervisor integration,
remote rollback or process-tree traversal beyond the owned POSIX group is
included. Jobs stop is not a universal stop button for every Nightshift process.

## Decisions

1. **Inventory and CLI contract.** Add `list_jobs(state_dir)` and
   `stop_job(state_dir, job_id)` to `nightshift/specloop_job.py`. Enumerate only
   `proposals`, `admissions` and `revisions` job directories, including historical
   attempts whose parent receipt now references another job. The full UUID is
   the public ID; no prefixes or path arguments. Sort by stage, repository,
   item number and ID. Print ID, stage, repository/item, command, state, PID/PGID
   when known, and log path. States are `starting`, `running`, `finished`,
   `failed`, `stopping`, `stopped`, or `unknown`. Finished means execution
   completed, not necessarily delivered. Missing metadata in older jobs must
   remain listable; do not infer a signal target from `_children` or PID alone.

   Extend `nightshift/cli.py` argument parsing without changing existing command
   defaults or flags. Dispatch jobs before configuration loading, as with
   pause/resume. Neither command invokes GitHub, specloop, or credential-dependent
   services. Empty inventory prints `No background jobs.` and returns 0.
   Successful stop and repeated stop of a confirmed stopped job return 0;
   unknown/unsafe/uncontrollable IDs and operational failures return 1;
   malformed syntax returns 2. Completed/failed jobs are reported as already
   finished with exit 0 and are not relabelled stopped. Invalid IDs cannot
   escape the state directory. Unreadable entries are reported with exit 1
   while valid inventory rows remain visible.

2. **Durable identity and ownership.** Add atomic `process.json` containing
   version, job ID, PID, PGID, process-start identity and start time. The wrapper
   records its identity before calling specloop, preserving the inherited
   lifetime lock and existing request/result formats. Query process-start
   identity through a small injectable helper using local `ps` on macOS/POSIX;
   verify start identity, PGID and the wrapper command's absolute job-directory
   argument before signalling. Never signal PGID 0, 1 or the caller's group.
   An identity mismatch, absent legacy metadata, or a missing leader with live
   descendants is an explicit refusal, not a guessed target. A launch still
   awaiting metadata may be retried by the operator; the command does not wait
   indefinitely. Additive metadata must not break existing receipt readers.

3. **Stop protocol and bounded escalation.** Use a separate per-job
   `control.lock`; never wait for the child's lifetime lock before signalling.
   Under the control lock, check completion and identity, then atomically write
   `stop.json` with state `stopping`, request time and identity before SIGTERM
   to the whole group. Wait up to 5 seconds with a monotonic clock; if the same
   owned group remains, send SIGKILL and allow 1 further second for exit.
   Record `stopped` and stop time only after termination is confirmed. Probe
   group members, not only the wrapper; zombies awaiting reaping are not
   executing work. Reap owned children where possible. Keep logs and all
   receipts/results. Permission errors, uncertain ownership or a surviving
   group return an actionable error; persist any accepted stop intent so it
   continues to suppress delivery, and allow a later stop call to retry.
   Signal-free mocks test failure paths; real subprocesses test termination.

4. **Completion versus stop.** Serialize wrapper result finalization,
   `collect()` and stop admission with `control.lock`. If completion is already
   durable or collection has accepted the result, stop reports already finished
   and does not change its delivery eligibility. If stop intent wins, finalization
   cannot clear it and `collect()` returns `None` even if a result subsequently
   appears. A small durable collection marker closes the interval between
   collection and parent-receipt updates. Thus a stopped result never enters a
   cached receipt that bypasses collection. Holding `control.lock` is brief;
   release it before termination waits so the wrapper can exit.

   Leave the intake receipt's job reference intact. Existing `_collect()` then
   keeps stopped work pending without clearing the reference, delivering a
   result, starting another attempt or escalating an intentional cancellation.
   Pause/resume and daemon restart do not clear stop intent. Automatic rearming
   of stopped work is outside this change; README must say that label changes
   alone do not release this local hold. Any manual receipt recovery requires
   pausing first and inspecting admission state and remote effects. No changes
   to `specloop_intake.py` or the engine are required by this design.

5. **Pause boundary.** Keep the early paused return and guards in
   `daemon.poll_specloop()`. Check immediately before each launch and each
   external delivery operation, including retries. A job may write its local
   result while paused, but the daemon must not push, create/comment/edit PRs
   or issues, remove labels, publish, or mark delivery complete. Resume uses
   existing idempotent delivery markers and does not rerun the job. An operation
   already dispatched when pause appears may finish; later operations wait.
   Pausing is not a transaction that rolls back remote calls.

6. **Validation and operator documentation.** Extend
   `tests/test_specloop_background.py` and add focused CLI/job tests under
   `tests/`, tagged with the scenario IDs in tasks.md. Required acceptance tests
   are `test_jobs_stop_terminates_process_group` and
   `test_paused_daemon_does_not_deliver_finished_admission`. Use temporary state,
   fake GitHub/git/specloop interfaces, real disposable wrapper/child/grandchild
   groups, and barrier files instead of timing guesses. Clear `_children` or
   use a fresh CLI process to exercise restart behavior. Test teardown must
   terminate/reap test groups even on failure. Run `uv run pytest` for the
   implementation; this documentation-only draft does not claim that result.

   README will show `pause`, `jobs`, `jobs stop <id>` and `resume`, explain the
   grace period and persistent stopped hold, and correct the implication that
   unloading the daemon kills detached jobs. Explain that admission committed
   before cancellation may still build under `specloop serve`.

## Risks / Trade-offs

- PID reuse makes stored PIDs insufficient. Identity checks and refusal on
  uncertainty reduce accidental signalling; POSIX checks and signals are not
  one atomic operation. Tests must cover identity mismatches and exclude the
  caller's group. Descendants that create a new session are outside the group.
- A terminal stopped job remains pending internally to reuse the intake
  interface; the jobs listing is the authoritative operator view. This favors
  a durable hold over automatic retry and avoids expanding the engine protocol.
- Legacy live jobs without verifiable identity cannot safely be stopped by this
  command. Report that limitation and the log path rather than claim success.
- SIGKILL can leave partial local artifacts. Existing admission recovery applies
  only when a later attempt is deliberately enabled; never erase evidence here.
- CI can prove local group termination and absence of new delivery calls. It
  cannot prove the remote result of stopping during `git push`. Before operational
  rollout, a human must use a disposable remote, stop during an observed push,
  inspect remote refs/PRs and local logs, and record any accepted remote effects.
  A daemon-owned push outside the selected job's group is not killed by jobs stop.
- Real launchd unload behavior requires a macOS host check: launch a disposable
  job, unload the daemon, list and stop the surviving job from another terminal.
  Do not infer this service-manager check from a mocked subprocess test.

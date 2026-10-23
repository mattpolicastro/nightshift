## Context

`specloop_job.start()` records a UUID in an intake receipt and creates
`<stage>/<encoded-repo>/<item>.jobs/<id>/` with request, lifetime lock, log,
process identity and eventual result. Stages are proposals, admissions and
revisions. #8 adds `stop.json` and `control.lock`: `collect()` returns pending
for either stopping or stopped attempts, even if a late result exists.
`finish_stop()` records stopped only after termination is confirmed.

`propose_pending()`, `admit_pending()` and `revise_pending()` retain that
reference. `_collect()` and revision legacy initialization can write receipts
from earlier reads. Release introduces a second receipt writer, so an atomic
file replacement alone is insufficient to prevent stale writes.

Build revisions currently write `<item>.<event>.feedback.json`. Release can
permit another invocation with the same event, overwriting the input named by
the stopped job's retained request. Initial drafts and spec revisions already
use unique idea files. #13's health gates and recorded identities remain in
force. No AGENTS.md was found in the checkout or its checked ancestor paths.

## Goals / Non-Goals

Provide a local, explicit way to detach a confirmed stopped attempt while
keeping its evidence and cancellation permanent. Keep all existing intake
eligibility, delivery, consumed-feedback and assertion guarantees except the
explicitly modified prohibition on replacement after release.

Do not restart the stopped process, execute a new job from the CLI, reset
feedback history, change labels, bypass blockers, alter pause or admitted run
state, or repair partially committed external effects. Do not modify the stop
protocol or require a new label event solely because release occurred.

## Decisions

1. **Local interface and validation.** Add
   `release_job(state_dir: Path, job_id: str) -> str` and `ReleaseJobError` in
   `nightshift/specloop_job.py`, returning `released` or `already released`.
   Resolve the exact lowercase 32-hex ID through the same local inventory as
   stop; reject prefixes, paths, unknown IDs and duplicate matches. Derive the
   receipt path from the enumerated job directory, never user-provided paths
   or the current repository configuration. Reject symlinked receipt targets.
   Require readable valid job records, a confirmed `stopped` stop record with
   its recorded termination timestamp and matching persisted process identity,
   and an available lifetime lock opened without creation. A busy lifetime
   lock, `stopping`, running/starting, finished, failed, collected, malformed
   or missing evidence is a refusal. Do not re-probe a dead PID: the durable
   stop confirmation is the authority, not current PID ownership. Existing
   stop validation/signalling behavior remains unchanged.

2. **Minimal receipt transaction.** Under the selected attempt's existing
   `control.lock`, reread and validate the stop evidence and parent JSON object.
   Missing/unreadable/malformed receipts are errors, never recreated. If its
   `job` is absent or identifies another valid attempt, return `already
   released` without any write; a malformed `job` value is an error. A matching
   receipt must have neither cached `result` nor a completed-delivery flag;
   inconsistent completion evidence is unverifiable and cannot be released.
   Only for this validated matching release, create/open sibling
   `<item>.release.lock`, take its exclusive flock and reread the receipt before
   atomically saving a copy without `job`. Preserve all other fields, including
   consumed IDs, snapshots, unknown fields and recorded change identities.
   No release marker or second metadata commit is needed: absence of the
   matching reference makes retries idempotent, including a crash after the
   atomic replace. The release lock is an inert coordination file, not a retry
   trigger; an interrupted write may leave it present without releasing the
   receipt. Report write errors with exit 1 and never truncate the old receipt.

3. **Collection coordinates with the new writer.** In
   `nightshift/specloop_intake.py`, protect job-bearing receipt refresh,
   collection, validation and receipt persistence with the selected job's
   `control.lock`. Include `_initialize_revision_feedback()`'s read/modify/save
   when a revision receipt has a job; it currently precedes `_collect()`.
   Open an existing release lock without creating it. Lock order is always
   job control, then receipt release lock; never reacquire a flock through a
   second descriptor. Factor a private already-locked collection helper in
   `specloop_job.py` while preserving the public `collect()` contract. Compare
   the freshly read receipt's job with the selected ID before any mutation.
   If release or replacement won, discard the stale snapshot and defer that
   item until the next poll; do not return a stopped result or save stale data.
   If collection sees stop intent first, it returns pending and performs no
   subsequent receipt write. Tests use barriers to exercise both orderings,
   including legacy initialization. No network operation, process wait or
   signal runs while these locks are held; fetch legacy migration inputs before
   locking and revalidate the receipt before committing them.

   Ordinary no-job reads, blocked polling, and existing-PR recovery never create
   the release lock or its parent directory. Preserve the exact filesystem
   assertions in `tests/test_specloop_intake.py` around lines 318 and 467. No
   blanket receipt-lock context is added around every intake call. This design
   coordinates release with the existing single poller; it does not introduce
   support for concurrent independent daemon schedulers.

4. **Next-poll behavior.** With no job reference and no result, proposal and
   admission paths follow their existing no-running-job handling; revisions
   preserve `previous` as the feedback ledger while selecting a fresh snapshot.
   Keep recorded proposal identity available for existing-PR recovery even
   when the released receipt lacks a result; do not accidentally replace a
   legacy identity with a title-derived one during recovery. Only new rounds
   adopt the existing new-name rules. Normal trusted triggers, open-item state,
   `needs-human` exclusions, pause, issue blockers and stage-specific health
   gates still decide whether work starts. Existing open-PR or committed-run
   recovery wins over duplicate work. Approval-head binding remains unchanged.
   A stopped result is never delivered or used to acknowledge feedback. Already
   consumed IDs remain consumed; stopped unacknowledged feedback remains eligible
   subject to existing legacy migration rules. Unreleased stopped attempts
   retain today's hold across restart, resume and relabeling.

5. **Immutable build feedback.** In the admitted branch of `revise_pending()`,
   write UTF-8 JSON to an absolute
   `<item>.jobs/feedback-<fresh-uuid>.json` before dispatch, using exclusive file
   creation and closing it before `_start()`. Keep `--feedback-format json`
   and the existing complete-block serialization. Each invocation, including
   synchronous injected runners, gets its own file even with an unchanged event
   or round marker. Existing attempts keep their old event-named inputs; no
   migration, renaming or cleanup occurs. Write failure prevents dispatch and
   follows existing failure handling. Do not change comment-marker semantics
   merely to obtain unique input names. A retained `request.json` must continue
   to resolve to the exact bytes that attempt received after subsequent edits,
   release, replacement and restart.

6. **CLI and documentation.** Extend `_jobs()` and argparse validation in
   `nightshift/cli.py` before configuration/credential loading. Successful
   release prints `<id>: released`; no-op prints `<id>: already released`;
   both exit 0. Refusals print a diagnostic to stderr and exit 1; malformed
   command syntax exits 2. Inventory continues to show the historical attempt
   as stopped, not running or finished. Update the root README recovery block,
   `KNOWN-LIMITATIONS.md`'s stopped-job paragraph, and
   `docs/specloop/README.md`'s operations paragraph together. Replace the manual
   receipt-edit workflow for this case with stop, inspect, release, and normal
   next-poll eligibility. Cite issue #34, not an invented implementation PR.
   Retain inspection guidance about committed effects, independent supervisors,
   revision commit/result crash boundaries, retention and automatic-retry limits.

7. **Validation and preserved contracts.** Use offline fake GitHub/status
   runners, temporary receipts, isolated local locks and controlled process
   barriers. Add `test_released_stopped_job_allows_a_new_round`,
   `test_release_refuses_a_running_job`, `test_release_is_idempotent`, and
   `test_released_revision_keeps_stopped_attempt_input`. Parameterize stage
   coverage for proposals, admissions, spec revisions and build revisions.
   Include unchanged-event feedback edits, a replacement job followed by a
   second release of the old ID, invalid records, atomic-save failure, stale
   collection and legacy migration. Keep existing assertions verbatim; fixture
   or fake setup changes must model the new state accurately and each must be
   explained in the implementation PR. Run focused tests with each task, then
   require green `uv run pytest` for implementation acceptance.

Human checks for the implementation PR, never implementing-agent tasks:

- On the next real daemon stop during delivery, record the job ID and inspect
  local/remote effects, run release, and check that the stopped result stays
  suppressed while the next poll recovers or starts work only when eligible.
  CI can exercise barriers and local processes but cannot prove that live
  daemon/GitHub delivery boundary. Do not claim this check passed until observed.
- Read the three changed operator passages together; verify links and current
  issue/PR identities, and confirm they no longer call issue #34's missing
  command an open gap or imply rollback/automatic retry.

## Risks / Trade-offs

- Release adds a receipt writer. Lock scope, stale legacy migration, and lock
  ordering are the main correctness risks; regression tests must force the
  interleavings instead of relying on sleeps. The receipt lock is created only
  by a validated release, protecting filesystem-sensitive poll behavior.
- A stopped admission or revision may already have committed effects. Existing
  admission recovery avoids a duplicate admission when durable state exists;
  revision commit-before-result replay remains an explicit unresolved limit.
  Releasing is permission for normal intake, not rollback or exactly-once work.
- Immutable feedback files increase retained disk usage. Existing attempt
  retention is intentional; garbage collection is outside this change.
- Old/corrupt records without trustworthy stop evidence require diagnosis;
  refusing them is preferable to treating a missing result as proof of stop.
- The checkout's missing `main` branch must be handled by the caller's normal
  checkout setup if encountered during implementation. This draft requires no
  branch creation and does not change repository base-selection behavior.

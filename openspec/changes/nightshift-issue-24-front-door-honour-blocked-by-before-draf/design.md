## Context

`propose_pending` in `nightshift/specloop_intake.py` lists issues armed with
`labels.ready` or `labels.spec_draft`, then its `propose(number)` closure reads
the current issue and any durable proposal receipt. `_collect` handles running
jobs before the closure decides whether to deliver a result or start another
round. Existing PR lookup and receipt handling also recover interrupted
delivery and distinguish replacement rounds after a PR is closed.

The worker path already uses `queue.blockers(body)` and
`queue.open_blockers(repo, numbers, runner=...)`. The parser recognizes explicit
`blocked-by:` lines, including multiple numbers and tolerated quote/bullet
prefixes, and deduplicates numbers. The lookup treats only `CLOSED` as satisfied;
lookup failures caught by the helper and missing/non-closed states count as
open. Front-door PR bodies already include `Closes #N`.

## Goals / Non-Goals

Goals: prevent a new drafting round while any explicit dependency remains open,
retry naturally on later polls, preserve all state on blocked skips, and keep
existing job collection and delivery working even when a blocker reopens.

Non-goals: changing the worker, queue helpers, dependency syntax, admission,
publication, PR revision, trust rules, receipt format, or poll scheduling.
Dependency graph traversal, cycle detection, cancellation of running work and
verification that a closed issue really has a merged PR are outside this change.
Implementation is confined to `nightshift/specloop_intake.py` and additive tests
in `tests/test_specloop_intake.py`.

## Decisions

1. **Gate only the new-round branch.** In `propose(number)`, insert the check
   after the existing `if pr is not None` and `if receipt` delivery branches
   have returned, immediately before the final `if drafting_events is None`
   and construction of a new receipt. Call exactly
   `queue.open_blockers(repo.name, queue.blockers(raw["body"]), runner=gh)`.
   This uses the freshly fetched body, not the list response or saved idea.
   Existing logic decides whether an old receipt still represents delivery or
   a new round is required. A re-armed question/rejection round or replacement
   for a closed PR passes through the same gate; discarding a receipt in memory
   must not delete or overwrite its file when blocked.

2. **Return a normal skip with no durable effects.** If the helper returns any
   numbers, use a module logger (`logging.getLogger(__name__)`) at INFO level
   and return `None`. For one blocker the message is exactly
   `#7 is blocked by #6 — not drafting`; for several it is
   `#7 is blocked by #5, #6 — not drafting`, in helper-returned order, listing
   only unsatisfied blockers. Do not create a receipt or job, save an existing
   receipt, push a branch, create/edit a PR, change labels or post/edit comments.
   A blocked issue is neither an error nor a pending background job; processing
   of other issues continues through `_each`.

3. **Use existing dependency semantics on each eligible poll.** Do not cache
   blocker state. An empty list allows the normal new-round path, including
   existing trust checks. All listed dependencies must close before drafting
   starts. Keep helper behavior for unreadable blockers without adding a new
   exception policy or changing `nightshift/queue.py`. No dependency lookups
   are added to running-job handling, completed-receipt delivery or existing
   open-PR recovery. Existing eligibility and pause rules still apply.

4. **Test through injected boundaries without a host application.** Add tests
   only in `tests/test_specloop_intake.py`. Use its `poll`, `Commands`, `repo`
   fixture and a new local wrapper/subclass around `DraftGh` to distinguish
   issue-body reads from blocker `--json state` reads. Keep production queue
   helpers active so runner forwarding, body parsing and unreadable lookup
   behavior are exercised. Do not edit shared fixtures or existing tests.
   Capture INFO logs with `caplog` and keep receipts under `tmp_path`.

   Required tests:
   - `test_blocked_issue_is_not_drafted_until_blockers_close`: exercise both
     intake labels, repeated open polls, multiple blockers with partial closure,
     and a lookup raising `queue.GhError`. Assert no draft until all are closed,
     fresh state reads on each eligible poll, the skip log, and exactly one
     normal proposal and delivery after closure without re-arming.
   - `test_blocked_issue_changes_no_labels_or_comments`: snapshot labels,
     comments, PR state and receipt bytes across repeated blocked polls. Cover
     a fresh issue with no receipt and re-armed rounds after questions/rejection
     or a closed PR with an old receipt. Assert no GitHub mutation, CLI launch,
     git command or receipt write; an existing receipt remains byte-identical.
   - `test_finished_draft_is_delivered_even_if_a_blocker_reopens`: start with a
     closed dependency, finish drafting but interrupt delivery (for example,
     fail the push), reopen the blocker, then poll again. Assert the saved
     result is delivered, its receipt becomes delivered, and no second propose
     or blocker lookup occurs during recovery.

   Add focused tests for a running background job (inject job start/collection,
   observe the normal pending callback, then collect and deliver its result),
   existing open-PR recovery without a receipt, and a body with no dependencies.
   Mark the new tests with the scenario IDs listed in `tasks.md`. Run the intake
   tests during implementation, then `uv run pytest` for acceptance.

5. **Keep live closure timing as a human check.** Unit tests can simulate
   `OPEN` to `CLOSED` across polls, but cannot prove real GitHub `Closes #N`
   processing or daemon timing. Matt checks the first chained pair: arm both
   issues, confirm the dependent stays armed and undrafted while its blocker
   is open, merge the blocker's PR, observe the linked issue close, then verify
   the dependent drafts on a subsequent poll without manual re-arming. Record
   the issue/PR links and observed order; do not treat CI as proof of this check.

## Risks / Trade-offs

- Each otherwise eligible new round adds one GitHub state lookup per distinct
  blocker. Repeated logs and reads while blocked are deliberate; no new cache
  or poll delay is introduced.
- An unreadable dependency delays drafting until a later poll can establish
  closure. This matches the existing worker helper's conservative behavior.
- A dependency can reopen after the check. The gate is a poll-time decision,
  not a lock; work already started continues by design.
- Manually closing an issue satisfies the helper even without a merge. This
  change preserves that contract and relies on the usual `Closes #N` workflow.
- Placing the check too early would stall delivery; placing it too late could
  mutate a receipt or escalate an issue before skipping. The lifecycle and
  byte-preservation tests protect the chosen placement.
